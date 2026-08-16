"""持久化层：库存 lot、活跃订单、client_order_index 计数器、last mid、每日统计。

lot 模型：每次买单成交记一个多仓 lot；卖单成交按 FIFO 匹配一个 lot，
单轮利润 = (卖价 - 买价) × base。状态存 state.json，进程重启后可恢复。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


@dataclass
class Lot:
    id: int
    side: str                # 'long' | 'short'
    base_amount: float       # base 资产成交量（浮点，正数）
    entry_price: float       # 成交均价（浮点）
    level_index: int         # 所属档位 index
    created_at: float = field(default_factory=time.time)


@dataclass
class ActiveOrder:
    client_order_index: int
    order_index: Optional[int]  # 交易所 order_index（下单后回填）
    market_index: int
    price_int: int
    base_amount: int
    side: str                 # 'buy' | 'sell'
    reduce_only: bool
    level_index: int


@dataclass
class GridState:
    mid: float = 0.0
    last_mid: float = 0.0
    lots: list[Lot] = field(default_factory=list)
    active_orders: dict[int, ActiveOrder] = field(default_factory=dict)  # client_order_index -> order
    client_order_counter: int = 0
    last_tick_at: float = 0.0
    total_rounds: int = 0
    gross_pnl_usd: float = 0.0
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "mid": self.mid,
            "last_mid": self.last_mid,
            "lots": [asdict(l) for l in self.lots],
            "active_orders": {str(k): asdict(v) for k, v in self.active_orders.items()},
            "client_order_counter": self.client_order_counter,
            "last_tick_at": self.last_tick_at,
            "total_rounds": self.total_rounds,
            "gross_pnl_usd": self.gross_pnl_usd,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GridState":
        s = cls()
        s.mid = d.get("mid", 0.0)
        s.last_mid = d.get("last_mid", 0.0)
        s.lots = [Lot(**l) for l in d.get("lots", [])]
        s.active_orders = {int(k): ActiveOrder(**v) for k, v in d.get("active_orders", {}).items()}
        s.client_order_counter = d.get("client_order_counter", 0)
        s.last_tick_at = d.get("last_tick_at", 0.0)
        s.total_rounds = d.get("total_rounds", 0)
        s.gross_pnl_usd = d.get("gross_pnl_usd", 0.0)
        s.version = d.get("version", 1)
        return s

    def next_client_order_index(self) -> int:
        self.client_order_counter += 1
        return self.client_order_counter

    def add_lot(self, lot: Lot) -> None:
        self.lots.append(lot)

    def match_lot_fifo(self, side: str) -> Optional[Lot]:
        """FIFO 弹出最早的指定方向 lot（'long' 或 'short'）。"""
        for i, l in enumerate(self.lots):
            if l.side == side:
                return self.lots.pop(i)
        return None

    def net_position_base(self) -> float:
        """净持仓 base 量，正 = 多，负 = 空。"""
        net = 0.0
        for l in self.lots:
            net += l.base_amount if l.side == "long" else -l.base_amount
        return net

    def inventory_base(self, side: str) -> float:
        """指定方向的持仓总量（base 量）。"""
        return sum(l.base_amount for l in self.lots if l.side == side)


class StateStore:
    """线程安全的状态持久化。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.state = self._load()

    def _load(self) -> GridState:
        if self.path.exists():
            try:
                return GridState.from_dict(json.loads(self.path.read_text()))
            except Exception:
                pass
        return GridState()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state.to_dict(), indent=2))
            tmp.replace(self.path)

    def touch(self) -> None:
        self.state.last_tick_at = time.time()
