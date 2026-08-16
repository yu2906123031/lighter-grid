"""风控层：sizing、保证金预算、爆仓距离投影、价差闸。

适配 Lighter perp 保证金模型：
  - IMF = initial_margin_fraction（1/杠杆）
  - MMF = maintenance_margin_fraction
  - 挂单即锁定保证金（locked_balance），opening 订单保证金 = notional × IMF
"""
from __future__ import annotations

from dataclasses import dataclass

from .quantize import quantize_base_down


@dataclass
class MarketParams:
    """市场保证金/精度参数（来自 orderBookDetails）。"""
    size_decimals: int
    price_decimals: int
    min_base_amount: float      # 最小订单量（base 资产）
    min_quote_amount: float     # 最小订单名义（USD）
    default_imf: int            # 默认初始保证金率（bps）
    min_imf: int                # 最小初始保证金率（bps，= 最大杠杆）
    mmf: int                    # 维持保证金率（bps）
    closeout_mf: int            # 强平线（bps）
    lot_size: float = 0.0       # 有效 lot（= min_base_amount）

    def imf_for(self, leverage: int) -> float:
        return 1.0 / leverage

    @property
    def mmf_frac(self) -> float:
        return self.mmf / 10000.0

    @property
    def closeout_frac(self) -> float:
        return self.closeout_mf / 10000.0


def compute_size_base(
    equity: float,
    margin_frac: float,
    leverage: int,
    grid_count: int,
    mid: float,
    mp: MarketParams,
) -> int:
    """计算单档 base 数量（整数，量化到 lot）。

    sizeBase = equity × margin_frac × leverage / (grid_count × mid)
    向下量化到 lot 整数倍。
    """
    raw = equity * margin_frac * leverage / (grid_count * mid)
    return quantize_base_down(raw, mp.size_decimals, mp.min_base_amount)


def full_opening_margin(
    size_base: int,
    grid_count: int,
    mid: float,
    leverage: int,
    mp: MarketParams,
) -> float:
    """fresh build 时 grid_count 档全为 opening 订单的预计保证金（USD）。

    size_base 为整数，除以 10^size_decimals 得 base 资产量。
    """
    base_float = size_base / (10 ** mp.size_decimals)
    per_level_notional = base_float * mid
    total_notional = per_level_notional * grid_count
    return total_notional * (1.0 / leverage)


def margin_budget_ok(
    equity: float,
    margin_frac: float,
    size_base: int,
    grid_count: int,
    mid: float,
    leverage: int,
    mp: MarketParams,
) -> tuple[bool, float, float]:
    """保证金预算检查：full opening 保证金 < equity × margin_frac。

    返回 (passes, estimated_margin, budget)。
    """
    est = full_opening_margin(size_base, grid_count, mid, leverage, mp)
    budget = equity * margin_frac
    return est < budget, est, budget


def liquidation_pct(
    equity: float,
    margin_frac: float,
    leverage: int,
    mp: MarketParams,
) -> float:
    """单方向爆仓距离投影（%）。

    notional = equity × margin_frac × leverage
    absorbable = notional × (IMF - MMF) + (equity - notional × IMF)
    liq_pct = absorbable / notional
    """
    notional = equity * margin_frac * leverage
    imf = 1.0 / leverage
    init_margin = notional * imf
    maint_margin = notional * mp.mmf_frac
    free_buffer = equity - init_margin
    absorbable = (init_margin - maint_margin) + free_buffer
    if notional <= 0:
        return 0.0
    return absorbable / notional * 100.0


def spread_pct(best_bid: float, best_ask: float) -> float:
    """价差百分比。mid 为 0 时返回 0（中性，不触发闸）。"""
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0:
        return 0.0
    return (best_ask - best_bid) / mid * 100.0
