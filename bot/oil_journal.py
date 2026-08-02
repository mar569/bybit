"""Журнал исходов oil-setup: сбылось / нет / время вышло."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class OilSetupWatch:
    side: str  # LONG | SHORT
    entry: float
    stop: float
    tp1: float
    tp2: float | None
    price_at_signal: float
    catalyst: str
    created_ts: float
    quality: int = 0
    resolved: bool = False
    outcome: str = ""  # hit_tp | hit_sl | expired | manual_ok | manual_fail


def risk_checklist_lines(
    *,
    side: str,
    price: float,
    entry_lo: float | None,
    entry_hi: float | None,
    stop: float | None,
    tp1: float | None,
    tp2: float | None,
    invalidation: float | None,
    catalyst: str = "",
    account_risk_pct: float = 1.0,
) -> list[str]:
    """Чеклист перед входом — выжать важное."""
    lines = ["<b>✅ Чеклист перед входом</b>"]
    dir_ru = "покупка (LONG)" if side == "LONG" else "продажа (SHORT)"
    lines.append(f"• Направление: <b>{dir_ru}</b>")
    if entry_lo is not None and entry_hi is not None:
        lines.append(f"• Уровень входа: <b>{entry_lo:.2f}–{entry_hi:.2f}</b>")
    elif price:
        lines.append(f"• Ориентир цены сейчас: <b>{price:.2f}</b>")
    if stop is not None:
        lines.append(f"• Стоп: <b>{stop:.2f}</b>")
    tps = []
    if tp1 is not None:
        tps.append(f"{tp1:.2f}")
    if tp2 is not None:
        tps.append(f"{tp2:.2f}")
    if tps:
        lines.append(f"• Цели: <b>{' / '.join(tps)}</b>")

    entry_mid = None
    if entry_lo is not None and entry_hi is not None:
        entry_mid = (float(entry_lo) + float(entry_hi)) / 2.0
    elif price:
        entry_mid = float(price)

    if entry_mid and stop is not None and entry_mid > 0:
        stop_dist = abs(entry_mid - float(stop))
        risk_pct = stop_dist / entry_mid * 100.0
        lines.append(
            f"• Риск по стопу: ≈<b>{risk_pct:.2f}%</b> цены "
            f"(${stop_dist:.2f} на баррель)"
        )
        rr = ""
        if tp1 is not None and stop_dist > 0:
            reward = abs(float(tp1) - entry_mid)
            rr = f" · R:R до TP1 ≈ <b>{reward / stop_dist:.1f}</b>"
        risk_acc = max(0.25, min(2.0, float(account_risk_pct)))
        lines.append(
            f"• Размер: риск <b>{risk_acc:g}%</b> депозита на эту сделку"
            f"{rr}"
        )
        lines.append(
            f"  <i>Пример: депо $10 000 → риск ${10_000 * risk_acc / 100:.0f}; "
            f"движение стопа ${stop_dist:.2f} → объём ≈ "
            f"${(10_000 * risk_acc / 100) / stop_dist:.0f} нотионала</i>"
        )

    if invalidation is not None:
        if side == "LONG":
            lines.append(f"• Отмена, если цена уйдёт ниже <b>{invalidation:.2f}</b>")
        else:
            lines.append(f"• Отмена, если цена уйдёт выше <b>{invalidation:.2f}</b>")
    if catalyst:
        cat = catalyst[:120].replace("<", "").replace(">", "")
        lines.append(f"• Главный почему (новость): <i>{cat}</i>")
    lines.append(
        "• Помни: новости США/Иран/Ормуз сейчас важнее «красивого» графика"
    )
    return lines


def resolve_watch_against_price(watch: OilSetupWatch, price: float) -> str | None:
    """None если ещё рано; иначе hit_tp / hit_sl."""
    if watch.resolved or price <= 0:
        return None
    if watch.side == "LONG":
        if price <= watch.stop:
            return "hit_sl"
        if price >= watch.tp1:
            return "hit_tp"
        if watch.tp2 is not None and price >= watch.tp2:
            return "hit_tp"
    else:  # SHORT
        if price >= watch.stop:
            return "hit_sl"
        if price <= watch.tp1:
            return "hit_tp"
        if watch.tp2 is not None and price <= watch.tp2:
            return "hit_tp"
    return None


def format_outcome_message(watch: OilSetupWatch, *, price_now: float) -> str:
    mark = {
        "hit_tp": "✅",
        "hit_sl": "❌",
        "expired": "⚪",
        "manual_ok": "✅",
        "manual_fail": "❌",
    }.get(watch.outcome, "⚪")
    title = {
        "hit_tp": "Сбылось — цель взята",
        "hit_sl": "Не сбылось — стоп",
        "expired": "Время вышло — без чёткого исхода",
        "manual_ok": "Отмечено: сбылось",
        "manual_fail": "Отмечено: не сбылось",
    }.get(watch.outcome, "Итог")
    age_h = max(0.0, (time.time() - watch.created_ts) / 3600.0)
    lines = [
        f"{mark} <b>Журнал нефти · {title}</b>",
        f"<i>{watch.side} · сигнал ${watch.price_at_signal:.2f} · сейчас ${price_now:.2f} · {age_h:.1f}ч</i>",
        "",
        f"• Вход был ≈{watch.entry:.2f} · стоп {watch.stop:.2f} · TP {watch.tp1:.2f}"
        + (f"/{watch.tp2:.2f}" if watch.tp2 else ""),
    ]
    if watch.catalyst:
        lines.append(f"• Катализатор: <i>{_esc(watch.catalyst[:140])}</i>")
    if watch.outcome == "hit_tp":
        lines.append("• Вывод: новость/план отработали в нужную сторону.")
    elif watch.outcome == "hit_sl":
        lines.append(
            "• Вывод: рынок пошёл против — в следующий раз жди подтверждение "
            "или меньший размер."
        )
    elif watch.outcome == "expired":
        lines.append("• Вывод: импульса не было — лучше не насиловать вход.")
    lines.append("")
    lines.append("<i>Короткий разбор для памяти, не финсовет.</i>")
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class OilSetupJournal:
    """In-memory очередь активных setup'ов."""

    def __init__(self, *, max_active: int = 8) -> None:
        self._watches: list[OilSetupWatch] = []
        self._max = max_active

    def register(
        self,
        *,
        side: str,
        entry: float,
        stop: float,
        tp1: float,
        tp2: float | None,
        price: float,
        catalyst: str = "",
        quality: int = 0,
    ) -> OilSetupWatch | None:
        if side not in {"LONG", "SHORT"}:
            return None
        if entry <= 0 or stop <= 0 or tp1 <= 0:
            return None
        w = OilSetupWatch(
            side=side,
            entry=float(entry),
            stop=float(stop),
            tp1=float(tp1),
            tp2=float(tp2) if tp2 else None,
            price_at_signal=float(price),
            catalyst=(catalyst or "")[:160],
            created_ts=time.time(),
            quality=int(quality),
        )
        self._watches = [x for x in self._watches if not x.resolved][-self._max :]
        self._watches.append(w)
        return w

    def active(self) -> list[OilSetupWatch]:
        return [w for w in self._watches if not w.resolved]

    def check_price(
        self,
        price: float,
        *,
        min_age_sec: float = 900.0,
        max_age_sec: float = 36 * 3600.0,
    ) -> list[OilSetupWatch]:
        """Резолвит watches по цене; возвращает только что закрытые."""
        now = time.time()
        done: list[OilSetupWatch] = []
        for w in self._watches:
            if w.resolved:
                continue
            age = now - w.created_ts
            if age < min_age_sec:
                continue
            if age > max_age_sec:
                w.resolved = True
                w.outcome = "expired"
                done.append(w)
                continue
            hit = resolve_watch_against_price(w, price)
            if hit:
                w.resolved = True
                w.outcome = hit
                done.append(w)
        return done
