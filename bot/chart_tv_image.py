"""Подготовка скрина TV: обрезка полей, нормальный размер под Telegram."""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# Как у SIGNAL_CHART_FIG_SIZE @ 160 dpi
TV_EXPORT_WIDTH = 1280
TV_EXPORT_HEIGHT = 704


def trim_tv_screenshot(png: bytes, *, lum_threshold: int = 38) -> bytes:
    """Убрать чёрные поля embed-виджета."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return png

    im = Image.open(io.BytesIO(png)).convert("RGB")
    arr = np.asarray(im)
    bright = (arr[:, :, 0].astype(np.int32) + arr[:, :, 1] + arr[:, :, 2]) > lum_threshold
    if not bright.any():
        return png
    rows = bright.any(axis=1)
    cols = bright.any(axis=0)
    y0, y1 = int(np.argmax(rows)), int(arr.shape[0] - np.argmax(rows[::-1]))
    x0, x1 = int(np.argmax(cols)), int(arr.shape[1] - np.argmax(cols[::-1]))
    pad = 6
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(arr.shape[1], x1 + pad)
    y1 = min(arr.shape[0], y1 + pad)
    w, h = x1 - x0, y1 - y0
    if w < 80 or h < 80:
        return png
    cropped = im.crop((x0, y0, x1, y1))
    out = io.BytesIO()
    cropped.save(out, format="PNG", optimize=True)
    return out.getvalue()


def normalize_tv_export_size(png: bytes) -> bytes:
    """Единый крупный PNG для Telegram (широкий, читаемый)."""
    try:
        from PIL import Image
    except ImportError:
        return png

    im = Image.open(io.BytesIO(png)).convert("RGB")
    im = im.resize((TV_EXPORT_WIDTH, TV_EXPORT_HEIGHT), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue()


def prepare_tv_background(png: bytes) -> bytes | None:
    """Trim + resize; None если после обрезки слишком узко (битый скрин)."""
    trimmed = trim_tv_screenshot(png)
    try:
        from PIL import Image
    except ImportError:
        return trimmed

    im = Image.open(io.BytesIO(trimmed))
    w, h = im.size
    if w < 200 or h < 120:
        logger.warning("TV screenshot too small after trim: %sx%s", w, h)
        return None
    if w / max(h, 1) < 1.25:
        logger.warning("TV screenshot too narrow (%sx%s) — likely bad capture", w, h)
        return None
    return normalize_tv_export_size(trimmed)
