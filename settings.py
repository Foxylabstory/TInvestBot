"""
Управление тем, какие категории сигналов включены/выключены —
настраивается прямо из Telegram командой /settings (см. telegram_commands.py).
Состояние сохраняется в settings.json, чтобы не сбрасываться при
перезапуске бота.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("moex_bot.settings")

_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# Категории сигналов, которые можно включать/выключать.
# Ключи "candlestick"/"support_resistance"/"classic"/"volume" совпадают
# с PatternSignal.category, "large_order" — отдельная категория для
# сигналов о крупных заявках (detector.py).
CATEGORIES = {
    "large_order": "🐋 Крупные заявки",
    "candlestick": "🕯️ Свечные паттерны",
    "support_resistance": "📏 Уровни поддержки/сопротивления",
    "classic": "📐 Классические фигуры",
    "volume": "📊 Объёмные всплески",
}

_DEFAULT_STATE = {key: True for key in CATEGORIES}


def _load() -> dict:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # на случай, если в будущем добавятся новые категории —
            # подставляем значение по умолчанию для отсутствующих ключей
            for key in CATEGORIES:
                data.setdefault(key, True)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Не удалось прочитать settings.json (%s), использую значения по умолчанию", exc)
    return dict(_DEFAULT_STATE)


def _save(state: dict) -> None:
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.error("Не удалось сохранить settings.json: %s", exc)


class SignalSettings:
    def __init__(self):
        self._state = _load()

    def is_enabled(self, category: str) -> bool:
        return self._state.get(category, True)

    def toggle(self, category: str) -> bool:
        """Переключает категорию и возвращает новое состояние (True=включено)."""
        self._state[category] = not self._state.get(category, True)
        _save(self._state)
        return self._state[category]

    def all_state(self) -> dict:
        return dict(self._state)