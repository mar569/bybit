"""Ручной TA-анализ: парсинг тикера и таймфрейма для отдельного чата."""
from __future__ import annotations

import re

MANUAL_TA_TIMEFRAMES: tuple[int, ...] = (5, 10, 15, 30, 60)
# Callback-алиасы → normalize_chart_source() → единый PRO annotated.
MANUAL_TA_CHART_SOURCES: tuple[str, ...] = ("annotated", "tv_annotated", "annotated_pro")


def manual_ta_use_simple_chart(_chart_source: str | None) -> bool:
    """Ручной TA всегда рисуется minimal-chart, без PRO/TV-панелей на PNG."""
    return True
MTA_CALLBACK_PREFIX = "mta|"
MTW_CALLBACK_PREFIX = "mtw|"
MTC_CALLBACK_PREFIX = "mtc|"
MTCW_CALLBACK_PREFIX = "mtcw|"
MTA_ALERT_CALLBACK_PREFIX = "mtaa|"
MTA_INTENT_CALLBACK_PREFIX = "mtai|"
MTA_MUTE_CALLBACK_PREFIX = "mtam|"
MTAI_CALLBACK_PREFIX = "mtai|"
MTW_CANCEL_CALLBACK = "mtw|cancel|0"
MTA_WIZARD_KEY = "mta_wizard"


def normalize_symbol(raw: str) -> str | None:
    text = raw.strip().upper().replace("/", "").replace("-", "").replace(" ", "")
    if not text:
        return None
    if text.endswith("USDT"):
        base = text[:-4]
    elif text.endswith("USD"):
        base = text[:-3]
    else:
        base = text
    base = re.sub(r"[^A-Z0-9]", "", base)
    if len(base) < 2 or len(base) > 20:
        return None
    return f"{base}USDT"


def parse_manual_ta_input(text: str) -> tuple[str | None, int | None]:
    """Возвращает (symbol, interval_minutes или None)."""
    if not text:
        return None, None
    cleaned = text.strip()
    interval: int | None = None
    tf_match = re.search(
        r"\b(5|10|15|30|60)\s*m(?:in(?:ute)?s?)?\b|(?:^|\s)(1)\s*h(?:our)?s?\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if tf_match:
        interval = 60 if tf_match.group(2) else int(tf_match.group(1))
        cleaned = cleaned[: tf_match.start()] + cleaned[tf_match.end() :]
    cleaned = cleaned.strip(" ,.;:")
    if not cleaned:
        return None, interval
    token_match = re.search(r"([A-Za-z0-9]{2,20}(?:USDT|USD)?)", cleaned.replace("/", ""))
    if not token_match:
        return None, interval
    return normalize_symbol(token_match.group(1)), interval


def pattern_chart_hours(interval_minutes: int) -> int:
    """Окно истории для поиска графических фигур (анализ, не зум экрана)."""
    return {5: 36, 10: 48, 15: 72, 30: 72, 60: 96}.get(interval_minutes, 48)


def chart_display_hours(interval_minutes: int, *, configured: int | None = None) -> int:
    """Сколько часов показывать на графике по умолчанию."""
    from .chart_display_policy import ed_chart_visible_hours

    env_vis = ed_chart_visible_hours()
    defaults = {5: 24, 10: 36, 15: 56, 30: 56, 60: 72}
    base = env_vis if env_vis is not None else defaults.get(interval_minutes, 24)
    if configured is None:
        return base
    return max(4, min(int(configured), 96))


def structure_aware_display_hours(
    *,
    interval_minutes: int,
    analysis_hours: int,
    configured: int | None,
    drawdown_pct: float = 0.0,
    structure_span_bars: int = 0,
    fib_span_bars: int = 0,
    max_display_hours: int | None = None,
) -> int:
    """Расширяет зум, чтобы на экране был весь импульс/дамп (как DEXE с 9 утра)."""
    cap = int(max_display_hours if max_display_hours is not None else analysis_hours)
    cap = max(4, min(cap, analysis_hours))
    base = chart_display_hours(interval_minutes, configured=configured)
    if configured is not None and configured > 0:
        return max(4, min(int(configured), cap))
    need = base
    # Крупный дамп/памп — показать больше истории
    if drawdown_pct >= 40.0:
        need = max(need, 14 if interval_minutes <= 5 else 16)
    elif drawdown_pct >= 20.0:
        need = max(need, 12)
    elif drawdown_pct >= 8.0:
        need = max(need, base + 4)
    # Покрыть импульсную ногу Fib (+ запас ~2ч)
    span = max(structure_span_bars, fib_span_bars)
    if span > 0 and interval_minutes > 0:
        span_h = int(span * interval_minutes / 60.0) + 2
        need = max(need, min(span_h, cap))
    return max(4, min(need, cap))


def manual_ta_hours(interval_minutes: int) -> int:
    """Сколько часов свечей грузим для ручного разбора (больше, чем зум по умолчанию)."""
    from .chart_display_policy import ed_chart_visible_hours

    env_vis = ed_chart_visible_hours()
    if env_vis is not None:
        return max(env_vis, 12)
    return {5: 36, 10: 48, 15: 72, 30: 72, 60: 96}.get(interval_minutes, 48)


def compute_structure_bar_span(ta: object, bars: list) -> int:
    """Самая ранняя точка структуры на загруженных барах → сколько баров назад от «сейчас»."""
    if not bars:
        return 0
    n = len(bars)
    earliest = n - 1

    def touch(idx: int) -> None:
        nonlocal earliest
        if 0 <= idx < n:
            earliest = min(earliest, idx)

    for tl in list(getattr(ta, "trend_lines", None) or []):
        touch(int(getattr(tl, "start_idx", n - 1)))

    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        touch(int(getattr(cons, "start_idx", n - 24)))

    for sw in list(getattr(ta, "swings", None) or [])[-12:]:
        touch(int(getattr(sw, "index", n - 1)))

    smc = getattr(ta, "smc", None)
    if smc is not None:
        for m in list(getattr(smc, "markers", None) or []):
            touch(int(getattr(m, "index", n - 1)))

    for attr in ("breakout_level", "breakdown_level"):
        _ = getattr(ta, attr, None)

    try:
        from .chart_reference_levels import session_reference_levels

        refs = {r.kind: float(r.price) for r in session_reference_levels(bars)}
        for kind, price in refs.items():
            if price <= 0:
                continue
            for i, bar in enumerate(bars):
                if kind == "daily_high" and float(bar.high) >= price * 0.999:
                    touch(i)
                    break
                if kind == "daily_low" and float(bar.low) <= price * 1.001:
                    touch(i)
                    break
    except Exception:
        pass

    seg = bars[-min(n, max(36, n // 2)) :]
    if seg:
        loc_hi = max(float(b.high) for b in seg)
        loc_lo = min(float(b.low) for b in seg)
        for i, bar in enumerate(bars):
            if float(bar.high) >= loc_hi * 0.9995:
                touch(i)
            if float(bar.low) <= loc_lo * 1.0005:
                touch(i)

    if n >= 8:
        hi_i = max(range(n), key=lambda i: float(bars[i].high))
        lo_i = min(range(n), key=lambda i: float(bars[i].low))
        touch(hi_i)
        touch(lo_i)

    return max(0, (n - 1) - earliest)


def _fib_span_bars(ta: object, bars: list) -> int:
    if not bars:
        return 0
    indices: list[int] = []
    for bucket in (
        getattr(ta, "elliott_draw_points", None),
        getattr(ta, "elliott_global_draw_points", None),
        getattr(ta, "elliott_local_draw_points", None),
    ):
        for p in list(bucket or []):
            i = int(getattr(p, "index", -1))
            if i >= 0:
                indices.append(i)
    if len(indices) < 2:
        return 0
    return max(indices) - min(indices)


def manual_chart_zoom_hours(
    ta: object,
    bars: list,
    *,
    interval_minutes: int,
    analysis_hours: int,
    configured: int | None,
) -> int:
    """Зум для ручного TA: расширяется под импульс/день мин-макс, но не шире загруженной истории."""
    span = compute_structure_bar_span(ta, bars)
    fib_span = _fib_span_bars(ta, bars)
    drawdown = float(getattr(ta, "drawdown_from_high_pct", 0) or 0)
    phase = str(getattr(ta, "phase", "") or "")

    zoom = structure_aware_display_hours(
        interval_minutes=interval_minutes,
        analysis_hours=analysis_hours,
        configured=configured,
        drawdown_pct=drawdown,
        structure_span_bars=span,
        fib_span_bars=fib_span,
        max_display_hours=analysis_hours,
    )

    if configured is not None and configured > 0:
        return zoom

    base = chart_display_hours(interval_minutes, configured=None)
    per_hour = max(1, 60 // max(1, interval_minutes))
    base_bars = base * per_hour
    # Структура занимает большую часть окна — показываем всю загруженную историю
    if span >= int(base_bars * 0.55):
        zoom = max(zoom, analysis_hours)
    if phase in {
        "impulse_up",
        "impulse_down",
        "correction_down",
        "correction_up",
        "breakout_setup",
        "consolidation",
    }:
        zoom = max(zoom, min(analysis_hours, base + 8))
    if drawdown >= 5.0:
        zoom = max(zoom, min(analysis_hours, base + 6))

    return max(6, min(int(zoom), analysis_hours))


def bars_per_hour(interval_minutes: int) -> int:
    return max(1, 60 // interval_minutes)


def build_mta_callback(symbol: str, interval_minutes: int) -> str:
    return f"{MTA_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}"


def build_mtw_callback(symbol: str, interval_minutes: int) -> str:
    return f"{MTW_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}"


def build_mtc_callback(symbol: str, interval_minutes: int, chart_source: str) -> str:
    return f"{MTC_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}|{chart_source}"


def build_mtcw_callback(symbol: str, interval_minutes: int, chart_source: str) -> str:
    return f"{MTCW_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}|{chart_source}"


def build_mtai_callback(symbol: str, interval_minutes: int) -> str:
    return f"{MTAI_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}"


def parse_mtai_callback(data: str) -> tuple[str, int] | None:
    return _parse_mta_style_callback(data, MTAI_CALLBACK_PREFIX)


def _parse_mta_style_callback(data: str, prefix: str) -> tuple[str, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data.split("|")
    if len(parts) != 3 or parts[0] != prefix.rstrip("|"):
        return None
    try:
        interval = int(parts[2])
    except ValueError:
        return None
    if interval not in MANUAL_TA_TIMEFRAMES:
        return None
    symbol = normalize_symbol(parts[1])
    if not symbol:
        return None
    return symbol, interval


def parse_mta_callback(data: str) -> tuple[str, int] | None:
    return _parse_mta_style_callback(data, MTA_CALLBACK_PREFIX)


def parse_mtw_callback(data: str) -> tuple[str, int] | None:
    return _parse_mta_style_callback(data, MTW_CALLBACK_PREFIX)


def _parse_chart_callback(data: str, prefix: str) -> tuple[str, int, str] | None:
    if not data.startswith(prefix):
        return None
    parts = data.split("|")
    if len(parts) != 4 or parts[0] != prefix.rstrip("|"):
        return None
    try:
        interval = int(parts[2])
    except ValueError:
        return None
    if interval not in MANUAL_TA_TIMEFRAMES:
        return None
    symbol = normalize_symbol(parts[1])
    if not symbol:
        return None
    chart_source = parts[3].strip().lower()
    if chart_source not in MANUAL_TA_CHART_SOURCES:
        return None
    return symbol, interval, chart_source


def parse_mtc_callback(data: str) -> tuple[str, int, str] | None:
    return _parse_chart_callback(data, MTC_CALLBACK_PREFIX)


def parse_mtcw_callback(data: str) -> tuple[str, int, str] | None:
    return _parse_chart_callback(data, MTCW_CALLBACK_PREFIX)


def build_mta_alert_callback(symbol: str, interval_minutes: int, side: str, mode: str = "breakout") -> str:
    side_norm = "short" if str(side).lower() == "short" else "long"
    mode_norm = str(mode).strip().lower()
    if mode_norm not in {"breakout", "retest", "volume"}:
        mode_norm = "breakout"
    return f"{MTA_ALERT_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}|{side_norm}|{mode_norm}"


def parse_mta_alert_callback(data: str) -> tuple[str, int, str, str] | None:
    if not data.startswith(MTA_ALERT_CALLBACK_PREFIX):
        return None
    parts = data.split("|")
    if len(parts) not in {4, 5} or parts[0] != MTA_ALERT_CALLBACK_PREFIX.rstrip("|"):
        return None
    symbol = normalize_symbol(parts[1])
    if not symbol:
        return None
    try:
        interval = int(parts[2])
    except ValueError:
        return None
    if interval not in MANUAL_TA_TIMEFRAMES:
        return None
    side = parts[3].strip().lower()
    if side not in {"long", "short"}:
        return None
    mode = "breakout"
    if len(parts) == 5:
        mode = parts[4].strip().lower()
    if mode not in {"breakout", "retest", "volume"}:
        return None
    return symbol, interval, side, mode


def build_mta_intent_callback(symbol: str, interval_minutes: int, side: str) -> str:
    side_norm = "short" if str(side).lower() == "short" else "long"
    return f"{MTA_INTENT_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}|{side_norm}"


def parse_mta_intent_callback(data: str) -> tuple[str, int, str] | None:
    if not data.startswith(MTA_INTENT_CALLBACK_PREFIX):
        return None
    parts = data.split("|")
    if len(parts) != 4 or parts[0] != MTA_INTENT_CALLBACK_PREFIX.rstrip("|"):
        return None
    symbol = normalize_symbol(parts[1])
    if not symbol:
        return None
    try:
        interval = int(parts[2])
    except ValueError:
        return None
    if interval not in MANUAL_TA_TIMEFRAMES:
        return None
    side = parts[3].strip().lower()
    if side not in {"long", "short"}:
        return None
    return symbol, interval, side


def build_mta_mute_callback(symbol: str, interval_minutes: int, action: str) -> str:
    act = str(action).strip().lower()
    if act not in {"mute", "stop", "unmute"}:
        act = "mute"
    return f"{MTA_MUTE_CALLBACK_PREFIX}{symbol.upper()}|{interval_minutes}|{act}"


def parse_mta_mute_callback(data: str) -> tuple[str, int, str] | None:
    if not data.startswith(MTA_MUTE_CALLBACK_PREFIX):
        return None
    parts = data.split("|")
    if len(parts) != 4 or parts[0] != MTA_MUTE_CALLBACK_PREFIX.rstrip("|"):
        return None
    symbol = normalize_symbol(parts[1])
    if not symbol:
        return None
    try:
        interval = int(parts[2])
    except ValueError:
        return None
    action = parts[3].strip().lower()
    if action not in {"mute", "stop", "unmute"}:
        return None
    return symbol, interval, action


_INTENT_SHORT_RE = re.compile(
    r"(?:хочу|открыть|войти|беру|смотрю|мой|иду\s+в)\s*(?:в\s+)?"
    r"(?:шорт|short|sell|продаж)",
    re.IGNORECASE,
)
_INTENT_LONG_RE = re.compile(
    r"(?:хочу|открыть|войти|беру|смотрю|мой|иду\s+в)\s*(?:в\s+)?"
    r"(?:лонг|long|buy|покуп)",
    re.IGNORECASE,
)
_INTENT_SHORT_BARE_RE = re.compile(r"^(?:шорт|short|sell)$", re.IGNORECASE)
_INTENT_LONG_BARE_RE = re.compile(r"^(?:лонг|long|buy)$", re.IGNORECASE)


def parse_user_trade_intent(text: str) -> str | None:
    """Распознать намерение пользователя: long / short. None если не похоже на идею сделки."""
    if not text:
        return None
    cleaned = text.strip()
    if _INTENT_SHORT_RE.search(cleaned) or _INTENT_SHORT_BARE_RE.match(cleaned):
        return "short"
    if _INTENT_LONG_RE.search(cleaned) or _INTENT_LONG_BARE_RE.match(cleaned):
        return "long"
    return None


def manual_ta_help_text() -> str:
    return (
        "<b>📐 Чат ручного TA-анализа</b>\n\n"
        "Отправьте <b>скрин</b> с подписью тикера или просто текст:\n"
        "• <code>GRASSUSDT</code>\n"
        "• <code>GRASS 10m</code>\n"
        "• <code>BTC 15m</code>\n"
        "• <code>MON 15m @0.034</code> — разбор как на скрине (цена)\n"
        "• <code>MON бар -2</code> — срез до 2-й свечи с конца\n\n"
        "Если таймфрейм не указан — выберите кнопку:\n"
        "<b>5m</b> · <b>10m</b> · <b>15m</b>\n\n"
        "После разбора нажмите <b>«Мой SHORT / LONG»</b> или напишите:\n"
        "<code>хочу шорт</code> · <code>открыть long</code>\n\n"
        "Алерты (пробой / ретест / объём): кнопки под графиком.\n"
        "<b>🔕 Монета OFF</b> — не слать повторы по монете 24ч.\n"
        "<b>⏹ Стоп алерты</b> — отменить активные алерты.\n\n"
        "Бот оценит вашу идею, покажет сценарий и пунктирный прогноз на графике."
    )


def manual_ta_wizard_start_text() -> str:
    return (
        "<b>📐 Ручной анализ</b>\n\n"
        "Отправьте данные <b>в любом порядке</b>:\n\n"
        "1️⃣ <b>Одним сообщением</b> — фото + подпись:\n"
        "   <code>GRASS 10m</code> или <code>BTCUSDT 15m</code>\n\n"
        "2️⃣ <b>Два шага</b> — сначала скрин, потом тикер текстом\n"
        "   (или наоборот: тикер → скрин)\n\n"
        "3️⃣ Если TF не указан — выберите кнопку <b>5m / 10m / 15m</b>\n\n"
        "4️⃣ После графика — ваша идея сделки:\n"
        "   кнопки <b>«Мой SHORT / LONG»</b> или текст <code>хочу шорт</code>\n"
        "   Бот оценит вход, сценарий и нарисует пунктир (откат / продолжение).\n\n"
        "Готовый разбор с графиком уйдёт в <b>чат ручного TA</b>.\n"
        "На графике: уровни, тренд, боковик, стрелки пробоя, сценарии.\n\n"
        "Отмена: <code>/cancel</code>"
    )
