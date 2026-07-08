"""
Получение свечей (candles) по инструменту через MOEX ISS API.

Эндпоинт бесплатный: .../securities/{ticker}/candles.json
Интервалы: 1 (минута), 10, 60 (час), 24 (день), 7/31/4 (неделя/месяц/квартал).

Документация: https://iss.moex.com/iss/reference/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import aiohttp

logger = logging.getLogger("moex_bot.candles")

ISS_BASE_URL = "https://iss.moex.com/iss"


@dataclass
class Candle:
    open: float
    close: float
    high: float
    low: float
    volume: int
    begin: datetime  # время открытия свечи

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_shadow(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        return min(self.open, self.close) - self.low


class MoexCandlesClient:
    def __init__(self, engine: str, market: str, board: str):
        self.engine = engine
        self.market = market
        self.board = board
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "MoexCandlesClient":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    async def get_recent_candles(self, ticker: str, interval: int, limit: int) -> list[Candle]:
        """
        Возвращает последние `limit` свечей заданного интервала (в минутах:
        1, 10, 60, 24, 7, 31, 4) для тикера, отсортированные по возрастанию
        времени (старые -> новые).

        ВАЖНО: без явного указания from/till ISS может вернуть НЕ последние
        свечи, а первую страницу доступного окна истории (которая может
        быть устаревшей или вообще из другого торгового дня). Поэтому
        явно ограничиваем запрос сегодняшним днём.
        """
        assert self._session is not None, "используйте 'async with MoexCandlesClient(...)'"

        today = datetime.now().strftime("%Y-%m-%d")

        url = (
            f"{ISS_BASE_URL}/engines/{self.engine}/markets/{self.market}/"
            f"boards/{self.board}/securities/{ticker}/candles.json"
        )
        params = {
            "iss.meta": "off",
            "interval": interval,
            "from": today,
            "till": today,
            "limit": 2000,
        }

        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200 or resp.content_type != "application/json":
                    logger.warning("Неожиданный ответ candles для %s: status=%s", ticker, resp.status)
                    return []
                data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка запроса свечей %s: %s", ticker, exc)
            return []

        return self._parse_candles(data, limit)

    @staticmethod
    def _parse_candles(data: dict, limit: int) -> list[Candle]:
        block = data.get("candles")
        if not block or not block.get("data"):
            return []

        columns = block["columns"]
        idx = {name: columns.index(name) for name in ("open", "close", "high", "low", "volume", "begin")}

        candles: list[Candle] = []
        for row in block["data"]:
            try:
                candles.append(
                    Candle(
                        open=float(row[idx["open"]]),
                        close=float(row[idx["close"]]),
                        high=float(row[idx["high"]]),
                        low=float(row[idx["low"]]),
                        volume=int(row[idx["volume"]]),
                        begin=datetime.strptime(row[idx["begin"]], "%Y-%m-%d %H:%M:%S"),
                    )
                )
            except (TypeError, ValueError):
                continue

        return candles[-limit:]