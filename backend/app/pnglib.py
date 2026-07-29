"""Minimal RGBA PNG encoder (stdlib only) for serving analysis rasters."""

import struct
import zlib

import numpy as np


def encode_png(rgba: np.ndarray) -> bytes:
    """Encode an (H, W, 4) uint8 array as a PNG byte string."""
    if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("expected (H, W, 4) uint8 array")
    h, w = rgba.shape[:2]
    # Prepend the per-row filter byte (0 = None) required by the PNG spec.
    rows = np.zeros((h, 1 + w * 4), dtype=np.uint8)
    rows[:, 1:] = rgba.reshape(h, w * 4)
    raw = rows.tobytes()

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
