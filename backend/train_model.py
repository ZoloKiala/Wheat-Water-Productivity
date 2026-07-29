"""CLI: train (or retrain) the LightGBM model and save a versioned artifact.

Usage:  python train_model.py
"""

from app.model_service import MODEL

if __name__ == "__main__":
    MODEL.load_or_train()
    info = MODEL.info()
    print(f"Active model: {info['version']}  (trained {info['trained_at']})")
    print(f"Holdout metrics: {info['metrics']}")
    print("Top features by gain:")
    for row in info["importance"][:5]:
        print(f"  {row['label']:<28} {row['importance']:>6.1f}")
