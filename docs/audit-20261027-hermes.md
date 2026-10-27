# Hermes 审查报告（2026-10-27）

> 创建：2026-10-27 · 审查方：Hermes · 范围：只读静态审查，未修改代码，未跑 pytest（本机不跑 pytest，遵循 AGENTS.md）· 分支：nightly/20261023

## 结论

工程化程度高，但存在契约强制性、点时一致性（PIT）、写路径语义上的真实缺陷，不是风格问题。开发 AI 近期 F1/F3 修复把写路径补齐，却在“契约强制性 / PIT 数据 / 静默吞错”上留口子。请按 P0→P1→P2 逐项修并补单测，更新 iteration-log。

## P0（必须修）

### P0-1 `KlineDAO.upsert_many` close_qfq 契约不强制
- 位置：`src/models/database.py:1470-1472`、`:1496`
- 证据：`qfq = r.get('close_qfq') or r.get('close')`；冲突更新 `COALESCE(excluded.close_qfq, excluded.close, close_qfq)`。
- 问题：调用方可显式传与 close 不同的 close_qfq，直接违反 §9.1；新 close 为 NULL 时 close_qfq 保留旧值，`close` 变 NULL，契约发散。
- 修法：强制 `close_qfq = excluded.close`（或 upsert 时 `close_qfq = r.get('close')`），NULL close 场景 skip/fail，不留旧值。

### P0-2 市场状态向量 `gross_margin_median` 前视偏差
- 位置：`src/research/market_state.py:209-217`
- 证据：查询 financial_history 全表取每只股票 MAX(report_date) 的 gross_margin，未按 trade_date 截断，也未用 notice_date 做披露时点。
- 问题：历史 trade_date 回算会把未来年报毛利率灌进过去，破坏 PIT。
- 修法：加 `report_date <= trade_date`（或 notice_date <= trade_date）过滤。

### P0-3 `stock_snapshot` 覆盖写，无法支撑历史状态向量
- 位置：`src/models/database.py:660`（INSERT OR REPLACE）；`src/research/market_state.py:189-208`
- 证据：snapshot 表只保留每个 code 最新一行；`MAX(snapshot_date) <= trade_date` 对过去日期通常返回 NULL 或错误“最新”。
- 问题：市场状态历史回算在数据层不成立。
- 修法：要么 snapshot 表按 `(code, snapshot_date)` 版本化，要么明确只支持“当日状态”，别在接口/文档里暗示历史可算。修前与用户确认 schema 方案。

## P1（应修）

### P1-1 market/instrument_id 推导静默跳过
- 位置：`src/models/database.py:1473-1478`
- 证据：`if iid is None and len(code) == 6 and code.isdigit()`；带前缀（如 sh600000）或非 6 位代码直接跳过，无日志。
- 修法：至少 warning；或提供显式 market 校验，别把“不臆造”当“静默吞错”。

### P1-2 f-string SQL 无白名单/转义
- 位置：`src/models/database.py:560-567`；`src/research/parquet.py:100-132`；`src/research/snapshot.py:105-110`
- 证据：`PRAGMA table_info({table})`、`CREATE VIEW {table}`、`read_parquet('{_posix(dest)}')` 直接插值。
- 修法：table 名正则白名单 `^[A-Za-z_][A-Za-z0-9_]*$`；path 做引号转义。manifest 可被篡改时是注入入口。

### P1-3 裸 `except:`
- 位置：`src/notifications/discord_notifier.py:166`
- 修法：改为 `except Exception:`，别吞 KeyboardInterrupt/SystemExit。

### P1-4 数值精度策略不一致
- 位置：`src/utils.py:33-41`；`src/research/market_state.py:288-289`
- 证据：`safe_float` 统一 round 2 位，但波动率 round 4 位、avg_turnover 再 /1e8。
- 修法：按字段指定精度，避免回测/状态向量微小差异被抹平。

## P2（改进）

### P2-1 跨包 import scripts
- 位置：`src/scheduler.py:348`
- 证据：`from scripts.fix_price_contract import backfill_trading_calendar`，失败被 catch 成 warning，交易日历维护静默失效。
- 修法：下沉到 src/ 或把 scripts 加入 path。

### P2-2 refresh_benchmarks 对 code 前缀无规范化
- 位置：`src/outcome_service.py:52-56`
- 证据：`targets[b["code"]] = b["code"]`，config 用 sh000300（带前缀）；若用户配 000300 会失败且无明确错误。
- 修法：加前缀规范化 + 返回非空校验。

### P2-3 compute_vector 多次 db_conn
- 位置：`src/research/market_state.py:190,227,242,274`
- 修法：单连接内完成，降低 pi1 环境开销。

## 备注

- 本报告只审查，未改仓库。修 P0-2/P0-3 前请与用户确认 snapshot 是否版本化。
- 验证声明：以上均为静态阅读结论，未执行命令验证；开发 AI 修完后应跑 `bash scripts/gate.sh` 并更新 `docs/iteration-log.md`。