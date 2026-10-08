from __future__ import annotations

import io

from PIL import Image

from .chart_tv_image import normalize_tv_export_size, prepare_tv_background, trim_tv_screenshot


def _black_with_center_chart() -> bytes:
    im = Image.new("RGB", (800, 600), (0, 0, 0))
    for x in range(200, 600):
        for y in range(80, 520):
            im.putpixel((x, y), (40, 50, 60))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_trim_removes_horizontal_margins():
    raw = _black_with_center_chart()
    out = trim_tv_screenshot(raw)
    im = Image.open(io.BytesIO(out))
    assert im.width < 800
    assert im.width > 350


def test_prepare_rejects_narrow_strip():
    im = Image.new("RGB", (100, 600), (50, 50, 50))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    assert prepare_tv_background(buf.getvalue()) is None


def test_normalize_export_size():
    im = Image.new("RGB", (400, 200), (30, 30, 30))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    out = normalize_tv_export_size(buf.getvalue())
    im2 = Image.open(io.BytesIO(out))
    assert im2.size == (1280, 704)
