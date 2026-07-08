"""
Диагностический скрипт: печатает сырой ответ MOEX ISS по свечам
конкретного тикера, чтобы понять, что реально приходит с сервера.

Запуск:
    python debug_candles.py VTBR
"""

import asyncio
import sys
from datetime import datetime

import aiohttp

import config


async def main(ticker: str) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    url = (
        f"https://iss.moex.com/iss/engines/{config.MOEX_ENGINE}/markets/{config.MOEX_MARKET}/"
        f"boards/{config.MOEX_BOARD}/securities/{ticker}/candles.json"
    )

    print(f"--- Без from/till (старое поведение) ---")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params={"iss.meta": "off", "interval": 1}) as resp:
            data = await resp.json()
            rows = data.get("candles", {}).get("data", [])
            print(f"Всего строк: {len(rows)}")
            if rows:
                print("Первая:", rows[0])
                print("Последняя:", rows[-1])

    print(f"\n--- С from/till={today} (новое поведение) ---")
    async with aiohttp.ClientSession() as session:
        params = {"iss.meta": "off", "interval": 1, "from": today, "till": today}
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            rows = data.get("candles", {}).get("data", [])
            print(f"Всего строк: {len(rows)}")
            if rows:
                print("Первая:", rows[0])
                print("Последняя:", rows[-1])

    print(f"\n--- Текущая цена (top-of-book) ---")
    tob_url = (
        f"https://iss.moex.com/iss/engines/{config.MOEX_ENGINE}/markets/{config.MOEX_MARKET}/"
        f"boards/{config.MOEX_BOARD}/securities/{ticker}.json"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(tob_url, params={"iss.meta": "off", "iss.only": "marketdata"}) as resp:
            data = await resp.json()
            print(data.get("marketdata", {}))


if __name__ == "__main__":
    ticker_arg = sys.argv[1] if len(sys.argv) > 1 else "VTBR"
    asyncio.run(main(ticker_arg))