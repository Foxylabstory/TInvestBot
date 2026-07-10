"""
Диагностический скрипт: печатает сырой ответ T-Invest API по последним
свечам конкретного тикера, без каких-либо преобразований времени —
чтобы понять, что реально приходит с сервера.

Запуск:
    python debug_tinvest_time.py SBER
"""

import asyncio
import sys
from datetime import datetime, timezone

import config
from tinvest_client import TInvestClient


async def main(ticker: str) -> None:
    print(f"Текущее локальное время компьютера:      {datetime.now()}")
    print(f"Текущее время UTC:                         {datetime.now(timezone.utc)}")
    print()

    async with TInvestClient(config.T_INVEST_TOKEN) as client:
        body = {
            "depth": 1,
            "instrumentId": client._instrument_id(ticker),
        }
        ob_data = await client._post("MarketDataService", "GetOrderBook", body)
        print("--- Сырой ответ GetOrderBook (без преобразований) ---")
        print(ob_data)
        print()

        from datetime import timedelta
        now = datetime.now(timezone.utc)
        from_ = now - timedelta(minutes=30)
        candles_body = {
            "from": from_.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "to": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "interval": "CANDLE_INTERVAL_1_MIN",
            "instrumentId": client._instrument_id(ticker),
        }
        candles_data = await client._post("MarketDataService", "GetCandles", candles_body)
        print("--- Сырой ответ GetCandles (без преобразований) ---")
        rows = (candles_data or {}).get("candles", [])
        print(f"Всего свечей: {len(rows)}")
        if rows:
            print("Последняя свеча (сырая, как пришла от API):")
            print(rows[-1])


if __name__ == "__main__":
    ticker_arg = sys.argv[1] if len(sys.argv) > 1 else "SBER"
    asyncio.run(main(ticker_arg))