"""查看网格运行状态：读取 state.json 打印摘要。

用法：python scripts/status.py [--live]
  --live  额外连 API 拉实时账户/仓位/活跃订单
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from src.state import GridState


def print_state(state: GridState) -> None:
    net = sum(l.base_amount for l in state.lots)
    avg_entry = sum(l.entry_price * l.base_amount for l in state.lots) / net if net > 0 else 0.0
    print("=== Phoenix Grid 状态 ===")
    print(f"mid={state.mid:.4f}  last_mid={state.last_mid:.4f}")
    print(f"未平 lot={len(state.lots)}  净持仓={net:.2f} LIT  持仓均价={avg_entry:.4f}")
    print(f"完成轮数={state.total_rounds}  毛PnL={state.gross_pnl_usd:.4f} USD")
    print(f"活跃订单={len(state.active_orders)}")
    for ci, o in sorted(state.active_orders.items(), key=lambda kv: kv[1].level_index):
        print(f"  client={ci} level={o.level_index} side={o.side:4s} price_int={o.price_int} base={o.base_amount} reduce={o.reduce_only}")
    print(f"last_tick_at={state.last_tick_at:.0f}")


async def live_extra(cfg: Config) -> None:
    import lighter
    from src.live_venue import LiveVenue
    from src.risk import MarketParams

    api = lighter.ApiClient(lighter.Configuration(host=cfg.base_url))
    oa = lighter.OrderApi(api)
    aa = lighter.AccountApi(api)
    r = await oa.order_book_details(market_id=cfg.market)
    d = r.order_book_details[0]
    acc = await aa.account(by="index", value=str(cfg.account_index))
    acct = acc.accounts[0] if acc.accounts else None
    print("\n=== 实时账户 ===")
    if acct:
        print(f"collateral={float(acct.collateral):.4f}  可用={float(acct.available_balance):.4f}")
        for p in acct.positions or []:
            if int(p.market_id) == cfg.market:
                print(f"持仓={float(p.position)} 均价={float(p.avg_entry_price):.4f} 爆仓价={p.liquidation_price} 未实现PnL={p.unrealized_pnl}")
    print(f"最新价={d.last_trade_price}")
    await api.close()


def main() -> None:
    cfg = Config()
    if not cfg.state_path.exists():
        print("无状态文件，网格未运行过。")
        return
    state = GridState.from_dict(json.loads(cfg.state_path.read_text()))
    print_state(state)
    if "--live" in sys.argv:
        asyncio.run(live_extra(cfg))


if __name__ == "__main__":
    main()
