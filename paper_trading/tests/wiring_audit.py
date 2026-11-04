"""配置接线审计 v2（T64）：把"定义了、默认值对、但消费点拿不到"变成机器可查的断层。

为什么重写（2026-11-03 用户令：这类小问题不该再出现，交给员工系统性消灭）：
v1 只查三件事——字段在不在 `AgentConfig` ⇄ `cli.py` 有没有 `AgentConfig(...)` 传参 ⇄
`config.yaml` 有没有键。`agent.llm_timeout` 那次事故**三件事全过**：字段有、cli 传了、
yaml 有键，唯独**第二跳**（`AgentConfig.llm_timeout → LLMConfig.timeout`）没接上，
用了 `LLMConfig` 自己的 30 秒默认 ⇒ 97KB 上下文的问答必超时。v1 的结构决定了它看不见
这一类，所以补四类尺子：

1. **零消费**（dead knob）：字段/键存在，搬运层之外**读点=0** ⇒ 改了 config 也不改变行为；
2. **LLM 请求侧跨对象传递矩阵**：每个真正发请求的调用点逐字段打勾，空格就是
   `llm_timeout` 的同类（含"经 `_ask_llm` 间接层"与"调用点传字面量"两种形状）；
3. **三面白名单对照**：`config.yaml` 的键 ⇄ 归一化层（`utils/config.load_config`、
   `engine.config.DEFAULTS`）⇄ dataclass 字段，任一面缺键都是"写了也丢"；
4. **构造点供给**：目标对象的参数是被供给还是一路用默认值（`**kwargs` 摊派单独识别）。

豁免必须带理由：`EXEMPT` 每条都要写字符串原因，空理由算断层——防止为了让门禁变绿
把问题扫到地毯下。

用法：
    venv/bin/python tests/wiring_audit.py            # 人读
    venv/bin/python tests/wiring_audit.py --json     # 机器读
    venv/bin/python tests/wiring_audit.py --quiet    # 只打结论（CI 用）
退出码：0 = 无未豁免断层；1 = 有断层（门禁口径，`tests/test_wiring_t64.py` 里也跑）
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "paper_trading"

#: 审计的配置面（surface）：name → (类型, 模块, 对象名)
SURFACE_SPECS = {
    "AgentConfig": ("dataclass", "paper_trading.agent.loop", "AgentConfig"),
    "LLMConfig": ("dataclass", "paper_trading.llm.provider", "LLMConfig"),
    "TradingConfig": ("dataclass", "paper_trading.models.types", "TradingConfig"),
    "EventConfig": ("dataclass", "paper_trading.agent.events", "EventConfig"),
    "RiskManager": ("init_params", "paper_trading.risk.risk_manager", "RiskManager"),
    "engine.DEFAULTS": ("dict_keys", "paper_trading.engine.config", "DEFAULTS"),
}

#: `config.yaml` 的段 ⇄ 对应 surface（段名写错=静默丢，所以两边都要能对上）
YAML_SECTION_SURFACE = {
    "agent": "AgentConfig",
    "engine": "engine.DEFAULTS",
    "trading": "TradingConfig",
}

#: 归一化层所在文件/函数：这里的 `"键名"` 只证明"从 yaml 搬了一次"，不算消费
NORMALIZER = ("paper_trading/utils/config.py", "load_config")

#: 有意不接线的字段：**必须给理由**（无理由视为断层）
EXEMPT: dict[tuple[str, str], str] = {
    ("EventConfig", "max_symbols"):
        "代码内常量（单次异动最多交给几只标的）。没开 agent.event_max_symbols 是有意的："
        "它和事件评审的调用预算联动，要开得连同 prompts 一起改",
    ("RiskManager", "db_path"):
        "由 TradingBridge 固定传账本路径（峰值/日亏基线必须与账本同库）；"
        "开放 risk.db_path 等于允许把风控状态写到别的库里去",
    ("LLMConfig", "api_key"):
        "只从 secrets/面板设置来（AGENTS「LLM 接入安全」：Key 永不进 config.yaml 与账本）",
    ("LLMConfig", "base_url"):
        "接口地址属于密钥面，走 secrets.local.json / llm-config，不走 config.yaml",
    ("LLMConfig", "model"):
        "模型名同上：改模型不该要动 config.yaml，也不该落进版本库",
    ("LLMConfig", "temperature"):
        "没有配置面（面板也没这个设置项），是代码默认；开成 knob 属于产品决策，"
        "不在接线审计的范围里",
}

#: LLM 请求侧字段：这些字段"一条路径接了、另一条没接"就是事故同类
LLM_FIELDS = ("llm_timeout", "llm_retries", "llm_max_tokens", "llm_thinking")

#: 字段 → 请求侧真正吃它的参数名。`llm_timeout` 事故的形状就是"参数换了个名字，
#: 于是没人检查得到"：AgentConfig.llm_timeout 要落到 LLMConfig.timeout 才算接上。
LLM_FIELD_TARGET = {
    "llm_timeout": "timeout",
    "llm_retries": "retries",        # 不是 LLMConfig 字段：重试在调用方循环里实现
    "llm_max_tokens": "max_tokens",
    "llm_thinking": "extra_body",    # thinking 开关是透传体，不是独立字段
}

#: 间接层的判定特征：函数体内把参数写进请求对象（`cfg.timeout = …`）并最终调 `chat`。
#: 这一层内部那次 `chat` 调用不该算"漏传"——真正要看的是它的**调用点**传的是配置还是字面量。
#: 旧版本这里是个人工名单（`{"_ask_llm", "chat_with_retry", "_ask"}`），而名单会过期：
#: 新增一层转传却没人登记，矩阵就把那层的内部调用当成"漏传"、把它外面的调用点整个跳过，
#: 断层反而被洗白。T68 改成 AST 自动识别（`detect_helpers`），下面的集合只当**期望值**用：
#: 推导结果与期望不一致就红（要么名单过期，要么冒出了没人复核过的新层）。
HELPER_WRITE_RE = re.compile(r"\bcfg\.(timeout|max_tokens|extra_body|temperature)\s*=")
LLM_HELPER_EXPECT = {"chat_with_retry", "_ask_llm"}
#: 请求函数在 provider 里的原名；`from … import chat as _chat` 这类别名靠
#: `Scan.aliases` 还原，否则换了个调用名就漏检（`_ask` 已经踩过一次）。
CHAT_NAMES = ("chat", "_chat")
HELPER_PARAM_POS = {"llm_timeout": 3, "llm_retries": 4,
                    "llm_max_tokens": 5, "llm_thinking": 6}
#: 目标参数名：`timeout=xxx` 这种具名传参也是供给（值来自上一层的形参，不是硬编码）
TARGET_BY_FIELD = {"llm_timeout": "timeout", "llm_retries": "retries",
                   "llm_max_tokens": "max_tokens", "llm_thinking": "thinking"}

#: 直连发起请求的函数名（`_ask_llm` 是间接层，单独按调用点判）
LLM_CALLERS = ("chat", "_chat")

#: 构造点用 `**(kwargs)` 整体摊派的写法（RiskManager 就是这么吃 `risk:` 段的）
SPLIT_RE = re.compile(r"\*\*\s*[\{(]")

_LITERAL_RE = re.compile(r"[\d.]+|True|False|\"[^\"]*\"|'[^']*'", re.I)

ROLE_LABEL = {"path": "请求路径", "helper": "间接层内部", "helper_site": "间接层调用点"}


# ------------------------------------------------------------------ surface 载入

def _import(module: str):
    import importlib

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(module)


def surface_fields(name: str) -> set[str]:
    kind, module, obj = SURFACE_SPECS[name]
    target = getattr(_import(module), obj)
    if kind == "dataclass":
        return {f.name for f in target.__dataclass_fields__.values()}
    if kind == "dict_keys":
        return set(target.keys())
    if kind == "init_params":
        import inspect

        sig = inspect.signature(target.__init__)
        return {p for p in sig.parameters if p not in ("self", "args", "kwargs")}
    raise ValueError(f"未知 surface 类型：{kind}")


def _source_files() -> list[Path]:
    return [p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts]


# ------------------------------------------------------------------ AST 扫描

@dataclass
class Site:
    path: str
    line: int
    kind: str          # attr / subscript / dict_get / kwarg / field_def / assign
    text: str = ""


def fk(path: str, fname: str) -> str:
    """函数按 `文件::名` 索引——同名函数在两个文件里都存在（`run`/`emit`/`main` 都是），
    只按名字建表会让后出现的那份 body 永远查不到，矩阵就**静默少一行**。"""
    return f"{path}::{fname}"


@dataclass
class Scan:
    reads: dict[str, list[Site]] = dc_field(default_factory=dict)
    declares: dict[str, list[Site]] = dc_field(default_factory=dict)
    functions: dict[str, tuple[str, int, int]] = dc_field(default_factory=dict)
    calls: dict[str, list[tuple[str, str, int]]] = dc_field(default_factory=dict)
    #: 文件内 `from x import name as alias` 的还原表（alias → 原名）
    aliases: dict[str, dict[str, str]] = dc_field(default_factory=dict)
    #: 函数形参名（判断"薄壳是否原样转传自己的形参"要用）
    params: dict[str, set[str]] = dc_field(default_factory=dict)
    #: 函数体源码（间接层判定要用；顺带让整把尺子能喂合成源码，不必读磁盘）
    bodies: dict[str, str] = dc_field(default_factory=dict)

    def sites(self, name: str) -> list[Site]:
        return self.reads.get(name, [])


def scan_sources(files: dict[str, str]) -> Scan:
    """对 {相对路径: 源码} 做 AST 扫描，按**节点语义**分类，不按行文本猜。

    - `obj.field`（Load）/ `obj["field"]` / `.get("field")` / `getattr(o, "field")` ⇒ 读点；
    - `Foo(field=…)` 的 keyword ⇒ 写点（构造供给），不算消费；
    - dataclass 的 `field: type = …` 与 `{"field": …}` 字典字面量键 ⇒ 定义/搬运，不算消费。
      v1 把后面这些也算「用过了」，结果"零消费"这一栏永远不会红——而它要抓的恰恰是
      `llm_timeout` 那种"字段活着、没人读"的断层。
    """
    s = Scan()
    for rel, text in files.items():
        try:
            tree = ast.parse(text)
        except Exception:                       # noqa: BLE001 - 语法不过的文件由单测自己管
            continue
        lines = text.splitlines()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                for al in n.names:
                    s.aliases.setdefault(rel, {})[al.asname or al.name] = al.name
            elif isinstance(n, ast.Import):
                for al in n.names:
                    s.aliases.setdefault(rel, {})[(al.asname or al.name).split(".")[0]] = al.name

        def note(bucket, name, node, kind):
            lineno = getattr(node, "lineno", 0)
            bucket.setdefault(name, []).append(
                Site(rel, lineno, kind,
                     lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""))

        def walk(nodes, enclosing: str) -> None:
            for n in ast.iter_child_nodes(nodes):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fname = f"{enclosing}.{n.name}" if enclosing else n.name
                    key = fk(rel, fname)
                    s.functions[key] = (rel, n.lineno, n.end_lineno or n.lineno)
                    s.params[key] = {a.arg for a in
                                     list(n.args.args) + list(n.args.posonlyargs)
                                     + list(n.args.kwonlyargs)}
                    s.bodies[key] = "\n".join(
                        lines[n.lineno - 1:(n.end_lineno or n.lineno)])
                    walk(n, fname)
                    continue
                if isinstance(n, ast.ClassDef):
                    walk(n, enclosing)
                    continue
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                    note(s.declares, n.target.id, n, "field_def")
                if isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Del):
                    note(s.reads, n.attr, n, "attr")
                elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                        and isinstance(n.slice.value, str):
                    note(s.reads, n.slice.value, n, "subscript")
                elif isinstance(n, ast.Call):
                    fn = n.func
                    cname = (fn.id if isinstance(fn, ast.Name) else
                             fn.attr if isinstance(fn, ast.Attribute) else "")
                    if cname:
                        s.calls.setdefault(cname, []).append((rel, enclosing, n.lineno))
                    if cname in ("get", "getattr"):
                        arg = n.args[1] if cname == "getattr" else (
                            n.args[0] if n.args else None)
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            note(s.reads, arg.value, n, "dict_get")
                    for kw in n.keywords:
                        if kw.arg:
                            note(s.declares, kw.arg, kw, "kwarg")
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            note(s.declares, t.id, n, "assign")
                walk(n, enclosing)

        walk(tree, "")
    return s


def scan_package() -> Scan:
    """扫整个 `paper_trading/` 包（生产代码是唯一真相，测试代码不算消费点）。"""
    return scan_sources({str(p.relative_to(ROOT)).replace("\\", "/"):
                         p.read_text(encoding="utf-8") for p in _source_files()})


# ------------------------------------------------------------------ 四类尺子

def dead_knobs(scan: Scan, surfaces: dict[str, set[str]]) -> list[dict]:
    """零消费：字段在 surface 上定义，但**搬运层之外**没有任何读点。

    只在 `utils/config.py` 里 `agent.get("x")` 出现过 = 把 yaml 的值搬进 AgentConfig
    之后没人读 ⇒ 改了 config 也不改变行为。`llm_timeout` 当时就是这个形状：值一路搬到
    `AgentConfig.llm_timeout`，然后没有任何人读它（读的人拿的是 LLMConfig 的 30 秒默认）。
    """
    out = []
    for name, fields in surfaces.items():
        for f in sorted(fields):
            sites = scan.sites(f)
            real = [x for x in sites if x.path != NORMALIZER[0]]
            if real:
                continue
            out.append({"surface": name, "field": f,
                        "reads_total": len(sites),
                        "only_normalizer": bool(sites),
                        "where": [f"{x.path}:{x.line}" for x in sites][:3],
                        "declared": [f"{d.path}:{d.line}"
                                     for d in scan.declares.get(f, [])][:3],
                        "exempt": EXEMPT.get((name, f), "")})
    return out


def yaml_vs_surfaces(scan: Scan, surfaces: dict[str, set[str]]) -> list[dict]:
    """三面尺：config.yaml 的键 ⇄ 归一化白名单 ⇄ dataclass 字段。"""
    issues: list[dict] = []
    cfg_src = (ROOT / "config.yaml").read_text(encoding="utf-8")
    norm_src = (ROOT / NORMALIZER[0]).read_text(encoding="utf-8")
    for section, surface in YAML_SECTION_SURFACE.items():
        m = re.search(rf"^{section}:\n((?:[ \t].*\n)*)", cfg_src, re.M)
        yaml_keys = set(re.findall(r"^[ \t]+([a-z_][a-z0-9_]*):",
                                   m.group(1), re.M)) if m else set()
        fields = surfaces.get(surface, set())
        if surface == "AgentConfig":
            blk = re.search(r'"agent": \{\n(.*?)\n        \}', norm_src, re.S)
            norm_keys = set(re.findall(r'"([a-z_][a-z0-9_]*)":', blk.group(1))) \
                if blk else set()
        else:
            norm_keys = fields
        for k in sorted(yaml_keys - fields):
            issues.append({"kind": "yaml_key_not_in_surface", "section": section,
                           "surface": surface, "key": k,
                           "why": "config.yaml 写了这个键，但目标对象没有同名字段，"
                                  "值会在构造或白名单处被丢掉"})
        for k in sorted(yaml_keys - norm_keys):
            issues.append({"kind": "yaml_key_not_in_normalizer", "section": section,
                           "key": k,
                           "why": "归一化层没这个键 ⇒ 写了也到不了消费对象"
                                  "（v0.7.6 的 news_* 就是这个形状）"})
        for k in sorted((fields & yaml_keys) - norm_keys):
            issues.append({"kind": "field_not_in_normalizer", "section": section,
                           "surface": surface, "key": k,
                           "why": "字段存在且 yaml 有键，但归一化层不搬 ⇒ cli 拿默认值"})
    return issues


def _split_args(text: str) -> list[str]:
    """按顶层逗号切参数（忽略嵌套括号与字符串里的逗号）。"""
    parts, depth, cur, instr = [], 0, "", None
    for ch in text:
        if instr:
            cur += ch
            if ch == instr:
                instr = None
            continue
        if ch in "\"'":
            instr = ch
            cur += ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _site_cell(field: str, args_text: str) -> dict:
    """间接层调用点的供给判定：出现配置字段名，**或**目标参数以非字面量具名传入。

    后者是给"薄壳委托"留的路：`_ask_llm` 改成调 `chat_with_retry(timeout=timeout,…)` 之后
    壳里不再出现 `llm_timeout` 字样——那只算把上一层的值转下去，不算漏传。真正算漏传的
    是"这一跳给了字面量"（`retries=1` 那种）或"这一跳根本没提这个参数"。
    """
    tgt = TARGET_BY_FIELD.get(field, "")
    by_name = bool(re.search(rf"\b{field}\b", args_text))
    by_kwarg = bool(tgt and re.search(rf"\b{tgt}\s*=\s*(?!['\"0-9])", args_text))
    return {"path": by_name or by_kwarg, "ctor": False, "target": tgt,
            "ok": by_name or by_kwarg}


def _hardcoded_helper_args(args_text: str) -> list[str]:
    """`_ask_llm(cfg, prompt, system, timeout, 1, max_tokens, thinking)`：
    第 5 个实参是裸 `1` 而不是 `…llm_retries` ⇒ 配置被硬编码盖住
    （今天恰好等于默认值，所以行为看不出问题，改 config 无效）。"""
    out = []
    parts = _split_args(args_text)
    for f, pos in HELPER_PARAM_POS.items():
        tgt = TARGET_BY_FIELD.get(f, "")
        if re.search(rf"\b{f}\b", args_text):
            continue
        if tgt and re.search(rf"\b{tgt}\s*=\s*(?!['\"0-9])", args_text):
            continue                      # 具名转下去的（薄壳委托），不算硬编码
        kw = re.search(rf"\b{tgt}\s*=\s*({_LITERAL_RE.pattern})", args_text) if tgt else None
        if kw:
            out.append(f"{f} 被硬编码 {kw.group(1)} 覆盖（改 config 无效）")
            continue
        if len(parts) > pos and _LITERAL_RE.fullmatch(parts[pos].strip()):
            out.append(f"{f} 被硬编码 {parts[pos].strip()} 覆盖（改 config 无效）")
    return out


def _extract_call_args(text: str, call_index: int) -> str:
    """从 `_ask_llm` 的调用处取**完整实参文本**（按括号配平，不能截在第一个 `)`）。

    用非贪婪正则截过一次就出假：`_ask_llm(b._llm_config(), user_p, sys_p, …)` 被截成
    `b._llm_config(`，四个配置字段全被判成"没传"。误报的代价是门禁被人当噪声关掉。
    """
    i = text.index("(", call_index)
    depth = 0
    for j in range(i, len(text)):
        ch = text[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
    return text[i + 1:]


#: 间接层必须把值真正写进请求，否则"调用点传了配置"也是白传
HELPER_FORWARD = {
    "llm_timeout": r"cfg\.timeout\s*=",
    "llm_retries": r"range\(.*retries.*\+\s*1\)",
    "llm_max_tokens": r"cfg\.max_tokens\s*=",
    "llm_thinking": r"cfg\.extra_body\s*=",
}


def resolve_name(scan: Scan, path: str, name: str) -> str:
    """把调用名还原成它在定义模块里的原名（`import chat as _chat` 那种类名）。"""
    return scan.aliases.get(path, {}).get(name, name)


def iter_calls(scan: Scan, names) -> list[tuple[str, str, int, str]]:
    """遍历"解析后命中 names 的调用点"，返回 (文件, 外层函数, 行号, 原名)。"""
    out = []
    wanted = set(names)
    for (callee, sites) in scan.calls.items():
        for (path, enclosing, line) in sites:
            if resolve_name(scan, path, callee) in wanted:
                out.append((path, (enclosing or "").split(".")[0], line, callee))
    return sorted(out)


def _find_call(body: str, callee: str) -> int:
    """找函数调用的位置，避开**子串碰撞**：`def llm_ask(...)` 里就含 `_ask(`。

    真出过这个假阳：`llm_ask` 被报成"四个字段都没传"，因为提取到的是它自己的
    形参列表。要求调用名前不是单词字符也不是 `.`（排除 `mod._ask(` 与方法名尾巴）。
    """
    m = re.search(r"(?<![\w.])" + re.escape(callee) + r"\(", body)
    return m.start() if m else -1


def detect_helpers(scan: Scan) -> dict[str, dict]:
    """自动识别"把配置写进请求对象再转传"的间接层，含**多层链**（T68 切入点①）。

    第 1 层（直接层）：函数体内有 `cfg.<目标参数> =` 的写入，且它（含内部闭包）最终调 `chat`。
    第 2 层起（传递层）：函数调用已知间接层，且四个 LLM 字段都以**非字面量**转传下去
    （`_ask_llm` 就是这么把 timeout/retries/max_tokens/thinking 交给 `chat_with_retry` 的）。
    只认第 1 层会漏掉真正的风险面：调用点藏在第 2 层后面，`retries=1` 那种硬编码就没人看了。
    人工名单会过期——新加一层没登记，矩阵把那层的内部调用报成漏传、又把它外面的调用点
    整条跳过，**断层反而被洗白**，所以改成推导，登记集合只当期望值用（漂移即红）。
    """
    derived: dict[str, dict] = {}
    chat_sites = iter_calls(scan, CHAT_NAMES)
    for (path, outer, line, callee) in chat_sites:
        if not outer or fk(path, outer) not in scan.functions:
            continue
        fpath, start, end = scan.functions[fk(path, outer)]
        if fpath != path:
            continue
        body = scan.bodies.get(fk(path, outer), "")
        writes = sorted(set(HELPER_WRITE_RE.findall(body)))
        if writes:
            got = derived.setdefault(outer, {"file": fpath, "writes": [], "line": line,
                                             "level": 1})
            got["writes"] = sorted(set(got["writes"]) | set(writes))

    # 传递层：**只认真正的薄壳**——它把自己形参原样交给下一层（`_ask_llm` 的
    # `timeout/retries/max_tokens/thinking` 就是形参）。业务函数调 `_ask_llm` 时传的是
    # `self.cfg.llm_*`，那是**供给点**（要看它有没有把配置带上），不是壳；
    # 把业务函数也算成层会雪崩——`run/tick/main` 全都变"层"，矩阵就没人被检查了。
    while True:
        added = False
        for name in sorted(derived):
            for (path, outer, line, callee) in iter_calls(scan, [name]):
                key = fk(path, outer)
                if outer in derived or not outer or key not in scan.functions:
                    continue          # 已经在层里/或不是函数内的调用
                params = scan.params.get(key, set())
                if not params:
                    continue
                fpath = scan.functions[key][0]
                body = scan.bodies.get(key, "")
                idx = _find_call(body, callee)
                if idx < 0:
                    continue
                args = _extract_call_args(body, idx)
                # 形参名用 TARGET_BY_FIELD（`thinking`），不是 LLM_FIELD_TARGET
                # （`extra_body`，那是请求侧属性名）——两张表含义不同，混用会把
                # 薄壳判成"没转传"，链条就断在这里。
                forwarded = all(
                    f in args or (
                        re.search(rf"\b{TARGET_BY_FIELD[f]}\s*=\s*"
                                  rf"{TARGET_BY_FIELD[f]}\b", args)   # 具名原样转传
                        or TARGET_BY_FIELD[f] in params)
                    for f in LLM_FIELDS)
                if forwarded and set(TARGET_BY_FIELD.values()) <= params:
                    derived[outer] = {"file": fpath, "writes": [], "line": line,
                                      "level": derived[name].get("level", 1) + 1,
                                      "forwards_to": name}
                    added = True
                    break
        if not added:
            break
    return derived


def llm_hop_matrix(scan: Scan, ctor_kwargs: Optional[set[str]] = None,
                   helpers: Optional[dict] = None) -> list[dict]:
    """每个真正发 LLM 请求的调用点，逐字段看 agent.llm_* 有没有落到请求上。

    三类行，各自的"接上"定义不同：
    * `path`（直连 `chat`）：本函数引用了配置，或构造点把值喂给了目标参数
      （`LLMConfig(timeout=…)` 主 AI 11-03 就是这么修的）；
    * `helper`（间接层自身）：值必须真写进 `cfg.timeout/cfg.max_tokens/cfg.extra_body`
      与重试循环，否则调用点传了也白传；
    * `helper_site`（间接层的调用点）：实参里出现配置字段名，或目标参数以非字面量具名
      转传才算供给；那个位置给字面量 ⇒ **硬编码覆盖**（今天恰好等于默认，改 config 无效）。
    只算一路会两头出假：只算路径会把已修的 timeout 报成漏传，只算构造点会把
    "问答路径没带 max_tokens"这种真漏传洗白。
    """
    ctor_kwargs = ctor_kwargs or set()
    helpers = helpers if helpers is not None else detect_helpers(scan)
    rows: list[dict] = []

    def body_of(path: str, outer: str) -> str:
        return scan.bodies.get(fk(path, outer), "")   # 扫描时已带源码

    for (path, outer, line, callee) in iter_calls(scan, CHAT_NAMES):
        if not outer or fk(path, outer) not in scan.functions:
            continue
        if outer in helpers:                       # 间接层内部那次由 helper 行统一判
            continue
        body = body_of(path, outer)
        cells = {}
        for f in LLM_FIELDS:
            tgt = LLM_FIELD_TARGET.get(f, "")
            in_path = bool(re.search(rf"\b{f}\b", body))
            by_ctor = tgt in ctor_kwargs
            cells[f] = {"path": in_path, "ctor": by_ctor, "target": tgt,
                        "ok": in_path or by_ctor}
        rows.append({"call": f"{callee}()", "file": path, "function": outer,
                     "line": line, "role": "path", "cells": cells})

    for name in sorted(helpers):
        info = helpers[name]
        if info.get("level", 1) > 1:
            # 传递层（如 `_ask_llm`）自己不写 cfg，只把参数交给下一层——这点在推导时
            # 已经逐字段核过"非字面量转传"，这里如实标注，不再套 `cfg.x =` 的判据。
            cells = {f: {"path": True, "ctor": False, "target": f, "ok": True,
                         "via": info.get("forwards_to", "")} for f in LLM_FIELDS}
            rows.append({"call": f"{name} 转传层", "file": info["file"],
                         "function": name, "line": info["line"], "role": "helper",
                         "cells": cells})
        else:
            body = body_of(info["file"], name)
            cells = {f: {"path": bool(re.search(pat, body)), "ctor": False,
                         "target": f, "ok": bool(re.search(pat, body))}
                     for f, pat in HELPER_FORWARD.items()}
            rows.append({"call": f"{name} 自身", "file": info["file"], "function": name,
                         "line": info["line"], "role": "helper", "cells": cells})
        for (path, outer, line, callee) in iter_calls(scan, [name]):
            if path == info["file"] and outer == name:
                continue                            # 层内部的自调用不是供给点
            body = scan.bodies.get(fk(path, outer), "")
            idx = _find_call(body, callee)
            if not body or idx < 0:
                continue
            args = _extract_call_args(body, idx)
            rows.append({"call": f"{name} 调用点", "file": path, "function": outer,
                         "line": line, "role": "helper_site",
                         "cells": {f: _site_cell(f, args) for f in LLM_FIELDS},
                         "hardcoded": _hardcoded_helper_args(args), "args": args})

    return sorted(rows, key=lambda x: (x["role"], x["file"], x["line"]))


def _ctor_kwargs_for(scan: Scan, target: str, want_file: str,
                     aliases: Iterable[str] = ()) -> tuple[set[str], int, bool]:
    """目标对象在指定文件里的构造点 kwargs（`**{...}` 摊派单独标出来）。"""
    kwargs: set[str] = set()
    sites = [c for c in scan.calls.get(target, []) if want_file in c[0]]
    for cname in set(aliases):
        sites += [c for c in scan.calls.get(cname, []) if want_file in c[0]]
    splat = False
    for path, _enc, line in sites:
        src = (ROOT / path).read_text(encoding="utf-8").splitlines()
        blob = "\n".join(src[line - 1:min(len(src), line + 14)])
        kwargs |= set(re.findall(r"([a-z_][a-z0-9_]*)=", blob))
        if SPLIT_RE.search(blob):
            splat = True
    return kwargs, len(sites), splat


def hop_supply(scan: Scan, surfaces: dict[str, set[str]]) -> list[dict]:
    """构造点供给：目标对象的每个参数，在构造处是被供给还是一路用默认值。"""
    out = []
    checks = [("LLMConfig", "cli.py", ()), ("EventConfig", "agent/events.py", ("cls",)),
              ("RiskManager", "cli.py", ())]
    norm_src = (ROOT / NORMALIZER[0]).read_text(encoding="utf-8")
    risk_blk = re.search(r'"risk": \{\n(.*?)\n        \}', norm_src, re.S)
    risk_keys = set(re.findall(r'"([a-z_][a-z0-9_]*)":', risk_blk.group(1))) \
        if risk_blk else set()
    for target, want_file, aliases in checks:
        kwargs, n_sites, splat = _ctor_kwargs_for(scan, target, want_file, aliases)
        supplied = kwargs & surfaces[target]
        if splat and target == "RiskManager":
            supplied |= risk_keys        # `**(risk_kwargs)`：归一化白名单键就是供给集
        ex = {k for (s, k) in EXEMPT if s == target}
        missing = sorted(surfaces[target] - supplied)
        out.append({"target": target, "ctor_sites": n_sites, "splat": splat,
                    "supplied": sorted(supplied),
                    "defaulted": [m for m in missing if m not in ex],
                    "exempted": [m for m in missing if m in ex]})
    return out


# ------------------------------------------------------------------ 汇总

def audit() -> dict:
    scan = scan_package()
    surfaces = {name: surface_fields(name) for name in SURFACE_SPECS}
    llm_ctor, _n, _s = _ctor_kwargs_for(scan, "LLMConfig", "cli.py")
    dead = dead_knobs(scan, surfaces)
    helpers = detect_helpers(scan)
    hops = llm_hop_matrix(scan, llm_ctor, helpers)
    yaml_issues = yaml_vs_surfaces(scan, surfaces)
    supply = hop_supply(scan, surfaces)

    dead_bad = [d for d in dead if not d["exempt"]]
    hop_bad = [{"function": h["function"], "file": h["file"], "line": h["line"],
                "role": h["role"], "hardcoded": h.get("hardcoded") or [],
                "missing": [f for f, c in h["cells"].items() if not c["ok"]]}
               for h in hops
               if any(not c["ok"] for c in h["cells"].values()) or h.get("hardcoded")]
    defaulted_bad = [{"target": s["target"], "fields": s["defaulted"]}
                     for s in supply if s["defaulted"]]
    bad_exempt = [f"{s}/{f}" for (s, f), reason in EXEMPT.items() if not str(reason).strip()]
    # 间接层漂移：推导出来的人没登记（新转传层没人复核过判定），或登记了但代码里已经没有
    # 那一层（判定还按间接层走，等于给不存在的层开后门）。两种都要红。
    helper_drift = {"unregistered": sorted(set(helpers) - LLM_HELPER_EXPECT),
                    "obsolete": sorted(LLM_HELPER_EXPECT - set(helpers))}

    return {
        "surfaces": {k: len(v) for k, v in surfaces.items()},
        "llm_config_ctor": sorted(llm_ctor & surfaces["LLMConfig"]),
        "helpers_derived": helpers,
        "helper_drift": helper_drift,
        "dead_knobs": dead,
        "dead_knobs_unexempted": dead_bad,
        "llm_hop_matrix": hops,
        "llm_hops_unsupplied": hop_bad,
        "yaml_mismatches": yaml_issues,
        "ctor_supply": supply,
        "ctor_defaulted": defaulted_bad,
        "exempt_missing_reason": bad_exempt,
        "ok": not (dead_bad or hop_bad or yaml_issues or bad_exempt
                   or helper_drift["unregistered"] or helper_drift["obsolete"]),
    }


def render(r: dict) -> str:
    out = []
    out.append("配置面字段数：" + "，".join(f"{k}={v}" for k, v in r["surfaces"].items()))
    out.append("")
    out.append(f"① 零消费（改了 config 也没用）：未豁免 {len(r['dead_knobs_unexempted'])} 项"
               f"，另有豁免 {len(r['dead_knobs']) - len(r['dead_knobs_unexempted'])} 项")
    for d in r["dead_knobs_unexempted"]:
        how = "只在归一化层被搬了一次" if d["only_normalizer"] else "全包零读点"
        out.append(f"   🔴 {d['surface']}.{d['field']}  {how}  "
                   f"声明={'，'.join(d['declared'])}")
    for d in r["dead_knobs"]:
        if d["exempt"]:
            out.append(f"   ⚪ {d['surface']}.{d['field']} 已豁免：{d['exempt']}")
    out.append("")
    out.append(f"② LLM 请求侧跨对象传递矩阵：断层 {len(r['llm_hops_unsupplied'])} 处｜"
               f"LLMConfig 构造点已供给：{r['llm_config_ctor']}")
    out.append(f"   {'调用点':<22}{'角色':<14}{'文件':<26}"
               + "".join(f"{f:>17}" for f in LLM_FIELDS))
    for h in r["llm_hop_matrix"]:
        cells = []
        for f in LLM_FIELDS:
            c = h["cells"][f]
            mark = "✅" if c["ok"] else "🔴漏传"
            how = "路径" if c["path"] else ("构造" if c["ctor"] else "—")
            cells.append(f"{mark}{how}".rjust(17))
        out.append(f"   {h['function'][:21]:<22}{ROLE_LABEL[h['role']]:<14}"
                   f"{h['file']:<26}" + "".join(cells))
    for h in r["llm_hops_unsupplied"]:
        if h["missing"]:
            out.append(f"   🔴 {h['file']}:{h['line']} {h['function']}"
                       f"（{ROLE_LABEL[h['role']]}）没带进请求：{h['missing']}")
        for note_text in h["hardcoded"]:
            out.append(f"   🔴 {h['file']}:{h['line']} {h['function']} {note_text}")
    drift = r["helper_drift"]
    out.append(f"   间接层（AST 推导，含多层链）：{sorted(r['helpers_derived'])}——"
               f"第 1 层把配置写进 cfg 再调 chat，第 2 层起是把自己形参原样转传的薄壳；"
               f"登记外/过期：{drift['unregistered'] or '无'} / {drift['obsolete'] or '无'}")
    if drift["unregistered"] or drift["obsolete"]:
        out.append("   🔴 间接层与登记不一致：新转传层没人复核过判定，或名单里留着已不存在的层")
    out.append("")
    out.append(f"③ 三面白名单（yaml ⇄ 归一化 ⇄ 字段）：断层 {len(r['yaml_mismatches'])} 条")
    for i in r["yaml_mismatches"]:
        out.append(f"   🔴 [{i['kind']}] {i.get('section')}/{i['key']} — {i['why']}")
    out.append("")
    out.append("④ 构造点供给（默认值兜住不等于接线；只报不进门禁，供人工过一遍）：")
    for s in r["ctor_supply"]:
        how = "（`**kwargs` 摊派）" if s["splat"] else ""
        out.append(f"   {s['target']}（构造点 {s['ctor_sites']} 处{how}）已供给 {s['supplied']}")
        if s["defaulted"]:
            out.append(f"      ⚠️ 走默认：{s['defaulted']}")
        if s["exempted"]:
            out.append(f"      ⚪ 已豁免：{s['exempted']}")
    if r["exempt_missing_reason"]:
        out.append(f"\n🔴 豁免没写理由：{r['exempt_missing_reason']}")
    out.append("")
    out.append("结论：" + ("✅ 无未豁免断层" if r["ok"] else "🔴 有断层（退出码 1）"))
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    r = audit()
    if "--json" in argv:
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    elif "--quiet" in argv:
        print("断层：" + ("无" if r["ok"] else
             f"零消费 {len(r['dead_knobs_unexempted'])}、传递矩阵 "
             f"{len(r['llm_hops_unsupplied'])}、白名单 {len(r['yaml_mismatches'])}"))
    else:
        print(render(r))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
