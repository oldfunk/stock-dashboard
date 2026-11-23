# 策略契约（Batch 22 / T87–T91）

> 子项目必须能**独立于母库/LLM**运行：内置策略只吃本地日线，产出 `Signal`；
> 执行仍走风控 + 队列/撮合，策略不得直接下单。
>
> 契约由 `paper_trading/strategy/registry.py` 执行；坏配置 **fail-closed**
> （启动失败 + `op_log[strategy:config]`），绝不用默认值静默兜底。

## 1. 配置

### 1.1 默认（无配置）

`config.yaml` 的 `strategy:` 段（`short_window/long_window/buy_volume/sell_volume`）
构造内置 `ma_cross`；这是现有行为，未配置策略列表时不变。

### 1.2 策略列表

`config.yaml`：

```yaml
strategy:
  strategies:
    - name: ma
      class: ma_cross
      params: {short_window: 5, long_window: 20}
      enabled: true
    - name: breakout
      class: breakout
      params: {high_window: 20, exit_window: 10}
      enabled: true
```

`strategy.local.yaml`（gitignored）的同名键**优先**；该文件继续承载
`active` / `instruction`，本契约只读 `strategies`，不与方案配置抢写：

```yaml
strategies:
  - {name: breakout, class: breakout, params: {high_window: 20}}
```

- `enabled: false` 的策略不构造、不产信号；
- `name` 在同一份列表里必须唯一；
- 所有参数按下表校验（类型/范围/候选值）；未知参数直接拒绝。

### 1.3 内置参数表

| class | 参数 | 类型 | 默认 | 范围 |
|---|---|---|---|---|
| `ma_cross` | `short_window` | int | 5 | 2–120 |
| | `long_window` | int | 20 | 3–250 |
| | `buy_volume` / `sell_volume` | int | 100 | 100–100000 |
| `breakout` | `high_window` | int | 20 | 2–250 |
| | `exit_window` | int | 10 | 2–120 |
| | `buy_volume` / `sell_volume` | int | 100 | 100–100000 |
| `rsi_zone` | `period` | int | 14 | 2–100 |
| | `buy_below` | float | 30 | 1–50 |
| | `sell_above` | float | 70 | 50–99 |
| | `buy_volume` / `sell_volume` | int | 100 | 100–100000 |

多策略合并：同标的冲突时**卖出优先**（risk-off），同向取最大手数。

## 2. 自定义策略

### 2.1 契约

```python
from paper_trading.strategy.base_strategy import BaseStrategy
from paper_trading.models import Signal, SignalType


class MyStrategy(BaseStrategy):
    params_schema = {
        "factor": {"type": int, "default": 1, "min": 1, "max": 10},
    }

    def __init__(self, factor: int = 1):
        super().__init__(name="my")
        self.factor = factor

    def generate_signals(self, bars):
        return {}   # 只产 Signal；不得调用 broker/queue/order
```

- 必须是 `BaseStrategy` 子类；`generate_signals` 返回 `{symbol: Signal}`；
- **不得直接下单**：不接触 `PaperBroker`、`Order`、`queue`、`ledger`；
- `params_schema` 可选；声明了就必须过校验，未声明则参数原样交给构造函数
  （构造异常同样 fail-closed）。

### 2.2 加载白名单

`class` 写成 `pkg.Module.Class`，只允许：

1. 已安装包（模块文件位于 `site-packages` / `dist-packages`）；
2. `PAPER_TRADING_STRATEGY_PATH` 列出的目录（`os.pathsep` 分隔）；
   临时加到 `sys.path` 后立即移除。

导入失败 / 非子类 / 不在白名单 ⇒ `StrategyConfigError`，启动失败并写
`op_log[strategy:config]`。

## 3. 装配

- `TradingBridge.strategy`（CLI/cron）与 `PaperTradingEngine.strategy`
  （`main.py`）都走注册表，不再硬编码 `MACrossStrategy`；
- 空策略列表 ⇒ 内置默认 MA5/20；
- 坏配置 ⇒ 启动即失败；cron 下一轮仍会失败并留痕，不会带病下单。

## 4. 离线独立运行（断网 + 无 LLM key）

数据已在本地 `data.db` 时：

```bash
cd paper-trading
python -m paper_trading.cli \
  --data-db data.db --account-db /tmp/offline_account.db \
  --scheme general \
  run --symbols 600519 000001 --dry-run --json
```

- 不读母库（`--scheme general` + 本地数据）；
- 不调 LLM；
- `--dry-run` 只出信号 + 风控预览，不落单；
- 策略只看 `daily_bars`/本地行情；离线日建议用 `breakout` 或 `ma_cross`。

## 5. 回归与回滚

- 单测：`tests/test_strategy_registry_t87.py`（正常/未知类/坏参数/重复名/
  禁用/自定义白名单/内置信号/Bridge 装配与留痕）；
- 全量 `pytest` 必须绿（除既存时窗失败）；
- 回滚：删掉 `strategy.local.yaml` 的 `strategies` 或把 `config.yaml` 的列表
  清空 ⇒ 自动回到内置 MA5/20；不改账本、不改执行底座。
