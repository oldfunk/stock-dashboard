"""母项目版本号。

**必须与 `pyproject.toml` 的 `project.version` 保持一致**，
`tests/test_version_sync.py` 会强制两者同步（发版只改这两处 + README 状态行）。
"""

__version__ = "2.1.0"
