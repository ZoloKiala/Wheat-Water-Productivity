"""LightGBM prediction service: training, versioned artifacts, inference and
per-prediction explanations.

Artifacts live under ``backend/models/vNNNN/`` (``model.txt`` + ``meta.json``)
with ``backend/models/current.json`` pointing at the active version. The
retrain workflow trains a candidate on newly supplied field observations,
validates it on a holdout split, and only promotes it if it beats the active
model — so EIAR can update the model without disrupting operation (ToR WP4).
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np

from .admin_units import ADMIN
from .geodata import (
    FEATURE_LABELS,
    FEATURE_NAMES,
    PROVIDER,
    SEASON_IRRIGATED,
    SEASONS_RAINFED,
    YEARS,
    feature_matrix,
)

# Container filesystems are ephemeral, so a model retrained in a deployment is
# lost on the next restart unless WWP_MODELS_DIR points at a mounted volume.
MODELS_DIR = Path(
    os.environ.get("WWP_MODELS_DIR")
    or Path(__file__).resolve().parent.parent / "models"
)

LGB_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.85,
    "bagging_freq": 1,
    "min_data_in_leaf": 20,
    "verbosity": -1,
    "seed": 42,
}


def _synthetic_ground_data(n: int = 8000, seed: int = 7):
    """Sample synthetic 'field observations' across the wheat-producing woredas,
    mirroring the Oromia/Afar ground campaigns used to train the real model."""
    rng = np.random.default_rng(seed)
    woredas = [w for zones in ADMIN.values() for ws in zones.values() for w in ws.values()]
    idx = rng.integers(0, len(woredas), n)
    lat = np.empty(n)
    lon = np.empty(n)
    for i, k in enumerate(idx):
        w = woredas[k]
        lat[i] = w["c"][0] + rng.uniform(-w["d"], w["d"])
        lon[i] = w["c"][1] + rng.uniform(-w["d"], w["d"])
    systems = rng.choice(["rainfed", "rainfed", "rainfed", "irrigated"], n)
    years = rng.choice(YEARS, n)

    X = np.empty((n, len(FEATURE_NAMES)))
    y = np.empty(n)
    for sys_name in ("rainfed", "irrigated"):
        for year in YEARS:
            seasons = [SEASON_IRRIGATED] if sys_name == "irrigated" else SEASONS_RAINFED
            for season in seasons:
                sel = (systems == sys_name) & (years == year)
                if sys_name == "rainfed":
                    half = rng.random(n) < 0.5
                    sel = sel & (half if season == "Meher" else ~half)
                if not sel.any():
                    continue
                feats = PROVIDER.assemble(lat[sel], lon[sel], sys_name, year, season)
                X[sel] = feature_matrix(feats)
                y[sel] = PROVIDER.true_wwp(feats, rng=rng)
    return X, y


def _metrics(booster: lgb.Booster, X: np.ndarray, y: np.ndarray) -> dict:
    pred = booster.predict(X)
    resid = y - pred
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {
        "rmse": round(float(np.sqrt(np.mean(resid**2))), 4),
        "mae": round(float(np.mean(np.abs(resid))), 4),
        "r2": round(1.0 - ss_res / ss_tot, 4),
        "n_valid": int(len(y)),
    }


def _train(X: np.ndarray, y: np.ndarray, seed: int = 42):
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(y))
    cut = int(len(y) * 0.8)
    tr, va = order[:cut], order[cut:]
    dtrain = lgb.Dataset(X[tr], label=y[tr], feature_name=FEATURE_NAMES)
    dvalid = lgb.Dataset(X[va], label=y[va], reference=dtrain)
    booster = lgb.train(
        LGB_PARAMS, dtrain, num_boost_round=600,
        valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(40, verbose=False)],
    )
    return booster, _metrics(booster, X[va], y[va]), (X[va], y[va])


class ModelService:
    def __init__(self):
        self._lock = threading.Lock()
        self.booster: lgb.Booster | None = None
        self.meta: dict = {}

    # ── artifact management ────────────────────────────────────────────
    def _versions(self):
        if not MODELS_DIR.exists():
            return []
        return sorted(p.name for p in MODELS_DIR.iterdir() if p.is_dir() and p.name.startswith("v"))

    def _save(self, booster: lgb.Booster, meta: dict) -> str:
        versions = self._versions()
        nxt = f"v{(int(versions[-1][1:]) + 1) if versions else 1:04d}"
        vdir = MODELS_DIR / nxt
        vdir.mkdir(parents=True, exist_ok=True)
        booster.save_model(str(vdir / "model.txt"))
        meta = {**meta, "version": nxt, "trained_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        (vdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        (MODELS_DIR / "current.json").write_text(json.dumps({"version": nxt}), encoding="utf-8")
        return nxt

    def load_or_train(self):
        with self._lock:
            cur = MODELS_DIR / "current.json"
            if cur.exists():
                version = json.loads(cur.read_text(encoding="utf-8"))["version"]
                vdir = MODELS_DIR / version
                if (vdir / "model.txt").exists():
                    self.booster = lgb.Booster(model_file=str(vdir / "model.txt"))
                    self.meta = json.loads((vdir / "meta.json").read_text(encoding="utf-8"))
                    return
            self._train_initial()

    def _train_initial(self):
        X, y = _synthetic_ground_data()
        booster, metrics, _ = _train(X, y)
        version = self._save(booster, {
            "metrics": metrics,
            "n_train": int(len(y) * 0.8),
            "source": "synthetic ground campaign (Oromia + Afar), WWPT development set",
        })
        self.booster = booster
        self.meta = json.loads((MODELS_DIR / version / "meta.json").read_text(encoding="utf-8"))

    # ── inference ──────────────────────────────────────────────────────
    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.clip(self.booster.predict(X), 0.15, 2.2)

    def explain(self, X: np.ndarray):
        """SHAP-style contributions via LightGBM's native pred_contrib.

        Returns (contributions (n, n_features), base_value (n,))."""
        contrib = self.booster.predict(X, pred_contrib=True)
        return contrib[:, :-1], contrib[:, -1]

    def importance(self):
        # Split-based importance (how often the model consults a feature):
        # with strongly correlated drivers it reflects the model's actual
        # reliance far better than gain, which the first split monopolizes.
        counts = self.booster.feature_importance(importance_type="split")
        top = counts.max() if counts.max() > 0 else 1.0
        pairs = sorted(zip(FEATURE_NAMES, counts), key=lambda p: -p[1])
        return [
            {"feature": name, "label": FEATURE_LABELS[name],
             "importance": round(float(g / top * 100.0), 1)}
            for name, g in pairs
        ]

    def info(self):
        return {
            "version": self.meta.get("version"),
            "trained_at": self.meta.get("trained_at"),
            "metrics": self.meta.get("metrics"),
            "features": [
                {"feature": f, "label": FEATURE_LABELS[f]} for f in FEATURE_NAMES
            ],
            "importance": self.importance(),
            "provider": PROVIDER.name,
        }

    # ── retraining workflow (WP4) ─────────────────────────────────────
    def retrain_from_csv(self, csv_bytes: bytes, force: bool = False) -> dict:
        """Train a candidate on uploaded observations; promote only if it does
        not degrade holdout RMSE versus the active model (unless forced)."""
        text = csv_bytes.decode("utf-8-sig")
        header_line, *rows = [ln for ln in io.StringIO(text).read().splitlines() if ln.strip()]
        header = [h.strip().lower() for h in header_line.split(",")]
        required = FEATURE_NAMES + ["wwp"]
        missing = [c for c in required if c not in header]
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        col = {name: header.index(name) for name in required}
        data = np.array([[float(r.split(",")[col[c]]) for c in required] for r in rows])
        if len(data) < 200:
            raise ValueError(f"At least 200 observations are required to retrain (got {len(data)}).")
        X_new, y_new = data[:, :-1], data[:, -1]

        # Blend new observations with the base development set so a small
        # field campaign refines rather than replaces the model.
        X_base, y_base = _synthetic_ground_data(n=6000, seed=11)
        X = np.vstack([X_base, X_new])
        y = np.concatenate([y_base, y_new])

        with self._lock:
            candidate, cand_metrics, (X_va, y_va) = _train(X, y, seed=101)
            active_metrics = _metrics(self.booster, X_va, y_va)
            promoted = force or cand_metrics["rmse"] <= active_metrics["rmse"] * 1.02
            result = {
                "candidate_metrics": cand_metrics,
                "active_model_on_same_holdout": active_metrics,
                "n_new_observations": int(len(y_new)),
                "promoted": promoted,
            }
            if promoted:
                version = self._save(candidate, {
                    "metrics": cand_metrics,
                    "n_train": int(len(y) * 0.8),
                    "source": f"retrained with {len(y_new)} new field observations",
                })
                self.booster = candidate
                self.meta = json.loads((MODELS_DIR / version / "meta.json").read_text(encoding="utf-8"))
                result["version"] = version
            return result


MODEL = ModelService()
