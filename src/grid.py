"""网格构建：围绕 mid 生成对称档位，以及再锚定(nearest-level / reanchor)辅助。

约定：
  - grid_count 为奇数，中轴 1 档作为参考不挂单；上下各 (grid_count-1)//2 档。
  - 实际挂单档数 = grid_count - 1（active_levels）。
  - 下方档 = 买单(bid)，上方档 = 卖单(ask)。
  - level index 从 0(最低买价) 递增到 grid_count-1(最高卖价)。
"""
from __future__ import annotations

from dataclasses import dataclass

from .quantize import quantize_price


@dataclass
class Level:
    index: int
    price: float
    price_int: int
    side: str  # 'buy' | 'sell' | 'mid'

    @property
    def is_bid(self) -> bool:
        return self.side == "buy"

    @property
    def is_ask(self) -> bool:
        return self.side == "sell"


def active_levels(grid_count: int) -> int:
    """实际挂单档数（中轴参考档除外）。"""
    return grid_count - 1


def build_grid(mid: float, spacing_pct: float, grid_count: int, price_decimals: int) -> list[Level]:
    """围绕 mid 构建对称网格。

    返回按价格从低到高排序的 Level 列表，长度 = grid_count。
    """
    half = (grid_count - 1) // 2
    levels: list[Level] = []
    # 下方买单档（从最远到最近 mid）
    for i in range(half, 0, -1):
        p = mid * (1.0 - spacing_pct / 100.0) ** i
        levels.append(Level(index=-1, price=p, price_int=quantize_price(p, price_decimals), side="buy"))
    # 中轴参考档
    levels.append(Level(index=-1, price=mid, price_int=quantize_price(mid, price_decimals), side="mid"))
    # 上方卖单档（从最近到最远）
    for i in range(1, half + 1):
        p = mid * (1.0 + spacing_pct / 100.0) ** i
        levels.append(Level(index=-1, price=p, price_int=quantize_price(p, price_decimals), side="sell"))
    # 重排 index：0..grid_count-1 从低到高
    for i, lv in enumerate(levels):
        lv.index = i
    return levels


def nearest_level_index(grid: list[Level], price: float) -> int:
    """返回价格最接近的档位 index。grid 为空返回 -1。"""
    if not grid:
        return -1
    best = min(grid, key=lambda lv: abs(lv.price - price))
    return best.index


def half_band_usd(mid: float, spacing_pct: float, grid_count: int) -> float:
    """半个网格带（USD），价格偏离 mid 超过此值触发再锚定。"""
    half = (grid_count - 1) // 2
    return mid * (1.0 - (1.0 - spacing_pct / 100.0) ** half)
