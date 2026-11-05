# 批16-a（2026-11-05，Qoder 采集域）：东财 F10 两张表改为"仅缺字段才拉"

> 派工口径：15-b 把 `stock_profit_sheet_by_report_em` / `stock_cash_flow_sheet_by_report_em`
> 激活以后（以前一直传裸 6 位代码，两个拉全失败且 except 静默），平日增量对每只 todo
> 股票固定发 3 个请求。本批把其中两个改成**只在真正缺字段的那些年月才发**；
> `onboard` / 周六 `force_full` 保持全量。

## 1. 判据

`src/collector/akshare_fetcher.py`：`_em_sheets_needed(report_dates, filled, window=EM_DEEP_WINDOW)`
返回 `(需要利润表, 需要现金流量表)`，规则四条：

| 规则 | 为什么 |
| --- | --- |
| 判据字段：利润表看 `interest_coverage`，现金流量表看 `fcf` | 这两列是对应接口**唯一**的产物；`total_shares` 另有"净利润/EPS 倒推"兜底与 C2.5 回填，拿它当判据会把已经能算的票也拖去重拉 |
| **只判本次要写的最近 8 期**（`EM_DEEP_WINDOW`） | 产线只读实测：`interest_coverage` 在 2017 年及以前覆盖只有 12.4%，银行/券商/保险/共 26 只**全历史为 0**。若不设窗口，"有一期缺就拉"变成"永远都拉"，开关等于没装 |
| 新披露一期自动算缺 | 判定用"已有值的**报告期集合**"，不是"这只票填满了没"的布尔——布尔会被老期填满骗住，新期就该由骨架发现（这也是 DAO 返回集合而不是布尔的原因） |
| 骨架（同花顺）没出数 → 两个都不拉 | `records` 为空时两张表的产物本来没处消费；旧写法照样发 2 个请求再把返回值丢掉 |

接线要点：
- 请求从循环开头**挪到 `records` 构建之后**——判定要的期间集合来自骨架，位置不是随便挑的；
- `filled_map` 在循环外用 `FinancialHistoryDAO.get_deep_field_dates(codes)` 一次批量读，
  不在循环里逐只查（15-c 修 news 循环建连接是同一类问题）；
- `force_full` 走 `(True, True)`，即周六全量/onboard 照旧全拉——**老数据的修复手段就是它**；
- 跳过是安全的：`FinancialHistoryDAO.batch_save` 逐列 `COALESCE(excluded.x, x)`（2026-10-25 修的），
  这次没提供的字段保持原值，不会被抹成 NULL。用例
  `test_skipping_does_not_wipe_existing_deep_values` 专门钉这条，变异 N9（去掉 COALESCE）会打红它；
- 收尾新增一行日志：`[历史财务] 增量省请求：跳过东财利润表 X 只、现金流量表 Y 只（判定窗口=近 8 期，共少发 N 个请求）`。
  没有这行，"省了多少"以后只能靠推断。

## 2. 产线只读实测（开关值多少）

### 2.1 todo 规模（`/home/nec/stock-dashboard/data/logs/dashboard.log`）

```
2026-07-16 696 只    2026-07-17 737 只    2026-07-18 737 只
2026-10-28 1 + 521 + 401    2026-10-29 391 + 378 + 334
2026-10-30 273    2026-11-02 111    2026-11-03 6    2026-11-04 跳过（todo=0）
```
每只 3 个请求 → 峰值日 737×3 = **2211 次/天**，其中东财两表 1474 次。
曲线形态是"回填初期很大 → 逐渐衰减到 0"，因为 60 天规则看的是
`MAX(report_date)` 距今的天数：季报间隔 90 天，所以**每个季度有约 30 天全体候选股都被判为"旧"**，
每天重拉一遍。这正是本开关要省的那一段。

### 2.2 深度字段实况（`G:/qoder/probe_em_fields.py`、`probe_em_gate_exact.py`）

```
全表 150956 行：interest_coverage 非空 22.9%、total_shares 64.0%、fcf 61.5%、
              ocf_per_share 98.9%、notice_date 97.2%
按年：2025 行 8667  ic 51.2%  fcf 62.6%      2024  ic 51.1%  fcf 62.7%
     2021~2018  ic 46~50%   fcf 63~65%       2017  ic 12.4%（东财不供这么老的利息费用）
     2026 行 2869  ic  0.0%  fcf  0.0%  ← 两表 15-b 才激活、11-04 todo=0 没跑过
金融股（行业名含 银行/券商/证券/保险）26 只：1436 行 ic 非空 0、fcf 非空 0
```

### 2.3 按实现判据模拟（5527 只，逐只取它自己的最近 K 期）

| 窗口 | 仍需利润表 | 仍需现金流量表 | 峰值日 737 只 | 10-30 那天 273 只 |
| --- | --- | --- | --- | --- |
| 4 期 | 24.8% | 19.2% | 2211 → 约 1062（省 52%） | 819 → 约 393（省 52%） |
| **8 期（本批取这个）** | 40.5% | 19.2% | 2211 → 约 1177（**省 47%**） | 819 → 约 436（省 47%） |
| 12 期 | 87.8% | 19.3% | 2211 → 约 1527（省 31%） | 819 → 约 565（省 31%） |

两个必须说清的限制，不藏在脚注里：

1. **刚上线那一轮省不了**。2026 年各期的 `interest_coverage`/`fcf` 现在全是 NULL（0%），
   判据会老实要求拉——这本来就是对的：那两段数据从来没进过库。跑满一轮之后才开始省，
   省的量级就是上表。
2. **26 只金融股每次增量都会拉利润表**（ic 永远为空，东财不在利润表里单列利息费用）。
   相对现状不是回归（现在也是每次都拉），但省不到它们那份。要彻底省下需要
   "同一天同一报告期拉过就没数据"的冷却语义，产线已有 `backfill_progress`
   （`done/no_data/partial` + `covered_report_date`）可承载，**属跨域状态写入，未擅自加**，
   请母方裁定要不要做；做的话窗口可以继续放宽到 12 期。

## 3. 验证声明

- 新增 `tests/collector/test_em_incremental_gate_20261105.py`：判据参数化 6 例 + 乱序 1 例 +
  真跳过/新期仍拉/单表缺只拉一张/骨架空不拉/force_full 全拉/跳过不清空/写回生效/日志计数 +
  DAO 集合语义 = **17 条**。
- 判别性变异 `G:/qoder/mut_b16a.py`：**9/9 全被抓住**（N1 判据恒真、N2 忽略窗口、
  N3 骨架空照拉、N4 force_full 被开关吃掉、N5 两表判据互换、N6 判定不排序、
  N7 去掉计数日志、N8 DAO 把 NULL 当已填、N9 去掉 batch_save 的 COALESCE）。
  N4 同时打红批15 的 5 条前缀形态用例，说明"全量必须全拉"这条契约被两边一起看着。
- 本机全库套件：见下方"结果"。产线 `scripts/gate.sh` 由母方执行，本机不代跑。
- 未改产线数据；只读实测脚本在仓库外（`G:/qoder`）。

## 4. 结果（本机全库，2026-11-05）

`PYTHONPATH=G:/qoder/local_stubs python -m pytest tests -q --tb=no` →
**1247 passed, 12 failed, 3 skipped in 95.95s**。

12 条失败与 16 条 3 skipped 全是本机环境固定项，与本批无关，逐条对上：
- `tests/collector/test_kline_fetcher.py` 11 条：该文件用
  `patch("src.collector.akshare_fetcher.ak")`，本地自检桩（`G:/qoder/local_stubs/akshare.py`，
  任何属性访问即抛，用来阻止"本地假装打过网络"）在 mock 解析阶段就 `__func__` 报错；
- `tests/news/test_news_feature.py::test_caixin_fetch_market_parses_headline_and_date` 1 条：
  同桩，报 `akshare.stock_news_main_cx 被调用了`；
- 3 skipped 是缺 `duckdb`（研究域两条 import 期跳过 + 一条 fcntl 相关）。
  本机没装 akshare/duckdb，产线 `scripts/gate.sh` 才是权威闸，由母方跑。
