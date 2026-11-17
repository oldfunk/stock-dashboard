# report-t75.md — T74 残留两项加固（Qoder · 2026-11-05）

派发：Batch 19 · T75（P2）——① 1d 聚合 prev_close 的 NaN 断言（回退前一日日线）；
② 代码→交易所映射前缀覆盖测试（6/0/3/688/8）。分支 `worker/t75-t80-wrapup`，未合。

## ① prev_close 的 NaN：从"报失败"升级为"回退 + 留痕"

T73 交付时我先做的是"非有限就返回 None"（`Account.__init__` 会因此明确报错）。
Hermes 的 P2 要的是**回退**，这次照办并把口径搬到可测的地方：

- 新增纯函数 `store.aggregate_day_from_minutes(bars, day_key, symbol) -> (row, note)`
  （`store.py` 不 import rqalpha ⇒ CI 里直接测；`data_source.get_bar` 的 1d 分支
  只剩一次调用，不再自带聚合逻辑）。
- 回退定义就是 `prev_close(D) := 上一交易日收盘`：取本分钟序列里比 D 更近**那一天**
  的末根 close；涨跌停带用同一个 `bands.limit_prices` 现算（不留第二份档表，纪律 7）。
- 退无可退才 `(None, 原因)`——让"这天没得算"变成明确报错，而不是拿 NaN 建仓、
  再拿 NaN 判涨跌停（`matcher/base.py:207-218` 认的就是这两列）。
- 走了回退或失败 ⇒ `note` 非空 ⇒ 同时写进 `store.problems`（`rq state` 看得见）
  和日志（`logger.warning`），不留静默路径。

测试（`tests/test_t75_hardening.py`）：正常路径不动 store 给的带 / 回退值必须是
**上一交易日的末根**（不是"序列里随便一根"）/ 只有当天且带缺失 ⇒ 失败并说原因 /
当日无 bar ⇒ 不返回一根全零的假 bar。

## ② 前缀覆盖：已有表 + 新增"北交所整链路"一条

`tests/test_market_caliber_t59.py::SEGMENTS` 已经把 Hermes 点名的 6/0/3/688/8/92
全跑过五个调用点（新浪分钟 / 腾讯实时 / rqalpha 标的 / 涨跌停带 / markets 本体），
这次补的是**它到框架里之后的行为**：

- `test_order_book_id_prefix_coverage`：把 Hermes 列的六个前缀单独钉一遍
  （`store.to_order_book_id` 与 `markets.order_book_id` 必须同值——防两张表各自漂移）；
- `test_bj_symbol_survives_the_init_chain_loudly`：`920156` 序列化成
  `920156.BJ:100` → `reconcile_init_positions` 在标的表查不到时判 `unsupported`
  （实测 bundle 28,063 只里 `.BJ` 为 0）⇒ **如实剔除并留名字**，
  而不是"少一只仓但没人知道"。剔除项同时被 `T1FreezeMissing` 闸与
  `daily-check.init_align` 豁免，不会产生假红。

## 回归

四场景在 NEC 用本分支重跑一遍全绿（`/home/nec/tmp/t75/run.json`，含 guard），
因为 1d 聚合从 `data_source` 抽到了 `store`——那正是期初成本价的通路；
全量 836 passed / 11 skipped，`wiring_audit` 退出码 0。
