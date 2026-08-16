# Lighter Phoenix Grid

LIT 永续合约的 Phoenix 网格交易机器人（Python + `lighter-sdk`）。

自愈式方向性网格：围绕中价挂买单（下方）建多仓 lot、挂卖单（上方）平仓赚档位差价，
价格漂出网格带后自动再锚定（re-anchor）到新中价，网格永不被甩在后面。

## 策略参数（默认，已按 200U / 5x / LIT 校准）

| 参数 | 值 | 说明 |
|---|---|---|
| 标的 | LIT (market_index 5) | price 4 位小数、size 2 位小数、min 5 LIT |
| 杠杆 | 5x | LIT 最大杠杆（min_imf 2000 bps） |
| 网格档数 | 9 | 下方 4 买单 + 中轴 + 上方 4 卖单 |
| 档间距 | 0.5% | 单轮利润 ≈ spacing × 单档量 |
| 保证金分数 | 0.8 | 用 80% equity 做网格 |
| 单档量 | 85 LIT（≈195 U） | 满仓保证金 156 U < 160 U 预算 |

满仓单方向爆仓距离约 13%（LIT 日振幅约 3.4%）。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 填写配置
```

## 运行

```bash
# dry-run：用真实订单簿数据本地模拟成交，不碰真金
MODE=dryrun .venv/bin/python main.py

# 查看状态
.venv/bin/python scripts/status.py [--live]

# 合成序列回测（离线验证引擎闭环）
.venv/bin/python -c "import sys; sys.path.insert(0,'.'); exec(open('scripts/test_synthetic.py').read())"
```

## 架构

```
config.py            配置层（.env 校验）
src/quantize.py      精度/量化（Decimal 精确换算）
src/risk.py          sizing + 保证金预算 + 爆仓距离 + spread 闸
src/grid.py          网格构建 + 再锚定
src/state.py         持久化（lot / 活跃订单 / 统计）
src/venue.py         Venue 抽象接口
src/live_venue.py    真实下单后端（SDK 签名 + auth token + 成交归因）
src/dry_venue.py     dry-run 后端（真实订单簿驱动模拟）
src/engine.py        Phoenix 引擎（建网/归因/风控/再锚定/reconcile）
main.py              入口
scripts/status.py    状态查看
scripts/flat.py      紧急撤单+平仓（live）
```

## 风控闸门（每 tick）

- **保证金预算**：满仓保证金 < equity × MARGIN_FRAC，启动时超限直接拒绝
- **spread 闸**：spread > MAX_SPREAD_PCT 时进入只减仓模式，不挂新买单
- **止损 backstop**：STOP_LOSS_PCT > 0 时，价格跌破中价该百分比触发停机（默认关闭）
- **只减仓熔断**：安全停机后不再开新仓

## 上 live 前的检查清单

1. `pip install lighter-sdk` 并确认版本
2. 在 Lighter 创建 API key（`SignerClient.create_api_key`），私钥存 `.enc` 或环境变量，勿进 git
3. 确认账户已入金、`account_index` 正确（`accountsByL1Address` 查询）
4. 先用 dryrun 跑通，再切 `MODE=live`
5. live 启动需人工确认（本 bot 不会自动从 dryrun 切 live）

## 重要警告

- dry-run 的 PnL 是**理想成交上限**（无滑点/手续费/资金费率），真实会打折扣
- 标准账户 0 费率是当前促销价，可能变化
- 网格在单边趋势中会持续累积反向持仓，止损 backstop 是最后防线
- 本软件仅供学习研究，实盘盈亏自负
