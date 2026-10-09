"""Единый Chart Read: структура → триггер → сценарий → PNG + Telegram."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html import escape
from typing import Any

from ..bybit_klines import KlineBar
from ..chart_analysis_text import structure_break_label_ru
from ..chart_patterns import format_chart_pattern_compact
from ..chart_story_router import effective_rbr
from ..human_trade_brief import preferred_trade_side
from ..range_breakdown_retest import get_rbr_from_ta
from ..ta_analysis import TAAnalysisResult, fmt_price


@dataclass
class ChartReadLevel:
    price: float
    label: str
    priority: int = 50


@dataclass
class ChartRead:
    symbol: str
    phase: str
    phase_ru: str
    structure_ru: str
    bias: str  # long | short | wait
    situation_ru: str
    expect_ru: str
    trigger_ru: str
    flow_hint_ru: str
    pattern_label: str
    pattern_confidence: float
    levels: list[ChartReadLevel] = field(default_factory=list)
    scenario_waypoints: list[float] = field(default_factory=list)
    scenario_label_ru: str = ""
    smc_ready: bool = False
    smc_checklist_ru: str = ""
    draw_entry_bands: bool = False
    setup_hint_ru: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["levels"] = [asdict(lv) for lv in self.levels]
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ChartRead:
        levels = [
            ChartReadLevel(float(x["price"]), str(x["label"]), int(x.get("priority", 50)))
            for x in (raw.get("levels") or [])
            if isinstance(x, dict)
        ]
        return cls(
            symbol=str(raw.get("symbol") or ""),
            phase=str(raw.get("phase") or "observe"),
            phase_ru=str(raw.get("phase_ru") or ""),
            structure_ru=str(raw.get("structure_ru") or ""),
            bias=str(raw.get("bias") or "wait"),
            situation_ru=str(raw.get("situation_ru") or ""),
            expect_ru=str(raw.get("expect_ru") or ""),
            trigger_ru=str(raw.get("trigger_ru") or ""),
            flow_hint_ru=str(raw.get("flow_hint_ru") or ""),
            pattern_label=str(raw.get("pattern_label") or ""),
            pattern_confidence=float(raw.get("pattern_confidence") or 0),
            levels=levels,
            scenario_waypoints=[float(x) for x in (raw.get("scenario_waypoints") or [])],
            scenario_label_ru=str(raw.get("scenario_label_ru") or ""),
            smc_ready=bool(raw.get("smc_ready")),
            smc_checklist_ru=str(raw.get("smc_checklist_ru") or ""),
            draw_entry_bands=bool(raw.get("draw_entry_bands")),
            setup_hint_ru=str(raw.get("setup_hint_ru") or ""),
        )

    def telegram_prose_html(self) -> str:
        """2–3 фразы: что на графике → что ждём → один нюанс потока (без «Триггер:» на мусор)."""
        parts: list[str] = []

        opening = _sanitize_situation_for_prose(self.situation_ru, self.phase_ru)
        if opening:
            parts.append(escape(opening.rstrip(".") + "."))

        action = self._action_line_html()
        expect = _humanize_expect_line(self.expect_ru, self.bias)
        corridor = self._corridor_line_html()

        mid_bits: list[str] = []
        if action:
            mid_bits.append(action)
        if expect and expect not in self.situation_ru:
            want_expect = not action or any(k in expect.lower() for k in ("цел", "→", "шорт —", "если "))
            if want_expect and not any(expect[:28] in b for b in mid_bits):
                mid_bits.append(escape(expect.rstrip(".") + "."))
        if corridor and corridor not in " ".join(mid_bits):
            mid_bits.append(corridor)

        if mid_bits:
            parts.append(" ".join(mid_bits))

        if self.flow_hint_ru:
            blob = " ".join(parts)
            if self.flow_hint_ru not in blob and "oi" not in blob.lower():
                parts.append(escape(self.flow_hint_ru))

        if self.pattern_label and self.pattern_confidence >= 0.48:
            fig = f"Фигура: {self.pattern_label}"
            if fig not in " ".join(parts):
                parts.append(f"<i>{escape(fig)}</i>")

        text = " ".join(parts[:3])
        if len(text) > 480:
            text = text[:477].rsplit(" ", 1)[0] + "…"
        return text

    def _action_line_html(self) -> str:
        t = (self.trigger_ru or "").strip()
        if _is_actionable_trigger(t):
            t = _polish_trigger_phrase(t)
            return f"<b>Жду:</b> {escape(t.rstrip('.') + '.')}"
        return ""

    def _corridor_line_html(self) -> str:
        support = next((lv for lv in self.levels if lv.label == "S"), None)
        resist = next((lv for lv in self.levels if lv.label == "R"), None)
        if support and resist and resist.price > support.price:
            return (
                f"Коридор <b>{fmt_price(support.price)}</b>–"
                f"<b>{fmt_price(resist.price)}</b> — без market в середине."
            )
        return ""


def _sanitize_situation_for_prose(situation: str, phase_ru: str) -> str:
    import re

    s = (situation or "").strip()
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s*Close\s+[\d.,]+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s:
        return (phase_ru or "").strip()
    if s.lower().startswith("последние свечи") and len(s) > 165:
        s = s[:162].rsplit(" ", 1)[0] + "…"
    return s


def _humanize_expect_line(expect: str, bias: str) -> str:
    ex = (expect or "").strip()
    if not ex:
        return ""
    ex = ex.replace("При шорте ориентир:", "Если пойдёт вниз — цель")
    ex = ex.replace("При шорте цели:", "Шорт — цели")
    ex = ex.replace("При лонге ориентир:", "Если вверх — цель")
    ex = ex.replace("Дальше — по пунктиру на графике (сценарий вероятнее).", "")
    return ex.strip()


def _polish_trigger_phrase(t: str) -> str:
    out = t.replace("Close ≤", "закреп ниже").replace("Close ≥", "закреп выше")
    out = out.replace("close ≤", "закреп ниже").replace("close ≥", "закреп выше")
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return out


def _is_actionable_trigger(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 88:
        return False
    low = t.lower()
    junk = (
        "слабость",
        "новом лое",
        "новом хае",
        "боковик",
        "цель ≈",
        "oi ",
        "open interest",
        "лент",
        "покупател",
        "продаж на",
    )
    if any(x in low for x in junk):
        return False
    if ";" in t or t.count("→") >= 2:
        return False
    action = (
        "закреп",
        "пробой",
        "retest",
        "close",
        "ниже",
        "выше",
        "отказ",
        "под ",
        "над ",
        "≤",
        "≥",
        "между",
        "наблюд",
        "ждём",
        "шорт —",
        "лонг —",
    )
    return any(a in low for a in action)


def _flow_hint(ta: TAAnalysisResult) -> str:
    blob = " ".join(str(x) for x in (getattr(ta, "market_participation_lines", None) or [])).lower()
    if "закрытии позиц" in blob:
        return "OI снижается — движение часто на закрытии позиций, не на новых деньгах."
    if "cvd buy" in blob and any(c in blob for c in "789"):
        return "В ленте сильные покупки — у сопротивления это часто вынос, не готовый лонг."
    if "cvd sell" in blob or "агрессивные продаж" in blob:
        return "Продажи в ленте сильнее — отскоки могут сдаваться быстрее."
    if "без явного" in blob or "без совместного" in blob:
        return "Цена и OI не идут вместе — импульс слабый, ждём уровень."
    return ""


def _resolve_phase(ta: TAAnalysisResult, rbr: dict[str, Any] | None) -> tuple[str, str]:
    if rbr:
        ph = str(rbr.get("phase") or "")
        mapping = {
            "fade_top": ("range_top", "После импульса — боковик у потолка."),
            "await_break": ("range_wait", "Боковик: пробоя пола ещё не было."),
            "retest": ("retest", "Пробой пола был — смотрим retest."),
            "broken": ("breakdown", "Цена ниже диапазона — сценарий вниз в приоритете."),
        }
        if ph in mapping:
            return mapping[ph]
    if bool(getattr(ta, "post_pump", False)):
        return "post_pump", "Импульс отработан — цена у локального экстремума."
    mom = str(getattr(ta, "momentum_label", "") or "").lower()
    if "импульс" in mom:
        return "impulse", "Краткий импульс — не путать с готовой сделкой."
    if getattr(ta, "consolidation", None) is not None:
        return "range", "Консолидация — без market до триггера."
    return "observe", "Смотрим реакцию на ключевых уровнях."


def _structure_line(ta: TAAnalysisResult) -> str:
    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        tag = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"), short=True)
        return f"{tag} {fmt_price(float(smc.structure_break_level))}"
    sl = str(getattr(ta, "structure_label", "") or "").strip()
    return sl[:80] if sl else ""


def _humanize_setup_trigger(trig: str) -> str:
    import re

    t = trig.strip()
    if not t:
        return ""
    m = re.search(
        r"close\s*(\d+)m\s*[≥>=]+\s*([\d.]+)",
        t,
        flags=re.IGNORECASE,
    )
    if m:
        return f"Закреп {m.group(1)}m выше {fmt_price(float(m.group(2)))}."
    m2 = re.search(
        r"close\s*(\d+)m\s*[≤<=]+\s*([\d.]+)",
        t,
        flags=re.IGNORECASE,
    )
    if m2:
        return f"Закреп {m2.group(1)}m ниже {fmt_price(float(m2.group(2)))}."
    if re.search(r"close\s*\d+m", t, flags=re.IGNORECASE):
        return ""
    return t


def _trigger_ru(ta: TAAnalysisResult, rbr: dict[str, Any] | None) -> str:
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if rbr:
        ph = str(rbr.get("phase") or "")
        floor = float(rbr.get("range_bottom") or brdn or 0)
        ceil = float(rbr.get("range_top") or brk or 0)
        if ph == "await_break" and floor > 0:
            return f"Шорт после закрепа под {fmt_price(floor)} (retest, не в падение)."
        if ph == "fade_top" and ceil > 0:
            return f"Шорт после отказа у {fmt_price(ceil)}, не в зелёную свечу."
        if ph == "retest" and floor > 0:
            return f"Retest {fmt_price(floor)} — вход только после подтверждения."
        if ph == "broken" and floor > 0:
            return f"Уже ниже {fmt_price(floor)} — шорт на откатах, не в красный импульс."

    trig = _humanize_setup_trigger(str(getattr(ta, "setup_trigger", "") or ""))
    if trig and _is_actionable_trigger(trig):
        return trig

    cur = float(getattr(ta, "current_price", 0) or 0)
    if brk > 0 and cur > brk * 1.001:
        return f"Уже выше {fmt_price(brk)} — лонг не в импульс; шорт только на retest от R."
    if brdn > 0 and cur < brdn * 0.999:
        return f"Уже ниже {fmt_price(brdn)} — шорт не в падение; ждём retest S."

    side = preferred_trade_side(ta)
    if side == "long" and brk > 0:
        return f"Закреп выше {fmt_price(brk)} (close/retest)."
    if side == "short" and brdn > 0:
        return f"Пробой ниже {fmt_price(brdn)} и retest."
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()
    if seek and _is_actionable_trigger(seek):
        return seek[:88]
    if brk > 0 and brdn > 0:
        return f"Между {fmt_price(brdn)} и {fmt_price(brk)} — только наблюдение."
    return ""


def _expect_ru(ta: TAAnalysisResult, rbr: dict[str, Any] | None, *, stale: str = "") -> str:
    if stale:
        return stale
    side = preferred_trade_side(ta)
    tps = [float(x) for x in (getattr(ta, "target_prices", None) or []) if x]
    if rbr and str(rbr.get("direction") or "") == "short":
        tg = [float(x) for x in (rbr.get("targets") or []) if x][:2]
        if tg:
            return f"Шорт — цели {fmt_price(tg[0])}" + (f" → {fmt_price(tg[1])}" if len(tg) > 1 else "") + "."
        ph = str(rbr.get("phase") or "")
        if ph == "await_break":
            return "Ждём пробой пола и retest — вход не у потолка."
        return "Сценарий вниз — только по триггеру на графике."
    if side == "long" and tps:
        return f"Если вверх — цель {fmt_price(tps[0])}."
    if side == "short" and tps:
        return f"Если вниз — цель {fmt_price(tps[0])}."
    cont = getattr(ta, "continuation_path", None)
    if cont and getattr(cont, "label", ""):
        return str(cont.label)[:120]
    return "Дальше — по пунктиру на графике (сценарий вероятнее)." 


def _situation_ru(ta: TAAnalysisResult, phase_ru: str) -> str:
    recent = str(getattr(ta, "recent_price_action_ru", "") or "").strip()
    if recent:
        import re

        plain = re.sub(r"<[^>]+>", "", recent)
        if plain:
            return plain[:200]
    narrative = str(getattr(ta, "reading_narrative", "") or "").strip()
    if narrative:
        return narrative.split(".")[0][:180] + "."
    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    if stack:
        return f"{phase_ru} Контекст: {stack[:85]}."
    return phase_ru


def _collect_levels(ta: TAAnalysisResult, *, max_n: int = 5) -> list[ChartReadLevel]:
    cur = float(getattr(ta, "current_price", 0) or 0)
    out: list[ChartReadLevel] = []

    def add(p: float, label: str, pri: int) -> None:
        if p <= 0 or cur > 0 and abs(p - cur) / cur > 0.14:
            return
        if any(abs(p - x.price) / max(p, 1e-9) < 0.003 for x in out):
            return
        out.append(ChartReadLevel(p, label, pri))

    add(float(getattr(ta, "breakout_level", 0) or 0), "R", 92)
    add(float(getattr(ta, "breakdown_level", 0) or 0), "S", 92)
    smc = getattr(ta, "smc", None)
    if smc and getattr(smc, "structure_break_level", None):
        tag = structure_break_label_ru(str(getattr(smc, "structure_break_kind", "") or "bos"), short=True)
        add(float(smc.structure_break_level), tag, 88)
    add(float(getattr(ta, "nearest_resistance", 0) or 0), "R ближ.", 70)
    add(float(getattr(ta, "nearest_support", 0) or 0), "S ближ.", 70)
    cons = getattr(ta, "consolidation", None)
    if cons is not None:
        add(float(cons.top), "боковик ↑", 68)
        add(float(cons.bottom), "боковик ↓", 68)
    for fl in getattr(ta, "fib_levels", None) or []:
        if float(getattr(fl, "ratio", 0) or 0) in {0.5, 0.618}:
            add(float(fl.price), f"Fib {getattr(fl, 'ratio', 0):g}", 64)
            break
    out.sort(key=lambda x: -x.priority)
    return out[:max_n]


def _bars_closed_above(bars: list[KlineBar] | None, level: float, *, lookback: int = 4) -> bool:
    if not bars or level <= 0:
        return False
    for b in bars[-lookback:]:
        if float(b.close) > level * 1.0008:
            return True
    return False


def _bars_closed_below(bars: list[KlineBar] | None, level: float, *, lookback: int = 4) -> bool:
    if not bars or level <= 0:
        return False
    for b in bars[-lookback:]:
        if float(b.close) < level * 0.9992:
            return True
    return False


def _scenario_from_ta(
    ta: TAAnalysisResult,
    bars: list[KlineBar] | None = None,
) -> tuple[list[float], str]:
    cur = float(getattr(ta, "current_price", 0) or 0)
    if cur <= 0 and bars:
        cur = float(bars[-1].close)
    if cur <= 0:
        return [], ""

    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    tps = [float(x) for x in (getattr(ta, "target_prices", None) or []) if x]

    above_r = brk > 0 and (cur > brk * 1.001 or _bars_closed_above(bars, brk))
    below_s = brdn > 0 and (cur < brdn * 0.999 or _bars_closed_below(bars, brdn))

    if above_r:
        retest = brk
        ext = tps[0] if tps and tps[0] > cur else brk * 1.012
        if ext <= cur:
            ext = cur * 1.008
        return [cur, retest, ext], "пробой ↑ · retest R"
    if below_s:
        retest = brdn
        ext = tps[0] if tps and tps[0] < cur else brdn * 0.988
        if ext >= cur:
            ext = cur * 0.992
        return [cur, retest, ext], "пробой ↓ · retest S"

    path = getattr(ta, "continuation_path", None)
    if path and list(getattr(path, "waypoints", None) or []):
        wps = [float(x) for x in path.waypoints if x]
        if len(wps) >= 2:
            label = str(getattr(path, "label", "") or "продолжение")[:40]
            if below_s and "↑" in label and wps[0] > wps[-1]:
                pass
            else:
                return [cur] + wps[:4], label
    corr = getattr(ta, "correction_path", None)
    if corr and list(getattr(corr, "waypoints", None) or []):
        wps = [float(x) for x in corr.waypoints if x]
        if len(wps) >= 2:
            return [cur] + wps[:4], str(getattr(corr, "label", "") or "откат")[:40]

    side = preferred_trade_side(ta)
    if side == "short" and brdn > 0 and cur > brdn and brk > 0 and cur < brk * 0.998:
        tgt = tps[0] if tps and tps[0] < cur else brdn * 0.992
        return [cur, brdn, tgt], "к полу range"
    if side == "long" and brk > 0 and cur < brk and brdn > 0 and cur > brdn * 1.002:
        tgt = tps[0] if tps and tps[0] > cur else brk * 1.008
        return [cur, brk, tgt], "к потолку range"
    if brk > 0 and brdn > 0 and brdn < cur < brk:
        mid = (brk + brdn) / 2
        edge = brk if cur >= mid else brdn
        return [cur, edge], "к границе range"
    return [], ""


def _fib_plan_from_ta(ta: TAAnalysisResult) -> object:
    from ..fib_entry_rules import FibEntryPlan, evaluate_fib_entry

    mm = dict(getattr(ta, "market_metrics", None) or {})
    fp = mm.get("fib_plan")
    if isinstance(fp, dict) and fp:
        return FibEntryPlan(
            prefer_ratios=[float(x) for x in (fp.get("ratios") or [])],
            rule_ru=str(fp.get("rule") or ""),
            in_preferred_zone=bool(fp.get("in_zone")),
            near_ob=bool(fp.get("near_ob")),
            checklist_ok=bool(fp.get("checklist_ok")),
            label_ru=str(fp.get("label") or ""),
        )
    smc = getattr(ta, "smc", None)
    cur = float(getattr(ta, "current_price", 0) or 0)
    htf = getattr(smc, "htf_structure", "") if smc else ""
    return evaluate_fib_entry(
        current=cur,
        fib_levels=list(getattr(ta, "fib_levels", None) or []),
        wave_phase="",
        htf_structure=htf or "",
        wave_confidence=0,
        htf_change_pct=0.0,
        zones=[],
        accept_fib=bool(getattr(ta, "reading_accept_fib", True)),
    )


def _smc_checklist(ta: TAAnalysisResult) -> tuple[bool, str]:
    mm = dict(getattr(ta, "market_metrics", None) or {})
    cached = mm.get("smc_pdf_checklist")
    if isinstance(cached, dict) and "ready" in cached:
        return bool(cached.get("ready")), str(cached.get("label_ru") or "")

    smc = getattr(ta, "smc", None)
    cur = float(getattr(ta, "current_price", 0) or 0)
    if not smc or cur <= 0:
        return False, ""

    try:
        from ..smc_pdf_checklist import evaluate_smc_pdf_checklist
        from ..zone_model import TradingZone

        zones: list[TradingZone] = []
        for z in list(getattr(ta, "zones", None) or [])[:6]:
            top = float(getattr(z, "top", 0) or 0)
            bot = float(getattr(z, "bottom", 0) or 0)
            if top <= bot:
                continue
            raw_kind = str(getattr(z, "kind", "") or "demand")
            if raw_kind not in {
                "sr_support",
                "sr_resistance",
                "demand",
                "supply",
                "ob_bull",
                "ob_bear",
                "breaker_bull",
                "breaker_bear",
            }:
                raw_kind = "demand" if "bull" in raw_kind or "support" in raw_kind else "supply"
            zones.append(
                TradingZone(
                    kind=raw_kind,  # type: ignore[arg-type]
                    top=top,
                    bottom=bot,
                    tf_label="",
                    freshness="fresh",
                    valid=True,
                    label_ru=str(getattr(z, "label", "") or "")[:40],
                    start_idx=int(getattr(z, "start_idx", 0) or 0),
                )
            )
        fib = _fib_plan_from_ta(ta)
        htf = str(getattr(smc, "htf_structure", "") or "")
        cl = evaluate_smc_pdf_checklist(
            smc=smc,
            fib=fib,
            zones=zones,
            current=cur,
            htf_structure=htf,
        )
        mm["smc_pdf_checklist"] = {
            "ready": cl.ready,
            "label_ru": cl.label_ru,
            "score": cl.score,
        }
        ta.market_metrics = mm
        return cl.ready, cl.label_ru
    except Exception:
        if getattr(smc, "liquidity_sweep", False) and getattr(smc, "structure_break", False):
            return False, "свип + BOS (ждём зону)"
        return False, ""


def build_chart_read(
    ta: TAAnalysisResult,
    bars: list[KlineBar] | None = None,
    *,
    symbol: str = "",
) -> ChartRead:
    sym = (symbol or getattr(ta, "symbol", "") or "").upper()
    rbr = effective_rbr(ta) or get_rbr_from_ta(ta)
    phase, phase_ru = _resolve_phase(ta, rbr)
    side = preferred_trade_side(ta) or "wait"
    struct = _structure_line(ta)
    situation = _situation_ru(ta, phase_ru)
    from ..plan_staleness import plan_staleness_plain

    stale = plan_staleness_plain(ta)
    expect = _expect_ru(ta, rbr, stale=stale)
    trigger = _trigger_ru(ta, rbr)
    flow = _flow_hint(ta)
    primary = getattr(ta, "primary_chart_pattern", None)
    pat_label = format_chart_pattern_compact(primary) if primary else ""
    pat_conf = float(getattr(primary, "confidence", 0) or 0) if primary else 0.0
    levels = _collect_levels(ta)
    wps, sc_lbl = _scenario_from_ta(ta, bars)
    ready, cl_ru = _smc_checklist(ta)
    from ..chart_display_policy import chart_trade_plan_on_chart_enabled
    from ..chart_plan_chart_gate import plan_ok_to_draw_on_chart

    draw_entry = ready and chart_trade_plan_on_chart_enabled() and plan_ok_to_draw_on_chart(ta)
    setup_hint = ""
    if bars:
        try:
            from ..chart_pdf_style import _pdf_setup_hint

            setup_hint = (_pdf_setup_hint(ta, bars) or "").strip()[:140]
        except Exception:
            setup_hint = ""
    if not setup_hint:
        hint_parts = [struct, trigger[:60] if trigger else ""]
        if sc_lbl:
            hint_parts.append(f"сценарий: {sc_lbl}")
        setup_hint = " · ".join(p for p in hint_parts if p)[:140]

    return ChartRead(
        symbol=sym,
        phase=phase,
        phase_ru=phase_ru,
        structure_ru=struct,
        bias=side,
        situation_ru=situation,
        expect_ru=expect,
        trigger_ru=trigger,
        flow_hint_ru=flow,
        pattern_label=pat_label,
        pattern_confidence=pat_conf,
        levels=levels,
        scenario_waypoints=wps,
        scenario_label_ru=sc_lbl,
        smc_ready=ready,
        smc_checklist_ru=cl_ru,
        draw_entry_bands=draw_entry,
        setup_hint_ru=setup_hint,
    )


def get_or_build_chart_read(
    ta: TAAnalysisResult,
    bars: list[KlineBar] | None = None,
    *,
    symbol: str = "",
) -> ChartRead:
    sym = (symbol or getattr(ta, "symbol", "") or "").upper()
    mm = dict(getattr(ta, "market_metrics", None) or {})
    cached = mm.get("chart_read_v1")
    cur = float(getattr(ta, "current_price", 0) or 0)
    if isinstance(cached, dict) and cached.get("symbol") == sym:
        if abs(float(cached.get("_price", cur) or 0) - cur) / max(cur, 1e-9) < 0.0008:
            return ChartRead.from_dict(cached)
    read = build_chart_read(ta, bars, symbol=sym)
    payload = read.to_dict()
    payload["_price"] = cur
    mm["chart_read_v1"] = payload
    ta.market_metrics = mm
    return read
