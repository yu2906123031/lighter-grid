"""Phoenix 网格引擎核心。

纯多头方向性网格：
  - 下方买单(opening) 买入建多仓 lot
  - 上方卖单(reduce_only) 平多仓 lot，赚 spacing
  - 价格漂移超出半带 → 再锚定(re-anchor)到新 mid，网格自愈

每 tick：snapshot → 归因成交 → 风控闸 → 再锚定判断 → 订单 reconcile → 持久化。
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from .grid import Level, active_levels, build_grid, half_band_usd
from .quantize import int_to_float, quantize_price
from .risk import MarketParams, compute_size_base, margin_budget_ok, spread_pct
from .state import ActiveOrder, GridState, Lot, StateStore
from .venue import Venue, VenueSnapshot

log = logging.getLogger("grid.engine")


class GridEngine:
    def __init__(self, config, venue: Venue, store: StateStore):
        self.cfg = config
        self.venue = venue
        self.store = store
        self.state: GridState = store.state
        self.mp: Optional[MarketParams] = None
        self.size_base: int = 0
        self.max_inventory_base: float = 0.0
        self.grid: list[Level] = []
        self.only_reduce = False
        self.halt_reason: Optional[str] = None
        self._processed_fills: set[str] = set()
        self._lot_counter = 0

    # ---- 生命周期 ----

    async def start(self) -> None:
        self.mp = await self.venue.market_params()
        await self.venue.set_leverage(self.cfg.leverage)
        # fresh build：清空上一轮网格状态（lots / 活跃订单 / 成交水位 / 统计）
        # 注意：真实"重启接管已有持仓"是后续增强，当前 start 语义 = 全新建网
        self.state.lots = []
        self.state.active_orders = {}
        self.state.mid = 0.0
        self.state.last_mid = 0.0
        self.state.total_rounds = 0
        self.state.gross_pnl_usd = 0.0
        self._processed_fills = set()
        self._lot_counter = 0

        snap = await self.venue.snapshot()
        if snap.equity <= 0:
            raise RuntimeError(f"账户 equity 为 {snap.equity}，请先入金")
        if snap.mid <= 0:
            raise RuntimeError("无法获取市场价格")

        # sizing：基于每侧库存上限（双向中性网格，最坏净持仓 = max_inventory 档）
        inv = self.cfg.max_inventory
        self.size_base = compute_size_base(
            snap.equity, self.cfg.margin_frac, self.cfg.leverage,
            inv, snap.mid, self.mp,
        )
        ok, est, budget = margin_budget_ok(
            snap.equity, self.cfg.margin_frac, self.size_base,
            inv, snap.mid, self.cfg.leverage, self.mp,
        )
        self.max_inventory_base = self.size_base * inv / (10 ** self.mp.size_decimals)
        log.info(
            "sizing: size_base=%d (%s %s) 库存上限=%d档 最坏保证金=%.2f 预算=%.2f 通过=%s",
            self.size_base, int_to_float(self.size_base, self.mp.size_decimals),
            self.cfg.symbol, inv, est, budget, ok,
        )
        if not ok:
            raise RuntimeError(
                f"保证金预算超限: 满仓保证金 {est:.2f} >= 预算 {budget:.2f}，"
                f"请降低 GRID_COUNT 或 LEVERAGE"
            )

        self.state.mid = snap.mid
        self.grid = build_grid(snap.mid, self.cfg.grid_spacing_pct, self.cfg.grid_count, self.mp.price_decimals)
        await self._reconcile(snap)
        self.store.save()
        log.info("网格已启动: mid=%.4f size_base=%d 档数=%d", snap.mid, self.size_base, len(self.grid))

    async def tick(self) -> None:
        if self.halt_reason:
            return
        try:
            snap = await self.venue.snapshot()
            await self._attribute_fills()
            self._risk_gates(snap)

            half = half_band_usd(self.state.mid, self.cfg.grid_spacing_pct, self.cfg.grid_count)
            if abs(snap.mid - self.state.mid) > half and self.state.mid > 0:
                log.info("re-anchor: mid %.4f -> %.4f", self.state.mid, snap.mid)
                self.state.last_mid = self.state.mid
                self.state.mid = snap.mid
                self.grid = build_grid(
                    snap.mid, self.cfg.grid_spacing_pct, self.cfg.grid_count, self.mp.price_decimals
                )

            await self._reconcile(snap)
            self.store.touch()
            self.store.save()
        except Exception as exc:  # noqa: BLE001 - 网络/临时故障不应终止进程
            log.error("tick 异常（保留状态，下一轮继续）: %s", exc, exc_info=True)
            self.store.save()

    # ---- 归因 ----

    async def _attribute_fills(self) -> None:
        fills = await self.venue.recent_fills()
        # 收集未处理的新成交，按时间戳排序（同 tick 多笔成交保持顺序）
        new_fills = []
        for f in fills:
            tid = str(f.get("trade_id") or "")
            if tid and tid in self._processed_fills:
                continue
            if tid:
                self._processed_fills.add(tid)
            new_fills.append(f)
        # 按时间戳排序（同 tick 多笔成交保持成交顺序）
        new_fills.sort(key=lambda f: int(f.get("ts") or 0))

        for f in new_fills:
            side = f["side"]
            size = float(f["size"])
            price = float(f["price"])
            ci = f.get("client_order_index")
            level_index = self._level_for_client(ci)

            if side == "buy":
                # 买入：先平空 lot，否则开多 lot
                short = self.state.match_lot_fifo("short")
                if short:
                    pnl = (short.entry_price - price) * short.base_amount
                    self.state.gross_pnl_usd += pnl
                    self.state.total_rounds += 1
                    log.info("买入平空: %.2f @ %.4f 开空价 %.4f pnl=%.4f (轮数 %d)",
                             short.base_amount, price, short.entry_price, pnl, self.state.total_rounds)
                else:
                    lot = Lot(id=self._lot_counter, side="long", base_amount=size,
                              entry_price=price, level_index=level_index)
                    self.state.add_lot(lot)
                    self._lot_counter += 1
                    log.info("买入开多: %.2f %s @ %.4f (lot %d)", size, self.cfg.symbol, price, lot.id)
            else:  # sell
                # 卖出：先平多 lot，否则开空 lot
                long_lot = self.state.match_lot_fifo("long")
                if long_lot:
                    pnl = (price - long_lot.entry_price) * long_lot.base_amount
                    self.state.gross_pnl_usd += pnl
                    self.state.total_rounds += 1
                    log.info("卖出平多: %.2f @ %.4f 成本 %.4f pnl=%.4f (轮数 %d)",
                             long_lot.base_amount, price, long_lot.entry_price, pnl, self.state.total_rounds)
                else:
                    lot = Lot(id=self._lot_counter, side="short", base_amount=size,
                              entry_price=price, level_index=level_index)
                    self.state.add_lot(lot)
                    self._lot_counter += 1
                    log.info("卖出开空: %.2f %s @ %.4f (lot %d)", size, self.cfg.symbol, price, lot.id)

            # 成交后从活跃订单移除
            if ci is not None and ci in self.state.active_orders:
                del self.state.active_orders[ci]

    def _level_for_client(self, ci: Optional[int]) -> int:
        if ci is None:
            return -1
        o = self.state.active_orders.get(ci)
        return o.level_index if o else -1

    # ---- 风控 ----

    def _risk_gates(self, snap: VenueSnapshot) -> None:
        # spread 闸
        if snap.spread_pct > self.cfg.max_spread_pct:
            if not self.only_reduce:
                log.warning("spread %.3f%% 超阈值 %.2f%%，进入只减仓模式", snap.spread_pct, self.cfg.max_spread_pct)
            self.only_reduce = True
        else:
            if self.only_reduce:
                log.info("spread 回落，恢复正常挂单")
            self.only_reduce = False

        # 止损 backstop（可选）
        if self.cfg.stop_loss_pct > 0 and self.state.mid > 0:
            stop_price = self.state.mid * (1.0 - self.cfg.stop_loss_pct / 100.0)
            if snap.mid < stop_price and snap.position_base > 0:
                log.warning("触发止损: mid %.4f < 止损线 %.4f", snap.mid, stop_price)
                self.halt_reason = f"stop_loss triggered at {snap.mid}"

    # ---- 订单规划 ----

    def _desired_orders(self, snap: VenueSnapshot) -> dict[int, dict]:
        """计算理想订单集合（双向中性做市），key = level_index。

        下方买单开多、上方卖单开空。库存上限按单侧持仓限制：
        每侧最多 max_inventory_base 的持仓，达到上限后停止同向开仓，
        且只挂「剩余允许」的档位（优先挂最接近 mid 的档）。
        """
        desired: dict[int, dict] = {}
        max_base = self.max_inventory_base
        size_float = self.size_base / (10 ** self.mp.size_decimals)
        long_inv = self.state.inventory_base("long")
        short_inv = self.state.inventory_base("short")
        # 剩余可开仓档数（整数）
        long_remaining = max(0, int((max_base - long_inv) / size_float + 1e-9))
        short_remaining = max(0, int((max_base - short_inv) / size_float + 1e-9))

        def _emit(lv: Level) -> None:
            desired[lv.index] = {
                "price_int": lv.price_int,
                "side": lv.side,
                "reduce_only": False,
                "base_amount": self.size_base,
            }

        # 买单：从最近 mid 的档位向外，最多 long_remaining 档
        buy_levels = sorted((lv for lv in self.grid if lv.side == "buy"), key=lambda lv: -lv.index)
        for lv in buy_levels[:long_remaining]:
            _emit(lv)

        # 卖单：从最近 mid 的档位向外，最多 short_remaining 档
        sell_levels = sorted((lv for lv in self.grid if lv.side == "sell"), key=lambda lv: lv.index)
        for lv in sell_levels[:short_remaining]:
            _emit(lv)

        return desired

    async def _reconcile(self, snap: VenueSnapshot) -> None:
        desired = self._desired_orders(snap)

        # 1) 撤掉不再需要的活跃订单
        for ci, o in list(self.state.active_orders.items()):
            spec = desired.get(o.level_index)
            stale = (
                spec is None
                or spec["price_int"] != o.price_int
                or spec["reduce_only"] != o.reduce_only
                or spec["side"] != o.side
            )
            if stale:
                if o.order_index is not None:
                    ok = await self.venue.cancel_order(o.order_index, self.cfg.market)
                    log.info("撤单 level=%d price_int=%d ok=%s", o.level_index, o.price_int, ok)
                del self.state.active_orders[ci]

        # 2) 挂缺失的订单
        for level_index, spec in desired.items():
            already = any(
                o.level_index == level_index and o.price_int == spec["price_int"]
                and o.reduce_only == spec["reduce_only"] and o.side == spec["side"]
                for o in self.state.active_orders.values()
            )
            if already:
                continue
            if self.only_reduce and not spec["reduce_only"]:
                continue  # 只减仓模式，跳过 opening 买单
            ci = self.state.next_client_order_index()
            result = await self.venue.place_order(
                client_order_index=ci,
                base_amount=spec["base_amount"],
                price_int=spec["price_int"],
                side=spec["side"],
                reduce_only=spec["reduce_only"],
            )
            if result.ok:
                self.state.active_orders[ci] = ActiveOrder(
                    client_order_index=ci,
                    order_index=result.order_index,
                    market_index=self.cfg.market,
                    price_int=spec["price_int"],
                    base_amount=spec["base_amount"],
                    side=spec["side"],
                    reduce_only=spec["reduce_only"],
                    level_index=level_index,
                )
            else:
                log.error("下单失败 level=%d: %s", level_index, result.error)
