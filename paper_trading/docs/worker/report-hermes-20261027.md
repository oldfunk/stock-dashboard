# Hermes 独立审查报告（2026-10-27，只读，未改代码）

> 审查范围：执行链/风控/条件单/数据源/母库集成。未执行任何写账本命令，未 checkout/merge，未触碰 worker 分支。
> 基线：`pytest` 全绿 306 passed / 1 skipped；`wiring_audit.py` 27/27/27 通过。以下问题均**不在 R0–R9 已修清单**内，属新增疑点，按"疑罪从有"列出，待主 AI/员工 AI 复核。

## P0（数据/风控 correctness）

### H-1 尺度门禁在缺基准时形同虚设
- 位置：`paper_trading/agent/loop.py:1048-1061`
- 证据：`_ref` 为 0 时 `_dev = 0.0`（代码显式 `if ... else 0.0`），随后 `_dev > threshold` 必假。`all_bars` 缺失/空时 `_ref` 退化为 0，门禁直接跳过；但 `px` 可能仍来自 `latest`/计划价，继续下单。
- 风险：事故类"无 K 线/缺 bars"场景下，唯一跨时点基准校验被静默关闭，回到"同源自洽"盲区。
- 建议：`_ref <= 0` 时应视为无基准 → 拒单（或 `_dev = inf`），并把"无基准价"作为独立拒绝分支，放在价格校验之前。

### H-2 `_execute_actions` 静默丢弃超出切片的动作
- 位置：`paper_trading/agent/loop.py:1002`
- 证据：`for a in actions[: self.cfg.max_orders_per_run + 5]`，超出部分既不执行也不写入 `decided`，最终 `ai:decide` 流水看不到这些条目；LLM 输出 10 条动作时后 2 条"消失"。
- 风险：审计链不完整，无法复盘"为什么没做"；与 R3 "可审计"承诺冲突。
- 建议：超出部分也应落 `decided` 并标 `拒绝：超出审计切片/笔数上限`，或至少在日志里计数。

## P1（执行链/条件单）

### H-3 计划执行 `allow_buy` 不含持仓标的，持仓股买入被误拒但卖出条件可能漏挂
- 位置：`paper_trading/agent/loop.py:956-976`（`_execute_plan`）、`_execute_actions` 内 `allow_buy = set(symbols)`（~998）
- 证据：`_execute_plan` 的 `syms` 来自 `pending["symbols"]`（计划原池），虽把持仓并入 `all_bars/latest/positions`，但传给 `_execute_actions` 的 `symbols=syms` 未合并持仓。`allow_buy` 因此不含持仓标的；若 LLM 对持仓股给出 buy/add，会被"不在股票池"拒单；而 conditions 侧 `_arm_conditions` 的 `held_now` 用的是实时持仓，两侧口径不一致。
- 风险：计划里"对已有持仓加仓/补仓"类动作永远失败且无明确原因。
- 建议：`_execute_plan` 传 `symbols = list(set(syms) | set(positions))`，与 `_execute_actions` 的 `allow_sell` 对齐；或 `_execute_actions` 内 `allow_buy |= set(positions)`。

### H-4 条件单尺度闸在 `prices` 为空时退化为不校验
- 位置：`paper_trading/engine/ai_conditions.py`（`parse_conditions_verbose` 的 `prices` 参数路径）+ 调用点 `loop.py:490-496` / `_conditions_from_raw`
- 证据：`review_conditions` 从 `rt.get_quotes` 取价，失败时 `quotes={}`，`prices={}`；`_conditions_from_raw` 把空 dict 透传。空 prices 下 price_* 条件值无带闸。
- 风险：行情接口抖动时，反而放开条件单尺度校验，与"行情失败不阻断"的本意冲突（应 fail-closed 对尺度）。
- 建议：prices 为空时，price_* 条件应进入 `parse_rejected`（"无现价，无法校验尺度"），而不是放行。

### H-5 `_execute_plan` 无条件 `mark_plan_done`，即使 `_execute_actions` 抛异常
- 位置：`paper_trading/agent/loop.py:977`
- 证据：`mark_plan_done` 在 `return res` 之前无 try/except；若 `_execute_actions` 抛未捕获异常，计划仍被标 done（因为异常发生在 return 之前？实际上 mark 在 res 赋值之后、return 之前；异常若在 `_execute_actions` 内抛，mark 不执行。但 `_execute_actions` 内部 broad except 吞掉多数错误，且 broker/DB 异常可能部分提交）。更关键：即使部分动作失败，计划仍标 done，无法重试。
- 风险：半失败计划无法幂等重跑；与"失败可重试"预期不符。
- 建议：仅在 `_execute_actions` 返回 `ok=True` 时 mark_done；失败时保留 pending 并写 `ai:decide` 拒因，或增加 `status='partial'`。

## P2（数据/缓存/边界）

### H-6 `daily_bars` 尺度闸拒绝后 `adj`/`source` 状态残留 + 本地回退日期顺序
- 位置：`paper_trading/integration/mother.py:451-488`
- 证据：拒绝分支 `bars, source, asof, adj = [], ...`；随后 `if not bars and local_bars is not None` 回退本地，但 `bars` 从本地 Bar 转 norm 后未重新 sort；母库分支有 `bars.sort`（source.py）但 daily_bars 本身不 sort。本地 `get_bars` 返回顺序需调用方保证升序；若降序，`asof = bars[0]["date"]` 取到最早日期，stale 判定失真。
- 风险：stale 标记与 asof 错误。
- 建议：本地回退分支也按 date 升序 sort，并取 `bars[-1]` 为 asof（或统一 `_norm_local` 后 sort）。

### H-7 `_scale_guard` 只比最近共有日，且 `local_bars` 为 None 时闸完全旁路
- 位置：`paper_trading/integration/mother.py:351-378` + `daily_bars:444-456`
- 证据：`if bars and local_bars is not None` 才跑闸；母库独有标的（本地无）直接放行，无法对照；且只取首个共有日（母库倒序首个命中），未做多日交叉验证。
- 风险：母库新增标的的尺度污染无法被闸拦截（文档已承认"只能信 provenance"），但无任何兜底量级 sanity（如价格 ≤0 或 >10 万）。
- 建议：增加绝对量级 sanity（价格必须在合理区间，如 0<price<100000），作为无对照时的最低防线；多日比对可提升闸敏感度。

### H-8 `financial_summary` SQL 列名拼接虽用引号，但 `_columns` 缓存跨库可能串（TTL 兜底）
- 位置：`paper_trading/integration/mother.py:246-261` + `522`
- 证据：`_COLUMNS` 键含 `_dbkey(conn)`；`_dbkey` 依赖 `PRAGMA database_list` 的路径。若同一路径被 ATTACH/替换，缓存旧列；TTL 60-300s 兜底。R4.2 已修主要问题，残余为"schema 变更窗口内 SELECT 可能缺列"。
- 风险：低；仅在母库 schema 热变更窗口。
- 建议：列缓存 TTL 独立于数据缓存，或 schema hash 变更时失效。

## 已确认非问题（避免重复劳动）

- `wiring_audit.py` 27/27/27 通过；R0-R9 清单全部勾选，基线 306 passed。
- `paper_broker.submit_order` 涨跌停/高低价检查同源自洽但已由 R1.3 + v0.7.5 门禁覆盖，不重复提。
- `_execute_actions` 的 `actions[:max_orders+5]` 切片本身有注释，仅 H-2 的审计缺口为新问题。

## 建议下一步（不改代码，供主 AI 拍板）

1. 先复核 H-1/H-3/H-4（均涉及 fail-closed 语义），决定是否开 `worker/h-*` 分支修。
2. H-6/H-7 为数据层，与母库契约相关，建议对照 `docs/mother-interface.md` §6 后再动。
3. 本报告不修改任何产品代码/配置；未 `git add`。
