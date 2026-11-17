"""发版版本号单一来源：`pyproject.toml` 与 `src/version.py` 必须同步。

发版只改这两处 + README 的"当前状态"行，然后打 annotated tag。
"""

import re
from pathlib import Path

import src.version

_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_version_matches_src_version():
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "pyproject.toml 缺 [project].version"
    assert m.group(1) == src.version.__version__, (
        f"pyproject={m.group(1)} vs src/version.py={src.version.__version__}；"
        "发版请同步改两处")


def test_readme_status_mentions_current_version():
    text = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"v{src.version.__version__}" in text, (
        "README 的“当前状态”行要写当前版本号")
