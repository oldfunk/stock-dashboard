"""守卫：``scripts/*.py`` 不得调用 ``os.chdir``（批11-a，2026-10-29）。

原因见 `scripts/backfill_financial_extra.py` 主入口注释：src 侧所有路径都基于
``__file__`` 生成绝对路径（``get_db_path`` / ``load_config`` / 日志目录 / .env 都是），
脚本切 cwd 只是在污染调用方——pytest 会话中途换 cwd 曾导致偶发挂起。
这类回归不该靠 code review 的记性，所以做成机器守卫。

用 AST 而不是 grep：注释里出现 "chdir" 是合法的（正解说明本身就写在 extra.py 里），
**只有真的调用才算违规**。
"""

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _chdir_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "chdir":
            hits.append(node.lineno)
        elif isinstance(func, ast.Name) and func.id == "chdir":
            hits.append(node.lineno)
    return hits


def test_scripts_are_found():
    """守卫本身不能空转：找不到脚本文件时报 0 个文件，先钉住非空。"""
    files = list(SCRIPTS.rglob("*.py"))
    assert len(files) > 10, f"脚本目录扫到 {len(files)} 个文件，守卫可能失效"


def test_no_chdir_calls_in_scripts():
    offenders = {}
    for path in sorted(SCRIPTS.rglob("*.py")):
        lines = _chdir_calls(path)
        if lines:
            offenders[path.name] = lines
    assert not offenders, (
        f"脚本里不要切工作目录（路径一律基于 __file__）：{offenders}")
