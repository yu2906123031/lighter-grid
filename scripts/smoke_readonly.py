"""只读冒烟测试：验证账户/余额/市场精度连通，不涉及任何下单。

用法：python scripts/smoke_readonly.py <account_index> <l1_address>
"""
from __future__ import annotations

import asyncio
import sys

import lighter


async def main() -> None:
    account_index = int(sys.argv[1])
    l1_address = sys.argv[2] if len(sys.argv) > 2 else ""

    cfg = lighter.Configuration(host="https://api.rh.lighter.xyz/")
    client = lighter.ApiClient(cfg)
    aa = lighter.AccountApi(client)
    oa = lighter.OrderApi(client)

    print("=== 1) 按 L1 地址查账户 ===")
    if l1_address:
        r = await aa.accounts_by_l1_address(l1_address=l1_address)
        print(f"sub_accounts 数量={len(r.sub_accounts) if hasattr(r,'sub_accounts') else '?'}")
        subs = getattr(r, "sub_accounts", None) or []
        for s in subs[:5]:
            print(f"  index={s.index} l1={s.l1_address}")

    print("\n=== 2) 按 index 查账户详情 ===")
    acc = await aa.account(by="index", value=str(account_index))
    accts = acc.accounts or []
    if not accts:
        print("未找到账户（可能 index 不对或账户不存在）")
    for a in accts:
        print(f"index={a.index} collateral={float(a.collateral):.4f} 可用={float(a.available_balance):.4f}")
        print(f"  账户类型={a.account_type} 交易模式={a.account_trading_mode}")
        print(f"  持仓数={len(a.positions or [])} 资产数={len(a.assets or [])}")
        for p in a.positions or []:
            print(f"  持仓: {p.symbol} market={p.market_id} 数量={p.position} 均价={p.avg_entry_price} 爆仓={p.liquidation_price} 未实现PnL={p.unrealized_pnl}")
        for ast in a.assets or []:
            print(f"  资产: {ast.symbol} balance={ast.balance} margin={ast.margin_balance} locked={ast.locked_balance}")

    print("\n=== 3) LIT 市场精度（market_id=5）===")
    r = await oa.order_book_details(market_id=5)
    d = r.order_book_details[0]
    print(f"symbol={d.symbol} 最新价={d.last_trade_price}")
    print(f"size_decimals={d.size_decimals} price_decimals={d.price_decimals}")
    print(f"min_base={d.min_base_amount} min_quote={d.min_quote_amount}")
    print(f"default_imf={d.default_initial_margin_fraction} min_imf={d.min_initial_margin_fraction} mmf={d.maintenance_margin_fraction}")
    print(f"taker_fee={d.taker_fee} maker_fee={d.maker_fee}")

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
