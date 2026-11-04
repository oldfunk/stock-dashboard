# T64 · 配置接线全量审计（2026-11-03，Qoder）

派发原文（Batch 15）：

> **T64（Qoder，P0·接线完整性）配置接线全量审计**：
> - 扩展 `tests/wiring_audit.py`（或新脚本）：覆盖**跨对象传递**断层面
> - 全仓扫描：`AgentConfig`/`LLMConfig`/`TradingConfig` 的字段 → 实际消费点；
>   **列出"零消费"与"构造漏传"清单**
> - 修复清单里的全部漏项（逐个接线 + 单测锁定）；`report-t64.md`：审计方法 + 漏项清单 + 修复对照
> - 门禁化：`wiring_audit` 退出码非 0 = 有断层（并入 CI 口径）

一句话结论：v1 的三件套查不出这次事故那一类，**v2 加了四类尺子**；跑出来的漏项是
**问答路径不带 `llm_retries/llm_max_tokens/llm_thinking`** 与
**`design_conditions` 把重试次数硬编码成 1**，两处都修了（修法是收敛成
一个 provider 原语，不是各补各），并且检测器自己被测试钉住"合成源码真会红"。

---

## 1. 逐条核销

| 派发点 | 状态 | 落在哪 / 证据 |
| --- | --- | --- |
| 扩展审计覆盖跨对象传递断层面 | ✅ | `tests/wiring_audit.py` v2：尺子②「LLM 请求侧跨对象传递矩阵」，行 = 每个真发请求的调用点，列 = `llm_timeout/llm_retries/llm_max_tokens/llm_thinking`；供给算三路（路径引用 / 构造点喂给目标参数 / 经 `_ask_llm` 间接层），**调用点给字面量单独判"硬编码覆盖"** |
| 全仓扫描三个 Config 的字段 → 实际消费点，列"零消费"与"构造漏传"清单 | ✅ | 面扩到 6 个：`AgentConfig`(27)、`LLMConfig`(7)、`TradingConfig`(9)、`EventConfig`(8)、`RiskManager` 参数(6)、`engine.DEFAULTS`(10)；尺子①零消费、③yaml⇄归一化⇄字段三面、④构造点供给。**现网结果：零消费 0 项、白名单断层 0 条**（v0.7.6 那轮补的 news_*/require_* 都还在位），矩阵断层 2 处 ⇒ §2 |
| 修复清单里的全部漏项 + 单测锁定 | ✅ 2 处 | 见 §3；`tests/test_wiring_t64.py` 15 例（行为级：改配置 ⇒ 请求里真的变） |
| 门禁化：退出码非 0 = 有断层 | ✅ | `python tests/wiring_audit.py [--json/--quiet]`，`main() → 0/1`；并挂进 pytest：`test_audit_gate_passes_on_the_repo`（CI 跑测试就顺带跑门禁，不需要额外接线） |

## 2. 审计方法（以及 v1 为什么必然漏）

v1 只比对三件事：字段在不在 dataclass ⇄ `cli.py` 的 `AgentConfig(...)` 有没有传 ⇄
`config.yaml` 有没有键。`agent.llm_timeout` 那次**三件事全过**：字段在、cli 传了、
yaml 有键——断的是第二跳 `AgentConfig.llm_timeout → LLMConfig.timeout`（`cli.py:719`
构造 `LLMConfig` 时没带，于是吃 `LLMConfig` 自己的 30 秒默认）。v1 的结构里根本没有"第二跳"这个概念。

v2 的四类尺子，每类都对应一个真实失效形状：

1. **零消费**：字段被搬到对象里，然后**没有任何人读**。判定按 AST 节点语义：
   `obj.f` / `obj["f"]` / `.get("f")` / `getattr(o, "f")` 算读；
   `Foo(f=1)` 的 keyword、`{"f": 1}` 的字面量键、dataclass 的 `f: int = 1` 声明**都不算**。
   v1 把后两者也算成"用过了"，所以那一栏永远不会红。`llm_timeout` 在修复前正是这一类。
2. **传递矩阵**：每个真发请求的调用点逐字段打勾。三种"接上"都认，因为只认一种必然出假：
   只认路径引用 ⇒ 主 AI 用 `LLMConfig(timeout=…)` 修好的那次会被继续报成漏传；
   只认构造点 ⇒ "问答路径根本没带 max_tokens"这种真漏传被洗白。
   间接层 `_ask_llm` 单独建模：它自己那次 `chat()` 不算漏传，**看它的调用点**传的是配置还是字面量。
3. **三面白名单**：`config.yaml` 的键 ⇄ 归一化层（`utils/config.load_config` 手写的
   `agent/risk/strategy` 白名单、`engine.config.DEFAULTS`）⇄ dataclass 字段。
   任一面缺键都是"用户写了但到不了"——v0.7.6 的 `news_*` 就是归一化层漏了。
4. **构造点供给**：目标对象的参数是被供给还是一路用默认值。`RiskManager(**risk_kwargs)`
   这种整体摊派要专门识别，否则会把已经接线的 5 个风控参数报成"构造点 1 处只供给 db_path"。

**误报的代价我付过一次**：中间版本把 `_ask_llm` 内部的 `_chat(cfg)` 调用、
以及 `_ask_llm(b._llm_config(), user_p, …)` 这种**嵌套调用被正则截断**的实参
（`(.*?)` 停在 `_llm_config()` 的右括号上）都判成"四个字段全没传"——一次报出 5 处断层，
其中 3 处是假的。门禁如果cry wolf，下一次真断层就没人看了。修法是括号配平取实参 +
间接层建模 + 具名转传算供给（`_hardcoded_helper_args` 对 `retries=retries` 不报、
对 `retries=1` 报）。

## 3. 漏项清单与修复对照

| # | 漏项 | 现网证据 | 修法 | 锁定它的测试 |
| --- | --- | --- | --- | --- |
| F1 | `llm_ask`（面板问答）不带 `llm_retries/llm_max_tokens/llm_thinking`，只有 timeout 被 475f994 补上 | 问答直接 `chat(cfg,…)`，`cfg` 来自 `_llm_config()`，那里只喂 `timeout` ⇒ 问答实际用 `LLMConfig` 默认 `max_tokens=4096`、`extra_body={}`（不关思考）、不重试 | 把"配置怎么落到请求上"提到 `provider.chat_with_retry`（超时重试 + 1210 不支持关思考时降 low 档并重给预算），`_ask_llm` 变成它的薄壳，问答与条件单设计都走这一个原语 | `test_qa_path_carries_timeout_and_thinking`、`test_qa_path_honours_retries_only_on_timeout`、`test_ask_thinking_enabled_does_not_send_disable_flag` |
| F2 | `design_conditions` 把重试次数**硬编码 1**（`cli.py:1059`） | 审计在 helper_site 行报 `llm_retries 被硬编码 1 覆盖（改 config 无效）` | 改传 `int(bridge.agent_cfg.llm_retries or 0)` | `test_design_conditions_retry_is_config_driven_not_literal` |
| F3 | `agent.context_budget` 被消费但**保证不了语义**（详见 T63 报告 §2：旧压缩任何预算都停在同一个体积，且不报告） | `cli.py:977` 读了 budget，旧 `compress_context` 裁不到体积大头；四种预算输出字节相同 | 归 T63 主线（重写分级 + `within_budget` 如实报告）；同时把 `llm_context` 里 `except Exception: pass` 改成 warn + 元信息（压缩失败不许静默） | `tests/test_context.py::test_compression_actually_reaches_the_budget_and_stops_at_the_floor`、`test_llm_context_compression_failure_is_loud_not_silent` |

**顺手澄清一个我自己的错判**（写在这里是因为它曾经是"报告里的一句话"）：
我最初测得"旧压缩是彻底的空操作、253,677 → 253,677"，那是**我把已压缩的输出又喂回压缩器**
测出来的。同一份原始装配重测后：旧压缩确实裁了一刀（253,677 → 97,910），
但**裁到哪与预算无关**（8000/4000/2000/1000 全停在 97,910），且从不报告超没超。
结论从"knob 没接"修正为"knob 接了但语义保证不了"——两类都要抓，但不是同一类。

## 4. 为什么矩阵只覆盖 LLM 面（以及不覆盖什么）

跨对象传递的"目标参数改名"现象在 LLM 面最凶（`llm_timeout → timeout`、
`llm_thinking → extra_body`），而 `engine.*`/`risk.*` 是同名 dict 取值，构造漏传的概率低。
我没有给 engine 面做**行为级**断言（改 `engine.interval_sec` 看 runner 是否真的用）——
那些键的读点在 runner/rules 里是直接的 `cfg.get(...)`，静态读点检查已覆盖。
如果要把 engine 面也做成行为级，那是另一件事（需要合成引擎配置 + 跑一轮 tick），
我没顺手加，避免把 T64 变成大杂烩。

另外三条**不算断层但值得记一笔**的观察（审计④"只报不进门禁"栏）：
`LLMConfig.extra_body/max_tokens` 在构造点走默认（由调用方按路径覆写，设计如此）；
`EventConfig.max_symbols` 是代码内常量（无配置面，已带理由豁免）；
`LLMConfig.temperature` 没有配置面也没有面板项（想开成 knob 属产品决策）。

## 5. 交付物

- `tests/wiring_audit.py`（重写，约 470 行）：四类尺子 + 带理由的豁免登记 + `--json/--quiet` + 退出码。
- `paper_trading/llm/provider.py`：新增 `chat_with_retry`（超时重试 + 思考透传降级），
  `paper_trading/agent/loop.py::_ask_llm` 改为薄壳（签名不变，既有测试与调用点都不动）。
- `paper_trading/cli.py`：`llm_ask` 走原语并带配置；`design_conditions` 用 `llm_retries`；
  `TradingBridge.last_context_meta`；`minute sync --now`（T53 类时钟注入点）。
- `paper_trading/risk/risk_manager.py::describe()`（只读版风控态，供上下文/面板复用）。
- 测试：`tests/test_wiring_t64.py` 15 例（检测器有牙齿 5 例 + 行为接线 8 例 + 门禁 1 例 + 豁免理由 1 例）。
- 全量：`python -m pytest -q tests/` **701 passed / 9 skipped**；`tests/wiring_audit.py --quiet` → `断层：无`（退出码 0）。

## 6. 待拍板（写进 questions.md）

1. **`ASK_MAX_TOKENS=1536` 的取值口径**：实测问答耗时随 max_tokens 上限增长
   （4096→82.4s、1536→45.5s、1200→28.3s），而真实输出都约 650 token。
   要更接近派发里的 ≤20 秒就得把上限压到 ~1000 以下，代价是长答案可能被截断
   （`finish_reason=length` 目前不会告知用户）。建议同时加"答案被截断"的信号，
   再谈更小的默认值。
2. **是否把 `wiring_audit` 挂进真正的 CI**：现在靠 `test_audit_gate_passes_on_the_repo`
   随 pytest 跑；如果仓库以后接 GitHub Actions，门禁应该单独一步（退出码可直接告警）。
3. **`engine.` / `strategy.` 面要不要也做行为级断言**（我做成了静态读点，理由见 §4）。
