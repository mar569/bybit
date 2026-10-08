"""PRO-разбор поверх скрина TradingView (координаты 0–1, без текста при visual-only)."""
from __future__ import annotations

import logging

import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, Polygon, Rectangle

from .bybit_klines import KlineBar
from .chart_breakout_markers import collect_breakout_retest_events
from .chart_display_policy import chart_breakout_marker_labels_enabled, ed_chart_visual_only
from .chart_tv_coords import bar_x_norm, price_to_tv_y
from .range_breakdown_retest import get_rbr_from_ta
from .ta_analysis import TAAnalysisResult

logger = logging.getLogger(__name__)

COL_BREAK = "#ff7b72"
COL_RETEST = "#ffd33d"
COL_FALSE = "#ffa657"


class TvCoordMapper:
    """Bar index + price → норм. область свечей на скрине TV."""

    def __init__(self, bars: list[KlineBar], y_min: float, y_max: float) -> None:
        self.bars = bars
        self.n = len(bars)
        self.y_min = y_min
        self.y_max = y_max
        vis_count = max(24, min(self.n, int(self.n * 0.58))) if self.n else 24
        self.vis_start = max(0, self.n - vis_count)
        self.vis_n = max(1, self.n - self.vis_start)
        self.x_start = 0.02
        self.x_end = 0.96

    def y(self, price: float) -> float:
        return price_to_tv_y(float(price), self.y_min, self.y_max)

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


def _draw_primary_pattern_tv(ax: plt.Axes, mapper: TvCoordMapper, pattern, *, color: str) -> None:
    bars = mapper.bars
    if not pattern or not bars:
        return
    for line in getattr(pattern, "lines", None) or []:
        end_idx = min(len(bars) - 1, max(line.end_idx, line.start_idx))
        if end_idx == line.start_idx:
            y1 = line.start_price
        else:
            slope = (line.end_price - line.start_price) / (line.end_idx - line.start_idx)
            y1 = line.start_price + slope * (end_idx - line.start_idx)
        ax.plot(
            [mapper.x(line.start_idx), mapper.x(end_idx)],
            [mapper.y(line.start_price), mapper.y(y1)],
            color=color,
            linewidth=1.25,
            alpha=0.92,
            zorder=5,
        )
    pts = list(getattr(pattern, "points", None) or [])
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
        ax.plot(mapper.x(p.index), mapper.y(p.price), marker="o", color=color, markersize=3.5, linestyle="None", zorder=6)


def _draw_sweep_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    smc = getattr(ta, "smc", None)
    if smc is None or not mapper.bars:
        return
    w, h = 0.018, 0.012
    for marker in getattr(smc, "markers", []) or []:
        if getattr(marker, "kind", "") != "sweep" or marker.index >= len(mapper.bars):
            continue
        color = "#ffd33d" if getattr(marker, "direction", "") == "long" else "#ff7b72"
        ax.add_patch(
            Ellipse(
                (mapper.x(marker.index), mapper.y(marker.price)),
                w,
                h,
                fill=False,
                edgecolor=color,
                linewidth=2.0,
                zorder=7,
            )
        )


def _draw_breakout_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    events = collect_breakout_retest_events(mapper.bars, ta, max_events=2)
    show_labels = chart_breakout_marker_labels_enabled()
    for ev in events:
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
            ax.text(
                mapper.x(ev.bar_idx),
                mapper.y(ev.price),
                ev.label[:24],
                color=color,
                fontsize=6,
                ha="center",
                va="bottom",
                zorder=9,
            )


def _draw_context_zone_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
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
    color = "#3fb950" if any(k in kind for k in ("demand", "bull", "support")) else "#f85149"
    i0 = max(0, len(mapper.bars) - min(len(mapper.bars), 56))
    mapper.rect(ax, i0, len(mapper.bars) - 1, bot, top, color=color, alpha=0.14)


def _draw_rbr_story_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    rbr = get_rbr_from_ta(ta)
    if not rbr:
        return
    floor = float(rbr.get("range_bottom") or 0)
    ceil = float(rbr.get("range_top") or 0)
    phase = str(rbr.get("phase") or "")
    resistance = ceil if ceil > 0 else float(rbr.get("entry_hi") or 0)

    if floor > 0:
        mapper.hline(ax, floor, color="#8b949e", lw=1.0, alpha=0.65, ls="--")

    if phase in {"fade_top", "await_break"} and resistance > 0:
        el = float(rbr.get("entry_lo") or resistance * 0.985)
        eh = float(rbr.get("entry_hi") or resistance * 1.006)
        z_lo, z_hi = min(el, resistance * 0.998), max(eh, resistance * 1.002)
        i0 = max(0, len(mapper.bars) - min(len(mapper.bars), 48))
        mapper.rect(ax, i0, len(mapper.bars) - 1, z_lo, z_hi, color="#f0c040", alpha=0.11)
        mapper.hline(ax, z_hi, color="#f0c040", lw=1.5, alpha=0.9)
        mapper.hline(ax, z_lo, color="#f0c040", lw=1.5, alpha=0.9)
    elif phase == "retest" and floor > 0:
        el = float(rbr.get("entry_lo") or floor * 0.996)
        eh = float(rbr.get("entry_hi") or floor * 1.01)
        mapper.hline(ax, eh, color="#58a6ff", lw=1.5, alpha=0.9)
        mapper.hline(ax, el, color="#58a6ff", lw=1.5, alpha=0.9)

    _draw_forward_boxes_tv(ax, mapper, ta, rbr)


def _draw_forward_boxes_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult, rbr: dict | None) -> None:
    from .chart_plan_display import build_display_plan
    from .chart_position_boxes import plan_for_display

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
    elif side == "long" and stop < entry_lo and tp > entry_hi:
        y_stop, y_ent_lo, y_ent_hi, y_tp = mapper.y(stop), mapper.y(entry_lo), mapper.y(entry_hi), mapper.y(tp)
        ax.add_patch(Rectangle((x_box, min(y_stop, y_ent_lo)), width, abs(y_ent_lo - y_stop), facecolor="#f85149", alpha=0.32, zorder=9))
        ax.add_patch(Rectangle((x_box, min(y_ent_lo, y_ent_hi)), width, abs(y_ent_hi - y_ent_lo), facecolor="#e3b341", alpha=0.22, zorder=9))
        ax.add_patch(Rectangle((x_box, min(y_ent_hi, y_tp)), width, abs(y_tp - y_ent_hi), facecolor="#3fb950", alpha=0.30, zorder=9))


def _draw_probable_path_tv(ax: plt.Axes, mapper: TvCoordMapper, ta: TAAnalysisResult) -> None:
    side = str(getattr(ta, "action_priority", "") or "").lower()
    tps = [float(x) for x in (getattr(ta, "target_prices", None) or []) if x]
    if not tps or not mapper.bars:
        return
    tp = tps[0]
    cur = float(getattr(ta, "current_price", 0) or mapper.bars[-1].close)
    x0 = mapper.x(len(mapper.bars) - 1)
    x1 = min(x0 + 0.12, 0.86)
    ax.annotate(
        "",
        xy=(x1, mapper.y(tp)),
        xytext=(x0, mapper.y(cur)),
        arrowprops=dict(arrowstyle="-|>", color="#58a6ff", lw=1.2, linestyle=(0, (4, 3)), alpha=0.82),
        zorder=5,
    )


def compose_tradingview_pro_png(
    tv_png: bytes,
    bars: list[KlineBar],
    ta: TAAnalysisResult,
    *,
    interval_minutes: int = 15,
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
    img_h, img_w = img.shape[:2]
    if img_w < 100:
        img_w, img_h = TV_EXPORT_WIDTH, TV_EXPORT_HEIGHT
    fig, ax = plt.subplots(figsize=(img_w / dpi, img_h / dpi), dpi=dpi)
    fig.subplots_adjust(0, 0, 1, 1)
    fig.patch.set_facecolor("#0d1117")
    ax.set_position([0, 0, 1, 1])
    # aspect='equal' на широком fig даёт «полоску» по центру — только auto
    ax.imshow(img, extent=[0, 1, 0, 1], aspect="auto", zorder=0, interpolation="bilinear")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_anchor("C")
    y_min, y_max = tv_visible_price_range(bars, ta)
    draw_tv_pro_layers(ax, bars, ta, y_min=y_min, y_max=y_max, interval_minutes=interval_minutes)
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
) -> None:
    if not bars:
        return
    try:
        from .chart_story_router import enrich_ta_for_chart_story

        ta = enrich_ta_for_chart_story(ta, bars)
    except Exception:
        logger.debug("TV overlay enrich skipped", exc_info=True)

    mapper = TvCoordMapper(bars, y_min, y_max)

    from .chart_pro import resolve_pro_chart_mode

    mode = resolve_pro_chart_mode(ta, allow_legacy=False)

    if mode == "ed_story":
        _draw_rbr_story_tv(ax, mapper, ta)
    elif mode in {"range_wait", "observation"}:
        brk = float(getattr(ta, "breakout_level", 0) or 0)
        brdn = float(getattr(ta, "breakdown_level", 0) or 0)
        if brk > 0:
            mapper.hline(ax, brk, color="#f0c040", lw=1.2, alpha=0.75)
        if brdn > 0:
            mapper.hline(ax, brdn, color="#3fb950", lw=1.2, alpha=0.75)
        _draw_forward_boxes_tv(ax, mapper, ta, get_rbr_from_ta(ta))
        _draw_probable_path_tv(ax, mapper, ta)

    from .chart_education_visual import draw_education_visuals_tv

    draw_education_visuals_tv(mapper, ax, ta)
    _draw_context_zone_tv(ax, mapper, ta)
    _draw_breakout_tv(ax, mapper, ta)
