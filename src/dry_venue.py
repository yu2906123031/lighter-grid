"""DryRunVenue：用真实 Lighter 订单簿数据驱动本地成交模拟。

不真实下单，不碰真金。用真实 best_bid/best_ask 判断挂单是否触及：
  - buy 限价单在 best_ask <= 挂单价时成交（maker，成交价 = 挂单价）
  - sell 限价单在 best_bid >= 挂单价时成交
本地维护 collateral / position / fills。
"""
from __future__ import annotations

import time
from typing import Optional

import lighter

from .risk import MarketParams, liquidation_pct
from .venue import PlacedOrder, Venue, VenueSnapshot


class DryRunVenue(Venue):
    def __init__(self, config):
        self.cfg = config
        self.symbol = config.symbol
        self.market = config.market
        api_client = lighter.ApiClient(lighter.Configuration(host=config.base_url))
        self.api = api_client
        self.order_api = lighter.OrderApi(api_client)
        self._mp: Optional[MarketParams] = None

        # 本地账户状态
        self.initial_collateral = 200.0
        self.collateral = float(self.initial_collateral)
        self.position_base = 0.0
        self.avg_entry_price = 0.0
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0

        # 本地挂单 + 成交
        self._orders: dict[int, dict] = {}  # client_order_index -> order
        self._fills: list[dict] = []
        self._next_order_index = 1_000_000

    async def market_params(self) -> MarketParams:
        if self._mp is not None:
            return self._mp
        r = await self.order_api.order_book_details(market_id=self.market)
        d = r.order_book_details[0]
        self._mp = MarketParams(
            size_decimals=d.size_decimals,
            price_decimals=d.price_decimals,
            min_base_amount=float(d.min_base_amount),
            min_quote_amount=float(d.min_quote_amount),
            default_imf=d.default_initial_margin_fraction,
            min_imf=d.min_initial_margin_fraction,
            mmf=d.maintenance_margin_fraction,
            closeout_mf=d.closeout_margin_fraction,
            lot_size=float(d.min_base_amount),
        )
        return self._mp

    async def _live_book(self) -> tuple[float, float, float]:
        """真实订单簿的 best_bid / best_ask / mid。"""
        ob = await self.order_api.order_book_orders(market_id=self.market, limit=1)
        best_bid = float(ob.bids[0].price) if ob.bids else 0.0
        best_ask = float(ob.asks[0].price) if ob.asks else 0.0
        mid = (best_bid + best_ask) / 2.0 if best_bid and best_ask else (best_bid or best_ask)
        return best_bid, best_ask, mid

    def _process_fills(self, best_bid: float, best_ask: float, mp: MarketParams) -> None:
        """用真实 bid/ask 判断本地挂单是否触及并成交。"""
        if best_ask <= 0 or best_bid <= 0:
            return
        size_f = 10 ** mp.size_decimals
        to_remove: list[int] = []
        for ci, o in self._orders.items():
            price = o["price"]
            base_float = o["base_amount"] / size_f
            if o["side"] == "buy" and best_ask <= price:
                # 买单成交：以挂单价买入
                self._apply_fill(ci, "buy", base_float, price, o)
                to_remove.append(ci)
            elif o["side"] == "sell" and best_bid >= price:
                # reduce_only 卖单在无多头持仓时不成交（真实交易所会拒绝开空）
                if o.get("reduce_only") and self.position_base <= 1e-9:
                    continue
                self._apply_fill(ci, "sell", base_float, price, o)
                to_remove.append(ci)
        for ci in to_remove:
            self._orders.pop(ci, None)

    def _apply_fill(self, ci: int, side: str, base: float, price: float, o: dict) -> None:
        """成交后更新持仓/已实现 pnl（符号化仓位模型：正=多，负=空）。"""
        eps = 1e-9
        usd = base * price
        if side == "buy":
            if self.position_base >= 0:
                new_pos = self.position_base + base
                self.avg_entry_price = (self.avg_entry_price * self.position_base + price * base) / new_pos
                self.position_base = new_pos
            else:
                close = min(base, -self.position_base)
                self.realized_pnl += (self.avg_entry_price - price) * close
                remaining = base - close
                self.position_base += close
                if abs(self.position_base) < eps:
                    self.position_base = 0.0
                    self.avg_entry_price = 0.0
                if remaining > 0:
                    self.position_base = remaining
                    self.avg_entry_price = price
        else:  # sell
            if self.position_base <= 0:
                new_pos = self.position_base - base
                self.avg_entry_price = (self.avg_entry_price * (-self.position_base) + price * base) / (-new_pos)
                self.position_base = new_pos
            else:
                close = min(base, self.position_base)
                self.realized_pnl += (price - self.avg_entry_price) * close
                remaining = base - close
                self.position_base -= close
                if abs(self.position_base) < eps:
                    self.position_base = 0.0
                    self.avg_entry_price = 0.0
                if remaining > 0:
                    self.position_base = -remaining
                    self.avg_entry_price = price
        self._fills.append({
            "client_order_index": ci,
            "side": side,
            "size": base,
            "price": price,
            "usd_amount": usd,
            "trade_id": str(len(self._fills) + 1),
            "ts": int(time.time() * 1000),
        })

    async def snapshot(self) -> VenueSnapshot:
        mp = await self.market_params()
        best_bid, best_ask, mid = await self._live_book()
        self._process_fills(best_bid, best_ask, mp)
        spread = (best_ask - best_bid) / mid * 100.0 if mid > 0 else 0.0
        self.unrealized_pnl = self.position_base * (mid - self.avg_entry_price) if self.position_base > 0 else 0.0
        equity = self.collateral + self.realized_pnl + self.unrealized_pnl
        liq = None
        if self.position_base > 0 and self.avg_entry_price > 0:
            # 简化爆仓价：多仓在 mid 跌到 avg_entry × (1 - 可吸收%) 时爆仓
            liq = self.avg_entry_price * (1.0 - 0.12)
        return VenueSnapshot(
            mid=mid, best_bid=best_bid, best_ask=best_ask, spread_pct=spread,
            equity=equity, position_base=self.position_base,
            avg_entry_price=self.avg_entry_price, liquidation_price=liq,
            unrealized_pnl=self.unrealized_pnl,
        )

    async def place_order(
        self, client_order_index: int, base_amount: int, price_int: int,
        side: str, reduce_only: bool,
    ) -> PlacedOrder:
        mp = await self.market_params()
        price = price_int / (10 ** mp.price_decimals)
        self._orders[client_order_index] = {
            "order_index": self._next_order_index,
            "client_order_index": client_order_index,
            "price": price,
            "price_int": price_int,
            "base_amount": base_amount,
            "side": side,
            "reduce_only": reduce_only,
            "created": time.time(),
        }
        self._next_order_index += 1
        return PlacedOrder(client_order_index, self._orders[client_order_index]["order_index"], True)

    async def cancel_order(self, order_index: int, market: int = 0) -> bool:
        for ci, o in list(self._orders.items()):
            if o["order_index"] == order_index:
                self._orders.pop(ci, None)
                return True
        return False

    async def active_orders(self) -> dict[int, dict]:
        return {ci: dict(o) for ci, o in self._orders.items()}

    async def recent_fills(self) -> list[dict]:
        return list(self._fills)

    async def set_leverage(self, leverage: int) -> bool:
        return True

    async def close(self) -> None:
        await self.api.close()
