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

import asyncio
import logging
import os
import ssl
import time
from datetime import datetime, timedelta, timezone

import aiohttp
import certifi

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

# T-Bank переходит на TLS-сертификаты НУЦ Минцифры (Russian Trusted Root CA) —
# они не входят в стандартный список доверенных корневых сертификатов,
# который использует Python/aiohttp. Кладём файлы сюда (см. README):
#   certs/russian_trusted_root_ca.cer
#   certs/russian_trusted_sub_ca.cer
# Скачать: https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer
#          https://gu-st.ru/content/Other/doc/russian_trusted_sub_ca.cer
_CERTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")
_RUSSIAN_ROOT_CA = os.path.join(_CERTS_DIR, "russian_trusted_root_ca.cer")
_RUSSIAN_SUB_CA = os.path.join(_CERTS_DIR, "russian_trusted_sub_ca.cer")


def _build_ssl_context() -> ssl.SSLContext:
    """
    Строит SSL-контекст, который доверяет и обычным (Mozilla/certifi)
    корневым сертификатам, и российским (НУЦ Минцифры) — без отключения
    проверки сертификатов вообще.
    """
    context = ssl.create_default_context(cafile=certifi.where())

    found_any = False
    for path in (_RUSSIAN_ROOT_CA, _RUSSIAN_SUB_CA):
        if os.path.exists(path):
            context.load_verify_locations(cafile=path)
            found_any = True
        else:
            logger.warning(
                "Не найден файл сертификата %s — если T-Invest вернёт "
                "SSLCertVerificationError, скачайте сертификаты НУЦ Минцифры "
                "(см. README) и положите в папку certs/.",
                path,
            )

    if found_any:
        logger.info("Российские корневые сертификаты (НУЦ Минцифры) подключены")

    return context


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
    # Максимум одновременных запросов к T-Invest API. При большом числе
    # тикеров, опрашиваемых параллельно через asyncio.gather, без этого
    # ограничения сервер может обрывать "лишние" соединения
    # (Connection closed / Server disconnected).
    MAX_CONCURRENT_REQUESTS = 5

    # Сколько раз повторить запрос при обрыве соединения, прежде чем
    # сдаться и вернуть None
    MAX_RETRIES = 2

    def __init__(self, token: str):
        self._token = token
        self._session: aiohttp.ClientSession | None = None
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)

    async def __aenter__(self) -> "TInvestClient":
        ssl_context = _build_ssl_context()
        connector = aiohttp.TCPConnector(
            ssl=ssl_context,
            limit=self.MAX_CONCURRENT_REQUESTS,
            limit_per_host=self.MAX_CONCURRENT_REQUESTS,
            enable_cleanup_closed=True,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
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

        async with self._semaphore:
            for attempt in range(1, self.MAX_RETRIES + 1):
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
                except (aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError) as exc:
                    if attempt < self.MAX_RETRIES:
                        logger.debug(
                            "Обрыв соединения при запросе %s.%s (попытка %d/%d), повторяю: %s",
                            service, method, attempt, self.MAX_RETRIES, exc,
                        )
                        await asyncio.sleep(0.3 * attempt)
                        continue
                    logger.error(
                        "Ошибка запроса T-Invest %s.%s после %d попыток: %s",
                        service, method, self.MAX_RETRIES, exc,
                    )
                    return None
                except Exception as exc:  # noqa: BLE001
                    logger.error("Ошибка запроса T-Invest %s.%s: %s", service, method, exc)
                    return None
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