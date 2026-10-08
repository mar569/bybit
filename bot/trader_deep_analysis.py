"""Пуш «разбор как у Ed» в чат анализов — короткая подпись + PNG."""
from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Signal
    from .ta_analysis import TAAnalysisResult

from .range_breakdown_retest import get_rbr_from_ta, should_push_trader_deep_analysis
from .ta_analysis import fmt_price


def _compact_mtf_hint(ta: object) -> str:
    stack = str(getattr(ta, "reading_tf_stack", "") or "").strip()
    if not stack:
        return ""
    parts = [p.strip() for p in stack.replace("→", "·").split("·") if p.strip()]
    if len(parts) > 4:
        parts = parts[:2] + ["…"] + parts[-1:]
    return " · ".join(parts)[:120]


def build_trader_deep_analysis_html(
    signal: "Signal",
    ta: "TAAnalysisResult",
) -> str:
    sym = (signal.symbol or "").upper()
    ex = (signal.exchange or "bybit").title()
    iv = int(getattr(ta, "analysis_interval_minutes", 0) or 0)
    mm = getattr(ta, "market_metrics", None) or {}
    scan_iv = int(mm.get("chart_scanner_interval", 0) or 0) if isinstance(mm, dict) else 0
    tf_line = f"{iv}m"
    if scan_iv and scan_iv != iv:
        tf_line = f"сетап {iv}m (сканер {scan_iv}m)"

    link = (signal.link or "").strip()
    link_html = f'\n<a href="{escape(link)}">CoinGlass</a>' if link else ""

    rbr = get_rbr_from_ta(ta)
    if rbr:
        label = escape(str(rbr.get("label_ru", "") or "сценарий"))
        phase = str(rbr.get("phase") or "")
        floor = fmt_price(float(rbr.get("range_bottom") or 0))
        ceil = fmt_price(float(rbr.get("range_top") or 0))
        stop = fmt_price(float(rbr["stop"])) if rbr.get("stop") else "—"
        el, eh = rbr.get("entry_lo"), rbr.get("entry_hi")
        entry = (
            f"{fmt_price(float(el))}–{fmt_price(float(eh))}"
            if el and eh
            else "—"
        )
        tgs = " → ".join(
            fmt_price(float(t)) for t in (rbr.get("targets") or [])[:2] if t
        )
        conf = int(getattr(ta, "verdict_confidence", 0) or 0)

        lines = [
            f"📐 <b>{escape(sym)}</b> · {ex} · {escape(tf_line)}",
            f"<b>{label}</b>",
        ]
        if phase in {"fade_top", "await_break"}:
            lines.append(
                f"Зона <b>{floor}–{ceil}</b> · смотрим <b>реакцию у {ceil}</b> — "
                f"шорт только после отказа, <b>не в зелёный импульс</b>."
            )
            lines.append(f"Черновик: вход {entry} · стоп {stop}" + (f" · цели {tgs}" if tgs else ""))
        elif phase == "retest":
            lines.append(
                f"Под полом <b>{floor}</b> · retest · вход {entry} · стоп {stop}"
                + (f" · цели {tgs}" if tgs else "")
            )
        else:
            lines.append(f"Вход {entry} · стоп {stop}" + (f" · цели {tgs}" if tgs else ""))

        if float(getattr(ta, "drawdown_from_high_pct", 0) or 0) >= 6.0 or bool(
            getattr(ta, "post_pump", False)
        ):
            lines.append("<i>На истории был сильный импульс/слив — без догонялок.</i>")

        mtf = _compact_mtf_hint(ta)
        if mtf:
            lines.append(f"🧭 {escape(mtf)}")

        lines.append(f"⏸ Не market сейчас ({conf}/10) — ориентир на график.")
        if phase in {"fade_top", "await_break"}:
            lines.append(
                "<i>👁 Если цена дойдёт до зоны — отдельное сообщение «в нашей зоне».</i>"
            )
        return "\n".join(lines) + link_html

    from .living_analysis import build_living_analysis_html

    living = build_living_analysis_html(ta, symbol=sym)
    if living:
        return living + link_html

    conf = int(getattr(ta, "verdict_confidence", 0) or 0)
    seek = str(getattr(ta, "reading_seek_label", "") or "").strip()[:200]
    body = seek or "Смотрим структуру на графике — без длинного текста."
    return (
        f"📐 <b>{escape(sym)}</b> · {ex} · {escape(tf_line)}\n"
        f"{escape(body)}\n"
        f"⏸ Не market ({conf}/10)."
        f"{link_html}"
    )


def should_push_trader_deep_analysis_for_settings(ta: object, *, enabled: bool) -> bool:
    if not enabled or not should_push_trader_deep_analysis(ta):
        return False
    if get_rbr_from_ta(ta):
        from .trade_plan_quality import validate_trade_plan

        pq = validate_trade_plan(ta)  # type: ignore[arg-type]
        if not pq.ok:
            return False
    return True
