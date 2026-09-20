import sys
from pathlib import Path

# 把项目根目录加入 sys.path，确保 pytest 能找到 src 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
