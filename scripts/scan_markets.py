"""标的扫描：评估所有 perp 市场对网格的适配度。

指标：
  - 日振幅（daily high/low）→ 网格完成轮次的核心收益来源
  - lot_USD（min_base × price）→ 200U 本金能否有效量化
  - 最大杠杆（min_imf）→ 决定爆仓距离
  - 日成交量 → 流动性
  - 近 30 天波动（candles）→ 最大单日跌幅（爆仓风险）+ 日均振幅

用法：python scripts/scan_markets.py
"""
from __future__ import annotations

import asyncio
import statistics
import time

import lighter


async def main() -> None:
    cfg = lighter.Configuration(host="https://api.rh.lighter.xyz/")
    client = lighter.ApiClient(cfg)
    oa = lighter.OrderApi(client)
    ca = lighter.CandlestickApi(client)

    r = await oa.order_book_details()
    perps = [d for d in r.order_book_details if d.market_type == "perp"]

    now = int(time.time() * 1000)
    rows = []
    for d in perps:
        mid = d.market_id
        price = float(d.last_trade_price) or 0
        lot_usd = float(d.min_base_amount) * price
        daily_amp = 0.0
        if d.daily_price_high and d.daily_price_low and price:
            daily_amp = (float(d.daily_price_high) - float(d.daily_price_low)) / price * 100
        max_lev = round(10000 / d.min_initial_margin_fraction, 1) if d.min_initial_margin_fraction else 0
        vol = float(d.daily_quote_token_volume) if d.daily_quote_token_volume else 0

        # 拉近 30 天日线算波动（静默，失败则跳过）
        avg_amp = 0.0
        max_drop = 0.0
        try:
            cr = await ca.candles(market_id=mid, resolution="1d",
                                  start_timestamp=now - 30 * 86400000, end_timestamp=now,
                                  count_back=30, set_timestamp_to_end=False)
            candles = cr.c if isinstance(cr.c, list) else []
            closes = [float(x.c) for x in candles if getattr(x, "c", None)]
            if len(closes) >= 5:
                rets = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes))]
                avg_amp = statistics.mean(abs(x) for x in rets)
                max_drop = min(rets)
        except Exception:
            pass

        rows.append({
            "symbol": d.symbol, "market_id": mid, "price": price,
            "lot_usd": lot_usd, "max_lev": max_lev, "daily_amp": daily_amp,
            "avg_amp": avg_amp, "max_drop": max_drop, "vol": vol,
        })

    # 排序：按日均振幅降序
    rows.sort(key=lambda x: x["avg_amp"], reverse=True)
    print(f"{'标的':<12}{'价格':>8}{'lot_USD':>9}{'最大杠杆':>8}{'日振幅':>8}{'30d日均':>8}{'30d最大跌':>9}{'日成交量':>12}")
    for x in rows:
        vol_str = f"{x['vol']/1e6:.1f}M" if x["vol"] >= 1e6 else f"{x['vol']/1e3:.0f}K"
        print(f"{x['symbol']:<12}{x['price']:>8.4f}{x['lot_usd']:>9.2f}{x['max_lev']:>7.1f}x"
              f"{x['daily_amp']:>8.2f}{x['avg_amp']:>8.2f}{x['max_drop']:>9.2f}{vol_str:>12}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
