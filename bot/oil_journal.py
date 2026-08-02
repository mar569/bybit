"""Журнал исходов oil-setup: сбылось / нет / время вышло + лёгкое обучение."""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_OUTCOMES_FILE = Path(__file__).resolve().parent / "oil_outcomes.json"
HISTORY_MAX = 400
MIN_SAMPLES_ADAPT = 8

# Теги катализатора → для статистики (не фильтр новостей)
_TAG_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hormuz", ("hormuz", "ормуз", "strait", "пролив")),
    ("iran", ("iran", "иран", "tehran", "тегеран")),
    ("trump", ("trump", "трамп", "white house", "белый дом")),
    ("eia", ("eia", "inventory", "запасы", "spr", "cushing", "crude stocks")),
    ("opec", ("opec", "опек", "production cut", "добыч")),
    ("sanction", ("sanction", "санкц")),
)


@dataclass
class OilSetupWatch:
    side: str  # LONG | SHORT
    entry: float
    stop: float
    tp1: float
    tp2: float | None = None
    price_at_signal: float = 0.0
    catalyst: str = ""
    created_ts: float = 0.0
    quality: int = 0
    resolved: bool = False
    outcome: str = ""  # hit_tp | hit_sl | expired | manual_ok | manual_fail
    watch_id: str = ""
    source: str = "confluence"  # confluence | micro | bounce | manual
    tags: list[str] = field(default_factory=list)
    resolved_ts: float = 0.0
    resolve_price: float = 0.0

    def __post_init__(self) -> None:
        if not self.watch_id:
            self.watch_id = uuid.uuid4().hex[:12]
        if not self.tags:
            self.tags = extract_catalyst_tags(self.catalyst)


@dataclass(frozen=True)
class OilOutcomeStats:
    samples: int
    wins: int
    losses: int
    expired: int
    winrate_pct: float | None
    by_side: dict[str, dict[str, int]]
    by_tag: dict[str, dict[str, int]]
    recent_lesson_ru: str


def extract_catalyst_tags(text: str) -> list[str]:
    low = (text or "").lower()
    tags: list[str] = []
    for tag, keys in _TAG_KEYWORDS:
        if any(k in low for k in keys):
            tags.append(tag)
    return tags


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


def _is_win(outcome: str) -> bool:
    return outcome in {"hit_tp", "manual_ok"}


def _is_loss(outcome: str) -> bool:
    return outcome in {"hit_sl", "manual_fail"}


def format_outcome_message(
    watch: OilSetupWatch,
    *,
    price_now: float,
    stats: OilOutcomeStats | None = None,
) -> str:
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
        f"<i>{watch.side} · {watch.source} · сигнал ${watch.price_at_signal:.2f} · "
        f"сейчас ${price_now:.2f} · {age_h:.1f}ч</i>",
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
    if stats is not None and stats.samples > 0:
        wr = f"{stats.winrate_pct:.0f}%" if stats.winrate_pct is not None else "—"
        lines.append(
            f"• Статистика бота: <b>{stats.wins}✓</b> / <b>{stats.losses}✗</b> "
            f"/ {stats.expired}⏳ · WR <b>{wr}</b> (n={stats.samples})"
        )
        if stats.recent_lesson_ru:
            lines.append(f"• Урок: <i>{_esc(stats.recent_lesson_ru)}</i>")
    lines.append("")
    lines.append(
        "<i>Авто-журнал в фоне. Кнопки «Сбылось/Не сбылось» — если хочешь "
        "поправить вручную. Не финсовет.</i>"
    )
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _watch_from_dict(raw: dict[str, Any]) -> OilSetupWatch:
    return OilSetupWatch(
        side=str(raw.get("side") or ""),
        entry=float(raw.get("entry") or 0),
        stop=float(raw.get("stop") or 0),
        tp1=float(raw.get("tp1") or 0),
        tp2=float(raw["tp2"]) if raw.get("tp2") not in (None, "") else None,
        price_at_signal=float(raw.get("price_at_signal") or 0),
        catalyst=str(raw.get("catalyst") or ""),
        created_ts=float(raw.get("created_ts") or time.time()),
        quality=int(raw.get("quality") or 0),
        resolved=bool(raw.get("resolved")),
        outcome=str(raw.get("outcome") or ""),
        watch_id=str(raw.get("watch_id") or ""),
        source=str(raw.get("source") or "confluence"),
        tags=list(raw.get("tags") or []),
        resolved_ts=float(raw.get("resolved_ts") or 0),
        resolve_price=float(raw.get("resolve_price") or 0),
    )


def compute_outcome_stats(history: list[OilSetupWatch]) -> OilOutcomeStats:
    wins = losses = expired = 0
    by_side: dict[str, dict[str, int]] = {}
    by_tag: dict[str, dict[str, int]] = {}

    def _bump(bucket: dict[str, dict[str, int]], key: str, kind: str) -> None:
        row = bucket.setdefault(key, {"wins": 0, "losses": 0, "expired": 0})
        row[kind] = int(row.get(kind, 0)) + 1

    for w in history:
        if _is_win(w.outcome):
            wins += 1
            kind = "wins"
        elif _is_loss(w.outcome):
            losses += 1
            kind = "losses"
        elif w.outcome == "expired":
            expired += 1
            kind = "expired"
        else:
            continue
        _bump(by_side, w.side or "?", kind)
        for tag in w.tags or ["other"]:
            _bump(by_tag, tag, kind)

    decided = wins + losses
    samples = decided + expired
    winrate = round(wins / decided * 100.0, 1) if decided else None
    lesson = _build_lesson(by_tag, by_side, wins, losses, expired)
    return OilOutcomeStats(
        samples=samples,
        wins=wins,
        losses=losses,
        expired=expired,
        winrate_pct=winrate,
        by_side=by_side,
        by_tag=by_tag,
        recent_lesson_ru=lesson,
    )


def _build_lesson(
    by_tag: dict[str, dict[str, int]],
    by_side: dict[str, dict[str, int]],
    wins: int,
    losses: int,
    expired: int,
) -> str:
    decided = wins + losses
    if decided < 3 and expired < 3:
        return "Мало данных — коплю исходы в фоне."
    weak: list[tuple[float, str]] = []
    for tag, row in by_tag.items():
        w = int(row.get("wins", 0))
        l = int(row.get("losses", 0))
        n = w + l
        if n < 3:
            continue
        wr = w / n
        if wr < 0.4:
            weak.append((wr, tag))
    weak.sort()
    if weak:
        tag = weak[0][1]
        names = {
            "hormuz": "Ормуз",
            "iran": "Иран",
            "trump": "Трамп/Белый дом",
            "eia": "EIA/запасы",
            "opec": "ОПЕК",
            "sanction": "санкции",
            "other": "прочие",
        }
        return (
            f"Слабее обычного по теме «{names.get(tag, tag)}» — "
            f"требую больше подтверждений, новости не глушу."
        )
    if decided >= 5 and wins / decided < 0.4:
        return "Общий WR низкий — чуть строже к качеству входа."
    if expired >= max(3, decided) and decided:
        return "Много «время вышло» — не форсирую вход без импульса."
    # side skew
    for side, row in by_side.items():
        w = int(row.get("wins", 0))
        l = int(row.get("losses", 0))
        n = w + l
        if n >= 4 and w / n < 0.35:
            return f"{side} в последнее время чаще мимо — осторожнее с этой стороной."
    if decided >= 5 and wins / decided >= 0.55:
        return "Серия рабочих — продолжаю тот же стиль, без эйфории."
    return "Коплю статистику; новости Ормуз/Иран остаются приоритетом."


def adaptive_min_quality(base: int, stats: OilOutcomeStats) -> int:
    """Мягкая подстройка порога setup (±1). Не трогает новости/Ормуз."""
    b = max(5, min(9, int(base)))
    decided = stats.wins + stats.losses
    if decided < MIN_SAMPLES_ADAPT or stats.winrate_pct is None:
        return b
    wr = float(stats.winrate_pct)
    if wr < 35.0:
        return min(9, b + 1)
    if wr > 55.0:
        return max(5, b - 1)
    return b


def gemini_memory_block(stats: OilOutcomeStats) -> str:
    """Короткий блок опыта для промпта Gemini (без дообучения модели)."""
    if stats.samples <= 0:
        return ""
    wr = f"{stats.winrate_pct:.0f}%" if stats.winrate_pct is not None else "н/д"
    lines = [
        f"Опыт бота по своим сигналам: n={stats.samples}, "
        f"✓{stats.wins} ✗{stats.losses} ⏳{stats.expired}, WR={wr}.",
    ]
    if stats.recent_lesson_ru:
        lines.append(f"Урок: {stats.recent_lesson_ru}")
    # топ слабых тегов
    weak = []
    for tag, row in stats.by_tag.items():
        w = int(row.get("wins", 0))
        l = int(row.get("losses", 0))
        n = w + l
        if n >= 3 and w / n < 0.4:
            weak.append(f"{tag}({w}/{n})")
    if weak:
        lines.append("Слабые темы: " + ", ".join(weak[:4]))
    lines.append(
        "Учти опыт, но НЕ игнорируй свежие критичные новости "
        "(Ормуз/Иран/Трамп/запасы)."
    )
    return "\n".join(lines)


class OilSetupJournal:
    """Очередь активных setup + персистентная история исходов."""

    def __init__(
        self,
        *,
        max_active: int = 12,
        path: Path | None = None,
    ) -> None:
        self._watches: list[OilSetupWatch] = []
        self._history: list[OilSetupWatch] = []
        self._max = max_active
        self._path = path or DEFAULT_OUTCOMES_FILE
        self._load()

    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._watches = [
                _watch_from_dict(x)
                for x in (raw.get("active") or [])
                if isinstance(x, dict)
            ]
            self._history = [
                _watch_from_dict(x)
                for x in (raw.get("history") or [])
                if isinstance(x, dict)
            ][-HISTORY_MAX:]
            # только нерезолвленные в active
            self._watches = [w for w in self._watches if not w.resolved][-self._max :]
        except Exception:
            logger.exception("Oil journal load failed: %s", self._path)
            self._watches = []
            self._history = []

    def _save(self) -> None:
        try:
            payload = {
                "updated_ts": time.time(),
                "active": [asdict(w) for w in self._watches if not w.resolved],
                "history": [asdict(w) for w in self._history[-HISTORY_MAX:]],
            }
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception:
            logger.exception("Oil journal save failed: %s", self._path)

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
        source: str = "confluence",
    ) -> OilSetupWatch | None:
        side_u = (side or "").upper()
        if side_u in {"LONG", "SHORT"}:
            pass
        elif side_u in {"L", "BUY"}:
            side_u = "LONG"
        elif side_u in {"S", "SELL"}:
            side_u = "SHORT"
        else:
            # bounce/micro иногда отдают long/short
            side_l = (side or "").lower()
            if side_l == "long":
                side_u = "LONG"
            elif side_l == "short":
                side_u = "SHORT"
            else:
                return None
        if entry <= 0 or stop <= 0 or tp1 <= 0:
            return None
        w = OilSetupWatch(
            side=side_u,
            entry=float(entry),
            stop=float(stop),
            tp1=float(tp1),
            tp2=float(tp2) if tp2 else None,
            price_at_signal=float(price),
            catalyst=(catalyst or "")[:160],
            created_ts=time.time(),
            quality=int(quality),
            source=(source or "confluence")[:24],
        )
        self._watches = [x for x in self._watches if not x.resolved][-self._max :]
        self._watches.append(w)
        self._save()
        return w

    def active(self) -> list[OilSetupWatch]:
        return [w for w in self._watches if not w.resolved]

    def stats(self) -> OilOutcomeStats:
        return compute_outcome_stats(self._history)

    def mark_manual(
        self,
        watch: OilSetupWatch,
        *,
        ok: bool,
        price_now: float | None = None,
    ) -> OilSetupWatch:
        watch.resolved = True
        watch.outcome = "manual_ok" if ok else "manual_fail"
        watch.resolved_ts = time.time()
        watch.resolve_price = float(price_now or watch.price_at_signal)
        self._append_history(watch)
        self._save()
        return watch

    def _append_history(self, watch: OilSetupWatch) -> None:
        # не дублировать один watch_id
        self._history = [h for h in self._history if h.watch_id != watch.watch_id]
        self._history.append(watch)
        self._history = self._history[-HISTORY_MAX:]

    def check_price(
        self,
        price: float,
        *,
        min_age_sec: float = 900.0,
        max_age_sec: float = 36 * 3600.0,
        allow_price_resolve: bool = True,
    ) -> list[OilSetupWatch]:
        """Резолвит watches; возвращает только что закрытые."""
        now = time.time()
        done: list[OilSetupWatch] = []
        for w in self._watches:
            if w.resolved:
                continue
            age = now - w.created_ts
            # micro — короче окно
            max_age = max_age_sec
            min_age = min_age_sec
            if w.source == "micro":
                max_age = min(max_age_sec, 6 * 3600.0)
                min_age = min(min_age_sec, 300.0)
            if age < min_age:
                continue
            if age > max_age:
                w.resolved = True
                w.outcome = "expired"
                w.resolved_ts = now
                w.resolve_price = float(price) if price > 0 else w.price_at_signal
                self._append_history(w)
                done.append(w)
                continue
            if not allow_price_resolve:
                continue
            hit = resolve_watch_against_price(w, price)
            if hit:
                w.resolved = True
                w.outcome = hit
                w.resolved_ts = now
                w.resolve_price = float(price)
                self._append_history(w)
                done.append(w)
        if done:
            self._save()
        return done
