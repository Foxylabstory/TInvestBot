"""
Детектор графических паттернов на основе истории минутных свечей.

Честная оговорка про качество разных категорий паттернов:

- Свечные паттерны (engulfing, hammer, doji) — считаются по чётким
  математическим правилам (соотношения тела/теней свечи). Надёжность
  зависит от того, насколько сам паттерн предиктивен на конкретном
  инструменте — это отдельный вопрос, но детекция как таковая точная.

- Пробой уровня поддержки/сопротивления — тоже довольно надёжно
  детектируется (локальные пивоты + проверка, что цена их пересекла).

- Классические фигуры (двойная вершина/дно, голова-плечи, треугольники)
  — принципиально эвристические алгоритмы. Даже двойная вершина/дно
  здесь определяется по совпадению двух пивотов в пределах допуска,
  без "официального" строгого определения (его и не существует).
  Голова-плечи и треугольники — наиболее спорные, дают больше всего
  ложных срабатываний. Используйте как один из фильтров, а не как
  единственный триггер для входа в сделку.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from datetime import datetime

from candles_api import Candle
from config import (
    DOUBLE_TOP_BOTTOM_TOLERANCE_PCT,
    PATTERN_HISTORY_LENGTH,
    PIVOT_WINDOW,
    SR_BREAKOUT_MIN_PCT,
    VOLUME_SPIKE_MULTIPLIER,
    VOLUME_SPIKE_MIN_ABS,
)
from risk_calculator import TradeLevels, compute_atr, levels_from_atr

logger = logging.getLogger("moex_bot.pattern_detector")


@dataclass
class PatternSignal:
    ticker: str
    pattern: str          # человекочитаемое имя паттерна
    category: str         # "candlestick" | "support_resistance" | "classic" | "volume"
    direction: str         # "bullish" | "bearish" | "neutral"
    price: float
    timestamp: datetime
    detail: str = ""       # доп. пояснение (например, уровень пробоя)
    levels: TradeLevels | None = None  # цена входа/стоп/тейк (None для neutral)


# ---------------------------------------------------------------------------
# Свечные паттерны — работают на последней свече (и предыдущей, где нужно)
# ---------------------------------------------------------------------------

def _detect_candlestick_patterns(candles: list[Candle]) -> list[tuple[str, str]]:
    """Возвращает список (имя_паттерна, direction) для последней свечи."""
    if len(candles) < 2:
        return []

    prev, last = candles[-2], candles[-1]
    found: list[tuple[str, str]] = []

    # --- Doji: тело очень маленькое относительно диапазона свечи
    if last.range > 0 and last.body / last.range < 0.1:
        found.append(("Доджи", "neutral"))

    # --- Молот (Hammer): маленькое тело вверху диапазона, длинная нижняя тень,
    #     короткая верхняя тень. Классически ищется после нисходящего движения.
    if last.range > 0:
        is_hammer_shape = (
            last.lower_shadow >= 2 * last.body
            and last.upper_shadow <= 0.3 * last.body
            and last.body > 0
        )
        if is_hammer_shape:
            trend_down = candles[-3].close > candles[-2].close if len(candles) >= 3 else True
            if trend_down:
                found.append(("Молот (Hammer)", "bullish"))

        # --- Перевёрнутый молот / падающая звезда shape: длинная верхняя тень
        is_shooting_shape = (
            last.upper_shadow >= 2 * last.body
            and last.lower_shadow <= 0.3 * last.body
            and last.body > 0
        )
        if is_shooting_shape:
            trend_up = candles[-3].close < candles[-2].close if len(candles) >= 3 else True
            label = "Падающая звезда" if trend_up else "Перевёрнутый молот"
            direction = "bearish" if trend_up else "bullish"
            found.append((label, direction))

    # --- Поглощение (Engulfing): тело последней свечи полностью
    #     перекрывает тело предыдущей, и направления противоположны
    prev_bullish, prev_bearish = prev.is_bullish, prev.is_bearish
    last_bullish, last_bearish = last.is_bullish, last.is_bearish

    if prev_bearish and last_bullish and last.open <= prev.close and last.close >= prev.open:
        found.append(("Бычье поглощение", "bullish"))
    elif prev_bullish and last_bearish and last.open >= prev.close and last.close <= prev.open:
        found.append(("Медвежье поглощение", "bearish"))

    return found


# ---------------------------------------------------------------------------
# Уровни поддержки/сопротивления и их пробой
# ---------------------------------------------------------------------------

def _find_pivots(candles: list[Candle], window: int) -> tuple[list[float], list[float]]:
    """
    Находит локальные пивот-максимумы и пивот-минимумы: точка считается
    пивотом, если она выше/ниже всех `window` соседних свечей с обеих сторон.
    """
    highs, lows = [], []
    n = len(candles)
    for i in range(window, n - window):
        window_slice = candles[i - window: i + window + 1]
        center = candles[i]
        if center.high == max(c.high for c in window_slice):
            highs.append(center.high)
        if center.low == min(c.low for c in window_slice):
            lows.append(center.low)
    return highs, lows


def _detect_sr_breakout(candles: list[Candle]) -> list[tuple[str, str, str]]:
    """Возвращает список (имя_паттерна, direction, detail) при пробое уровня."""
    if len(candles) < PIVOT_WINDOW * 2 + 3:
        return []

    # пивоты ищем по истории БЕЗ последней свечи — она кандидат на пробой
    history, last = candles[:-1], candles[-1]
    highs, lows = _find_pivots(history, PIVOT_WINDOW)

    found: list[tuple[str, str, str]] = []

    if highs:
        resistance = max(highs)
        breakout_pct = (last.close - resistance) / resistance * 100
        if last.close > resistance and breakout_pct >= SR_BREAKOUT_MIN_PCT:
            found.append((
                "Пробой сопротивления", "bullish",
                f"уровень {resistance:.2f}, закрытие {last.close:.2f} (+{breakout_pct:.2f}%)",
            ))

    if lows:
        support = min(lows)
        breakout_pct = (support - last.close) / support * 100
        if last.close < support and breakout_pct >= SR_BREAKOUT_MIN_PCT:
            found.append((
                "Пробой поддержки", "bearish",
                f"уровень {support:.2f}, закрытие {last.close:.2f} (-{breakout_pct:.2f}%)",
            ))

    return found


# ---------------------------------------------------------------------------
# Классические фигуры (эвристика, см. предупреждение в шапке файла)
# ---------------------------------------------------------------------------

def _detect_double_top_bottom(candles: list[Candle]) -> list[tuple[str, str, str]]:
    if len(candles) < PIVOT_WINDOW * 2 + 5:
        return []

    highs, lows = _find_pivots(candles[:-1], PIVOT_WINDOW)
    found: list[tuple[str, str, str]] = []

    # Двойная вершина: два последних пивот-максимума близки друг к другу
    # по цене (в пределах допуска), и текущая цена ниже этой зоны
    if len(highs) >= 2:
        h1, h2 = highs[-2], highs[-1]
        avg = (h1 + h2) / 2
        diff_pct = abs(h1 - h2) / avg * 100
        if diff_pct <= DOUBLE_TOP_BOTTOM_TOLERANCE_PCT and candles[-1].close < avg:
            found.append((
                "Двойная вершина", "bearish",
                f"пики {h1:.2f} и {h2:.2f} (расхождение {diff_pct:.2f}%)",
            ))

    if len(lows) >= 2:
        l1, l2 = lows[-2], lows[-1]
        avg = (l1 + l2) / 2
        diff_pct = abs(l1 - l2) / avg * 100
        if diff_pct <= DOUBLE_TOP_BOTTOM_TOLERANCE_PCT and candles[-1].close > avg:
            found.append((
                "Двойное дно", "bullish",
                f"впадины {l1:.2f} и {l2:.2f} (расхождение {diff_pct:.2f}%)",
            ))

    return found


def _detect_triangle(candles: list[Candle]) -> list[tuple[str, str, str]]:
    """
    Очень грубая эвристика: смотрим на последние несколько пивотов highs/lows
    и проверяем, сужается ли диапазон (highs убывают, lows растут) —
    признак треугольника/сжатия волатильности перед возможным пробоем.
    Это самый ненадёжный из детекторов в этом модуле.
    """
    if len(candles) < PIVOT_WINDOW * 2 + 8:
        return []

    highs, lows = _find_pivots(candles[:-1], PIVOT_WINDOW)
    if len(highs) < 2 or len(lows) < 2:
        return []

    highs_shrinking = highs[-1] < highs[-2]
    lows_rising = lows[-1] > lows[-2]

    if highs_shrinking and lows_rising:
        return [(
            "Сужающийся треугольник (возможна консолидация)", "neutral",
            f"максимумы {highs[-2]:.2f}->{highs[-1]:.2f}, минимумы {lows[-2]:.2f}->{lows[-1]:.2f}",
        )]
    return []


# ---------------------------------------------------------------------------
# Объёмный импульс
# ---------------------------------------------------------------------------

def _detect_volume_spike(candles: list[Candle]) -> list[tuple[str, str, str]]:
    if len(candles) < 10:
        return []

    history, last = candles[:-1], candles[-1]
    avg_volume = sum(c.volume for c in history) / len(history)

    if avg_volume <= 0:
        return []

    if last.volume >= avg_volume * VOLUME_SPIKE_MULTIPLIER and last.volume >= VOLUME_SPIKE_MIN_ABS:
        direction = "bullish" if last.is_bullish else "bearish" if last.is_bearish else "neutral"
        ratio = last.volume / avg_volume
        return [(
            "Аномальный объём свечи", direction,
            f"объём {last.volume} лотов, в {ratio:.1f}x больше среднего ({avg_volume:.0f})",
        )]
    return []


# ---------------------------------------------------------------------------
# Публичный интерфейс
# ---------------------------------------------------------------------------

class PatternDetector:
    """
    Держит по каждому тикеру историю свечей и на каждом обновлении
    прогоняет все включённые категории паттернов, возвращая новые сигналы.
    Дедупликация: один и тот же паттерн на одной и той же свече не
    отправляется повторно (сравниваем по времени последней свечи).
    """

    def __init__(self):
        self._last_processed_candle_time: dict[str, datetime] = {}

    def process(self, ticker: str, candles: list[Candle]) -> list[PatternSignal]:
        if len(candles) < 5:
            return []

        last_candle = candles[-1]

        # Не обрабатываем повторно одну и ту же (ещё не закрытую) свечу
        if self._last_processed_candle_time.get(ticker) == last_candle.begin:
            return []
        self._last_processed_candle_time[ticker] = last_candle.begin

        candles = candles[-PATTERN_HISTORY_LENGTH:]
        signals: list[PatternSignal] = []

        # ATR считаем один раз на снимок — используется для всех
        # направленных (не neutral) сигналов по этому тикеру в этом тике
        atr = compute_atr(candles)

        def make_levels(direction: str) -> TradeLevels | None:
            if direction == "neutral" or atr is None:
                return None
            return levels_from_atr(direction, last_candle.close, atr)

        for name, direction in _detect_candlestick_patterns(candles):
            signals.append(PatternSignal(
                ticker=ticker, pattern=name, category="candlestick",
                direction=direction, price=last_candle.close, timestamp=last_candle.begin,
                levels=make_levels(direction),
            ))

        for name, direction, detail in _detect_sr_breakout(candles):
            signals.append(PatternSignal(
                ticker=ticker, pattern=name, category="support_resistance",
                direction=direction, price=last_candle.close, timestamp=last_candle.begin, detail=detail,
                levels=make_levels(direction),
            ))

        double_patterns = _detect_double_top_bottom(candles)
        directions_found = {direction for _, direction, _ in double_patterns}
        if "bullish" in directions_found and "bearish" in directions_found:
            # Одновременно найдены и двойная вершина, и двойное дно —
            # это признак узкого бокового диапазона (шум), а не реальной
            # структуры разворота. Оба сигнала противоречат друг другу,
            # поэтому гасим их вместо отправки конфликтующих уведомлений.
            logger.debug(
                "%s: одновременно двойная вершина и двойное дно — "
                "похоже на боковик, сигналы подавлены",
                ticker,
            )
        else:
            for name, direction, detail in double_patterns:
                signals.append(PatternSignal(
                    ticker=ticker, pattern=name, category="classic",
                    direction=direction, price=last_candle.close, timestamp=last_candle.begin, detail=detail,
                    levels=make_levels(direction),
                ))

        for name, direction, detail in _detect_triangle(candles):
            signals.append(PatternSignal(
                ticker=ticker, pattern=name, category="classic",
                direction=direction, price=last_candle.close, timestamp=last_candle.begin, detail=detail,
                levels=make_levels(direction),
            ))

        for name, direction, detail in _detect_volume_spike(candles):
            signals.append(PatternSignal(
                ticker=ticker, pattern=name, category="volume",
                direction=direction, price=last_candle.close, timestamp=last_candle.begin, detail=detail,
                levels=make_levels(direction),
            ))

        return signals