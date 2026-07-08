"""
Расчёт торговых уровней (вход/стоп-лосс/тейк-профит) для сигналов.

ВАЖНО: это механический расчёт по фиксированным правилам (ATR-волатильность
или фиксированный процент + заданное соотношение риск/прибыль), а не
финансовая рекомендация. Уровни — отправная точка для дальнейшей проверки,
а не готовое решение "куда ставить ордера".
"""

from __future__ import annotations

from dataclasses import dataclass

from candles_api import Candle
from config import ATR_PERIOD, ATR_STOP_MULTIPLIER, LARGE_ORDER_STOP_LOSS_PCT, RISK_REWARD_RATIO


@dataclass
class TradeLevels:
    entry: float
    stop_loss: float
    take_profit: float
    risk_pct: float  # расстояние до стопа в % от входа


def compute_atr(candles: list[Candle], period: int = ATR_PERIOD) -> float | None:
    """
    Average True Range — классическая мера волатильности бумаги.
    True Range для каждого бара = max(high-low, |high-prev_close|, |low-prev_close|).
    ATR = скользящее среднее True Range за period баров.
    """
    if len(candles) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1].close
        c = candles[i]
        tr = max(
            c.high - c.low,
            abs(c.high - prev_close),
            abs(c.low - prev_close),
        )
        true_ranges.append(tr)

    recent = true_ranges[-period:]
    return sum(recent) / len(recent)


def levels_from_atr(direction: str, entry: float, atr: float) -> TradeLevels:
    """
    Рассчитывает уровни на основе ATR. direction: "bullish"/"BUY" -> лонг,
    "bearish"/"SELL" -> шорт.
    """
    is_long = direction in ("bullish", "BUY")
    stop_distance = atr * ATR_STOP_MULTIPLIER
    target_distance = stop_distance * RISK_REWARD_RATIO

    if is_long:
        stop_loss = entry - stop_distance
        take_profit = entry + target_distance
    else:
        stop_loss = entry + stop_distance
        take_profit = entry - target_distance

    risk_pct = stop_distance / entry * 100 if entry else 0.0
    return TradeLevels(entry=entry, stop_loss=stop_loss, take_profit=take_profit, risk_pct=risk_pct)


def levels_from_fixed_pct(direction: str, entry: float,
                           stop_pct: float = LARGE_ORDER_STOP_LOSS_PCT) -> TradeLevels:
    """
    Рассчитывает уровни на основе фиксированного процента от цены входа —
    используется там, где нет истории свечей для ATR (сигналы о крупных
    заявках, которые строятся только на top-of-book).
    """
    is_long = direction in ("bullish", "BUY")
    stop_distance = entry * stop_pct / 100
    target_distance = stop_distance * RISK_REWARD_RATIO

    if is_long:
        stop_loss = entry - stop_distance
        take_profit = entry + target_distance
    else:
        stop_loss = entry + stop_distance
        take_profit = entry - target_distance

    return TradeLevels(entry=entry, stop_loss=stop_loss, take_profit=take_profit, risk_pct=stop_pct)