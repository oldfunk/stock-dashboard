# 母库接口接入 TODO（子项目侧）

> 目标：**母库优先、行情自有兜底、按接口逐个降级、每条数据带来源标注**。
> 原则：只读母库（`mode=ro`），永不写；母库缺失/不可用只影响该接口，不整体切换；
> 母库正在回填，覆盖不到的标的/日期自然落到本地或缺失，**绝不填 0、绝不阻断决策**。
> 边界：交易行情（日线/分钟/实时）最终仍可由子项目自有 akshare/腾讯管线独立提供；
> 母库行情作为优先来源，母库缺该标的时自动回退本地。

## P0 — 契约与来源标注（本轮）

- [x] `integration/sources.py`：来源常量 + `block()` 结构化块（`source/asof/stale/available/data`）
- [x] `integration/mother.py`：只读连接（`mode=ro`、短超时、query_only）+ 表/列探测 + 内存 TTL 缓存
- [x] 逐接口降级（母库 → 本地 → 缺失）：
  - [x] `daily_bars`：母库 `kline_daily` → 本地 `data.db.daily_bars`（akshare）
  - [x] `financial_summary`：母库 → 缺失
  - [x] `dividend`：母库 → 缺失
  - [x] `benchmark`：母库 → 缺失
  - [x] `news`：母库 `news_item` → 本地 akshare 快讯
  - [x] `screening_detail`：母库 `screening_result`（含 score_detail）→ 本地手填池
  - [x] `thesis`：母库 `watchlist_thesis` → 缺失
  - [x] `outcomes`：母库 `analysis_outcome`（覆盖度提示）→ 缺失
  - [x] `regime`：母库 `market_index` → 缺失
- [x] `tests/test_mother_contract.py`：合成母库（离线可跑）+ 真母库存在时校验表/列（CI skip）

## P1 — AI 上下文接入

- [x] `llm_context` 增加 `value_view` 富化字段（财务摘要/股息/筛选明细/追踪）
- [x] `llm_context` 增加 `data_provenance`（每个接口的 source/asof/stale）
- [ ] 结构压缩：长文本截断、只保留关键字段（不塞全文）
- [ ] 更新 `TRADER_SYSTEM`：要求每条 action 给出 `basis`（引用的数据字段名）

## P2 — AI 决策 schema 细化（向后兼容）

- [x] action 支持 `thesis / catalyst / risk_stop / confidence / expected_hold_days / position_size_reason`
- [x] `basis` 字段名校验（fail-closed：前缀必须是上下文真实存在的数据域）
- [ ] 显式止损条件优先（买入必给 `risk_stop` 或卖出条件）—— 待 opt-in 开关，避免打断现有输出
- [x] 事件复盘复用同一富化上下文（`review_conditions` 走 `llm_context`，自动带富化+provenance）

## P3 — 上下文压缩技术

- [x] `agent/context.py`：预算感知装配（token 估算 + 分级截断）
- [x] 富化块按决策相关度截断（长文本→财务摘要白名单→股息/追踪条数→收盘根数）
- [x] 保留 provenance 元数据不被截断
- [x] `llm_context` 接入（`agent.context_budget`，默认 8000，超预算才压缩）

## P4 — 文档（供母项目对齐）

- [x] `docs/mother-interface.md`：接口契约表、来源标注、降级规则、性能约束
- [x] `docs/ROADMAP.md` / `AGENTS.md` 同步

## 性能约束（Pi 实机）

- 母库正在回填：所有母库访问只读、按 code 精确查询（已有索引）、内存 TTL 缓存、短超时。
- 不新增常驻进程、不触发母库回填、不 `SELECT *`、不扫全表（除 <10 万行的小表）。
