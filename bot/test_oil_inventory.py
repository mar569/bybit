"""Tests for EIA/SPR inventory parse + interpretation."""
from __future__ import annotations

from bot.oil_inventory import (
    InventoryPoint,
    SeriesSnapshot,
    build_inventory_status,
    format_inventory_status,
    interpret_spr,
    parse_eia_hist_html,
)


_SAMPLE_HTML = """
<tr>
<td class='B6'>&nbsp;&nbsp;2026-Jun</td>
<td class='B5'>06/19&nbsp;</td>
<td class='B3'>331,191&nbsp;&nbsp;&nbsp;</td>
<td class='B5'>06/26&nbsp;</td>
<td class='B3'>325,655&nbsp;&nbsp;&nbsp;</td>
<td class='B5'>&nbsp;</td>
<td class='B3'>&nbsp;&nbsp;&nbsp;</td>
</tr>
<tr>
<td class='B6'>&nbsp;&nbsp;2026-Jul</td>
<td class='B5'>07/17&nbsp;</td>
<td class='B3'>311,447&nbsp;&nbsp;&nbsp;</td>
<td class='B5'>07/24&nbsp;</td>
<td class='B3'>307,650&nbsp;&nbsp;&nbsp;</td>
<td class='B5'>&nbsp;</td>
<td class='B3'>&nbsp;&nbsp;&nbsp;</td>
</tr>
Release Date: 7/29/2026
Next Release Date: 8/5/2026
"""


def test_parse_eia_hist_html_latest():
    pts = parse_eia_hist_html(_SAMPLE_HTML)
    assert len(pts) >= 2
    assert pts[-1].date_label == "2026-07-24"
    assert abs(pts[-1].mbbl - 307.65) < 0.01


def test_interpret_spr_very_low():
    level, sense = interpret_spr(307.65, -3.8)
    assert "мало" in level.lower() or "очень" in level.lower()
    assert "ушло" in sense.lower() or "резерв" in sense.lower() or "поддерж" in sense.lower()


def test_build_inventory_status_verdict():
    spr = SeriesSnapshot(
        name="SPR",
        series_id="WCSSTUS1",
        latest=InventoryPoint("2026-07-24", 307.65),
        prev=InventoryPoint("2026-07-17", 311.45),
        wow_mb=-3.8,
        points=(
            InventoryPoint("2026-07-17", 311.45),
            InventoryPoint("2026-07-24", 307.65),
        ),
    )
    comm = SeriesSnapshot(
        name="Commercial",
        series_id="WCESTUS1",
        latest=InventoryPoint("2026-07-24", 420.0),
        prev=InventoryPoint("2026-07-17", 425.0),
        wow_mb=-5.0,
        points=(
            InventoryPoint("2026-07-17", 425.0),
            InventoryPoint("2026-07-24", 420.0),
        ),
    )
    st = build_inventory_status(spr=spr, commercial=comm, cushing=None)
    text = format_inventory_status(st)
    assert "307.7" in text or "307.6" in text
    assert "Итог для нефти" in text
    assert "сюрприз" in text.lower()
    assert "Госрезерв" in text or "госрезерв" in text
    assert "draw" not in text.lower()
    assert "Cushing" not in text
    assert "поддерживает" in st.verdict_ru or "рост" in st.verdict_ru or "смешан" in st.verdict_ru
