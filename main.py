"""Lighter Phoenix Grid 入口。

用法：
  python main.py            # 按 .env 的 MODE 运行（dryrun / live）
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import lighter

from config import Config
from src.engine import GridEngine
from src.state import StateStore
from src.dry_venue import DryRunVenue
from src.live_venue import LiveVenue


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def run(cfg: Config) -> None:
    if cfg.is_live:
        signer = lighter.SignerClient(
            url=cfg.base_url,
            api_private_keys={cfg.api_key_index: cfg.resolve_private_key()},
            account_index=cfg.account_index,
        )
        api_client = lighter.ApiClient(lighter.Configuration(host=cfg.base_url))
        venue = LiveVenue(cfg, signer, api_client)
    else:
        venue = DryRunVenue(cfg)

    store = StateStore(cfg.state_path)
    engine = GridEngine(cfg, venue, store)

    stop = asyncio.Event()

    def _sig(*_):
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sig)
        except NotImplementedError:
            pass

    try:
        await engine.start()
        logging.getLogger("grid.engine").info("进入 tick 循环，间隔 %d s", cfg.tick_ms)
        while not stop.is_set():
            await engine.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=cfg.tick_ms)
            except asyncio.TimeoutError:
                pass
    finally:
        store.save()
        await venue.close()
        logging.getLogger("grid.engine").info("已退出，状态已保存")


def main() -> int:
    cfg = Config()
    errs = cfg.validate()
    if errs:
        for e in errs:
            print(f"[配置错误] {e}", file=sys.stderr)
        return 1
    setup_logging(cfg.log_level)
    asyncio.run(run(cfg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
