# 三池（成长/红利/困境反转）dry-run 说明

`config/strategies.yaml` 定义三池阈值，`config/config.yaml` 中
`screening.multi_strategy: true` 即让生产流水线消费三池并集。
改动前先 dry-run 看三池各自命中数，不写库、不改生产数据。

## 只读 dry-run（不写库）

`score_candidates(multi_strategy=True)` 本身不写库（写库只在
`run_screener` 里），可直接在 proot Debian 里跑：

```bash
proot-distro login debian -- /usr/bin/env PATH=/usr/local/bin:/usr/bin:/bin /bin/bash -c \
  "cd /data/data/com.termux/files/home/work/stock-dashboard && ./.venvdeb/bin/python - <<'EOF'
import json
from src.screener.value_screener import ValueScreener, load_strategies
from src.config import load_config

cfg = load_config()
strategies = load_strategies()
print('策略:', {k: v.get('name') for k, v in strategies.items()})

# candidates：沿用生产口径（run_collect_pipeline 产物），此处仅示意形状
# from src.collector.akshare_fetcher import run_collect_pipeline
# candidates = run_collect_pipeline(cfg)
candidates = []  # 填入真实候选后跑
pools = ValueScreener(cfg).score_candidates(
    candidates, 'dryrun', 'dryrun',
    multi_strategy=True, strategies=strategies)
for name, pool in pools.items():
    print(f'{name}: {len(pool)} 只')
    for row in pool[:5]:
        print(' ', row['code'], row['name'], round(row['score']))
EOF"
```

## 开关打开后的生产行为

- `src/orchestrator.py` 读 `screening.multi_strategy`（默认 false）；
  为 true 时调 `run_screener(..., multi_strategy=True)`，持久化三池并集
  （按 code 去重、保留最高分行，`strategy_tags` 为全量命中标签），
  并打一行各池命中数日志。
- 打开后先观察一晚 15:30 流水线日志 `[筛选] 多策略模式` 行，
  确认三池 size 合理再合 main。
