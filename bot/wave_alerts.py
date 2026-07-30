"""Wave Watcher — сигналы только по волнам Эллиотта + Fib.

Сканирует топ-символы по klines → analyze_elliott_waves → жёсткий gate
(правила 1–5, Fib classic, зоны 2/4/C) → чат с графиком и «что ждём дальше».

Не заменяет Hot-сигналы OI; отдельный канал (TELEGRAM_WAVE_CHAT_ID /
anomaly chat / analysis).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .bybit_klines import BybitKlineCache, KlineBar
from .elliott_wave import ElliottWaveResult, analyze_elliott_waves
from .liquidation_alerts import base_ticker, coinglass_url, exchange_trade_url
from .ta_analysis import find_swing_points, fmt_price

if TYPE_CHECKING:
    from .scanner_engine import SignalEngine
    from .settings import ScannerSettings, SettingsManager

logger = logging.getLogger(__name__)

# Фазы, которые имеют торговый смысл для алертов (без «просто боковик»)
DEFAULT_WAVE_PHASES: tuple[str, ...] = (
    "impulse_2",
    "impulse_3",
    "impulse_4",
    "impulse_complete",
    "abc_C",
)

# Торговые сетапы (не watch). По умолчанию — волна 3 и отскок после 5.
DEFAULT_WAVE_SETUP_MODES: tuple[str, ...] = (
    "wave3_impulse",
    "wave5_bounce",
)

SETUP_PRIORITY: dict[str, int] = {
    "wave3_impulse": 0,
    "wave5_bounce": 1,
    "wave2_breakout": 2,
    "wave4_breakout": 3,
    "entry_ready": 4,
    "wave2_zone": 5,
    "wave4_zone": 6,
    "abc_c_zone": 7,
    "impulse_complete": 8,
    "path_active": 9,
    "structure_watch": 10,
}

SIDE_RU = {"long": "LONG", "short": "SHORT", "wait": "WAIT"}


@dataclass(frozen=True)
class WaveEvent:
    exchange: str
    symbol: str
    timestamp: float
    price: float
    side: str  # long | short | wait
    setup_kind: str
    phase: str
    label_ru: str
    detail: str
    importance: float = 0.0
    confidence: int = 0
    entry_ready: bool = False
    entry_mode: str = "wait"
    entry_price: float | None = None
    stop_price: float | None = None
    tp_prices: tuple[float, ...] = ()
    path_reason: str = ""
    path_bias: str = ""
    invalidation: float | None = None
    global_label: str = ""
    local_label: str = ""
    fib_note: str = ""
    expect_ru: str = ""
    # (label, open_time, price) — для точной отрисовки на графике
    ew_draw_ot: tuple[tuple[str, float, float], ...] = ()
    ew_global_ot: tuple[tuple[str, float, float], ...] = ()
    ew_local_ot: tuple[tuple[str, float, float], ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


def _pack_ew_points_ot(
    pts: list | None,
    bars: list[KlineBar],
) -> tuple[tuple[str, float, float], ...]:
    """Сериализация точек волны: label + open_time + price (индексы на другом окне неверны)."""
    if not pts or not bars:
        return ()
    out: list[tuple[str, float, float]] = []
    n = len(bars)
    for p in pts:
        label = str(getattr(p, "label", "") or "")
        price = float(getattr(p, "price", 0) or 0)
        idx = int(getattr(p, "index", -1))
        if not label or price <= 0:
            continue
        t = float(bars[idx].open_time) if 0 <= idx < n else 0.0
        out.append((label, t, price))
    return tuple(out)


def _enabled_setup_modes(settings: Any) -> frozenset[str]:
    raw = getattr(settings, "wave_setup_modes", DEFAULT_WAVE_SETUP_MODES)
    if raw is None:
        return frozenset(DEFAULT_WAVE_SETUP_MODES)
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return frozenset(parts) if parts else frozenset(DEFAULT_WAVE_SETUP_MODES)
    return frozenset(str(p).strip() for p in raw if str(p).strip())


def _setup_mode_allowed(setup_kind: str, enabled: frozenset[str]) -> bool:
    if setup_kind in enabled:
        return True
    # legacy alias
    if setup_kind == "entry_ready" and "wave2_breakout" in enabled:
        return True
    return False


def _enabled_phases(settings: Any) -> frozenset[str]:
    raw = getattr(settings, "wave_phases_enabled", DEFAULT_WAVE_PHASES)
    if raw is None:
        return frozenset(DEFAULT_WAVE_PHASES)
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return frozenset(parts) if parts else frozenset(DEFAULT_WAVE_PHASES)
    return frozenset(str(p).strip() for p in raw if str(p).strip())


def _phase_allowed(phase: str, enabled: frozenset[str]) -> bool:
    if not phase or phase == "unknown":
        return False
    if phase in enabled:
        return True
    # impulse_2 покрывает impulse_forming рядом с волной 2
    if phase.startswith("impulse_") and any(p.startswith("impulse_") for p in enabled):
        # строго: только явные impulse_N из списка
        return phase in enabled
    if phase.startswith("abc_") and "abc_C" in enabled and phase in {"abc_C", "abc_complete"}:
        return True
    if phase.startswith("complex_") and "abcde_triangle" in enabled:
        return True
    return False


def _fib_zone_note(ew: ElliottWaveResult) -> str:
    imp = ew.impulse
    if imp is None:
        return ""
    bits: list[str] = []
    if imp.fib_w2_ok and imp.fib_w2_ratio > 0:
        bits.append(f"W2 Fib {imp.fib_w2_ratio:.0%}")
    if imp.fib_w4_ok and imp.fib_w4_ratio > 0:
        bits.append(f"W4 Fib {imp.fib_w4_ratio:.0%}")
    if imp.fib_classic_ok:
        bits.append("classic OK")
    elif imp.fib_notes:
        bits.append(imp.fib_notes[0][:60])
    return " · ".join(bits)


def _expect_ru(ew: ElliottWaveResult, setup_kind: str, side: str) -> str:
    """Что рисуем / ждём дальше — человеческий сценарий."""
    plan = ew.entry_plan
    path = (ew.path_reason_ru or "").strip()
    if setup_kind == "entry_ready" and plan and plan.trigger:
        return f"Вход готов: {plan.trigger}"
    if setup_kind == "wave2_breakout" and plan and plan.trigger:
        return f"Пробой после волны 2: {plan.trigger}"
    if setup_kind == "wave3_impulse":
        return (
            plan.trigger if plan and plan.trigger
            else "Волна 3 (часто самая длинная) — вход на продолжении импульса"
        )
    if setup_kind == "wave4_breakout":
        return (
            plan.trigger if plan and plan.trigger
            else "Волна 4 в Fib → пробой хая/лоя 3 для волны 5"
        )
    if setup_kind == "wave5_bounce":
        return (
            plan.trigger if plan and plan.trigger
            else "Импульс 1–5 завершён → сценарий отскока / коррекции"
        )
    if setup_kind == "wave2_zone":
        dir_ru = "вверх (волна 3)" if side == "long" else "вниз (волна 3)"
        return f"Волна 2 в Fib-зоне → ждём отскок {dir_ru}, стоп за основанием 1"
    if setup_kind == "wave4_zone":
        dir_ru = "вверх (волна 5)" if side == "long" else "вниз (волна 5)"
        return f"Волна 4 в Fib 38.2–50% ×3 → ждём отскок {dir_ru}"
    if setup_kind == "abc_c_zone":
        return "Коррекция ABC у зоны C (1.272/1.618×B) → лимит + продолжение импульса"
    if setup_kind == "impulse_complete":
        return "Импульс 1–5 завершён → ждём ABC; не ловить конец 5"
    if path:
        return path
    if ew.path_bias and ew.path_bias != "neutral":
        return f"Most-likely path: {ew.path_bias}"
    return "Структура валидна — ждём триггер входа по правилам"


def _classify_setup(ew: ElliottWaveResult) -> tuple[str, str]:
    """(setup_kind, side). side из entry_plan или направления импульса."""
    plan = ew.entry_plan
    imp = ew.impulse
    abc = ew.abc
    side = "wait"
    if plan and plan.side in {"long", "short"}:
        side = plan.side
    elif imp is not None:
        side = "long" if imp.direction == "up" else "short"

    if plan and plan.ready and plan.mode in {"conservative", "aggressive"}:
        wave = (imp.current_wave if imp else "") or ""
        ext = (imp.extension if imp else "") or ""
        trig = (plan.trigger or "").lower()
        if wave in {"5", "complete"} or "5 волн" in trig or "отскок" in trig:
            bounce_side = "short" if imp and imp.direction == "up" else "long"
            return "wave5_bounce", bounce_side
        if wave == "3" or ext == "3" or "волна 3" in trig:
            return "wave3_impulse", plan.side
        if wave == "4" or "волна 4" in trig or "после волны 4" in trig:
            return "wave4_breakout", plan.side
        if "волна 2" in trig or "после волны 2" in trig or wave in {"2", "forming"}:
            return "wave2_breakout", plan.side
        return "wave2_breakout", plan.side

    wave = (imp.current_wave if imp else "") or ""
    if wave == "2" and imp and (imp.fib_w2_ok or imp.fib_classic_ok):
        return "wave2_zone", side
    if wave == "4" and imp and (imp.fib_w4_ok or imp.fib_classic_ok):
        return "wave4_zone", side
    if abc and abc.phase in {"C", "complete"} and (
        getattr(abc, "at_aggressive_zone", False) or (plan and plan.mode == "aggressive")
    ):
        return "abc_c_zone", side
    if ew.phase in {"impulse_complete", "abc_complete"} or wave == "complete":
        return "impulse_complete", "wait" if not (plan and plan.ready) else side
    if ew.path_bias and ew.path_bias != "neutral" and ew.path_prices:
        return "path_active", side if side != "wait" else (
            "long" if ew.path_bias in {"up", "long", "bull"} else "short"
            if ew.path_bias in {"down", "short", "bear"} else "wait"
        )
    return "structure_watch", side


def compute_wave_importance(ew: ElliottWaveResult, setup_kind: str, settings: Any) -> float:
    conf = int(ew.confidence or 0)
    score = float(conf) * 8.0
    imp = ew.impulse

    if setup_kind == "wave3_impulse":
        score += 24.0
    elif setup_kind == "wave5_bounce":
        score += 22.0
    elif setup_kind == "wave2_breakout":
        score += 18.0
    elif setup_kind == "wave4_breakout":
        score += 16.0
    elif setup_kind == "entry_ready":
        score += 20.0
    elif setup_kind in {"wave2_zone", "wave4_zone", "abc_c_zone"}:
        score += 12.0
    elif setup_kind == "impulse_complete":
        score += 6.0

    if imp is not None:
        if imp.extension == "3" and setup_kind == "wave3_impulse":
            score += 10.0
        if imp.truncated and setup_kind == "wave5_bounce":
            score += 8.0
        if imp.valid:
            score += 8.0
        if imp.fib_classic_ok:
            score += 12.0
        if imp.fib_w2_ok or imp.fib_w4_ok:
            score += 6.0
        if imp.quality:
            score += min(12.0, imp.quality / 8.0)
        if imp.violations:
            score -= 6.0 * min(3, len(imp.violations))

    if ew.has_global and ew.has_local:
        score += 8.0
    if ew.path_reason_ru:
        score += 4.0
    if ew.abc and ew.abc.valid:
        score += 4.0

    # HTF-совместимость не всегда есть в light-scan — мягкий бонус из notes
    if any("HTF" in n or "глобаль" in n.lower() for n in (ew.notes or [])):
        score += 3.0

    return max(0.0, min(100.0, score))


def build_wave_event(
    exchange: str,
    symbol: str,
    ew: ElliottWaveResult,
    bars: list[KlineBar],
    settings: Any,
    *,
    price: float | None = None,
) -> WaveEvent | None:
    """Gate: валидная EW-структура + фаза + Fib/качество → WaveEvent.

    Тот же движок, что рисует волны в боте: analyze_elliott_waves
    (правила 1–5, Fib classic, ABC, entry_plan). Без валидной
    отрисовываемой разметки сигнал не уходит.
    """
    if not getattr(settings, "wave_enabled", False):
        return None
    if ew is None or not ew.has_structure or ew.impulse is None:
        return None

    # Должно быть что рисовать: точки 1–5 / ABC (иначе «сигнал без волн»)
    draw_n = len(ew.draw_points or []) + len(ew.global_draw_points or []) + len(
        ew.local_draw_points or []
    )
    if draw_n < 4:
        draw_n = len(ew.impulse.points or [])
        if ew.abc is not None:
            draw_n += len(ew.abc.points or [])
    if draw_n < 4:
        return None

    plan = ew.entry_plan
    phases = _enabled_phases(settings)
    if not _phase_allowed(ew.phase, phases) and not (plan and plan.ready):
        return None

    imp = ew.impulse
    require_classic = bool(getattr(settings, "wave_require_fib_classic", True))
    min_quality = int(getattr(settings, "wave_min_impulse_quality", 58))
    min_conf = int(getattr(settings, "wave_min_confidence", 5))
    require_valid = bool(getattr(settings, "wave_require_impulse_valid", True))
    allow_diagonal = bool(getattr(settings, "wave_allow_diagonal_signals", False))

    if require_valid and not imp.valid and not (plan and plan.ready and imp.quality >= 70):
        return None
    # Диагональ с overlap 4↔1 — не «чистый» импульс; в wave-чат только по флагу
    if imp.diagonal and not allow_diagonal:
        return None
    if imp.quality < min_quality and not (plan and plan.ready):
        return None
    if int(ew.confidence or 0) < min_conf and not (plan and plan.ready):
        return None

    # Нарушения чек-листа EW — блокируют торговые алерты
    hard_violations = [
        v for v in (imp.violations or [])
        if any(
            key in v.lower()
            for key in (
                "пересекла",
                "пробила",
                "зашла за",
                "коротк",
                "не превыш",
                "не вверх",
                "не вниз",
                "не обновила",
                "не откат",
            )
        )
        and "диагональ" not in v.lower()
        and "усечение" not in v.lower()
        and "допуск" not in v.lower()
    ]
    setup_kind, side = _classify_setup(ew)

    enabled_modes = _enabled_setup_modes(settings)
    trade_modes = {
        "wave2_breakout", "wave3_impulse", "wave4_breakout", "wave5_bounce", "entry_ready",
    }
    if setup_kind in trade_modes and not _setup_mode_allowed(setup_kind, enabled_modes):
        return None

    if require_classic:
        trade_fib = {
            "entry_ready", "wave2_breakout", "wave3_impulse", "wave4_breakout",
            "wave5_bounce", "wave2_zone", "wave4_zone", "abc_c_zone",
        }
        if setup_kind in trade_fib:
            if not imp.fib_classic_ok and not (imp.fib_w2_ok or imp.fib_w4_ok):
                return None
        if setup_kind in {
            "entry_ready", "wave2_breakout", "wave3_impulse", "wave4_breakout", "wave5_bounce",
        } and not imp.fib_classic_ok and imp.quality < 70:
            return None

    # Жёсткие violations — не шлём ни ENTRY, ни зоны 2/4/C
    if hard_violations and setup_kind in {
        "entry_ready", "wave2_breakout", "wave3_impulse", "wave4_breakout",
        "wave5_bounce", "wave2_zone", "wave4_zone", "abc_c_zone",
    }:
        return None

    require_ready = bool(getattr(settings, "wave_require_entry_ready", True))
    if require_ready and setup_kind in trade_modes and not (plan and plan.ready):
        return None

    allow_watch = bool(getattr(settings, "wave_allow_structure_watch", False))
    if setup_kind == "structure_watch" and not allow_watch:
        return None
    if setup_kind == "path_active" and not bool(getattr(settings, "wave_allow_path_alerts", False)):
        return None
    if setup_kind == "impulse_complete" and not (plan and plan.ready):
        if not bool(getattr(settings, "wave_allow_complete_alerts", False)):
            return None

    actionable = {
        "entry_ready", "wave2_breakout", "wave3_impulse", "wave4_breakout",
        "wave5_bounce", "wave2_zone", "wave4_zone", "abc_c_zone",
    }
    if setup_kind not in actionable and not (plan and plan.ready):
        if not allow_watch:
            return None

    px = float(price) if price and price > 0 else float(bars[-1].close)
    tps: list[float] = []
    if plan:
        if plan.tp1:
            tps.append(float(plan.tp1))
        if plan.tp2:
            tps.append(float(plan.tp2))
    if not tps and ew.fib_target_prices:
        tps = [float(x) for x in ew.fib_target_prices[:3] if x]

    importance = compute_wave_importance(ew, setup_kind, settings)
    min_imp = float(getattr(settings, "wave_min_importance", 58.0))
    if importance < min_imp:
        return None

    entry = float(plan.entry_price) if plan and plan.entry_price else None
    stop = float(plan.stop_price) if plan and plan.stop_price else None
    inv = ew.path_invalidation
    if inv is None and stop is not None:
        inv = stop
    fib_note = _fib_zone_note(ew)
    expect = _expect_ru(ew, setup_kind, side)

    # Опоздавший сетап: цена уже далеко от входа в сторону цели — не шлём
    if entry and entry > 0 and side in {"long", "short"}:
        dist_pct = abs(px - entry) / entry * 100.0
        max_late = float(getattr(settings, "wave_max_late_entry_pct", 1.2))
        already_past = (
            (side == "short" and px < entry) or (side == "long" and px > entry)
        )
        if already_past and dist_pct > max_late:
            return None

    detail_parts = [
        ew.label_ru or "EW структура",
        f"фаза {ew.phase}",
        f"conf {ew.confidence}/9",
    ]
    if fib_note:
        detail_parts.append(fib_note)
    if ew.structure_note_ru:
        detail_parts.append(ew.structure_note_ru)

    # Точки для графика: то, что реально нашли при алерте (не пересчитывать «вслепую»)
    g_ot = _pack_ew_points_ot(list(ew.global_draw_points or []), bars)
    l_ot = _pack_ew_points_ot(list(ew.local_draw_points or []), bars)
    d_ot = _pack_ew_points_ot(list(ew.draw_points or []), bars)
    if not d_ot and not g_ot and ew.impulse is not None:
        g_ot = _pack_ew_points_ot(list(ew.impulse.points or []), bars)
        if ew.abc is not None:
            d_ot = g_ot + _pack_ew_points_ot(list(ew.abc.points or []), bars)
        else:
            d_ot = g_ot
    # Без сериализуемых точек график будет пустым — сигнал не шлём
    if len(d_ot) + len(g_ot) < 3:
        return None

    return WaveEvent(
        exchange=exchange,
        symbol=symbol.upper(),
        timestamp=time.time(),
        price=px,
        side=side,
        setup_kind=setup_kind,
        phase=ew.phase,
        label_ru=ew.label_ru or "",
        detail=" · ".join(detail_parts),
        importance=importance,
        confidence=int(ew.confidence or 0),
        entry_ready=bool(plan and plan.ready),
        entry_mode=(plan.mode if plan else "wait"),
        entry_price=entry,
        stop_price=stop,
        tp_prices=tuple(tps[:4]),
        path_reason=ew.path_reason_ru or "",
        path_bias=ew.path_bias or "",
        invalidation=float(inv) if inv else None,
        global_label=ew.global_label_ru or "",
        local_label=ew.local_label_ru or "",
        fib_note=fib_note,
        expect_ru=expect,
        ew_draw_ot=d_ot,
        ew_global_ot=g_ot,
        ew_local_ot=l_ot,
        meta={
            "quality": imp.quality,
            "fib_classic_ok": imp.fib_classic_ok,
            "impulse_valid": imp.valid,
            "extension": ew.extension or imp.extension,
            "truncated": bool(ew.truncated or imp.truncated),
            "diagonal": ew.diagonal or imp.diagonal,
            "corr_type": ew.corr_type,
            "has_global": ew.has_global,
            "has_local": ew.has_local,
            "draw_points": draw_n,
            "path_prices": list(ew.path_prices or [])[:6],
            "path_labels": list(ew.path_labels or [])[:6],
        },
    )


async def analyze_symbol_waves(
    symbol: str,
    *,
    exchange: str = "Bybit",
    kline_cache: BybitKlineCache | None = None,
    settings: Any = None,
    hours: int = 18,
    interval_minutes: int = 5,
    price: float | None = None,
) -> WaveEvent | None:
    """Лёгкий EW-разбор одного символа (без полного TA/графика)."""
    cache = kline_cache or BybitKlineCache(ttl_seconds=60.0)
    limit = max(48, min(200, int(hours * 60 / max(1, interval_minutes)) + 8))
    bars = await cache.get_klines(
        symbol, limit=limit, interval_minutes=interval_minutes,
    )
    if len(bars) < 24:
        return None
    swings = find_swing_points(bars)
    if len(swings) < 4:
        return None
    ew = analyze_elliott_waves(bars, swings)
    return build_wave_event(exchange, symbol, ew, bars, settings, price=price)


SETUP_LABELS_RU: dict[str, str] = {
    "wave3_impulse": "🚀 WAVE 3 · импульс",
    "wave5_bounce": "↩️ WAVE 5 · отскок",
    "wave2_breakout": "🎯 ENTRY · пробой после 2",
    "wave4_breakout": "🎯 ENTRY · пробой после 4",
    "entry_ready": "🎯 ENTRY · волна готова",
    "wave2_zone": "📍 WATCH · волна 2 (Fib)",
    "wave4_zone": "📍 WATCH · волна 4 (Fib)",
    "abc_c_zone": "📍 WATCH · зона C (ABC)",
    "impulse_complete": "⏳ Импульс 1–5 завершён",
    "path_active": "🗺 Path · сценарий",
    "structure_watch": "👁 Структура",
}


def format_wave_alert(event: WaveEvent) -> str:
    exchange_key = "bybit" if "bybit" in event.exchange.lower() else "binance"
    if exchange_key == "bybit":
        exchange_emoji, exchange_name = "⚫", "ByBit"
    else:
        exchange_emoji, exchange_name = "🟡", "Binance"

    title = SETUP_LABELS_RU.get(event.setup_kind, "🌊 WAVE")
    side_tag = SIDE_RU.get(event.side, event.side.upper())
    ticker = base_ticker(event.symbol)
    cg_url = coinglass_url(event.symbol, event.exchange)
    ex_url = exchange_trade_url(event.symbol, event.exchange)
    ts = datetime.fromtimestamp(event.timestamp, tz=timezone.utc).strftime("%H:%M")

    how_read = (
        "На скрине: синие круги <b>1–5</b> = импульс, "
        "оранжевые <b>A–C</b> = коррекция, "
        "зелёный вход / красный стоп / цели TP"
    )

    lines = [
        f"<b>{title}</b> · <b>{side_tag}</b>",
        f'{exchange_emoji} <a href="{ex_url}">{exchange_name}</a> '
        f'<a href="{cg_url}">#{ticker}</a> · ${event.price:.6g}',
        "",
        how_read,
        "",
    ]
    if event.expect_ru:
        lines.append(f"<b>Дальше:</b> {event.expect_ru}")

    plan_bits: list[str] = []
    if event.entry_price:
        ready = " ✓ готов" if event.entry_ready else ""
        plan_bits.append(f"вход {fmt_price(event.entry_price)}{ready}")
    if event.stop_price:
        plan_bits.append(f"стоп {fmt_price(event.stop_price)}")
    if event.tp_prices:
        tps = " → ".join(fmt_price(t) for t in event.tp_prices[:2])
        plan_bits.append(f"цели {tps}")
    if plan_bits:
        lines.append(" · ".join(plan_bits))
    if event.invalidation:
        lines.append(f"отмена &lt;пробой&gt; {fmt_price(event.invalidation)}")

    lines.append(f"<i>{ts} UTC · conf {event.confidence}/9</i>")
    return "\n".join(lines)


class WaveBatcher:
    """Топ-N волновых сетапов за интервал, cooldown по символу."""

    def __init__(
        self,
        on_dispatch: Callable[[WaveEvent], Awaitable[bool]],
    ) -> None:
        self._on_dispatch = on_dispatch
        self._best_per_symbol: dict[str, WaveEvent] = {}
        self._symbol_cooldown_until: dict[str, float] = {}
        self._dispatch_times: deque[float] = deque(maxlen=64)
        self._last_flush = 0.0
        self._lock = asyncio.Lock()

    async def offer(self, event: WaveEvent, settings: Any) -> None:
        min_imp = float(getattr(settings, "wave_min_importance", 58.0))
        if event.importance < min_imp:
            return
        sym = event.symbol.upper()
        now = time.time()
        symbol_cd = int(getattr(settings, "wave_symbol_cooldown_seconds", 2400))
        async with self._lock:
            if now < self._symbol_cooldown_until.get(sym, 0.0):
                return
            prev = self._best_per_symbol.get(sym)
            if prev is None or event.importance > prev.importance:
                self._best_per_symbol[sym] = event

    async def flush(self, settings: Any) -> int:
        now = time.time()
        interval = float(getattr(settings, "wave_batch_interval_seconds", 90.0))
        if now - self._last_flush < interval:
            return 0
        self._last_flush = now

        max_per_min = int(getattr(settings, "wave_max_per_minute", 1))
        while self._dispatch_times and self._dispatch_times[0] < now - 60.0:
            self._dispatch_times.popleft()
        slots = max(0, max_per_min - len(self._dispatch_times))
        max_batch = int(getattr(settings, "wave_max_per_batch", 1))
        slots = min(slots, max(1, max_batch))
        if slots <= 0:
            return 0

        async with self._lock:
            if not self._best_per_symbol:
                return 0
            ranked = sorted(
                self._best_per_symbol.values(),
                key=lambda e: (
                    -e.importance,
                    SETUP_PRIORITY.get(e.setup_kind, 9),
                ),
            )
            to_send = ranked[:slots]
            sent_syms = {e.symbol.upper() for e in to_send}
            self._best_per_symbol = {
                k: v for k, v in self._best_per_symbol.items() if k not in sent_syms
            }

        symbol_cd = int(getattr(settings, "wave_symbol_cooldown_seconds", 2400))
        sent_count = 0
        for event in to_send:
            try:
                ok = await self._on_dispatch(event)
            except Exception:
                logger.exception(
                    "Wave dispatch failed %s %s", event.exchange, event.symbol,
                )
                ok = False
            if not ok:
                continue
            sent_count += 1
            async with self._lock:
                ts = time.time()
                self._dispatch_times.append(ts)
                self._symbol_cooldown_until[event.symbol.upper()] = ts + symbol_cd
            logger.info(
                "Wave sent %s %s %s/%s (importance %.0f)",
                event.exchange,
                event.symbol,
                event.setup_kind,
                event.side,
                event.importance,
            )
        return sent_count


class WaveScanEngine:
    """Периодический обход топ-символов → EW gate → batcher."""

    def __init__(
        self,
        settings_manager: "SettingsManager",
        scanner: "SignalEngine",
        batcher: WaveBatcher,
        *,
        kline_cache: BybitKlineCache | None = None,
    ) -> None:
        self.settings_manager = settings_manager
        self.scanner = scanner
        self.batcher = batcher
        self._kline_cache = kline_cache or BybitKlineCache(ttl_seconds=75.0)
        self._scan_cursor = 0
        self._lock = asyncio.Lock()

    def _candidate_symbols(self, settings: Any) -> list[tuple[str, str, float | None]]:
        """(exchange, symbol, live_price)."""
        limit = int(getattr(settings, "wave_scan_limit", 40))
        out: list[tuple[str, str, float | None]] = []
        seen: set[str] = set()

        top = self.scanner.get_bybit_top_symbols()
        for sym in top:
            key = sym.upper()
            if key in seen:
                continue
            seen.add(key)
            price = None
            snap = None
            try:
                hist = self.scanner.history.get(f"Bybit:{key}")
                if hist:
                    snap = hist[-1]
                    price = snap.price
            except Exception:
                price = None
            out.append(("Bybit", key, price))
            if len(out) >= limit * 2:
                break

        # Ротация: каждый цикл берём окно limit символов
        if not out:
            return []
        n = len(out)
        start = self._scan_cursor % n
        self._scan_cursor = (start + limit) % n
        window: list[tuple[str, str, float | None]] = []
        for i in range(min(limit, n)):
            window.append(out[(start + i) % n])
        return window

    async def scan_once(self) -> int:
        settings = self.settings_manager.settings
        if not getattr(settings, "wave_enabled", False):
            return 0
        if getattr(settings, "bot_paused", False):
            return 0

        symbols = self._candidate_symbols(settings)
        if not symbols:
            return 0

        concurrency = max(1, int(getattr(settings, "wave_scan_concurrency", 3)))
        hours = int(getattr(settings, "wave_chart_hours", 18))
        interval = int(getattr(settings, "wave_interval_minutes", 5))
        sem = asyncio.Semaphore(concurrency)
        offered = 0

        async def _one(exchange: str, symbol: str, price: float | None) -> None:
            nonlocal offered
            async with sem:
                try:
                    event = await analyze_symbol_waves(
                        symbol,
                        exchange=exchange,
                        kline_cache=self._kline_cache,
                        settings=settings,
                        hours=hours,
                        interval_minutes=interval,
                        price=price,
                    )
                except Exception:
                    logger.debug("Wave scan failed %s", symbol, exc_info=True)
                    return
                if event is None:
                    return
                await self.batcher.offer(event, settings)
                offered += 1

        await asyncio.gather(*[_one(ex, sym, px) for ex, sym, px in symbols])
        sent = await self.batcher.flush(settings)
        if offered or sent:
            logger.info(
                "Wave scan: checked=%d candidates=%d sent=%d",
                len(symbols),
                offered,
                sent,
            )
        return sent

    async def run_loop(self, interval: float | None = None) -> None:
        while True:
            settings = self.settings_manager.settings
            sleep_s = float(
                interval
                if interval is not None
                else getattr(settings, "wave_scan_interval_seconds", 120.0)
            )
            sleep_s = max(30.0, sleep_s)
            try:
                if getattr(settings, "wave_enabled", False) and not getattr(
                    settings, "bot_paused", False,
                ):
                    async with self._lock:
                        await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Wave scan loop error")
            await asyncio.sleep(sleep_s)
