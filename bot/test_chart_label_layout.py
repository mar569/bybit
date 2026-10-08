from __future__ import annotations

from bot.chart_label_layout import LabelBoard, _deconflict_ys


def test_near_prices_merge_keep_stronger_label():
    board = LabelBoard()
    board.add(104.45, "День макс. 104.45", "#58a6ff", priority=86, ref=104.0)
    board.add(104.48, "LTF-R сопр.", "#f85149", priority=48, ref=104.0)
    out = board.compact(current=104.0, max_labels=8)
    assert len(out) == 1
    assert "День макс" in out[0].text


def test_distinct_levels_kept():
    board = LabelBoard()
    board.add(105.0, "сопр. 105", "#f0c040", priority=90, ref=103.0)
    board.add(100.1, "День мин. 100.10", "#3fb950", priority=86, ref=103.0)
    board.add(96.96, "тейк 96.96", "#3fb950", priority=40, ref=103.0)
    out = board.compact(current=103.0, max_labels=8)
    assert len(out) == 3


def test_reserved_skips_duplicate_corridor():
    board = LabelBoard()
    board.reserve(104.45)
    board.add(104.46, "пробой 104.45", "#3fb950", priority=82, ref=104.0)
    assert board.items == []


def test_deconflict_ys_spread():
    ys = _deconflict_ys([104.4, 104.45, 104.5], y_min=96.0, y_max=106.0, min_frac=0.034)
    gaps = [ys[i] - ys[i - 1] for i in range(1, len(ys))]
    assert all(g >= (106.0 - 96.0) * 0.033 for g in gaps)
