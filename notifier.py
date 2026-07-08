"""Отправка сигналов в Telegram."""

from __future__ import annotations

import logging

from aiogram import Bot

from detector import Signal
from pattern_detector import PatternSignal

logger = logging.getLogger("moex_bot.notifier")

SIDE_LABEL = {"BUY": "🟢 ПОКУПКА", "SELL": "🔴 ПРОДАЖА"}
DIRECTION_LABEL = {"bullish": "🟢 бычий", "bearish": "🔴 медвежий", "neutral": "⚪ нейтральный"}
CATEGORY_LABEL = {
    "candlestick": "Свечной паттерн",
    "support_resistance": "Уровень",
    "classic": "Фигура",
    "volume": "Объём",
}


def format_signal(signal: Signal) -> str:
    ratio = signal.volume / signal.avg_volume if signal.avg_volume else 0
    text = (
        f"{SIDE_LABEL.get(signal.side, signal.side)} — <b>{signal.ticker}</b>\n"
        f"Цена: {signal.price:.2f} ₽\n"
        f"Объём заявки: {signal.volume} лотов (в {ratio:.1f}× больше среднего)\n"
        f"Сумма: {signal.notional_rub:,.0f} ₽\n".replace(",", " ")
    )
    if signal.levels:
        text += _format_levels(signal.levels)
    return text


def format_pattern_signal(signal: PatternSignal) -> str:
    category = CATEGORY_LABEL.get(signal.category, signal.category)
    direction = DIRECTION_LABEL.get(signal.direction, signal.direction)
    text = (
        f"📊 {category} — <b>{signal.ticker}</b>\n"
        f"Паттерн: {signal.pattern} ({direction})\n"
        f"Цена: {signal.price:.2f} ₽\n"
        f"Свеча: {signal.timestamp.strftime('%H:%M')}\n"
    )
    if signal.detail:
        text += f"{signal.detail}\n"
    if signal.levels:
        text += _format_levels(signal.levels)
    return text


def _format_levels(levels) -> str:
    return (
        f"\n<b>Вход:</b> {levels.entry:.2f} ₽\n"
        f"<b>Стоп-лосс:</b> {levels.stop_loss:.2f} ₽ (риск {levels.risk_pct:.2f}%)\n"
        f"<b>Тейк-профит:</b> {levels.take_profit:.2f} ₽\n"
        f"<i>Механический расчёт, не финансовая рекомендация</i>\n"
    )


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.chat_id = chat_id

    async def send_signal(self, signal: Signal) -> None:
        text = format_signal(signal)
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.error("Не удалось отправить сообщение в Telegram: %s", exc)

    async def send_pattern_signal(self, signal: PatternSignal) -> None:
        text = format_pattern_signal(signal)
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.error("Не удалось отправить сообщение в Telegram: %s", exc)

    async def close(self) -> None:
        await self.bot.session.close()