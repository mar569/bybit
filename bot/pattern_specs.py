"""Критерии графических фигур строго по BuyHold / analiz.md."""
from __future__ import annotations

# Допуски по цене (крипта intraday) — уже, чем «на глаз»
DOUBLE_EXTREMUM_TOLERANCE_PCT = 0.55
HEAD_SHOULDER_TOLERANCE_PCT = 2.2
LEVEL_TOUCH_TOLERANCE_PCT = 0.30
THREE_INDIANS_TOLERANCE_PCT = 0.65  # допуск к формуле 1.272 / трендовой
EXPANDING_MIN_SWINGS = 6

# Минимальные расстояния в барах (статья: 6–8 свечей для двойной вершины)
DOUBLE_MIN_BARS_BETWEEN = 6
FLAG_POLE_MIN_BARS = 4
FLAG_POLE_MAX_BARS = 14
FLAG_BODY_MIN_BARS = 5
FLAG_BODY_MAX_BARS = 18
PENNANT_BODY_MAX_BARS = 12  # вымпел сжатее обычного треугольника
FLAG_PARALLEL_SLOPE_RATIO = 0.55  # |slope_top−slope_bot| / max(|s|) — флаг ≈ параллель
TRIANGLE_MIN_SWINGS = 4
WEDGE_MIN_SWINGS = 4
# Чашка с ручкой — по решению пользователя в боте НЕ используем
CUP_ENABLED = False
CUP_MIN_BARS = 16          # оставлено для совместимости; детектор не вызывается
CUP_HANDLE_MAX_BARS = 16
CUP_RIM_TOLERANCE_PCT = 4.0
THREE_INDIANS_MIN_BARS = 5
TRIPLE_EXTREMUM_TOLERANCE_PCT = 0.65
TRIPLE_MIN_BARS_BETWEEN = 5
TRIPLE_MAX_SPAN_BARS = 80
# Объём: сжатие внутри фигуры / всплеск на пробое
VOLUME_CONTRACT_RATIO = 0.78      # body_vol / pole_vol ≤ это → сжатие
VOLUME_BREAKOUT_SPIKE = 1.35      # break_vol / body_vol ≥ это → подтверждение
DIAMOND_MIN_SWINGS = 6
RECTANGLE_MIN_BARS = 10
RECTANGLE_MAX_RANGE_PCT = 2.8

# Формулы из статьи
THREE_INDIANS_FIB = 1.272
TARGET_POLE_FACTOR = 0.85          # 0.8…1.0 минус страховка
TARGET_TRIANGLE_FACTOR = 0.90
TARGET_HS_FACTOR = 1.0
TARGET_RECTANGLE_FACTOR = 0.80     # ширина −20%

# Строгий скоринг: не рисовать «всё подряд»
MIN_PATTERN_CONFIDENCE = 0.68
MIN_TRADE_PATTERN_CONFIDENCE = 0.75
MIN_DRAW_CONFIDENCE = 0.70
MAX_CHART_PATTERNS = 1             # только primary на графике
MAX_REPORT_PATTERNS = 2            # в тексте максимум 2 непересекающихся

PATTERN_LABELS_RU: dict[str, str] = {
    "double_top": "Двойная вершина",
    "double_bottom": "Двойное дно",
    "triple_top": "Тройная вершина",
    "triple_bottom": "Тройное дно",
    "head_shoulders": "Голова и плечи",
    "inverse_head_shoulders": "Перевёрнутая ГиП",
    "flag": "Флаг",
    "pennant": "Вымпел",
    "triangle_symmetric": "Симметричный треугольник",
    "triangle_ascending": "Восходящий треугольник",
    "triangle_descending": "Нисходящий треугольник",
    "wedge_rising": "Восходящий клин",
    "wedge_falling": "Нисходящий клин",
    "rectangle": "Прямоугольник",
    "false_breakout": "Ложный пробой",
    "one_two_three": "1-2-3 разворот",
    "cup_handle": "Чашка с ручкой",
    "inverse_cup_handle": "Перевёрнутая чашка с ручкой",
    "baskerville_bullish": "Собака Баскервилей ↑",
    "baskerville_bearish": "Собака Баскервилей ↓",
    "three_indians": "Три индейца",
    "diamond": "Ромб (бриллиант)",
    "expanding_triangle": "Расходящийся треугольник",
}

# Семейства, которые нельзя показывать вместе (одна зона → один паттерн)
OVERLAP_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({
        "double_top",
        "double_bottom",
        "triple_top",
        "triple_bottom",
        "three_indians",
        "head_shoulders",
        "inverse_head_shoulders",
        "baskerville_bullish",
        "baskerville_bearish",
    }),
    frozenset({
        "flag",
        "pennant",
        "triangle_symmetric",
        "triangle_ascending",
        "triangle_descending",
        "rectangle",
    }),
    frozenset({"wedge_rising", "wedge_falling", "expanding_triangle", "diamond"}),
    frozenset({"false_breakout", "one_two_three"}),
)
