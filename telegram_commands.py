"""
Обработка команд Telegram: /settings показывает кнопки для включения и
выключения категорий сигналов прямо в чате, без правки config.py и
перезапуска бота.

Настройки общие на весь бот (один settings.json), а не персональные
под каждого пользователя. Поэтому обработка команд ограничена только
владельцем (TELEGRAM_CHAT_ID из config.py) — если бот не приватный,
это не даёт посторонним, написавшим боту, менять ваши настройки.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from settings import CATEGORIES, SignalSettings


def _build_keyboard(settings: SignalSettings) -> InlineKeyboardMarkup:
    rows = []
    for key, label in CATEGORIES.items():
        enabled = settings.is_enabled(key)
        icon = "✅" if enabled else "🚫"
        rows.append([InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"toggle:{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def setup_dispatcher(settings: SignalSettings, owner_chat_id: str) -> Dispatcher:
    dp = Dispatcher()

    def _is_owner(chat_id) -> bool:
        return str(chat_id) == str(owner_chat_id)

    @dp.message(Command("settings"))
    async def cmd_settings(message: Message) -> None:
        if not _is_owner(message.chat.id):
            return  # молча игнорируем чужие сообщения
        await message.answer(
            "Настройка уведомлений — нажмите на категорию, чтобы включить "
            "или выключить её (✅ включено, 🚫 выключено):",
            reply_markup=_build_keyboard(settings),
        )

    @dp.message(Command("start", "help"))
    async def cmd_help(message: Message) -> None:
        if not _is_owner(message.chat.id):
            return
        await message.answer(
            "Бот мониторит крупные заявки и графические паттерны по "
            "выбранным тикерам MOEX.\n\n"
            "Команды:\n"
            "/settings — включить/выключить категории сигналов"
        )

    @dp.callback_query(F.data.startswith("toggle:"))
    async def cb_toggle(callback: CallbackQuery) -> None:
        if not _is_owner(callback.message.chat.id):
            await callback.answer("Недоступно", show_alert=False)
            return

        category = callback.data.split(":", 1)[1]
        if category not in CATEGORIES:
            await callback.answer("Неизвестная категория")
            return

        new_state = settings.toggle(category)
        await callback.message.edit_reply_markup(reply_markup=_build_keyboard(settings))

        status = "включены" if new_state else "выключены"
        await callback.answer(f"{CATEGORIES[category]}: {status}")

    return dp