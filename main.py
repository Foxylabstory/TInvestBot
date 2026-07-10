"""
Точка входа. Запускает два параллельных цикла опроса через T-Invest API:
  1. Стакан (top-of-book) -> детекция крупных заявок
  2. Свечи (1 мин)         -> детекция графических паттернов

T-Invest API отдаёт данные почти в реальном времени, без искусственной
15-минутной задержки, характерной для бесплатного MOEX ISS.

Запуск:
    pip install -r requirements.txt
    Создайте .env с TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, T_INVEST_TOKEN
    python main.py
"""

from __future__ import annotations

import asyncio
import logging

import config
from detector import LargeOrderDetector
from notifier import TelegramNotifier
from pattern_detector import PatternDetector
from settings import SignalSettings
from telegram_commands import setup_dispatcher
from tinvest_client import TInvestClient

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(config.LOG_FILE, encoding="utf-8")],
)
logger = logging.getLogger("moex_bot.main")


async def poll_ticker(ticker: str, client: TInvestClient, detector: LargeOrderDetector,
                       notifier: TelegramNotifier, latest_prices: dict[str, float]) -> None:
    tob = await client.get_top_of_book(ticker)
    if tob is None:
        return

    # Запоминаем последнюю известную реальную цену — используется как
    # "эталон" для проверки адекватности данных из свечей
    if tob.bid_price and tob.ask_price:
        latest_prices[ticker] = (tob.bid_price + tob.ask_price) / 2

    signals = detector.process(tob)
    for signal in signals:
        logger.info(
            "Сигнал (крупная заявка): %s %s @ %.2f, объём=%d (avg=%.1f)",
            signal.ticker, signal.side, signal.price, signal.volume, signal.avg_volume,
        )
        await notifier.send_signal(signal)


async def poll_ticker_patterns(ticker: str, client: TInvestClient, detector: PatternDetector,
                                notifier: TelegramNotifier, latest_prices: dict[str, float]) -> None:
    candles = await client.get_recent_candles(
        ticker, interval=config.PATTERN_CANDLE_INTERVAL, limit=config.PATTERN_HISTORY_LENGTH,
    )
    if not candles:
        return

    # Sanity check оставлен как страховка (например, на случай проблем с
    # конкретным инструментом), хотя T-Invest не должен давать 15-минутную
    # задержку, как это было с бесплатным MOEX ISS.
    reference_price = latest_prices.get(ticker)
    last_close = candles[-1].close
    if reference_price:
        deviation_pct = abs(last_close - reference_price) / reference_price * 100
        if deviation_pct > config.PATTERN_MAX_PRICE_DEVIATION_PCT:
            logger.warning(
                "Пропускаю паттерны по %s: цена свечи %.2f сильно расходится "
                "с текущей ценой %.2f (%.1f%%) — похоже на битые/устаревшие данные",
                ticker, last_close, reference_price, deviation_pct,
            )
            return

    signals = detector.process(ticker, candles)
    for signal in signals:
        logger.info(
            "Сигнал (паттерн): %s %s [%s] @ %.2f",
            signal.ticker, signal.pattern, signal.category, signal.price,
        )
        await notifier.send_pattern_signal(signal)


async def orderbook_loop(client: TInvestClient, notifier: TelegramNotifier,
                          latest_prices: dict[str, float]) -> None:
    """Цикл опроса top-of-book и детекции крупных заявок."""
    detector = LargeOrderDetector()
    while True:
        start = asyncio.get_event_loop().time()
        await asyncio.gather(
            *(poll_ticker(t, client, detector, notifier, latest_prices) for t in config.TICKERS)
        )
        elapsed = asyncio.get_event_loop().time() - start
        await asyncio.sleep(max(0.0, config.POLL_INTERVAL_SEC - elapsed))


async def pattern_loop(client: TInvestClient, notifier: TelegramNotifier,
                        latest_prices: dict[str, float]) -> None:
    """Цикл опроса свечей и детекции графических паттернов."""
    detector = PatternDetector()
    while True:
        start = asyncio.get_event_loop().time()
        await asyncio.gather(
            *(poll_ticker_patterns(t, client, detector, notifier, latest_prices) for t in config.TICKERS)
        )
        elapsed = asyncio.get_event_loop().time() - start
        await asyncio.sleep(max(0.0, config.PATTERN_POLL_INTERVAL_SEC - elapsed))


async def telegram_polling_loop(dp, bot) -> None:
    """
    Запускает приём команд Telegram (/settings и т.п.) с автоматическим
    перезапуском при сбое. Без этой обёртки любая сетевая ошибка при
    старте polling (например, VPN не в режиме "туннель", из-за чего
    api.telegram.org недоступен) валит весь asyncio.gather в main_loop —
    то есть падают и рабочие циклы T-Invest, хотя проблема касалась
    только Telegram.
    """
    while True:
        try:
            await dp.start_polling(bot)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Ошибка приёма команд Telegram (%s) — повтор через 15 секунд. "
                "Если ошибка про 'semaphore timeout'/SSL — проверьте, что VPN "
                "включён и в режиме полного туннеля.",
                exc,
            )
            await asyncio.sleep(15)


async def main_loop() -> None:
    settings = SignalSettings()
    notifier = TelegramNotifier(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, settings)
    latest_prices: dict[str, float] = {}
    dp = setup_dispatcher(settings, owner_chat_id=config.TELEGRAM_CHAT_ID)

    logger.info(
        "Запуск мониторинга (T-Invest API) по %d тикерам: %s",
        len(config.TICKERS), ", ".join(config.TICKERS),
    )
    logger.info("Текущие настройки сигналов: %s", settings.all_state())

    try:
        async with TInvestClient(
            config.T_INVEST_TOKEN,
            max_requests_per_minute=config.T_INVEST_MAX_REQUESTS_PER_MINUTE,
        ) as client:
            await asyncio.gather(
                orderbook_loop(client, notifier, latest_prices),
                pattern_loop(client, notifier, latest_prices),
                telegram_polling_loop(dp, notifier.bot),
            )
    finally:
        await notifier.close()


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем.")