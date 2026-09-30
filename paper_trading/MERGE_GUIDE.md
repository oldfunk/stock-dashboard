# 合并指南（MERGE GUIDE）：paper-trading → stock-dashboard

> 本仓库保持独立可运行；以下为合并时的执行契约。
> 原则：**只读 Stock Dashboard 库、不写一字；合并工作流用母项目的规矩。**
> 注：普通用户本地使用可忽略本文档；它只面向下游 Stock Dashboard 的 subtree 集成。

## 1. 放哪里（零改 import 方案）

母仓库顶层新建 `paper_trading/` 目录（与 `src/` 平级，不要放 `src/paper/`），
本仓库除 `.git/` 外整体迁入。因为全仓 import 统一为 `paper_trading.*`，
从母仓库根运行 `python -m paper_trading.cli …` **零改动即跑**。
没有第二个方案——改包名意味着全仓重写 import，不接受。

## 2. 表契约（三张表，红线清晰）

| 方向 | 表 | 说明 |
|---|---|---|
| 拥有（读写自家文件） | `paper_account.db`: account/positions/orders/fills/nav_history/t1_freeze/op_log/agent_plans；`data.db`: daily_bars/stock_pool | 与母库 `paper_*` 旧表**同名不同库**，互不干扰；母库旧 M4a 表一个不动 |
| 只读母库 | `screening_result`（最新 active 候选）、`ai_watchlist`+`watchlist`、`stock_analysis_history`、`watchlist_notes`、`watchlist_thesis`、`stock_snapshot`、`market_index` | 一律 SQLite URI `mode=ro`；任一缺失/异常→回退空结果，永不抛错阻断交易链（`paper_trading/integration/` 全覆盖单测） |
| 永不碰 | 母库其他所有表 | 写操作一律禁止；审计时 `grep -rn "INSERT\|UPDATE\|DELETE" paper_trading/integration/` 必须零命中母库路径 |

## 3. 配置合并

本 `config.yaml` 的 `trading/account/strategy/risk/agent` 五段整体并入母 `config/config.yaml` 的
`paper_trading:` 命名空间下；`load_config` 加一个 `namespace="paper_trading"` 参数即可
（改动点唯一，约 10 行）。`stock_pool` 保留手填；新增 `--pool-from` 已支持
`watchlist/screening/all`（只读母库，见 `integration/pool.py`）。

## 4. 密钥

本 `secrets.local.json`（0600）与母 `.env/local.yaml` 互不干扰，各管各的 Key。
 dashboard 口令机制不变。合并后二选一统一，此前保持隔离。

## 5. 运行时共存

- venv 独立保留（依赖漂移风险：akshare/pandas 与母 FastAPI/pydantic/httpx 锁不同版本，先各跑各的，稳定后再谈统一）。
- 端口：面板 8080，母面板 9527，互不冲突；Phase 1 在母导航加一个外链即可，不迁 Jinja2。
- 定时：母内置 scheduler 不动；本 `agent run` cron 保留（单交易员原则：同一账户同一天只跑一边）。

## 6. 合并执行步骤（执行人照做）

1. 母仓库开 `nightly/paper-trading-merge` 分支（遵守母 AGENTS.md 工作流）。
2. 本仓库整体迁入顶层 `paper_trading/`（`git subtree` 或文件拷贝，保留 `.gitignore` 的 `secrets.local.json` 豁免）。
3. `config.yaml` 五段并入命名空间；`--pool-from` 默认仍 `config`（行为零变化）。
4. 生产跑母方 `scripts/gate.sh`（母仓库脚本）：本 44 单测必须全绿（hermetic：假路径+monkeypatch，不读 ambient）。
5. 冒烟：`agent run --dry-run` + 面板三路 200 + `llm ask` 一次。
6. 合 main + handoff 入账（按母规矩）。

## 7. 回滚

删顶层 `paper_trading/` 目录即回滚（母库零写入，无残留；本仓库独立继续跑）。
