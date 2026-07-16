"""Критерии графических фигур (BuyHold / классический TA)."""
from __future__ import annotations

# Допуски по цене (крипта intraday)
DOUBLE_EXTREMUM_TOLERANCE_PCT = 0.8
HEAD_SHOULDER_TOLERANCE_PCT = 3.0
LEVEL_TOUCH_TOLERANCE_PCT = 0.35

# Минимальные расстояния в барах
DOUBLE_MIN_BARS_BETWEEN = 6
FLAG_POLE_MIN_BARS = 3
FLAG_POLE_MAX_BARS = 14
FLAG_BODY_MIN_BARS = 4
FLAG_BODY_MAX_BARS = 22
TRIANGLE_MIN_SWINGS = 4
WEDGE_MIN_SWINGS = 4
CUP_MIN_BARS = 14
CUP_HANDLE_MAX_BARS = 18
CUP_RIM_TOLERANCE_PCT = 5.0
THREE_INDIANS_MIN_BARS = 4
DIAMOND_MIN_SWINGS = 6

# Цели (BuyHold: страховка 15–25%)
TARGET_POLE_FACTOR = 0.85
TARGET_TRIANGLE_FACTOR = 0.90
TARGET_HS_FACTOR = 1.0

# Скоринг
MIN_PATTERN_CONFIDENCE = 0.55
MIN_TRADE_PATTERN_CONFIDENCE = 0.70

PATTERN_LABELS_RU: dict[str, str] = {
    "double_top": "Двойная вершина",
    "double_bottom": "Двойное дно",
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
}
