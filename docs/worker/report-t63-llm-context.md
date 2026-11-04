# T63 · llm_context 瘦身（2026-11-03，Qoder）

派发原文（Batch 14）：

> **T63（Qoder，P1·性能）llm_context 瘦身**：现网 `llm_context()` = 97,285 字符（≈3 万 token）……
> 目标：**问答典型响应 ≤20 秒**。分级压缩设计（账户/持仓/规则/熔断态必带；
> 日线 bar 只带持仓 + 池内 Top N…；母库附加段按相关性或条数上限截断）；
> `llm_ask` 加 `symbols`/精简参数；请求体 token 量进日志（可观测）；
> `report-t63.md`：压缩前后体积/延迟对照；测试锁定（体积上限断言 + 压缩不丢关键字段）

一句话结论：**体积压下来了（上下文 3.5× 小、请求体 4.1× 小），但"≤20 秒"不是靠
上下文瘦身能达到的**——现网实测显示响应时间跟着 `max_tokens` **上限**走，
不跟着上下文和真实输出走。两件事分开做：瘦身照做并做成可保证的；
问答的输出预算单独收到 `ASK_MAX_TOKENS=1536`，并如实报告"要到 20 秒还需要什么"。

---

## 1. 逐条核销

| 派发点 | 状态 | 落在哪 / 证据 |
| --- | --- | --- |
| 分级压缩：账户/持仓/规则/熔断态**必带** | ✅（含补） | `paper_trading/agent/context.py` 新阶梯；**熔断态此前根本不在上下文里** ⇒ 新增 `risk_control` 段（`RiskManager.describe()` 只读版）。这就是 11-02 那次"为什么没交易"没人能答的根因：模型看不见熔断，只能猜 |
| 日线 bar 只带持仓 + 池内 Top N | ✅ | `_HELD_CLOSES=5 / _POOL_CLOSES=1`；问答口径（`slim=True`）装配时就只要 1 根，压缩器再兜一层 |
| 母库附加段按条数/相关性截断 | ✅ | 池内候选的 `dividend/outcomes/notes/thesis` 整段丢弃、`financial_summary` 收窄到 3 键、`ai_analysis` 截 160 字；持仓走 HELD 配额（股息 3 / 追踪 3 / 分析 400）；`screening` 8 行 + 理由 100 字 + 去 `score_detail`；`news` 10 条 + 摘要 80 字 |
| `llm_ask` 加 `symbols`/精简参数 | ✅ | `llm_ask(prompt, system, *, symbols=None, slim=True, max_tokens=None)`；CLI `--symbols/--full/--max-tokens`、面板 body `symbols/slim/max_tokens` 同入口 |
| 请求体 token 量进日志（可观测） | ✅ | `op_log[llm:ask]` 的 `params` 加 `req_chars/req_tokens/req_max_tokens/slim/symbols`，`result.context` 落压缩元信息（前后体积/裁了什么/是否进预算）；另有一行 `logger.info` |
| `report-t63.md` 压缩前后体积/延迟对照 | ✅ | 本文 §2、§4 |
| 测试锁定（体积上限断言 + 不丢关键字段） | ✅ 12 例 | `tests/test_context.py` 重写 |

## 2. 现网体积构成（这是设计的输入，不是事后包装）

`/home/nec/paper-trading` 只读测量（`data.db` 真库 + 账本副本），2026-11-03 傍晚：

| 段 | 字符 | 说明 |
| --- | --- | --- |
| `value_view.stock_cards`（40 只） | 61,120（旧压缩后口径） | 其中**股息史 26,850**、追踪结果 11,590、财务摘要 7,997、基本面 6,591、AI 分析 4,361、论点 423 |
| `recent_closes` | 14,677 | 40 只 × 5 根 |
| `value_view.screening` | 12,246 | 每行带 1000 字的 `reason` + `score_detail` |
| `news.items` | 6,466 | 24 条，每条 summary + ai_brief |
| 其余（账户/持仓/流水/净值/legend/provenance/portfolio） | ~2,300 | 问答真正需要的部分 |
| **原始装配** | **253,677 ≈ 68,463 估算 token** | 派发里的 97,285 是**旧压缩之后**的输出 |

**旧压缩做了什么、没做什么**（同一份原始装配逐预算喂给它，独立进程）：

```
预算 8000 → 97,910 字符 / 28,165 token
预算 4000 → 97,910 字符 / 28,165 token
预算 2000 → 97,910 字符 / 28,165 token
预算 1000 → 97,910 字符 / 28,165 token
```

它确实裁了一刀（253,677 → 97,910），但**裁到哪与预算无关**，超预算 3.5 倍也不说。
（我中途误判成"完全空操作"，那是把已压缩的输出又喂回压缩器测的；已在 T64 报告 §3 记下这条自我更正。）
所以 `context_budget` 的真相不是"没接线"，是"接线了但保证不了它承诺的语义"。

## 3. 新分级（保证是"要么达标，要么说清楚"）

`shrink_to_budget(ctx, budget)` 返回 `(ctx, meta)`，`meta` 里
`before/after_chars`、`before/after_tokens`、`caps`（各段被裁多少）、`dropped`（整块丢了什么）、
`within_budget`、`reason`。阶梯按实测分布排：

1. 池内候选的长史字段先走（股息史/追踪/notes/论点整段丢，AI 分析截 160）；
2. 收盘：持仓 5 根、池内 1 根；筛选 8 行；要闻 10 条；流水/净值 3 条；
3. 仍超 ⇒ 逐级丢整块：`pool_fundamentals → news → screening → pool_cards`；
4. 到地板（账户/持仓/熔断态/provenance/legend + 持仓卡细节 + 收盘 + 流水）就停，
   **如实报 `within_budget=False`**。地板在现网是 5,549 估算 token——再压要动的东西
   比"超预算"本身更贵（持仓细节与 provenance 是模型不瞎猜的前提）。

同一份原始装配喂新压缩：

| 预算 | 输出字符 | 估算 token | within_budget | 丢了什么 |
| --- | --- | --- | --- | --- |
| 8000 | 24,374 | 7,979 | ✅ | `pool_fundamentals` |
| 4000 | 18,257 | 5,549 | ❌（如实报） | pool_fundamentals/news/screening/pool_cards |
| 2000/1000 | 18,257 | 5,549 | ❌（同上，稳定在地板） | 同上 |

现网问答口径（`slim=True`，budget=8000）实测：装配前 240,245 字符 ≈ 64,988 token
⇒ 装配后 **22,554 字符 ≈ 7,564 token**，`within_budget=True`。

## 4. 延迟：瘦身不是主因（现网实测，同问题同端点同 120s 超时）

| 腿 | 请求体 | 估算 token | `max_tokens` | 真实 usage | 耗时 | 答案 |
| --- | --- | --- | --- | --- | --- | --- |
| 改前口径 | 99,668 字符 | 28,919 | 4096（`LLMConfig` 默认） | — | **82.4s** | 1099 字 |
| 改后口径 | 24,312 字符 | 8,318 | 8192（继承决策） | prompt 11,440 / completion 673 | **74.9s** | 1065 字 |
| 改后口径 | 同上 | 同上 | 1200 | prompt 11,440 / completion 642 | **28.3s** | 1022 字（`finish=stop`，未截断） |
| 端到端 `llm_ask`（`ASK_MAX_TOKENS=1536`） | 27,216 字符 | 8,318 | 1536 | — | **45.5s** | 985 字 |

三件事被数据分开：

1. **上下文瘦身有效但不是主因**：请求体小 4.1×、估算 token 少 3.5×，耗时只从 82.4s 到 74.9s
   （同一腿还把 max_tokens 从 4096 抬到 8192，抵消了一部分）。
2. **这个端点的响应时间跟着 `max_tokens` 上限走**：真实输出两次都约 650 token、
   答案都 1000+ 字且 `finish_reason=stop`，而 8192→74.9s、1200→28.3s。
   `thinking=disabled` 已生效（`reasoning_content` 0 字），排除"思考吃掉预算"这一种解释。
3. 所以 **≤20 秒要么把问答 `max_tokens` 压到 ~1000 以下**（风险：长答案被截断，
   而当前 `finish_reason=length` 不会告知用户），**要么换更快的端点/模型**。
   我按"不悄悄牺牲答案完整度"的原则取了 1536（实测 28.3~45.5s 区间），
   并把截断信号列为待拍板项（questions.md）。

顺带一个口径澄清：`context_budget: 8000` 是**估算 token**（CJK 1 字/token、其余 4 字/token）。
现网对照下来，`estimate_tokens` 比 GLM 真实 tokenizer **低约 1.4×**
（我估 8,318，真实 prompt 11,440）。预算含义是"估算 token"，代码与本报告都这么写；
要按真实 token 定价得从响应 `usage` 回填，那是另一个 knob（我没动 config.yaml）。

## 5. 测试锁定（12 例，`tests/test_context.py` 重写）

改测试的理由写在文件头：**旧用例的假数据把体积大头放在会被裁的字段上，与真实分布相反**，
所以它全绿而现网压不动。新用例锁的是：

- `test_compression_actually_reaches_the_budget_and_stops_at_the_floor`
  —— 预算不同 ⇒ 输出体积必须不同（旧实现四种预算输出一模一样）；到地板后稳定且不谎报；
- `test_pool_long_history_goes_first_held_detail_stays` —— 池内丢股息史/论点，持仓按 HELD 配额留；
- `test_provenance_account_positions_and_risk_control_are_never_cut` —— 压到极限也不裁这四段；
- `test_under_budget_returns_the_same_object_not_a_copy` —— 未超预算返回原对象（决策路径不复制大 dict）；
- `test_llm_context_uses_budget_and_reports_meta` / `test_llm_context_compression_failure_is_loud_not_silent`
  —— knob 真能改变体积；压缩器炸了必须 warn + 留 `reason`，不再 `except: pass`；
- `test_ask_trail_records_request_volume_and_compression` / `test_ask_slim_and_symbols_reach_llm_context`
  —— 可观测与参数直通（在 T64 测试文件里）。

## 6. 现网验证方式与边界

- 全部测量在 **只读**路径 + **账本副本**（`/home/nec/tmp/t63-acc/paper_account.db`）上做；
  生产 `paper_account.db` md5 前后同值（`835d5ff8…`）。
- 真实 LLM 请求共发了 5 次（归因需要 usage）。走的是 `provider.chat/chat_with_retry`
  **直接调用**，不经 `llm_ask` ⇒ **没有写生产 op_log**；用云端 Key 花额度这件事在此如实报备，
  依据是派发点要求"压缩前后体积/延迟对照"。**未改 config.yaml、未动 cron、未部署**
  （生产树仍在 main `475f994`，本分支未合并）。
- 2×2 归因（大/小上下文 × 1200/600）跑到一半 SSH 连接被重置、进程随之丢失，
  所以 §4 表格里"大上下文 + 小预算"那一格没测到；结论不依赖它（现有三点已能分离两个变量），
  但补齐只需重跑 `/home/nec/tmp/t63_grid.py`。

## 7. 交付物

`paper_trading/agent/context.py`（重写分级 + `shrink_to_budget` 元信息）、
`paper_trading/cli.py`（`llm_context` 熔断态段与压缩元信息、`llm_ask` 新参数、`ASK_MAX_TOKENS`、
CLI/面板参数、`minute sync --now`）、`paper_trading/risk/risk_manager.py`（`describe()` 只读）、
`tests/test_context.py`（12 例）。全量 **701 passed / 9 skipped**。
