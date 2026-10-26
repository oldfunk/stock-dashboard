"""Prompt 框架（2026-10-26，R0-9）。

- 模板文件：``research/prompts/<task>/vN.md``，统一结构：
  前置元信息（task/version/model_tier）+ ``## System`` / ``## Context`` /
  ``## Task`` / ``## Schema`` 四段。
- ``prompt_registry`` 记录 hash/版本；**prompt 变更生成新版本**（写 v(N+1).md）；
  **旧版本可回放**（文件保留，按版本号读取）。
- 变量用 ``{{name}}`` 占位，渲染时替换；数字表禁止文本压缩（R4 决策）。

用法::

    reg = PromptRegistry()
    reg.save("market_summary", content)      # 生成新版本并注册
    reg.render("market_summary", 1, {"market_state": "..."})
    reg.replay("market_summary", 1)          # 取原始模板
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from src.models import database as _db
from src.research.dao import ResearchDAO
from src.research.llm.tasks import TaskRegistry

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


class PromptError(ValueError):
    """模板缺失/结构非法/版本不存在。"""


def prompts_dir() -> Path:
    return Path(_db.get_db_path()).parent.parent.parent / "research" / "prompts"


def parse_version(filename: str) -> int:
    match = re.fullmatch(r"v(\d+)\.md", filename)
    if not match:
        raise PromptError(f"非法 prompt 文件名: {filename}")
    return int(match.group(1))


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise PromptError("缺少前置元信息（--- task/version/model_tier ---）")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, text[match.end():]


def _parse_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[match.group(1).strip().lower()] = body[start:end].strip()
    return sections


def _extract_schema(section: str) -> dict:
    match = _CODE_BLOCK_RE.search(section)
    if not match:
        raise PromptError("Schema 段缺少 ```json 代码块")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise PromptError(f"Schema JSON 非法: {exc}") from exc


def read_prompt(path: Path) -> dict[str, Any]:
    """解析模板文件为结构化对象。"""
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    for key in ("task", "version", "model_tier"):
        if key not in meta:
            raise PromptError(f"前置元信息缺字段: {key}")
    sections = _parse_sections(body)
    for key in ("system", "context", "task", "schema"):
        if key not in sections:
            raise PromptError(f"模板缺段落: ## {key}")
    return {
        "task": meta["task"],
        "version": int(meta["version"]),
        "model_tier": meta["model_tier"],
        "sections": sections,
        "schema": _extract_schema(sections["schema"]),
        "template": text,
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def render_template(template: str, variables: dict[str, Any]) -> str:
    """替换 ``{{name}}``；未提供的变量保留占位（不静默填空）。"""
    def _sub(match: re.Match) -> str:
        key = match.group(1).strip()
        if key not in variables:
            return match.group(0)
        return str(variables[key])
    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", _sub, template)


class PromptRegistry:
    def __init__(self, dao: ResearchDAO | None = None,
                 task_registry: TaskRegistry | None = None):
        self.dao = dao or ResearchDAO()
        self.task_registry = task_registry or TaskRegistry(self.dao)

    # ── 写入 / 注册 ──────────────────────────────────────────────────────────
    def save(self, task: str, content: str, model_tier: str | None = None,
             tools: list[str] | None = None,
             max_tokens: int | None = None) -> int:
        """写入下一个版本文件并注册；返回新版本号。"""
        task_dir = prompts_dir() / task
        task_dir.mkdir(parents=True, exist_ok=True)
        existing = [parse_version(p.name) for p in task_dir.glob("v*.md")]
        version = (max(existing) + 1) if existing else 1
        if model_tier is None or tools is None or max_tokens is None:
            spec = self.task_registry.require(task)
            model_tier = model_tier or spec.model_tier
            tools = tools if tools is not None else spec.tools
            max_tokens = max_tokens or spec.max_tokens
        # 前置元信息 + 四段
        front = (f"---\ntask: {task}\nversion: {version}\n"
                 f"model_tier: {model_tier}\n---\n\n")
        body = content.strip()
        if not body.startswith("## "):
            raise PromptError("prompt 正文须以 '## ' 段落开头")
        text = front + body + "\n"
        path = task_dir / f"v{version}.md"
        path.write_text(text, encoding="utf-8")
        info = read_prompt(path)
        self.dao.insert_prompt({
            "prompt_id": task, "version": version, "task": task,
            "template_hash": info["hash"], "schema": info["schema"],
            "model_tier": model_tier, "tools": tools, "max_tokens": max_tokens,
            "status": "active",
        })
        return version

    def sync(self, task: str) -> list[int]:
        """扫描目录，注册尚未登记的版本（不覆盖已存在版本）。"""
        task_dir = prompts_dir() / task
        if not task_dir.exists():
            return []
        added: list[int] = []
        for path in sorted(task_dir.glob("v*.md")):
            version = parse_version(path.name)
            if self.dao.get_prompt(task, version) is None:
                info = read_prompt(path)
                spec = self.task_registry.get(task)
                self.dao.insert_prompt({
                    "prompt_id": task, "version": version, "task": task,
                    "template_hash": info["hash"], "schema": info["schema"],
                    "model_tier": spec.model_tier if spec else info["model_tier"],
                    "tools": spec.tools if spec else [],
                    "max_tokens": spec.max_tokens if spec else 6000,
                    "status": spec.status if spec else "active",
                })
                added.append(version)
        return added

    # ── 读取 / 回放 ──────────────────────────────────────────────────────────
    def _path(self, task: str, version: int) -> Path:
        path = prompts_dir() / task / f"v{version}.md"
        if not path.exists():
            raise PromptError(f"prompt 版本不存在: {task} v{version}")
        return path

    def get(self, task: str, version: int | None = None) -> dict | None:
        if version is None:
            rows = self.dao.list_prompts(task)
            return rows[0] if rows else None
        return self.dao.get_prompt(task, version)

    def render(self, task: str, version: int,
               variables: dict[str, Any] | None = None) -> str:
        """按版本渲染模板（旧版本可回放）。"""
        info = read_prompt(self._path(task, version))
        if info["task"] != task:
            raise PromptError(f"模板 task 不匹配: {info['task']} != {task}")
        return render_template(info["template"], variables or {})

    def replay(self, task: str, version: int) -> str:
        """取原始模板（不做变量替换），用于复现/审计。"""
        return read_prompt(self._path(task, version))["template"]

    def schema(self, task: str, version: int) -> dict:
        return read_prompt(self._path(task, version))["schema"]
