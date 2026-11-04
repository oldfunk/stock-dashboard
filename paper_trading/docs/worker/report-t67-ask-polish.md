# T67 · 问答体验：截断可见 + 配置面 + 真实 token + CI 门禁（2026-11-03，Qoder）

派发原文（Batch 16）与批复出处：

> **T67（Qoder，P1·问答体验）**：① `finish_reason=length` 必须在回答里显式标注
> （面板/CLI 都可见；静默截断=纪律问题）；② `agent.ask_max_tokens` 配置键
> （config.yaml + 读取链 + wiring 测试；缺省 1536 不变）；③ `context_budget` 真实 token：
> 透出 `usage.prompt_tokens`，留痕同时记估算与真实；④ `wiring_audit` 进
> `.github/workflows/test.yml` 独立步骤；⑤ `report-t67.md`

四条都落地了，一条都没走"加个字段就交差"的路——每条都配了行为级断言。

---

## 1. 逐条核销

| 派发点 | 状态 | 落在哪 / 证据 |
| --- | --- | --- |
| ① 截断必须可见 | ✅ | `provider.LLMReply(str)` 带 `finish_reason/truncated/usage`；`llm_ask` 把 **⚠️ 标记直接追加进答案文本**（面板只渲染 answer，结构化字段给程序），同时返回 `truncated/notice` 并把 `truncated/finish_reason/usage` 写进 `op_log[llm:ask].result` |
| ② `agent.ask_max_tokens` 配置键 | ✅ 三件套 | `AgentConfig.ask_max_tokens=1536` + `utils/config.load_config()` 白名单 + `cli` 构造点（`max(128, …)` 防 0 值把问答打死）+ `config.yaml` 新键；按次覆盖（CLI `--max-tokens`、面板 body `max_tokens`）优先于配置；缺省语义不变 |
| ③ 真实 token | ✅ | `usage.prompt_tokens` 不再被 `chat()` 丢弃；留痕 `prompt_tokens_est` 与 `prompt_tokens_real` 并列（**不互相冒充**）；观测比值喂给 `estimate_tokens` 的校准系数 |
| ④ CI 独立步骤 | ✅ | `.github/workflows/test.yml` 新增 `wiring audit (配置接线门禁)` 一步，跑在 pytest **之前**——失败原因不会被混成"测试挂了" |
| ⑤ 报告 | ✅ | 本文 |

## 2. `LLMReply` 为什么是 str 的子类，而不是"返回 dict"

`chat()` 的返回物有两类消费者：决策路径拿它去做 JSON 抽取（`_extract_json`），
7 个测试文件 `monkeypatch` 的是 `provider.chat` 的字符串返回。改成返回 dict 等于把
T64 刚收敛好的那条路径再拆散一次，还会让"问答与决策共用原语"这件事重新分叉。
子类既是 str（旧写法一字不改地有效），又能带元信息；假桩返回普通 str 时读属性得
按"没有就当未知"处理——`llm_ask` 就是这么写的，`test_llmreply_is_still_a_plain_string_for_old_callers` 钉住这条兼容。

## 3. 校准：估算器承认自己偏低，而不是把预算改成另一个单位的数字

11-03 现网对照：我估 8,318 token、厂商 `usage.prompt_tokens` 报 11,440 ⇒ 估算低约 **1.37 倍**。
`context_budget: 8000` 若继续按估算口径读，实际发出去的是 ~1.1 万 token——
**单位不对，数值再精确也是假的**。所以：

- `estimate_tokens` 乘一个校准系数（默认 1.0，保证单测确定性与"未观测前不改变行为"）；
- 每次问答拿到 usage 后按比值更新，只接受 `[0.6, 2.5]` 区间——缓存命中、system 未计入
  一类异常观测会把系数拉飞，**宁可不校**（`test_absurd_calibration_is_refused`）；
- 留痕与日志同时给估算值和真实值，事后能复核校准有没有被污染。

面板是常驻进程，同进程内的第二次问答就开始用校准后的口径；CLI 单发型仍从 1.0 起步
（这是"首次请求偏大"的诚实代价，写在代码注释里，不假装已经完美）。

## 4. 现网端到端证据（账本副本，生产 md5 未变）

同一问题、`llm_ask` 全链路（`4340715`+`a33fabc`）：请求体 27,216 字符 ≈ 8,318 估算 token，
上下文 240,245 → 22,554 字符（`within_budget=True`，丢弃 `pool_fundamentals`），
`op_log[llm:ask].params` 含 `req_chars/req_tokens/req_max_tokens/slim`，
`result.context` 含压缩前后与 dropped；耗时 45.5s（`max_tokens=1536`）。

## 5. 延迟的实话（不重复 T63 的表格，只说结论）

**≤20 秒不是靠上下文达成的**。三点实测：4096→82.4s、1536→45.5s、1200→28.3s，
真实输出都约 650 token。缺的那格（大上下文 + 小预算）要 **2 次云端请求**才能补齐，
按批复"花额度先报备"我没发（脚本留在 `/home/nec/tmp/t63_grid.py`）。
要真进 20 秒，只有两条路：把问答上限压到 ~1000 以下（现在截断已经可见，所以这个
代价是**可讨论的**了——这正是批复 1 排在最前面的意义），或换更快的端点/模型。
我保持 1536，把选择权连同数据一起交回去。

## 6. 交付物与验证

- `paper_trading/llm/provider.py`（`LLMReply` + `chat` 返回带元信息）、
  `paper_trading/cli.py`（截断标注、真实 token 留痕、`ask_max_tokens` 取数、
  CLI/面板参数）、`paper_trading/agent/loop.py` 与 `utils/config.py` 与 `config.yaml`
  （新键三处同步）、`paper_trading/agent/context.py`（校准系数）、
  `.github/workflows/test.yml`（门禁步骤）。
- `tests/test_ask_t67.py` 15 例：截断/不截断/留痕、配置生效/按次覆盖/缺省不变、
  三面键一致、校准生效与拒绝离谱值、`LLMReply` 向后兼容、CI 步骤存在。
- 全量 **738 passed / 9 skipped**；`tests/wiring_audit.py --quiet` → 断层：无。
- 生产账本 `paper_account.db` md5 前后同值；未部署、未动 cron、未改既有配置默认值。
