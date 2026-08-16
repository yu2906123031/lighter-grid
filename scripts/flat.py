"""紧急平仓 + 撤单（live 模式危险操作，需显式确认）。

撤掉所有活跃订单，并用市价单反向平掉当前净持仓。

用法：python scripts/flat.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lighter

from config import Config
from src.live_venue import LiveVenue, ORDER_TYPE_MARKET, TIME_IN_FORCE_IOC


async def main() -> None:
    cfg = Config()
    if not cfg.is_live:
        print("flat 仅用于 live 模式。dryrun 请直接删除 data/state.json。")
        return

    signer = lighter.SignerClient(
        url=cfg.base_url,
        api_private_keys={cfg.api_key_index: cfg.resolve_private_key()},
        account_index=cfg.account_index,
    )
    api = lighter.ApiClient(lighter.Configuration(host=cfg.base_url))
    venue = LiveVenue(cfg, signer, api)

    snap = await venue.snapshot()
    print(f"当前持仓={snap.position_base} 均价={snap.avg_entry_price}")

    # 1) 撤所有活跃订单
    orders = await venue.active_orders()
    print(f"撤单 {len(orders)} 个活跃订单...")
    for ci, o in orders.items():
        if o.get("order_index"):
            ok = await venue.cancel_order(o["order_index"], cfg.market)
            print(f"  撤 client={ci} order={o['order_index']} ok={ok}")

    # 2) 平仓
    if abs(snap.position_base) > 0:
        mp = await venue.market_params()
        base_int = int(abs(snap.position_base) * (10 ** mp.size_decimals))
        side = "sell" if snap.position_base > 0 else "buy"
        print(f"市价平仓: {side} {snap.position_base} ...")
        _, _, err = await signer.create_order(
            market_index=cfg.market,
            client_order_index=0,  # 临时
            base_amount=base_int,
            price=0,
            is_ask=(side == "sell"),
            order_type=ORDER_TYPE_MARKET,
            time_in_force=TIME_IN_FORCE_IOC,
            reduce_only=True,
            api_key_index=cfg.api_key_index,
        )
        if err:
            print(f"平仓失败: {err}")
        else:
            print("平仓单已提交")
    else:
        print("无持仓，无需平仓")

    await api.close()
    print("完成。")


if __name__ == "__main__":
    asyncio.run(main())
