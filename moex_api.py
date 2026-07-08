"""
Клиент для MOEX ISS API.

ВАЖНО (изменено по факту, 2026-07): полный стакан заявок (orderbook,
несколько уровней глубины) на MOEX ISS доступен только по платной
подписке — бесплатный запрос .../orderbook.json возвращает не JSON,
а HTML-страницу с сообщением об отсутствии подписки.

Поэтому используем бесплатный эндпоинт .../securities/{ticker}.json,
блок "marketdata", который даёт:
  - BID / OFFER       — лучшая цена покупки / продажи
  - BIDDEPTH          — объём (в лотах) заявок по лучшей цене покупки
  - OFFERDEPTH        — объём (в лотах) заявок по лучшей цене продажи
  - BIDDEPTHT/OFFERDEPTHT — суммарный объём по всей видимой очереди

Это не полная глубина стакана, но вполне достаточно, чтобы поймать
ситуацию "на лучшей цене внезапно выставили аномально крупный объём" —
именно то, что нужно для детектора крупных заявок.

Документация: https://iss.moex.com/iss/reference/
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger("moex_bot.api")

ISS_BASE_URL = "https://iss.moex.com/iss"


@dataclass
class TopOfBook:
    """Лучшие цены и объёмы по инструменту (доступно бесплатно)."""
    ticker: str
    bid_price: float | None
    bid_volume: int | None       # объём по лучшей цене покупки, лоты
    ask_price: float | None
    ask_volume: int | None       # объём по лучшей цене продажи, лоты
    bid_total_volume: int | None  # суммарный видимый объём на покупку
    ask_total_volume: int | None  # суммарный видимый объём на продажу
    timestamp: float


class MoexIssClient:
    def __init__(self, engine: str, market: str, board: str):
        self.engine = engine
        self.market = market
        self.board = board
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "MoexIssClient":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    async def get_top_of_book(self, ticker: str) -> TopOfBook | None:
        """
        Запрашивает лучшие цены/объёмы по инструменту через бесплатный
        эндпоинт securities/{ticker}.json (блок marketdata).
        """
        assert self._session is not None, "используйте 'async with MoexIssClient(...)'"

        url = (
            f"{ISS_BASE_URL}/engines/{self.engine}/markets/{self.market}/"
            f"boards/{self.board}/securities/{ticker}.json"
        )
        params = {"iss.meta": "off", "iss.only": "marketdata"}

        try:
            async with self._session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                content_type = resp.content_type
                if resp.status != 200 or content_type != "application/json":
                    body_preview = (await resp.text())[:200]
                    logger.warning(
                        "Неожиданный ответ ISS для %s: status=%s, content_type=%s, preview=%r",
                        ticker, resp.status, content_type, body_preview,
                    )
                    return None
                data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка запроса marketdata %s: %s", ticker, exc)
            return None

        return self._parse_marketdata(ticker, data)

    @staticmethod
    def _parse_marketdata(ticker: str, data: dict) -> TopOfBook | None:
        block = data.get("marketdata")
        if not block or not block.get("data"):
            logger.debug("Пустой marketdata для %s (возможно, торги ещё не открылись)", ticker)
            return None

        columns = block["columns"]
        row = block["data"][0]  # для одного тикера всегда одна строка

        def col(name: str):
            try:
                idx = columns.index(name)
            except ValueError:
                return None
            return row[idx]

        return TopOfBook(
            ticker=ticker,
            bid_price=col("BID"),
            bid_volume=col("BIDDEPTH"),
            ask_price=col("OFFER"),
            ask_volume=col("OFFERDEPTH"),
            bid_total_volume=col("BIDDEPTHT"),
            ask_total_volume=col("OFFERDEPTHT"),
            timestamp=time.time(),
        )