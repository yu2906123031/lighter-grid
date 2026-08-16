"""LiveVenue：通过 Lighter SDK 真实执行（下单/撤单/查询）。

归因依赖 Trade 里的 ask_client_id_str / bid_client_id_str 与本地
client_order_index 匹配。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import lighter

from .risk import MarketParams
from .venue import PlacedOrder, Venue, VenueSnapshot

log = logging.getLogger("grid.venue")


# Lighter order type / time-in-force 常量（与 lighter-go constants.go 对齐）
ORDER_TYPE_LIMIT = 0
ORDER_TYPE_MARKET = 1
TIME_IN_FORCE_IOC = 0
TIME_IN_FORCE_GTT = 1
TIME_IN_FORCE_POST_ONLY = 2
DEFAULT_GTT_EXPIRY_MS = -1  # -1 = 28 days, signer converts to ms timestamp
PLACE_RETRIES = 3
PLACE_BACKOFF_MS = (500, 1000, 2000)
NETWORK_ERROR_MARKERS = (
    "dial tcp",
    "read tcp",
    "write tcp",
    "connectex",
    "i/o timeout",
    "forcibly closed",
    "connection refused",
    "connection reset",
    "timed out",
    "cannot connect",
    "server disconnected",
    "connection closed",
    "resolve",
)

def _is_network_error(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in NETWORK_ERROR_MARKERS)



class LiveVenue(Venue):
    def __init__(self, config, signer: "lighter.SignerClient", api_client: "lighter.ApiClient"):
        self.cfg = config
        self.signer = signer
        self.api = api_client
        self.account_api = lighter.AccountApi(api_client)
        self.order_api = lighter.OrderApi(api_client)
        self.symbol = config.symbol
        self.market = config.market
        self._auth_token: Optional[str] = None
        self._auth_deadline: float = 0.0
        self._mp: Optional[MarketParams] = None

    async def _auth(self) -> str:
        """懒加载 auth token（8 小时）。"""
        now = time.time()
        if self._auth_token and now < self._auth_deadline - 60:
            return self._auth_token
        tok, err = self.signer.create_auth_token_with_expiry(
            deadline=8 * 3600, api_key_index=self.cfg.api_key_index
        )
        if err:
            raise RuntimeError(f"auth token 生成失败: {err}")
        self._auth_token = tok
        self._auth_deadline = now + 8 * 3600
        return tok

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

    async def snapshot(self) -> VenueSnapshot:
        ob = await self.order_api.order_book_orders(market_id=self.market, limit=1)
        best_bid = float(ob.bids[0].price) if ob.bids else 0.0
        best_ask = float(ob.asks[0].price) if ob.asks else 0.0
        mid = (best_bid + best_ask) / 2.0
        spread = (best_ask - best_bid) / mid * 100.0 if mid > 0 else 0.0

        acc = await self.account_api.account(by="index", value=str(self.cfg.account_index))
        acct = acc.accounts[0] if acc.accounts else None
        equity = float(acct.collateral) if acct else 0.0
        pos_base = 0.0
        entry = 0.0
        liq = None
        upnl = 0.0
        if acct:
            for p in acct.positions or []:
                if int(p.market_id) == self.market:
                    pos_base = float(p.position)
                    entry = float(p.avg_entry_price)
                    liq = float(p.liquidation_price) if p.liquidation_price else None
                    upnl = float(p.unrealized_pnl)
                    break
        return VenueSnapshot(
            mid=mid, best_bid=best_bid, best_ask=best_ask, spread_pct=spread,
            equity=equity, position_base=pos_base, avg_entry_price=entry,
            liquidation_price=liq, unrealized_pnl=upnl,
        )

    async def place_order(
        self, client_order_index: int, base_amount: int, price_int: int,
        side: str, reduce_only: bool,
    ) -> PlacedOrder:
        is_ask = side == "sell"
        last_err: Optional[str] = None
        for attempt in range(PLACE_RETRIES + 1):
            try:
                _, resp, err = await self.signer.create_order(
                    market_index=self.market,
                    client_order_index=client_order_index,
                    base_amount=base_amount,
                    price=price_int,
                    is_ask=is_ask,
                    order_type=ORDER_TYPE_LIMIT,
                    time_in_force=TIME_IN_FORCE_GTT,
                    reduce_only=reduce_only,
                    order_expiry=DEFAULT_GTT_EXPIRY_MS,
                    api_key_index=self.cfg.api_key_index,
                )
            except Exception as exc:  # noqa: BLE001 - 网络层异常需转换为可重试错误
                err = str(exc)
            if not err:
                return PlacedOrder(client_order_index, None, True)
            last_err = str(err)
            if not _is_network_error(last_err):
                break
            if attempt < PLACE_RETRIES:
                log.warning(
                    "下单网络错误重试 %d/%d: %s",
                    attempt + 1, PLACE_RETRIES, last_err,
                )
                await asyncio.sleep(PLACE_BACKOFF_MS[attempt] / 1000.0)
        return PlacedOrder(client_order_index, None, False, last_err or "")

    async def cancel_order(self, order_index: int, market: int = 0) -> bool:
        m = market or self.market
        _, _, err = await self.signer.cancel_order(
            market_index=m, order_index=order_index, api_key_index=self.cfg.api_key_index
        )
        return err is None

    async def active_orders(self) -> dict[int, dict]:
        auth = await self._auth()
        r = await self.order_api.account_active_orders(
            authorization=auth, account_index=self.cfg.account_index, market_id=self.market
        )
        out: dict[int, dict] = {}
        for o in r.orders or []:
            ci = o.client_order_index
            if ci:
                out[int(ci)] = {
                    "order_index": int(o.order_index) if o.order_index else None,
                    "price": float(o.price),
                    "base_amount": int(o.initial_base_amount),
                    "remaining": int(o.remaining_base_amount),
                    "side": "sell" if o.is_ask else "buy",
                    "reduce_only": o.reduce_only,
                }
        return out

    async def recent_fills(self) -> list[dict]:
        auth = await self._auth()
        r = await self.order_api.trades(
            sort_by="timestamp", limit=100, authorization=auth,
            account_index=self.cfg.account_index, market_id=self.market,
        )
        fills: list[dict] = []
        for t in r.trades or []:
            is_ask = (t.ask_account_id == self.cfg.account_index) if t.ask_account_id else None
            cid = t.ask_client_id_str if is_ask else t.bid_client_id_str
            fills.append({
                "client_order_index": int(cid) if cid else None,
                "side": "sell" if is_ask else "buy",
                "size": float(t.size),
                "price": float(t.price),
                "usd_amount": float(t.usd_amount),
                "trade_id": t.trade_id_str,
                "ts": int(t.timestamp) if t.timestamp else 0,
            })
        return fills

    async def set_leverage(self, leverage: int) -> bool:
        # margin_mode: 0 = cross, 1 = isolated
        _, _, err = await self.signer.update_leverage(
            market_index=self.market, margin_mode=0, leverage=leverage,
            api_key_index=self.cfg.api_key_index,
        )
        return err is None
