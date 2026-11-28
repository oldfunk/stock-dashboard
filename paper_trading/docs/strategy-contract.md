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
- 空策略列表 ⇒ 内置默认 MA20/60；
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

## 5. 母项目价值策略输入（2026-11-24）

本文件的策略注册表（内置/自定义）是**子项目自己的执行策略**；母项目的价值策略
是另一条输入，走只读契约：

- 位置：`screening_result.ai_trade_strategy`（JSON 字符串，`signal/buy_zone/stop_loss`），
  详见 `docs/mother-interface.md` §6；
- 子项目消费点：`agent/mother_strategy.py`（买入闸门/AVOID 强制离场/计划整备）；
- 探针：`python -m paper_trading.cli data contract --json`，形状漂移 exit 3；
- 边界：母项目供数、子项目只读；不把母策略写进本文件的注册表，也不反向写母库。

## 6. 默认通用策略（general，2026-11-28）

旧默认 MA5/20 已更新为 **MA20/60 + 趋势/质量/风险**的通用中线手册：

- 唯一真相源：`paper_trading/strategy/general_playbook.py` 的 `GENERAL_RULES`；
- `general` 方案：`signal={short_window:20, long_window:60}`，`rules=GENERAL_RULES`；
- `SCHEME_TMPL` 通过 `{rules}` 注入 AI prompt（`_scheme_txt()`）；
- `config.yaml` / CLI / main / `utils.config` 的 fallback 同步 20/60；
- 默认是基线，可被 `custom` 覆盖；健康度仍 advisory，不自动拦买；
- 详细规则与边界见 `docs/default-strategy.md`。

## 7. 回归与回滚

- 单测：`tests/test_strategy_registry_t87.py`（正常/未知类/坏参数/重复名/
  禁用/自定义白名单/内置信号/Bridge 装配与留痕）；
  默认策略：`tests/test_general_strategy_t111.py`（方案/提示词/config 接线）；
- 全量 `pytest` 必须绿（除既存时窗失败）；
- 回滚：删掉 `strategy.local.yaml` 的 `strategies` 或把 `config.yaml` 的列表
  清空 ⇒ 回到内置默认（当前 MA20/60）；不改账本、不改执行底座。

## 8. 策略注册表 / 方案 / AI 风格 / 母策略（A7）

四层不是同一个东西，边界如下：

| 层 | 位置 | 谁决定 | 作用 |
|---|---|---|---|
| 策略注册表 | `config.yaml strategy.strategies` / `strategy.local.yaml` | 用户（本地文件） | 构造 `TradingBridge.strategy`，产出规则信号（MA/RSI/突破/自定义类） |
| 投资方案 scheme | `strategy/schemes.py` | 用户（`strategy.local.yaml active`） | `mother/general/custom` 三种打法；决定股票池、signal 参数、prompt 手册 |
| AI 风格 | prompt + `custom` 指令 + 每次决策的 thesis/reason | AI（在框架约束内） | 选股、择时、仓位、持有期；健康度/额度是输入，不是命令 |
| 母策略 | 母库 `screening_result.ai_trade_strategy` | 母项目 | 只读价值信号（signal/buy_zone/stop_loss）；`mother` 方案才消费 |

关系：

- `general` 方案的 `signal` 会覆盖 `strategy` 段的均线参数；注册表仍负责
  **构造实例**（默认 `ma_cross` 20/60）。两者冲突时以方案 signal 为准，
  并在启动留痕；
- `custom` 只替换 prompt 里的交易手册（`instruction`），不绕过注册表的
  信号构造、风控、额度、账本；
- 母策略不写进注册表：它是输入，不是子项目的执行策略；子项目只读，
  不反向写母库；
- AI 风格可以覆盖“买什么/何时买/拿多久”，但不能覆盖安全/账本/执行/数据口径；
- 多策略资金分配（A3）在注册表之上：`strategy.allocations` 用**策略实例名**
  给额度，方案切换不会隐式改权重；空配置=单方案旧行为。

### 8.1 多策略资金分配

```yaml
strategy:
  allocations:
    ma_cross: 0.6
    rsi_zone: 0.4
```

- 权重由用户/AI 显式配置，代码无默认偏好；和 ≤100%，否则启动失败；
- 额度 = `weight × NAV`，按**累计净买入成本**计；成交后累加，卖出按标的
  归属回冲（近似口径，CLI `strategy allocations` 明确标注）；
- 计划与执行两处都过闸：计划阶段裁掉超额买入并写 `actions_rejected`，
  执行阶段再校验一次；卖出/持有永不因额度被拒；
- 未配置 = 旧行为；配置后买入缺 `strategy` 字段直接拒（fail-closed）；
- 空/非法配置的行为：空=放行；非法=启动失败并写 `op_log[strategy:alloc]`；
- 默认策略对照：`strategy compare`（MA20/60 vs 旧 MA5/20，同一窗口/费用/风控，
  样本内外分段，**不是收益承诺**）。
