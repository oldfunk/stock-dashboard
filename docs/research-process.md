# 参数研究流程（D3）

> 原则：**样本内选择 → 样本外检验 → 前向观察**；任何一步不通过，参数不得上生产。
> 工具：`strategy research`（复用 `engine walkforward`，不自动写配置）。

## 1. 命令

```bash
python -m paper_trading.cli strategy research \
  --grid ma_cross --symbols 600519 000858 \
  --days 250 --split 0.6 --top 5 --min-oos-trades 5 --json
```

输出包含：

- `split`：样本内/样本外日期与天数；
- `in_sample_top`：样本内排名；
- `out_of_sample`：样本内前 N 名在样本外的表现；
- `overfit_gap_pct`：事后最优 − 样本内第 1 名（过拟合量度）；
- `data_fingerprint`：K 线/窗口/参数哈希，保证可复现；
- `research.decision`：`candidate` / `overfit_risk` / `reject`；
- `research.forward.status=pending`：前向观察需真实后续交易日，样本外不能冒充；
- `research.auto_apply=false`：**不写** `config.yaml` / `engine.local.yaml`。

## 2. 判定

`strategy/research.py::classify`：

- 样本内第 1 名样本外超额 ≤0 → `reject`；
- 样本外成交 < `min_oos_trades`（默认 5）→ `reject`；
- 过拟合差距 ≥5pp，或样本外正超额变体不足一半 → `overfit_risk`；
- 其余 → `candidate`（仍只是候选，不是生产结论）。

## 3. 上生产前

1. `candidate` 后做**前向模拟**（未来 N 个交易日，不偷看）；
2. 人工复核逻辑/参数/数据口径（`data qfq-check`、`data contract`）；
3. 改配置必须走代码审查 + 发布观察，禁止脚本自动写；
4. 记录 `data_fingerprint` 与研究结论；样本外/前向失败要写原因。

## 4. 红线

- 不把网格第一名直接写生产；
- 不用样本外结果反选参数（事后最优只用于量过拟合）；
- 样本不足/数据缺失时 `reject` 或明确标注，不硬凑结论；
- 前向观察未完成前，结论只能称“候选”。
