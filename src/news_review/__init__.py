"""F 线：新闻收盘检阅（2026-11-28）。

模块边界：
- `rules`：纯规则（实体/级别/聚类/引用/建议检查）
- `facts`：只读事实底座（母库 + 纸盘桥）
- `prompt`：提示词
- `pipeline`：编排与发布
"""
from src.news_review.pipeline import collect_inputs, run_close_review

__all__ = ["collect_inputs", "run_close_review"]
