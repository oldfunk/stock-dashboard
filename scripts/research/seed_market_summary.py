"""注册 market_summary 任务 + prompt（R1-4）。

用法：python scripts/research/seed_market_summary.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.research.llm.prompts import PromptRegistry
from src.research.llm.tasks import TaskRegistry

TASK_TOOLS = ["market_state", "features", "financials", "valuation", "outcomes"]


def main() -> int:
    tasks = TaskRegistry()
    tasks.register({
        "task_id": "market_summary", "version": 1,
        "description": "市场状态叙事：只解释状态向量，不引入新数字",
        "model_tier": "fast", "tools": TASK_TOOLS,
        "output_schema": {
            "type": "object", "required": ["summary", "citations"],
            "properties": {
                "summary": {"type": "string"},
                "citations": {"type": "array", "items": {"type": "string"}},
                "regime": {"type": "string"},
            }},
        "max_tokens": 600, "status": "active",
    })
    added = PromptRegistry(task_registry=tasks).sync("market_summary")
    print(json.dumps({"task": "market_summary", "prompt_versions_added": added},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
