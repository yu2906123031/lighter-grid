"""精度与量化工具：价格/数量在浮点(人类可读)与整数(raw, 交易所口径)之间转换，
并对网格档位做 lot 量化。

Lighter 口径：
  - base_amount 整数，1 单位 = 10^-size_decimals 个 base 资产
  - price 整数，1 单位 = 10^-price_decimals 个 quote 资产(USD)
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


def float_to_int(value: float, decimals: int) -> int:
    """浮点 → 整数，按指定小数位。用 Decimal 避免浮点误差。"""
    d = Decimal(str(value))
    return int((d * (Decimal(10) ** decimals)).to_integral_value(rounding=ROUND_HALF_UP))


def int_to_float(value: int, decimals: int) -> float:
    return float(Decimal(value) / (Decimal(10) ** decimals))


def quantize_price(price: float, decimals: int) -> int:
    """价格浮点 → 整数（四舍五入到 price_decimals 位）。"""
    return float_to_int(price, decimals)


def quantize_base(base: float, decimals: int) -> int:
    """数量浮点 → 整数（四舍五入到 size_decimals 位）。"""
    return float_to_int(base, decimals)


def quantize_base_down(base: float, decimals: int, lot_base: float) -> int:
    """数量向下量化到 lot 的整数倍（用于 sizing，避免保证金超预算）。

    lot_base 为最小订单量（min_base_amount，浮点 LIT）。
    返回整数 base_amount。
    """
    lot_int = float_to_int(lot_base, decimals)
    if lot_int <= 0:
        lot_int = 1
    raw = float_to_int(base, decimals)
    lots = raw // lot_int
    if lots < 1:
        return lot_int  # 至少 1 lot
    return lots * lot_int


def price_to_int(price: float, price_decimals: int) -> int:
    return quantize_price(price, price_decimals)


def size_to_int(base: float, size_decimals: int) -> int:
    return quantize_base(base, size_decimals)
