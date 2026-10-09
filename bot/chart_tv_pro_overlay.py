"""PRO-разбор поверх скрина TradingView (координаты 0–1, без текста при visual-only)."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, Polygon, Rectangle

from .bybit_klines import KlineBar
from .chart_breakout_markers import collect_breakout_retest_events
from .chart_display_policy import (
    chart_anno_text_enabled,
    chart_breakout_marker_labels_enabled,
    chart_teaching_tags_enabled,
    ed_chart_visual_only,
)
from .chart_tv_coords import (
    bar_index_on_tv_screen,
    bar_x_norm,
    interpolate_bar_price,
    price_on_tv_screen,
    price_to_tv_y,
    tv_vis_start,
    tv_visible_bar_count,
)
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)

COL_BREAK = "#ff7b72"
COL_RETEST = "#ffd33d"
COL_FALSE = "#ffa657"


class TvCoordMapper:
    """Bar index + price → норм. область свечей на скрине TV."""

    def __init__(
        self,
        bars: list[KlineBar],
        y_min: float,
        y_max: float,
        *,
        interval_minutes: int = 5,
        display_hours: int | None = None,
    ) -> None:
        self.bars = bars
        self.n = len(bars)
        self.y_min = y_min
        self.y_max = y_max
        self.interval_minutes = interval_minutes
        self.display_hours = display_hours
        vis_count = tv_visible_bar_count(
            self.n, interval_minutes=interval_minutes, display_hours=display_hours,
        )
        self.vis_start = tv_vis_start(
            self.n, interval_minutes=interval_minutes, display_hours=display_hours,
        )
        self.vis_n = max(1, self.n - self.vis_start)
        self.x_start = 0.055
        self.x_end = 0.915

    def y(self, price: float) -> float:
        return price_to_tv_y(float(price), self.y_min, self.y_max)

    def price_visible(self, price: float) -> bool:
        return price_on_tv_screen(price, self.y_min, self.y_max)

    def bar_visible(self, bar_idx: int) -> bool:
        return bar_index_on_tv_screen(bar_idx, vis_start=self.vis_start, n=self.n)

    def x(self, bar_idx: int) -> float:
        if self.n <= 0:
            return self.x_end
        bar_idx = max(0, min(int(bar_idx), self.n - 1))
        local = max(0, bar_idx - self.vis_start)
        return bar_x_norm(local, self.vis_n, x_start=self.x_start, x_end=self.x_end)

    def hline(self, ax: plt.Axes, price: float, *, color: str, lw: float = 1.4, alpha: float = 0.9, ls: str = "-") -> None:
        yy = self.y(price)
        ax.plot([self.x_start, self.x_end], [yy, yy], color=color, linewidth=lw, alpha=alpha, linestyle=ls, zorder=4)

    def rect(
        self,
        ax: plt.Axes,
        i0: int,
        i1: int,
        bot: float,
        top: float,
        *,
        color: str,
        alpha: float = 0.12,
    ) -> None:
        x0, x1 = self.x(i0), self.x(i1)
        y0, y1 = self.y(bot), self.y(top)
        ax.add_patch(
            Rectangle(
                (min(x0, x1), min(y0, y1)),
                abs(x1 - x0) or 0.01,
                abs(y1 - y0) or 0.001,
                facecolor=color,
                edgecolor=color,
                alpha=alpha,
                linewidth=0.7,
                zorder=2,
            )
        )


def draw_primary_pattern_tv_layer(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    from .chart_patterns import pattern_relevant_now

    primary = getattr(ta, "primary_chart_pattern", None)
    if not primary or not getattr(ta, "reading_accept_pattern", True):
        return
    cur = float(getattr(ta, "current_price", 0) or mapper.bars[-1].close)
    if not pattern_relevant_now(primary, mapper.bars, current=cur):
        return
    pts = list(getattr(primary, "points", None) or [])
    if pts and max(int(p.index) for p in pts) < mapper.vis_start - 4:
        return
    style = getattr(primary, "kind", "") or "double_top"
    from .chart_pattern_draw import PATTERN_STYLE

    color = str(PATTERN_STYLE.get(style, {}).get("color", "#58a6ff"))
    _draw_primary_pattern_tv(ax, mapper, primary, color=color)
    if chart_teaching_tags_enabled():
        from .chart_teaching_tags import primary_pattern_tag

        tag = primary_pattern_tag(ta)
        if tag:
            ax.text(
                0.5,
                0.965,
                tag,
                transform=ax.transAxes,
                ha="center",
                va="top",
                color=color,
                fontsize=7.2,
                fontweight="bold",
                zorder=12,
                bbox=dict(boxstyle="round,pad=0.28", facecolor="#161b22ee", edgecolor=color, alpha=0.94),
            )


def _draw_primary_pattern_tv(ax: plt.Axes, mapper: TvCoordMapper, pattern, *, color: str) -> None:
    bars = mapper.bars
    if not pattern or not bars:
        return
    vis = mapper.vis_start
    for line in getattr(pattern, "lines", None) or []:
        end_idx = min(len(bars) - 1, max(line.end_idx, line.start_idx))
        if end_idx < vis:
            continue
        start_idx = int(line.start_idx)
        start_price = float(line.start_price)
        if start_idx < vis:
            start_idx = vis
            start_price = interpolate_bar_price(
                bars,
                vis,
                price_a=line.start_price,
                price_b=line.end_price,
                idx_a=line.start_idx,
                idx_b=line.end_idx,
            )
        if end_idx == line.start_idx:
            y1 = line.start_price
        else:
            slope = (line.end_price - line.start_price) / (line.end_idx - line.start_idx)
            y1 = line.start_price + slope * (end_idx - line.start_idx)
        if not mapper.price_visible(start_price) and not mapper.price_visible(y1):
            continue
        ax.plot(
            [mapper.x(start_idx), mapper.x(end_idx)],
            [mapper.y(start_price), mapper.y(y1)],
            color=color,
            linewidth=1.25,
            alpha=0.92,
            zorder=5,
        )
    pts = [p for p in (getattr(pattern, "points", None) or []) if mapper.bar_visible(p.index)]
    if len(pts) >= 3:
        xs = [mapper.x(p.index) for p in pts[:8]]
        ys = [mapper.y(p.price) for p in pts[:8]]
        ax.add_patch(
            Polygon(
                list(zip(xs, ys)),
                closed=True,
                facecolor=color,
                edgecolor=color,
                alpha=0.08,
                linewidth=1.0,
                zorder=3,
            )
        )
        ax.plot(xs, ys, color=color, linewidth=1.15, alpha=0.9, zorder=5)
    for p in pts:
        if getattr(p, "role", "") in {"neck_left", "neck_right", "pole_start", "pole_end"}:
            continue
        if not mapper.price_visible(p.price):
            continue
        ax.plot(mapper.x(p.index), mapper.y(p.price), marker="o", color=color, markersize=3.5, linestyle="None", zorder=6)


def _draw_sweep_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    smc = getattr(ta, "smc", None)
    if smc is None or not mapper.bars:
        return
    from .chart_event_pins import draw_sweep_pin_tv

    for marker in getattr(smc, "markers", []) or []:
        if getattr(marker, "kind", "") != "sweep" or marker.index >= len(mapper.bars):
            continue
        draw_sweep_pin_tv(ax, mapper, mapper.bars, marker)


def _draw_breakout_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    events = collect_breakout_retest_events(mapper.bars, ta, max_events=1)
    show_labels = chart_breakout_marker_labels_enabled()
    for ev in events:
        if not mapper.bar_visible(ev.bar_idx):
            continue
        if ev.kind == "breakout":
            color = COL_BREAK
        elif ev.kind == "retest":
            color = COL_RETEST
        else:
            color = COL_FALSE
        ax.plot(
            mapper.x(ev.bar_idx),
            mapper.y(ev.price),
            marker="o",
            markersize=9,
            markerfacecolor="none",
            markeredgecolor=color,
            markeredgewidth=2.0,
            linestyle="None",
            zorder=8,
        )
        if show_labels:
            lbl = ev.label[:24]
            if chart_teaching_tags_enabled() and ed_chart_visual_only():
                lbl = {"breakout": "ПРОБОЙ", "retest": "RETEST", "false_break": "ЛОЖН."}.get(ev.kind, lbl[:8])
            ax.text(
                mapper.x(ev.bar_idx),
                mapper.y(ev.price),
                lbl,
                color=color,
                fontsize=6,
                ha="center",
                va="bottom",
                zorder=9,
            )
def _draw_context_zone_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    import os

    if os.environ.get("ED_CHART_PDF_ZONES", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    cur = float(getattr(ta, "current_price", 0) or mapper.bars[-1].close)
    metrics = getattr(ta, "market_metrics", None) or {}
    raw = metrics.get("pdf_zones") if isinstance(metrics, dict) else None
    picked = None
    if isinstance(raw, list):
        for z in raw:
            if not isinstance(z, dict) or not z.get("valid", False):
                continue
            top, bot = float(z.get("top", 0) or 0), float(z.get("bottom", 0) or 0)
            if top <= bot or abs((top + bot) / 2 - cur) / cur > 0.12:
                continue
            picked = (bot, top, str(z.get("kind", "")))
            break
    if picked is None:
        return
    bot, top, kind = picked
    if not mapper.price_visible(bot) and not mapper.price_visible(top):
        return
    color = "#3fb950" if any(k in kind for k in ("demand", "bull", "support")) else "#f85149"
    i0 = mapper.vis_start
    mapper.rect(ax, i0, len(mapper.bars) - 1, bot, top, color=color, alpha=0.14)


def _draw_rbr_story_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return
    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    phase = str(rbr.get("phase") or "")
    resistance = ceil if ceil > 0 else float(rbr.get("entry_hi") or 0)

    if floor > 0 and mapper.price_visible(floor):
        mapper.hline(ax, floor, color="#8b949e", lw=1.0, alpha=0.65, ls="--")
        if chart_teaching_tags_enabled():
            from .chart_level_labels import level_tag

            ax.text(
                mapper.x_start + 0.008,
                mapper.y(floor),
                f" {level_tag('range_bottom', floor)}",
                color="#8b949e",
                fontsize=6.5,
                va="top",
                zorder=8,
            )

    cur = float(getattr(ta, "current_price", 0) or mapper.bars[-1].close)
    if phase == "await_break" and floor > 0 and cur < floor * 0.996:
        el = float(rbr.get("entry_lo") or floor * 0.996)
        eh = float(rbr.get("entry_hi") or floor * 1.01)
        mapper.hline(ax, eh, color="#58a6ff", lw=1.5, alpha=0.9)
        mapper.hline(ax, el, color="#58a6ff", lw=1.5, alpha=0.9)
        if chart_teaching_tags_enabled():
            ax.text(mapper.x_start + 0.008, mapper.y(eh), " RETEST", color="#58a6ff", fontsize=6.5, va="bottom", zorder=8)
    elif phase == "await_break" and floor > 0 and ceil > floor:
        i0 = mapper.vis_start
        mapper.rect(ax, i0, len(mapper.bars) - 1, floor, ceil, color="#8b949e", alpha=0.08)
        if mapper.price_visible(ceil) and chart_teaching_tags_enabled():
            from .chart_level_labels import level_tag

            ax.text(
                mapper.x_start + 0.008,
                mapper.y(ceil),
                f" {level_tag('range_top', ceil)}",
                color="#f0c040",
                fontsize=6.5,
                va="bottom",
                zorder=8,
            )
    elif phase == "fade_top" and resistance > 0:
        el = float(rbr.get("entry_lo") or resistance * 0.985)
        eh = float(rbr.get("entry_hi") or resistance * 1.006)
        z_lo, z_hi = min(el, resistance * 0.998), max(eh, resistance * 1.002)
        i0 = mapper.vis_start
        mapper.rect(ax, i0, len(mapper.bars) - 1, z_lo, z_hi, color="#f0c040", alpha=0.11)
        mapper.hline(ax, z_hi, color="#f0c040", lw=1.5, alpha=0.9)
        mapper.hline(ax, z_lo, color="#f0c040", lw=1.5, alpha=0.9)
        from .chart_display_policy import chart_entry_zone_tags_enabled

        if chart_teaching_tags_enabled() and chart_entry_zone_tags_enabled():
            ax.text(mapper.x_start + 0.008, mapper.y(z_hi), " ЗОНА", color="#f0c040", fontsize=6.5, va="bottom", zorder=8)
    elif phase == "retest" and floor > 0:
        el = float(rbr.get("entry_lo") or floor * 0.996)
        eh = float(rbr.get("entry_hi") or floor * 1.01)
        mapper.hline(ax, eh, color="#58a6ff", lw=1.5, alpha=0.9)
        mapper.hline(ax, el, color="#58a6ff", lw=1.5, alpha=0.9)

    _draw_forward_boxes_tv(ax, mapper, ta, rbr)


def _draw_forward_boxes_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult, rbr: dict | None) -> None:
    from .chart_display_policy import chart_trade_plan_on_chart_enabled

    if not chart_trade_plan_on_chart_enabled():
        return
    from .chart_plan_display import build_display_plan
    from .chart_position_boxes import plan_for_display
    from .plan_staleness import plan_is_stale

    if plan_is_stale(ta):
        return
    raw = plan_for_display(ta)
    if raw is None:
        return
    side, entry, entry_lo, entry_hi, stop, tp = raw
    disp = build_display_plan(
        side=side,
        entry=entry,
        entry_lo=entry_lo,
        entry_hi=entry_hi,
        stop=stop,
        tp=tp,
    )
    stop, tp = disp.stop, disp.tp
    x_box, width = 0.905, 0.075
    if side == "short" and stop > entry_hi and tp < entry_lo:
        y_tp, y_ent_lo, y_ent_hi, y_stop = mapper.y(tp), mapper.y(entry_lo), mapper.y(entry_hi), mapper.y(stop)
        ax.add_patch(Rectangle((x_box, min(y_ent_hi, y_stop)), width, abs(y_stop - y_ent_hi), facecolor="#f85149", alpha=0.32, zorder=9))
        ax.add_patch(Rectangle((x_box, min(y_ent_lo, y_ent_hi)), width, abs(y_ent_hi - y_ent_lo), facecolor="#e3b341", alpha=0.22, zorder=9))
        ax.add_patch(Rectangle((x_box, min(y_tp, y_ent_lo)), width, abs(y_ent_lo - y_tp), facecolor="#3fb950", alpha=0.30, zorder=9))
        _tv_plan_glyphs(ax, x_box, width, y_tp, y_ent_lo, y_ent_hi, y_stop, side="short")
    elif side == "long" and stop < entry_lo and tp > entry_hi:
        y_stop, y_ent_lo, y_ent_hi, y_tp = mapper.y(stop), mapper.y(entry_lo), mapper.y(entry_hi), mapper.y(tp)
        ax.add_patch(Rectangle((x_box, min(y_stop, y_ent_lo)), width, abs(y_ent_lo - y_stop), facecolor="#f85149", alpha=0.32, zorder=9))
        ax.add_patch(Rectangle((x_box, min(y_ent_lo, y_ent_hi)), width, abs(y_ent_hi - y_ent_lo), facecolor="#e3b341", alpha=0.22, zorder=9))
        ax.add_patch(Rectangle((x_box, min(y_ent_hi, y_tp)), width, abs(y_tp - y_ent_hi), facecolor="#3fb950", alpha=0.30, zorder=9))
        _tv_plan_glyphs(ax, x_box, width, y_tp, y_ent_lo, y_ent_hi, y_stop, side="long")


def _tv_plan_glyphs(
    ax: plt.Axes,
    x_box: float,
    width: float,
    y_tp: float,
    y_ent_lo: float,
    y_ent_hi: float,
    y_stop: float,
    *,
    side: str,
) -> None:
    from .chart_display_policy import chart_plan_glyphs_enabled

    if not chart_plan_glyphs_enabled():
        return
    cx = x_box + width * 0.52
    for y, glyph, color in (
        ((y_ent_lo + y_tp) / 2, "TP", "#3fb950"),
        ((y_ent_lo + y_ent_hi) / 2, "IN", "#e6edf3"),
        ((y_ent_hi + y_stop) / 2 if side == "short" else (y_stop + y_ent_lo) / 2, "SL", "#f85149"),
    ):
        ax.text(
            cx,
            y,
            glyph,
            color=color,
            fontsize=8.5,
            fontweight="bold",
            ha="center",
            va="center",
            zorder=10,
            bbox=dict(boxstyle="circle,pad=0.25", facecolor="#0d1117cc", edgecolor=color, linewidth=0.7),
        )


def _draw_observation_baseline_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> int:
    """Минимум для «пустых» кадров (нефть, flat): сопр/подд если есть в TA."""
    from .chart_display_policy import chart_teaching_tags_enabled

    layers = 0
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk > 0 and mapper.price_visible(brk):
        mapper.hline(ax, brk, color="#f0c040", lw=1.25, alpha=0.82)
        if chart_teaching_tags_enabled():
            from .chart_level_labels import level_tag

            ax.text(
                mapper.x_start + 0.008,
                mapper.y(brk),
                f" {level_tag('break_up', brk)}",
                color="#f0c040",
                fontsize=6.5,
                va="bottom",
                zorder=8,
            )
        layers += 1
    if brdn > 0 and mapper.price_visible(brdn):
        mapper.hline(ax, brdn, color="#3fb950", lw=1.25, alpha=0.82)
        if chart_teaching_tags_enabled():
            from .chart_level_labels import level_tag

            ax.text(
                mapper.x_start + 0.008,
                mapper.y(brdn),
                f" {level_tag('break_down', brdn)}",
                color="#3fb950",
                fontsize=6.5,
                va="top",
                zorder=8,
            )
        layers += 1
    return layers


def _draw_probable_path_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    from .chart_display_policy import chart_trade_plan_on_chart_enabled
    from .plan_staleness import plan_is_stale
    from .pro_invariants import target_matches_side

    if not chart_trade_plan_on_chart_enabled() or plan_is_stale(ta):
        return
    side = str(getattr(ta, "action_priority", "") or "").lower()
    rbr = get_rbr_from_ta(ta)
    if rbr and str(rbr.get("direction") or "") in {"long", "short"}:
        side = str(rbr["direction"])
    tps = [float(x) for x in (getattr(ta, "target_prices", None) or []) if x]
    if rbr:
        tps = tps or [float(x) for x in (rbr.get("targets") or []) if x]
    if not tps or not mapper.bars or side not in {"long", "short"}:
        return
    tp = tps[0]
    cur = float(getattr(ta, "current_price", 0) or mapper.bars[-1].close)
    if not target_matches_side(side, cur, tp):
        return
    x0 = mapper.x(len(mapper.bars) - 1)
    x1 = min(x0 + 0.12, 0.86)
    ax.annotate(
        "",
        xy=(x1, mapper.y(tp)),
        xytext=(x0, mapper.y(cur)),
        arrowprops=dict(arrowstyle="-|>", color="#58a6ff", lw=1.2, linestyle=(0, (4, 3)), alpha=0.82),
        zorder=5,
    )


def _draw_tv_story_banner(ax: plt.Axes, ta: TAAnalysisResult, *, mode: str) -> None:
    if not chart_anno_text_enabled():
        return
    from .chart_teaching_tags import story_banner_line

    line = story_banner_line(ta, mode=mode)
    if not line:
        return
    ax.text(
        0.5,
        0.03,
        line,
        transform=ax.transAxes,
        va="bottom",
        ha="center",
        color="#e6edf3",
        fontsize=7.0,
        zorder=12,
        bbox=dict(boxstyle="round,pad=0.32", facecolor="#161b22ee", edgecolor="#484f58", alpha=0.96),
    )


def _draw_range_wait_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    brk = float(getattr(ta, "breakout_level", 0) or 0)
    brdn = float(getattr(ta, "breakdown_level", 0) or 0)
    if brk <= 0 or brdn <= 0 or brk <= brdn:
        return
    i0 = max(0, len(mapper.bars) - min(len(mapper.bars), 56))
    mapper.rect(ax, i0, len(mapper.bars) - 1, brdn, brk, color="#8b949e", alpha=0.12)
    mapper.hline(ax, brk, color="#f0c040", lw=1.35, alpha=0.85)
    mapper.hline(ax, brdn, color="#3fb950", lw=1.35, alpha=0.85)
    if chart_teaching_tags_enabled():
        ax.text(mapper.x_start + 0.008, mapper.y(brk), " СОПР", color="#f0c040", fontsize=6.5, va="bottom", zorder=8)
        ax.text(mapper.x_start + 0.008, mapper.y(brdn), " ПОДД", color="#3fb950", fontsize=6.5, va="top", zorder=8)
    _draw_forward_boxes_tv(ax, mapper, ta, get_rbr_from_ta(ta))
    _draw_probable_path_tv(ax, mapper, ta)


def compose_tradingview_pro_png(
    tv_png: bytes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
    display_hours: int | None = None,
) -> bytes | None:
    import io

    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt

    from .chart_tv_coords import tv_visible_price_range
    from .chart_tv_image import TV_EXPORT_HEIGHT, TV_EXPORT_WIDTH

    dpi = 160
    from .chart_tv_image import prepare_tv_background

    prepared = prepare_tv_background(tv_png)
    if prepared is None:
        return None

    img = mpimg.imread(io.BytesIO(prepared))
    fig_w, fig_h = TV_EXPORT_WIDTH / dpi, TV_EXPORT_HEIGHT / dpi
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
    fig.subplots_adjust(0, 0, 1, 1)
    fig.patch.set_facecolor("#0d1117")
    ax.set_position([0, 0, 1, 1])
    # aspect='equal' на широком fig даёт «полоску» по центру — только auto
    ax.imshow(img, extent=[0, 1, 0, 1], aspect="auto", zorder=0, interpolation="bilinear")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_anchor("C")
    y_min, y_max = tv_visible_price_range(
        bars, ta, interval_minutes=interval_minutes, display_hours=display_hours,
    )
    drew = draw_tv_pro_layers(
        ax,
        bars,
        ta,
        y_min=y_min,
        y_max=y_max,
        interval_minutes=interval_minutes,
        display_hours=display_hours,
    )
    from .range_breakdown_retest import get_rbr_from_ta

    if drew < 2 or (drew < 4 and not get_rbr_from_ta(ta)):
        plt.close(fig)
        try:
            from .chart_display_policy import ed_chart_composite_enabled

            if ed_chart_composite_enabled():
                logger.info(
                    "TV overlay light (%s layers) — full markup via matplotlib composite",
                    drew,
                )
            else:
                logger.warning("TV overlay too sparse (%s layers) — matplotlib fallback", drew)
        except Exception:
            logger.warning("TV overlay too sparse (%s layers) — matplotlib fallback", drew)
        return None
    buffer = io.BytesIO()
    fig.savefig(
        buffer,
        format="png",
        dpi=dpi,
        facecolor=fig.get_facecolor(),
        pad_inches=0,
        bbox_inches=None,
    )
    plt.close(fig)
    buffer.seek(0)
    return buffer.getvalue()


def draw_tv_pro_layers(
    ax: plt.Axes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    y_min: float,
    y_max: float,
    interval_minutes: int = 15,
    display_hours: int | None = None,
) -> int:
    """Число ключевых слоёв (для проверки «пустого» TV PNG)."""
    if not bars:
        return 0
    layers = 0
    try:
        from .chart_story_router import enrich_ta_for_chart_story

        ta = enrich_ta_for_chart_story(ta, bars)
    except Exception:
        logger.debug("TV overlay enrich skipped", exc_info=True)

    mapper = TvCoordMapper(
        bars,
        y_min,
        y_max,
        interval_minutes=interval_minutes,
        display_hours=display_hours,
    )

    try:
        from .core.playbook.chart_layers import try_draw_tv_playbook_layers

        spec_layers = try_draw_tv_playbook_layers(
            ax, mapper, ta, bars, symbol=str(getattr(ta, "symbol", "") or ""),
        )
        if spec_layers is not None:
            return spec_layers
    except Exception:
        logger.debug("playbook TV spec layers skipped", exc_info=True)

    from .chart_pro import resolve_pro_chart_mode

    mode = resolve_pro_chart_mode(ta, allow_legacy=False)

    if mode == "ed_story":
        _draw_rbr_story_tv(ax, mapper, ta)
        layers += 2
    elif mode == "range_wait":
        _draw_range_wait_tv(ax, mapper, ta)
        layers += 2
    elif mode == "observation":
        brk = float(getattr(ta, "breakout_level", 0) or 0)
        brdn = float(getattr(ta, "breakdown_level", 0) or 0)
        if brk > 0:
            mapper.hline(ax, brk, color="#f0c040", lw=1.2, alpha=0.75)
            layers += 1
        if brdn > 0:
            mapper.hline(ax, brdn, color="#3fb950", lw=1.2, alpha=0.75)
            layers += 1
        _draw_forward_boxes_tv(ax, mapper, ta, get_rbr_from_ta(ta))
        _draw_probable_path_tv(ax, mapper, ta)
        layers += 1

    draw_primary_pattern_tv_layer(ax, mapper, ta)
    if getattr(ta, "primary_chart_pattern", None):
        layers += 1
    _draw_sweep_tv(ax, mapper, ta)
    if getattr(getattr(ta, "smc", None), "markers", None):
        layers += 1

    from .chart_education_visual import draw_education_visuals_tv

    draw_education_visuals_tv(mapper, ax, ta)
    layers += 1
    _draw_context_zone_tv(ax, mapper, ta)
    _draw_breakout_tv(ax, mapper, ta)
    if collect_breakout_retest_events(mapper.bars, ta, max_events=1):
        layers += 1
    _draw_tv_story_banner(ax, ta, mode=mode)
    return layers
