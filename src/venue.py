"""Venue 抽象：统一 live（Lighter SDK）与 dryrun（本地模拟）两种执行后端的接口。

引擎只依赖这个接口，从而同一套策略逻辑可以：
  - live 模式真实下单；
  - dryrun 模式用真实订单簿数据本地模拟成交（不碰真金）。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from .risk import MarketParams


@dataclass
class VenueSnapshot:
    mid: float
    best_bid: float
    best_ask: float
    spread_pct: float
    equity: float
    position_base: float      # 净持仓（base 资产，正=多，负=空）
    avg_entry_price: float
    liquidation_price: Optional[float]
    unrealized_pnl: float
    ts: float = field(default_factory=time.time)


@dataclass
class PlacedOrder:
    client_order_index: int
    order_index: Optional[int]
    ok: bool
    error: str = ""


class Venue(ABC):
    symbol: str = ""
    market: int = 0

    @abstractmethod
    async def market_params(self) -> MarketParams:
        ...

    @abstractmethod
    async def snapshot(self) -> VenueSnapshot:
        ...

    @abstractmethod
    async def place_order(
        self,
        client_order_index: int,
        base_amount: int,
        price_int: int,
        side: str,             # 'buy' | 'sell'
        reduce_only: bool,
    ) -> PlacedOrder:
        ...

    @abstractmethod
    async def cancel_order(self, order_index: int, market: int = 0) -> bool:
        ...

    @abstractmethod
    async def active_orders(self) -> dict[int, dict]:
        """返回活跃订单，key = client_order_index，value 含 filled 状态。"""
        ...

    @abstractmethod
    async def recent_fills(self) -> list[dict]:
        """返回最近成交（用于归因）。每项含 side/base/price/order 标识。"""
        ...

    @abstractmethod
    async def set_leverage(self, leverage: int) -> bool:
        ...

    async def close(self) -> None:
        pass
