"""
Детектор "крупных заявок" на основе top-of-book данных (лучшая цена
покупки/продажи + объём по ней), которые бесплатно отдаёт MOEX ISS.

Логика:
1. Для каждого тикера храним скользящее окно "обычных" объёмов на
   лучшей цене bid/ask.
2. Когда видим объём на лучшей цене, который в N раз больше среднего —
   это кандидат на "крупную заявку".
3. Кандидат подтверждается, только если продержался в стакане дольше
   CONFIRM_STANDING_SEC (фильтр от заявок, которые ставят и почти сразу
   отменяют — так называемый спуфинг).
4. На один и тот же уровень (тикер+сторона+цена) уведомление шлём не
   чаще, чем раз в COOLDOWN_SEC.

ВАЖНО: т.к. мы видим только объём на ЛУЧШЕЙ цене (а не весь стакан),
детектор реагирует именно на всплеск объёма на текущей лучшей цене —
это чуть более узкий, но практически значимый сигнал: "именно сейчас,
по текущей рыночной цене, кто-то поставил гораздо больше обычного".
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from config import (
    CONFIRM_STANDING_SEC,
    COOLDOWN_SEC,
    MIN_ABSOLUTE_VOLUME,
    MIN_NOTIONAL_RUB,
    ROLLING_WINDOW,
    VOLUME_MULTIPLIER_THRESHOLD,
)
from moex_api import TopOfBook
from risk_calculator import TradeLevels, levels_from_fixed_pct


@dataclass
class PendingCandidate:
    """Заявка-кандидат, ожидающая подтверждения (простояла ли она в стакане)."""
    side: str  # "BUY" или "SELL"
    price: float
    volume: int
    first_seen: float


@dataclass
class SideState:
    volume_history: deque = field(default_factory=lambda: deque(maxlen=ROLLING_WINDOW))
    pending: PendingCandidate | None = None
    last_alert_at: float = 0.0
    last_alert_price: float | None = None

    def average_volume(self) -> float:
        if not self.volume_history:
            return 0.0
        return sum(self.volume_history) / len(self.volume_history)


@dataclass
class TickerState:
    bid: SideState = field(default_factory=SideState)
    ask: SideState = field(default_factory=SideState)


@dataclass
class Signal:
    ticker: str
    side: str
    price: float
    volume: int
    avg_volume: float
    notional_rub: float
    levels: TradeLevels | None = None


class LargeOrderDetector:
    def __init__(self):
        self._state: dict[str, TickerState] = {}

    def _get_state(self, ticker: str) -> TickerState:
        if ticker not in self._state:
            self._state[ticker] = TickerState()
        return self._state[ticker]

    def process(self, tob: TopOfBook) -> list[Signal]:
        """Обрабатывает свежий top-of-book снимок и возвращает подтверждённые сигналы."""
        state = self._get_state(tob.ticker)
        signals: list[Signal] = []

        if tob.bid_price is not None and tob.bid_volume is not None:
            sig = self._process_side(
                state.bid, "BUY", tob.ticker, tob.bid_price, tob.bid_volume, tob.timestamp,
            )
            if sig:
                signals.append(sig)

        if tob.ask_price is not None and tob.ask_volume is not None:
            sig = self._process_side(
                state.ask, "SELL", tob.ticker, tob.ask_price, tob.ask_volume, tob.timestamp,
            )
            if sig:
                signals.append(sig)

        return signals

    @staticmethod
    def _process_side(
        side_state: SideState, side: str, ticker: str,
        price: float, volume: int, now: float,
    ) -> Signal | None:
        avg_vol = side_state.average_volume()
        side_state.volume_history.append(volume)

        notional = price * volume
        is_large = (
            avg_vol > 0
            and volume >= avg_vol * VOLUME_MULTIPLIER_THRESHOLD
            and volume >= MIN_ABSOLUTE_VOLUME
            and notional >= MIN_NOTIONAL_RUB
        )

        if not is_large:
            side_state.pending = None
            return None

        pending = side_state.pending
        # Кандидат считается "тем же самым", если цена не изменилась
        # (иначе это уже новая заявка — отсчёт начинаем заново)
        if pending is None or pending.price != price:
            side_state.pending = PendingCandidate(
                side=side, price=price, volume=volume, first_seen=now,
            )
            return None

        standing_time = now - pending.first_seen
        if standing_time < CONFIRM_STANDING_SEC:
            return None

        if (
            side_state.last_alert_price == price
            and now - side_state.last_alert_at < COOLDOWN_SEC
        ):
            return None

        side_state.last_alert_at = now
        side_state.last_alert_price = price

        levels = levels_from_fixed_pct(side, price)

        return Signal(
            ticker=ticker, side=side, price=price, volume=volume,
            avg_volume=avg_vol, notional_rub=notional, levels=levels,
        )