"""合成价格序列回测：验证引擎的成交/归因/再锚定闭环（离线，不碰网络）。

生成一段震荡价格，驱动 GridEngine，验证：
  1. 价格下跌 → 买单成交 → 建 lot
  2. 价格回升 → 卖单成交 → 平 lot 赚 spacing
  3. 单边移动 → re-anchor
"""
from __future__ import annotations

import asyncio
import math
import time

from config import Config
from src.engine import GridEngine
from src.risk import MarketParams
from src.state import StateStore
from src.venue import PlacedOrder, Venue, VenueSnapshot


class SyntheticVenue(Venue):
    """价格序列驱动的本地 venue（复用 dryrun 的成交模型）。"""

    def __init__(self, mp: MarketParams, price_series: list[float]):
        self.mp = mp
        self.prices = price_series
        self.i = -1
        self.symbol = "LIT"
        self.market = 5
        self.position_base = 0.0
        self.avg_entry_price = 0.0
        self.realized_pnl = 0.0
        self.collateral = 200.0
        self._orders: dict[int, dict] = {}
        self._fills: list[dict] = []
        self._oid = 1000000
        self.mid = price_series[0]

    async def market_params(self) -> MarketParams:
        return self.mp

    def _advance(self):
        self.i += 1
        if self.i >= len(self.prices):
            self.i = len(self.prices) - 1
        self.mid = self.prices[self.i]
        self._process()

    def _process(self):
        mid = self.mid
        best_bid = mid - 0.00005
        best_ask = mid + 0.00005
        sf = 10 ** self.mp.size_decimals
        done = []
        for ci, o in self._orders.items():
            p = o["price"]
            base = o["base_amount"] / sf
            if o["side"] == "buy" and best_ask <= p:
                self._fill(ci, "buy", base, p)
                done.append(ci)
            elif o["side"] == "sell" and best_bid >= p:
                if o.get("reduce_only") and self.position_base <= 1e-9:
                    continue
                self._fill(ci, "sell", base, p)
                done.append(ci)
        for ci in done:
            self._orders.pop(ci, None)

    def _fill(self, ci, side, base, price):
        eps = 1e-9
        if side == "buy":
            if self.position_base >= 0:
                new = self.position_base + base
                self.avg_entry_price = (self.avg_entry_price * self.position_base + price * base) / new
                self.position_base = new
            else:
                close = min(base, -self.position_base)
                self.realized_pnl += (self.avg_entry_price - price) * close
                rem = base - close
                self.position_base += close
                if abs(self.position_base) < eps:
                    self.position_base = 0.0
                    self.avg_entry_price = 0.0
                if rem > 0:
                    self.position_base = rem
                    self.avg_entry_price = price
        else:
            if self.position_base <= 0:
                new = self.position_base - base
                self.avg_entry_price = (self.avg_entry_price * (-self.position_base) + price * base) / (-new)
                self.position_base = new
            else:
                close = min(base, self.position_base)
                self.realized_pnl += (price - self.avg_entry_price) * close
                rem = base - close
                self.position_base -= close
                if abs(self.position_base) < eps:
                    self.position_base = 0.0
                    self.avg_entry_price = 0.0
                if rem > 0:
                    self.position_base = -rem
                    self.avg_entry_price = price
        self._fills.append({"client_order_index": ci, "side": side, "size": base,
                            "price": price, "trade_id": str(len(self._fills) + 1),
                            "ts": int(time.time() * 1000)})

    async def snapshot(self) -> VenueSnapshot:
        self._advance()
        mid = self.mid
        upnl = self.position_base * (mid - self.avg_entry_price) if self.position_base > 0 else 0.0
        return VenueSnapshot(
            mid=mid, best_bid=mid - 0.00005, best_ask=mid + 0.00005,
            spread_pct=0.0001 / mid * 100, equity=self.collateral + self.realized_pnl + upnl,
            position_base=self.position_base, avg_entry_price=self.avg_entry_price,
            liquidation_price=None, unrealized_pnl=upnl,
        )

    async def place_order(self, client_order_index, base_amount, price_int, side, reduce_only):
        price = price_int / (10 ** self.mp.price_decimals)
        self._orders[client_order_index] = {
            "order_index": self._oid, "price": price, "base_amount": base_amount,
            "side": side, "reduce_only": reduce_only,
        }
        self._oid += 1
        return PlacedOrder(client_order_index, self._oid - 1, True)

    async def cancel_order(self, order_index, market=0):
        for ci, o in list(self._orders.items()):
            if o["order_index"] == order_index:
                self._orders.pop(ci, None)
                return True
        return False

    async def active_orders(self):
        return self._orders

    async def recent_fills(self):
        return list(self._fills)

    async def set_leverage(self, leverage):
        return True


def make_series(start: float, n: int, amplitude: float, period: int, trend: float = 0.0) -> list[float]:
    """合成价格序列：正弦震荡 + 线性趋势。"""
    out = []
    for i in range(n):
        wave = amplitude * math.sin(2 * math.pi * i / period)
        out.append(start + wave + trend * i)
    return out


async def main():
    mp = MarketParams(size_decimals=2, price_decimals=4, min_base_amount=5.0,
                      min_quote_amount=10.0, default_imf=5000, min_imf=2000,
                      mmf=1200, closeout_mf=800, lot_size=5.0)
    # 震荡：围绕 2.30，振幅 0.04（约 1.7%），周期 60 tick，轻微上升趋势
    series = make_series(2.30, 400, 0.04, 60, trend=0.0001)

    cfg = Config()
    cfg.grid_count = 9
    cfg.grid_spacing_pct = 0.5
    cfg.leverage = 5
    cfg.margin_frac = 0.8
    cfg.stop_loss_pct = 0.0

    venue = SyntheticVenue(mp, series)
    store = StateStore(cfg.state_path)
    eng = GridEngine(cfg, venue, store)

    await eng.start()
    print(f"start: mid={eng.state.mid:.4f} size_base={eng.size_base} 挂单={len(eng.state.active_orders)}")

    peak_lots = 0
    reanchors = 0
    prev_mid = eng.state.mid
    for _ in range(400):
        await eng.tick()
        peak_lots = max(peak_lots, len(eng.state.lots))
        if eng.state.mid != prev_mid:
            reanchors += 1
            prev_mid = eng.state.mid

    print(f"\n=== 结果 ===")
    print(f"final mid={eng.state.mid:.4f}")
    print(f"完成轮数={eng.state.total_rounds} 毛PnL={eng.state.gross_pnl_usd:.4f} USD")
    print(f"peak 同时持仓 lot={peak_lots}  再锚定次数={reanchors}")
    print(f"当前净持仓={venue.position_base:.2f} LIT  已实现pnl={venue.realized_pnl:.4f} USD")
    print(f"剩余活跃订单={len(eng.state.active_orders)}  剩余 lot={len(eng.state.lots)}")


if __name__ == "__main__":
    asyncio.run(main())
