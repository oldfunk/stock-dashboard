# report-t27.md — 结构/文档漏洞审查（Hermes）

## 范围
AGENTS.md / refactor-plan / WORKER / mother-interface / incident / founding-mandate / migration-nec / m1-data-adapter。

## 发现
1. P2：`spikes/rqalpha_mod_t13sim.py` 的 `band_of` 对 ST 使用 5%（AGENTS 合规表 #6 要求主板 ST 10%）——原型与文档不一致，M2 产品化前必须修正（report-t33 已标 P0）。
2. P2：AGENTS 硬规则 0 的 L1/L2 划分在代码注释中未显式标注；当前 L1 仍为自研 Backtester，rqalpha 未接生产——过渡态需在 architecture-map 中如实记录（T28）。
3. P1：WORKER.md T12 行曾标注 “T15b1/T16 已交付”，现已追加 T12 收尾报告；建议主 AI 更新该行文案避免误导。
4. P1：AGENTS 风险表 max_single_order_value 默认 30,000 与代码兜底 200,000 差异已文档化，但新贡献者易误解；建议在表注明确“config 为准，代码仅兜底”。
5. P0：未发现文档间硬矛盾或过时断言阻塞 M2；D1-D4 裁决已同步至 refactor-plan §5.4 与 questions.md。

## 覆盖清单
- 文档互查：AGENTS/migration-nec 路径已切 /home/nec；D1-D4 已落 §5.4；cron 清单 5+1 与 AGENTS 一致。
- 代码 vs 文档：合规表 16 条 spot check（佣金/印花税/过户费/涨跌幅）与 config.yaml 一致；ST 例外见 #1。
- 分层：L1=自研 Backtester（过渡态），L2=AI/门禁/审计/面板不动；rqalpha 仅 spike，未生产。

## 结论
无 P0 阻塞；P1/P2 列入报告，可进入 M2。
