# 纸盘桥（Paper Bridge）设计（2026-11-21）

> 上游：用户方向——子项目不再是"外挂程序"，而是价值投资体系里的**第二个数据域**；
> 子项目正在加的**分时/盘中数据**也要进入联合分析。
> 现状：母→子已有只读桥（`paper_trading/integration/mother.py`）；**子→母没有**。
> 本文定义反向桥、契约、失败语义与分阶段落地；不改子项目代码，不改 :8080。

## 1. 两个数据域，各自拥有

| 域 | 拥有（可写） | 只读消费 |
|---|---|---|
| 母项目 | 行情/财务/筛选/估值/研究结论/分析历史 | 纸盘账户、持仓、计划、成交、NAV、分钟线 |
| 子项目 | 账户/委托/成交/NAV/计划/决策/分钟线 | 母库筛选、快照、财务摘要、论点、交易日历 |

原则：**单向只读，不互写**。母项目永远不写 `paper_account.db` / `data.db`；
子项目已有 `mode=ro` 的母库只读通道。桥是两个只读方向的对称。

## 2. 通信方式：同机 SQLite 只读直连（推荐），HTTP 作为跨机后手

| 方案 | 优点 | 代价 | 结论 |
|---|---|---|---|
| **A. SQLite `mode=ro` 直连** | 同机零延迟、无新服务、与现有 mother 集成同构、WAL 下只读安全 | schema 耦合；要处理锁/实例路径 | **现在用** |
| B. HTTP 只读 API（复用 :8081 `/api/*`） | 解耦、跨机可用 | 多一个可用性依赖、要轮询/超时、面板挂了桥就断 | 跨机时再上 |
| C. 消息队列/共享文件 | 事件驱动 | 过度设计，当前日频 | 不做 |

**为什么 A 够用**：两个服务本来就在同一台 nec 上；子项目已经是"母库只读 + 失败降级"的
成熟模式，反向照抄即可。分钟线读取是短查询（按 symbol+日期），不会拖住面板。

### 2.1 只读连接纪律（缺一不可）

```python
sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
conn.execute("PRAGMA query_only=1")
```

- 只读 URI + `query_only`：物理上杜绝误写；
- `timeout` 短 + 失败降级：桥不能拖住 15:30 流水线；
- **实例路径显式配置**：只认合并树 `paper_trading/{data,paper_account}.db`；
  绝不自动发现 `:8080` 独立版（用户约定：不碰）；
- 每次返回都带 `source / asof / stale`（与 `integration/mother.py` 的 block 语义一致）。

## 3. 契约 v1（表 + 关键列 + 语义）

> 以 `PRAGMA table_info` 做**列存在性校验**（子项目没有可靠的 schema 版本表；
> `__version__` 也滞后）。缺列 → 该项 `unavailable`，并只告警一次，不抛异常。

| 表 | 关键列 | 语义/单位 |
|---|---|---|
| `account` | `cash`, `initial_cash` | 现金/初始资金（元） |
| `positions` | `symbol,total_volume,available_volume,avg_cost,peak_price` | `available` 已含 T+1 冻结 |
| `orders` | `order_id,symbol,direction,volume,status,filled_price,commission,stamp_duty,transfer_fee,created_at,filled_at` | `direction=1` 买 / `-1` 卖 |
| `fills` | `fill_id,order_id,symbol,direction,volume,price,commission,...` | 成交明细 |
| `nav_history` | `timestamp,cash,market_value,total_value,pnl,pnl_pct` | 收盘 NAV（当前有断档，见 issue #7） |
| `agent_plans` | `id,plan_date,symbols,plan_json,status,created_at,executed_at` | `status: pending/done/superseded` |
| `agent_decisions` | `decision_date,actions_json,result_json,created_at` | 决策审计（含拒因） |
| `minute_bars` | `symbol,ts,open,high,low,close,volume,amount,period,adjust` | **5 分钟**，保留约 25 天；仅用于执行质量/盘中展示 |
| `daily_bars` | `symbol,timestamp,open,high,low,close,volume,adjust` | 子项目本地日线（前复权） |
| `stock_pool` | `symbol,name,added_at` | 子项目本地池（非母筛选） |

**分时数据的定位**：`minute_bars` 是**执行质量**数据（成交价 vs 当分钟 VWAP、滑点、
盘中回撤），**不进母项目筛选/估值**，也不写回 `kline_daily`（域不同、保留期不同）。

## 4. 母侧接口设计

新增模块 `src/paper_bridge/`（母仓，纯只读）：

```text
src/paper_bridge/
  settings.py   # 路径解析：config.paper.bridge.{instance,data_db,account_db}
  conn.py       # mode=ro + query_only + timeout + 降级
  contract.py   # 必需列校验（缺列 -> unavailable）
  state.py      # account/positions/plans/fills/nav/minute
  api.py        # /api/paper/status|positions|plans|fills|nav|minute
```

- 路径默认 `paper_trading/data.db` / `paper_trading/paper_account.db`（合并树）；
  `instance=standalone` 也**不**自动指向 :8080，必须显式写绝对路径；
- 失败语义：库不存在/被锁/缺列 → `{"source":"unavailable","reason":...}`，
  调用方（面板/分析）按"无纸盘上下文"继续，绝不阻断主流程；
- 契约测试：`tests/paper_bridge/test_contract.py` 用**夹具库**（复制 schema）跑，
  gate 不依赖生产库；另有一条"生产库只读冒烟"只在人工验收时跑。

## 5. 联合分析（"合并进价值体系"的具体落点）

1. **分析上下文**：母项目在个股/候选分析时，带上"纸盘是否持有 / 当前计划 / 上次决策"
   （只读、标注 asof），让 AI 知道"我们自己的钱怎么看这只票"，但不作为买卖指令。
2. **策略口径**：纸盘继续用母策略（signal/buy_zone/stop，2026-11-19 已接），
   桥只补充"结果侧"数据，不反向影响母筛选。
3. **统一绩效**：纸盘 NAV vs 沪深300/中证红利；`analysis_outcome` 的 AI 信号收益
   与纸盘实际成交并排——回答"模型说的和真做的是否一致"。
4. **决策质量**：fills → 前向收益 → 听话率（母信号 vs 实际动作）、拒单、滑点。
5. **反馈闭环**：研究结论（H-xxx）→ 纸盘策略参数，必须经过纸盘验证后才回写筛选；
   在验证前**禁止**让纸盘数据影响母筛选。

## 6. 分阶段与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| **E0**（1–2 天） | `settings/conn/contract` + 夹具测试 | 缺库/缺列/锁 → unavailable；gate 通过；无写操作 |
| **E1**（3–5 天） | 母侧读账户/持仓/计划/成交/NAV；`/api/paper/status`；首页"纸盘"卡 | 数字与 :8081 一致；asof/stale 正确；面板挂了不影响母站 |
| **E2**（2–3 天） | 分钟线只读 + 执行质量周报（成交价 vs VWAP/滑点） | 抽样 5 笔复算一致；不参与筛选 |
| **E3**（3–5 天） | 统一绩效页/脚本（NAV vs 基准、决策质量） | 周报一张表；口径可复算 |
| **E4**（1–2 周） | 分析上下文接入纸盘状态；研究→纸盘参数试点 | AI 分析里能看到持仓/计划；无越权写入 |

## 7. 失败与回滚

- 桥失败只降级，不阻断：母站 15:30 流水线、AI 分析、纸盘 cron 均不依赖桥；
- schema 漂移：契约测试红 + 母侧 `unavailable`，提 issue 给子项目；
- 回滚：删 API/UI 与 `src/paper_bridge/` 即可，不涉及数据迁移；
- 安全：只读、无 Key、无写路径；:8080 不被扫描/连接。

## 8. 明确不做

- 母项目不写子项目任何表；子项目不写母库（维持现状）；
- 不把分钟线并入 `kline_daily`；不做双写/消息队列；
- 不在母站复制交易面板（下单仍只在 :8081），母站只做摘要与跨域分析。
