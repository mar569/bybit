"""PRO PNG: методология PDF (chart_pdf_style) или legacy spec."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt

from .bybit_klines import KlineBar
from .chart_display_policy import ed_pdf_chart_style_enabled, chart_trade_plan_on_chart_enabled
from .core.playbook.chart_spec import ChartSpec
from .ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)


def draw_pro_visual_mpl(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    spec: ChartSpec,
    *,
    interval_minutes: int = 15,
) -> int:
    if not bars:
        return 0
    if ed_pdf_chart_style_enabled():
        from .chart_pdf_style import draw_pdf_style_mpl

        drawn = draw_pdf_style_mpl(ax, bars, ta)
        if spec.show_trade_plan:
            _draw_trade_plan_mpl(ax, bars, ta)
        return max(drawn, 1)

    from .chart_pdf_style import sanitize_rbr_for_pdf

    ta = sanitize_rbr_for_pdf(ta, bars)
    logger.debug("legacy pro visual path (ED_PDF_CHART_STYLE=0)")
    return 1


def _draw_trade_plan_mpl(ax: plt.Axes, bars: list[KlineBar], ta: TAAnalysisResult) -> None:
    if not chart_trade_plan_on_chart_enabled():
        return
    from .chart_plan_chart_gate import plan_ok_to_draw_on_chart

    if not plan_ok_to_draw_on_chart(ta):
        return
    try:
        from matplotlib.patches import Rectangle
        from .chart_plan_display import build_display_plan
        from .chart_position_boxes import plan_for_display
        from .plan_staleness import plan_is_stale
        from .chart_ed_story import _visible_x_span
        from .chart_display_policy import chart_teaching_tags_enabled
        from .ta_analysis import fmt_price

        if plan_is_stale(ta):
            return
        raw = plan_for_display(ta)
        if raw is None:
            return
        side, _e, el, eh, stop, tp = raw
        disp = build_display_plan(side=side, entry=raw[1], entry_lo=el, entry_hi=eh, stop=stop, tp=tp)
        x0, x1 = _visible_x_span(ax, bars)
        w = max(x1 - x0, 0.001)

        def band(y0: float, y1: float, rgb: tuple[int, int, int], alpha: float, label: str) -> None:
            lo, hi = min(y0, y1), max(y0, y1)
            if hi <= lo:
                return
            ax.add_patch(
                Rectangle((x0, lo), w, hi - lo, facecolor=tuple(c / 255 for c in rgb), alpha=alpha, zorder=2)
            )
            if chart_teaching_tags_enabled():
                ax.text(x0 + w * 0.01, hi, f"  {label}  ", color="#e6edf3", fontsize=7, fontweight="bold", va="bottom", zorder=7)

        if side == "short":
            band(el, eh, (227, 179, 65), 0.28, f"Вход {fmt_price(el)}–{fmt_price(eh)}")
            if stop > eh:
                band(eh, stop, (248, 81, 73), 0.32, f"SL {disp.stop_label}")
            if tp < el:
                band(tp, el, (88, 166, 255), 0.28, f"TP {disp.tp_label}")
        else:
            band(el, eh, (227, 179, 65), 0.28, f"Вход {fmt_price(el)}–{fmt_price(eh)}")
            if stop < el:
                band(stop, el, (248, 81, 73), 0.32, f"SL {disp.stop_label}")
            if tp > eh:
                band(eh, tp, (88, 166, 255), 0.28, f"TP {disp.tp_label}")
    except Exception:
        logger.debug("trade plan mpl skipped", exc_info=True)
