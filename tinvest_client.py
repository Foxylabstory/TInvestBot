"""
Клиент для T-Invest API (Т-Банк) через официальный REST/JSON-шлюз.

Сознательно НЕ используется сторонняя Python-библиотека (tinkoff-investments) —
она на момент написания периодически попадает в карантин на PyPI вместе со
своей зависимостью, что делает установку ненадёжной. Вместо этого — обычные
POST-запросы через aiohttp (тот же пакет, что уже используется в проекте
для Telegram и MOEX ISS).

Документация:
- Протокол: https://developer.tbank.ru/invest/intro/developer/protocols/restapi
- Список методов: https://developer.tbank.ru/invest/api

Токен создаётся в приложении/личном кабинете Т-Инвестиций:
Настройки -> Инвестиции -> Токен для API. Для этого бота достаточно
токена только на чтение котировок (без торговых прав).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import aiohttp

from candles_api import Candle
from moex_api import TopOfBook

logger = logging.getLogger("moex_bot.tinvest")

BASE_URL = "https://invest-public-api.tbank.ru/rest/tinkoff.public.invest.api.contract.v1"

# Класс-код основного режима торгов акциями на MOEX. Позволяет обращаться
# к инструменту просто как "TICKER_TQBR", без отдельного резолвинга FIGI.
MOEX_SHARES_CLASS_CODE = "TQBR"

_INTERVAL_NAME = {
    1: "CANDLE_INTERVAL_1_MIN",
    5: "CANDLE_INTERVAL_5_MIN",
    15: "CANDLE_INTERVAL_15_MIN",
    60: "CANDLE_INTERVAL_HOUR",
}


def _quotation_to_float(q: dict | None) -> float | None:
    """Конвертирует формат цены T-Invest {units, nano} в float."""
    if not q:
        return None
    try:
        units = int(q.get("units", 0))
        nano = int(q.get("nano", 0))
    except (TypeError, ValueError):
        return None
    return units + nano / 1e9


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class TInvestClient:
    def __init__(self, token: str):
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "TInvestClient":
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
        )
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()

    async def _post(self, service: str, method: str, body: dict) -> dict | None:
        assert self._session is not None, "используйте 'async with TInvestClient(...)'"
        url = f"{BASE_URL}.{service}/{method}"
        try:
            async with self._session.post(
                url, json=body, timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(
                        "T-Invest %s.%s вернул статус %s: %s",
                        service, method, resp.status, text[:300],
                    )
                    return None
                return await resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка запроса T-Invest %s.%s: %s", service, method, exc)
            return None

    @staticmethod
    def _instrument_id(ticker: str) -> str:
        return f"{ticker}_{MOEX_SHARES_CLASS_CODE}"

    async def get_top_of_book(self, ticker: str) -> TopOfBook | None:
        body = {"depth": 1, "instrumentId": self._instrument_id(ticker)}
        data = await self._post("MarketDataService", "GetOrderBook", body)
        if data is None:
            return None

        bids = data.get("bids") or []
        asks = data.get("asks") or []

        bid_price = _quotation_to_float(bids[0]["price"]) if bids else None
        bid_volume = int(bids[0]["quantity"]) if bids else None
        ask_price = _quotation_to_float(asks[0]["price"]) if asks else None
        ask_volume = int(asks[0]["quantity"]) if asks else None

        return TopOfBook(
            ticker=ticker,
            bid_price=bid_price,
            bid_volume=bid_volume,
            ask_price=ask_price,
            ask_volume=ask_volume,
            bid_total_volume=None,
            ask_total_volume=None,
            volume_today=None,
            timestamp=time.time(),
        )

    async def get_recent_candles(self, ticker: str, interval: int, limit: int) -> list[Candle]:
        interval_name = _INTERVAL_NAME.get(interval, "CANDLE_INTERVAL_1_MIN")
        # запрашиваем с запасом по времени, чтобы гарантированно набрать
        # `limit` баров (в нерабочие часы/выходные баров может не быть)
        lookback_minutes = interval * limit * 3 + 120
        now = datetime.now(timezone.utc)
        from_ = now - timedelta(minutes=lookback_minutes)

        body = {
            "from": _iso(from_),
            "to": _iso(now),
            "interval": interval_name,
            "instrumentId": self._instrument_id(ticker),
        }
        data = await self._post("MarketDataService", "GetCandles", body)
        if data is None:
            return []

        rows = data.get("candles") or []
        candles: list[Candle] = []
        for row in rows:
            try:
                candles.append(
                    Candle(
                        open=_quotation_to_float(row["open"]),
                        close=_quotation_to_float(row["close"]),
                        high=_quotation_to_float(row["high"]),
                        low=_quotation_to_float(row["low"]),
                        volume=int(row["volume"]),
                        begin=datetime.strptime(row["time"][:19], "%Y-%m-%dT%H:%M:%S"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue

        return candles[-limit:]