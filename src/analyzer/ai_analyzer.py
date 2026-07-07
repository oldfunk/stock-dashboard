"""AI 选股分析模块（项目唯一的 AI 调用实现）

调用 LLM 对筛选出的股票生成：选股解析 / 投资策略 / 买卖策略。
所有脚本（run_pipeline.py / run_ai_analysis.py）都应通过本模块调用 AI，
避免多套重试策略与默认值不一致的问题。

设计要点：
- 默认使用 OpenCode Zen nemotron-3-ultra-free（免费、无 API Key 要求）
- 20 次重试应对偶发拥堵；间隔短（10s→30s）适合快速恢复的模型
- 健壮 JSON 解析（兼容 markdown 包裹 / 前后噪声文本 / 末尾逗号 / 单引号 / 截断）
- 默认 API/模型与 config.yaml 对齐，可被环境变量覆盖
"""

import json
import logging
import os
import re
import time
from typing import Optional, Callable

import httpx

logger = logging.getLogger(__name__)


# 重试策略：deepseek-v4-flash-free 是推理模型，响应约 30-60s
# 前 5 次快速探测（5→10→15→20→30s），之后固定 60s 间隔
# 共 20 次 ≈ 18 分钟最大等待
BACKOFF_SCHEDULE: list[int] = [
    5, 10, 15, 20, 30,       # 前 5 次：快速探测恢复
] + [60] * 15                # 之后：每 60s 重试一次

# 默认配置：OpenCode Zen → deepseek-v4-flash-free（免费，无需 API Key）
# 备选免费模型：nemotron-3-ultra-free（效果更好但偶有服务故障）
DEFAULT_API_BASE = "https://opencode.ai/zen/v1"
DEFAULT_MODEL = "deepseek-v4-flash-free"

# AI 分析 Prompt 模板 — 投资人笔记风格
# 不套框架、不写"视角一/二/三"、不用段永平/巴菲特/芒格名字
# 让 AI 扮演一位有经验的投资人，写出有观点、有温度的个人笔记
ANALYSIS_PROMPT = """你是一位有十年A股经验的价值投资人，正在自己的笔记本上记录对一只股票的思考。
你的语言要像一个人在自言自语地梳理思路——有观点、有态度、有犹豫、有结论。
不要套路、不要分点编号、不要写"视角一/二/三"、不要提股神名字。
就像你在跟朋友聊这只股票：先说你看不看好，然后说为什么，再说你最担心什么，最后说你的决定。

这只股票的基本面如下：
- {name}（{code}）
- 所属行业：{sector}
- PE：{pe} | PB：{pb} | ROE：{roe}%
- 营收增长：{revenue_growth}% | 净利增长：{profit_growth}%
- 资产负债率：{debt_ratio}% | 市值：{market_cap}亿

为什么会被筛选出来：{reason}

请你写三段话（写在 analysis 字段里）：
第一段：这生意怎么样？
这句话能不能说清楚它怎么赚钱？它的护城河是真有还是看着有？财务数据（ROE、增长、负债）在你看来是真是假？你觉得它10年后还在不在？

第二段：现在值不值？
这个价格你觉得贵不贵？PE/PB相对于增长和ROE是什么水平？安全垫够不够厚？如果判断错了你能亏多少？

第三段：你怎么决定？
诚实地说你的决定是 BUY / HOLD / AVOID 之一（注意：不是每只股票都值得买）。你的信心度是高/中/低？简单说核心理由。

写完三段后，在 investment_strategy 里写下你的投资策略——仓位建议、持有周期、什么类型的人适合买。
在 trade_strategy 里填入买卖信号、价格区间、止盈止损。

输出严格为以下JSON格式，不要包含其他内容：
{{
    "analysis": "你的三段式笔记正文。用第一人称，自然语气。不用markdown格式。",
    "investment_strategy": "投资策略建议。一句话说清仓位和周期。",
    "trade_strategy": {{
        "signal": "BUY 或 HOLD 或 AVOID",
        "confidence": "高 或 中 或 低",
        "buy_zone": "你觉得值得买入的价格区间",
        "target_price": "目标价",
        "stop_loss": "止损条件",
        "take_profit": "止盈条件"
    }}
}}
"""


def _escape_newlines_in_strings(text: str) -> str:
    """将 JSON 字符串值中的实际换行符替换为 \\n。

    推理模型（如 deepseek-v4-flash-free）经常在 JSON 字符串值内输出
    未经转义的换行符，导致 json.loads() 失败。此函数逐字符扫描，
    仅在双引号内部替换换行符。
    """
    result = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                result.append(ch)
                escape = False
                continue
            if ch == '\\':
                result.append(ch)
                escape = True
                continue
            if ch == '"':
                in_string = False
            elif ch in '\n\r':
                # 转义未转义的换行符
                result.append('\\n')
                continue
        elif ch == '"':
            in_string = True
        result.append(ch)
    return ''.join(result)


def _complete_truncated_json(text: str) -> str:
    """补全被截断的 JSON：统计未闭合的 { [ 并补全 } ]"""
    cleaned = text.replace("'", '"')
    cleaned = re.sub(r',\s*}', '}', cleaned)
    cleaned = re.sub(r',\s*]', ']', cleaned)
    
    # 统计未闭合的大括号和中括号
    open_braces = cleaned.count('{') - cleaned.count('}')
    open_brackets = cleaned.count('[') - cleaned.count(']')
    
    # 补全
    cleaned += '}' * max(0, open_braces)
    cleaned += ']' * max(0, open_brackets)
    return cleaned


def parse_ai_response(content: str) -> Optional[dict]:
    """健壮地解析 LLM 返回的 JSON。

    兼容：markdown 代码块包裹、前后噪声文本、末尾逗号、单引号、截断补全。
    返回包含 analysis / investment_strategy / trade_strategy 的 dict，失败返回 None。
    """
    if not content:
        return None

    text = content.strip()

    # 1. 去掉 markdown 代码块包裹
    if '```json' in text:
        text = text.split('```json', 1)[1]
        text = text.split('```', 1)[0].strip()
    elif '```' in text:
        blocks = text.split('```')
        # 取最后一个以 { 开头的代码块
        extracted = None
        for b in reversed(blocks):
            b = b.strip()
            if b.startswith('{'):
                extracted = b
                break
        text = extracted if extracted else (blocks[1] if len(blocks) > 1 else text)

    # 2. 截取第一个 { 到最后一个 } 之间的内容
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        text = m.group(1)

    # 3. 直接解析
    try:
        result = json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 4. 修复常见问题：末尾逗号、单引号、截断补全
        cleaned = text.replace("'", '"')
        cleaned = re.sub(r',\s*}', '}', cleaned)
        cleaned = re.sub(r',\s*]', ']', cleaned)
        # 处理截断：如果 trade_strategy 未闭合，补全它
        if cleaned.startswith('{') and not cleaned.endswith('}'):
            # 找到最后一个完整的字段，补全剩余结构
            cleaned += '}'
        try:
            result = json.loads(cleaned, strict=False)
        except json.JSONDecodeError:
            # 5. 推理模型常在 JSON 字符串内输出未转义的实际换行符
            #    尝试将字符串内容中的实际换行替换为 \n
            try:
                cleaned = _escape_newlines_in_strings(cleaned)
                result = json.loads(cleaned, strict=False)
            except (json.JSONDecodeError, ValueError):
                # 6. 终极兜底：逐层补全嵌套结构
                cleaned = _complete_truncated_json(text)
                try:
                    result = json.loads(cleaned, strict=False)
                except json.JSONDecodeError:
                    logger.error(f"[AI分析] JSON 解析失败，前200字符: {text[:200]}")
                    return None

    # 5. 校验必要字段
    required = ['analysis', 'investment_strategy', 'trade_strategy']
    if not all(k in result for k in required):
        logger.warning(f"[AI分析] 返回字段不完整: {list(result.keys())}")
        return None
    return result


# 已知的免费模型列表（无需 API Key）
# 不再硬编码——FreeModelPool 自动从 /api/models 发现
FREE_MODELS = {"nemotron-3-ultra-free", "deepseek-v4-flash-free", "mimo-v2.5-free"}

# FreeModelPool 全局单例（延迟初始化）
_model_pool: Optional['FreeModelPool'] = None


class FreeModelPool:
    """免费模型自动发现与故障轮换池。

    每 30 分钟查询 OpenCode Zen /api/models，筛选出所有 "-free" 后缀的模型，
    按偏好顺序排列。调用 acquire() 返回一个当前可用的模型，
    如果模型调用失败，调用 mark_dead() 将其暂时黑名单，自动切换到下一个。
    """

    # 偏好顺序：效果好的排前面
    PREFERRED_ORDER = [
        "nemotron-3-ultra-free",
        "deepseek-v4-flash-free",
        "mimo-v2.5-free",
    ]

    def __init__(self, api_base: str, api_key: str = None):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self._pool: list[str] = []        # 当前可用模型列表（按偏好排序）
        self._dead: set[str] = set()      # 本轮已标记为不可用的模型
        self._current = 0                 # 轮换游标
        self._last_refresh = 0.0
        self._refresh_interval = 1800     # 30 秒刷新（首次会立即刷新）
        self._logger = logging.getLogger("FreeModelPool")

    # ── 公开接口 ──

    def acquire(self) -> Optional[str]:
        """获取一个当前可用的模型。如果全部不可用，强制刷新后重试。"""
        now = time.time()
        if now - self._last_refresh > self._refresh_interval or not self._pool:
            self._refresh()

        # 在池中找一个不在黑名单的
        for _ in range(len(self._pool) + 1):
            if not self._pool:
                break
            idx = self._current % len(self._pool)
            self._current = idx + 1
            model = self._pool[idx]
            if model not in self._dead:
                return model

        # 全死了 → 强制刷新并清空黑名单
        self._logger.warning("[FreeModelPool] 全部模型已下线，强制刷新列表")
        self._dead.clear()
        self._current = 0
        self._refresh()
        return self._pool[0] if self._pool else None

    def mark_dead(self, model: str):
        """标记一个模型为不可用（本次分析周期不再尝试）。"""
        if model in self._dead:
            return
        self._dead.add(model)
        alive = len(self._pool) - len(self._dead)
        self._logger.warning(
            "[FreeModelPool] ❌ %s 下线（剩余 %d 个候选）", model, alive)

    def current_pool(self) -> list[str]:
        """返回当前模型池快照（用于日志/展示）。"""
        return list(self._pool)

    # ── 内部 ──

    def _refresh(self):
        """从 API 查询可用免费模型。"""
        self._last_refresh = time.time()
        try:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            url = f"{self.api_base}/models"

            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url, headers=headers)

            if resp.status_code != 200:
                self._logger.warning(
                    "[FreeModelPool] 查询模型列表 HTTP %d，使用已有缓存", resp.status_code)
                return

            data = resp.json()
            all_models = [m["id"] for m in data.get("data", [])]
            free_models = [m for m in all_models if m.endswith("-free")]

            if not free_models:
                self._logger.warning("[FreeModelPool] API 未返回任何 free 模型，保留旧缓存")
                return

            # 按偏好排序 + 保留新发现的
            ordered = []
            seen = set()
            for pref in self.PREFERRED_ORDER:
                if pref in free_models and pref not in seen:
                    ordered.append(pref)
                    seen.add(pref)
            for m in free_models:
                if m not in seen:
                    ordered.append(m)
                    seen.add(m)

            self._pool = ordered
            # 从黑名单中移除已不在池中的模型
            self._dead &= set(self._pool)
            self._current = 0
            self._logger.info(
                "[FreeModelPool] ✅ 发现 %d 个免费模型: %s",
                len(self._pool), ", ".join(self._pool))

        except Exception as e:
            self._logger.warning(
                "[FreeModelPool] 刷新失败: %s，使用已有缓存", e)

    def __repr__(self):
        alive = len(self._pool) - len(self._dead)
        return f"FreeModelPool({alive}/{len(self._pool)} alive)"


def get_model_pool(api_base: str, api_key: str = None) -> FreeModelPool:
    """获取 FreeModelPool 全局单例。"""
    global _model_pool
    if _model_pool is None:
        _model_pool = FreeModelPool(api_base, api_key)
    return _model_pool


class AiAnalyzer:
    """AI 选股分析器（项目唯一实现）。"""

    def __init__(self, config: dict = None):
        """
        Args:
            config: config.yaml 中 ai 段，形如::

                {
                    'api_base': 'https://opencode.ai/zen/v1',
                    'model': 'deepseek-v4-flash-free',
                    'temperature': 0.3,
                    'max_tokens': 6000,
                }

            环境变量优先于 config：STOCK_AI_API_KEY / STOCK_AI_API_BASE / STOCK_AI_MODEL
        """
        cfg = config or {}
        self.api_base = (
            os.getenv('STOCK_AI_API_BASE')
            or cfg.get('api_base')
            or DEFAULT_API_BASE
        ).rstrip('/')
        # 允许 .env 里已包含 /chat/completions
        if self.api_base.endswith('/chat/completions'):
            self.api_base = self.api_base[:-len('/chat/completions')]
        self.model = os.getenv('STOCK_AI_MODEL') or cfg.get('model') or DEFAULT_MODEL
        self.temperature = cfg.get('temperature', 0.3)
        self.max_tokens = cfg.get('max_tokens', 6000)

        self.api_key = os.getenv('STOCK_AI_API_KEY') or os.getenv('OPENAI_API_KEY')
        self._is_free_model = self.model.endswith('-free')
        # 免费模型自动启用模型池（自动发现 + 故障轮换）
        if self._is_free_model:
            self._pool = get_model_pool(self.api_base, self.api_key)
        else:
            self._pool = None
        if not self.api_key and not self._is_free_model:
            logger.warning(
                "[AI分析] 未设置 API Key（STOCK_AI_API_KEY / OPENAI_API_KEY），"
                "且模型 %s 不是已知免费模型", self.model
            )

    @property
    def configured(self) -> bool:
        return bool(self.api_key) or self._is_free_model

    def analyze_stock(self, stock: dict) -> Optional[dict]:
        """对一只股票进行 AI 分析，返回解析后的 dict（失败返回 None）。"""
        if not self.api_key and not self._is_free_model:
            logger.error("[AI分析] 无 API Key，跳过分析")
            return None

        prompt = ANALYSIS_PROMPT.format(
            name=stock.get('name', ''),
            code=stock.get('code', ''),
            pe=stock.get('pe', 'N/A'),
            pb=stock.get('pb', 'N/A'),
            roe=stock.get('roe', 'N/A'),
            revenue_growth=stock.get('revenue_growth', 'N/A'),
            profit_growth=stock.get('profit_growth', 'N/A'),
            debt_ratio=stock.get('debt_ratio', 'N/A'),
            market_cap=stock.get('market_cap', 'N/A'),
            sector=stock.get('sector', '未知'),
            reason=stock.get('reason', ''),
        )

        content, used_model = self._call_llm(prompt)
        if not content:
            return None
        logger.info(f"[AI分析] 模型=[{used_model}] {stock.get('code')} {stock.get('name')}")
        return parse_ai_response(content)

    # ── 模型调用（含故障轮换） ──

    def _resolve_model(self) -> str:
        """返回本次应使用的模型名称。免费模型从池中获取，非免费用配置值。"""
        if self._pool:
            model = self._pool.acquire()
            if model:
                return model
            logger.warning("[AI分析] 模型池无可用模型，回退到配置值: %s", self.model)
        return self.model

    def _mark_model_dead(self, model: str):
        """标记模型不可用（仅对池中的免费模型生效）。"""
        if self._pool:
            self._pool.mark_dead(model)

    def _call_llm(self, prompt: str) -> tuple[Optional[str], Optional[str]]:
        """调用 LLM API，使用 BACKOFF_SCHEDULE 做 20 次退避重试。

        返回 (content, used_model)，失败时 content 为 None。
        当 enable_pool=True 时，模型会在连续故障时自动轮换。
        """
        headers = {
            'Content-Type': 'application/json',
        }
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        # 当前的模型（可能会在轮换中改变）
        current_model = self._resolve_model()
        url = f"{self.api_base}/chat/completions"
        max_retries = len(BACKOFF_SCHEDULE)

        for attempt in range(max_retries):
            payload = {
                'model': current_model,
                'messages': [
                    {'role': 'system',
                     'content': '你只输出JSON，不输出其他任何内容。必须严格按照用户指定的JSON结构输出。'},
                    {'role': 'user', 'content': prompt},
                ],
                'temperature': self.temperature,
                'max_tokens': self.max_tokens,
            }

            try:
                with httpx.Client(timeout=240.0) as client:
                    resp = client.post(url, headers=headers, json=payload)

                if resp.status_code == 200:
                    try:
                        content = resp.json()['choices'][0]['message']['content']
                    except (KeyError, IndexError, ValueError) as e:
                        logger.warning("[AI分析] 200但响应结构异常: %s (%s), 轮换模型", e, current_model)
                        self._mark_model_dead(current_model)
                        current_model = self._resolve_model()
                        time.sleep(2)
                        continue
                    if not content or len(content) < 10:
                        logger.warning(
                            "[AI分析] 响应内容过短(%s字符, %s), 轮换模型重试...",
                            len(content), current_model)
                        self._mark_model_dead(current_model)
                        current_model = self._resolve_model()
                        continue
                    return content, current_model

                # ── 非 200 状态码 ──
                should_rotate = self._is_fatal_model_error(resp)
                if should_rotate:
                    self._mark_model_dead(current_model)
                    current_model = self._resolve_model()
                    logger.warning(
                        "[AI分析] HTTP %d (%s), 已轮换至新模型", resp.status_code, current_model)
                elif resp.status_code == 429:
                    self._log_rate_limit(resp, attempt, max_retries)
                else:
                    logger.warning(
                        "[AI分析] HTTP %d, 退避 %ds (%s)",
                        resp.status_code, BACKOFF_SCHEDULE[attempt], current_model)
                time.sleep(BACKOFF_SCHEDULE[attempt])

            except httpx.TimeoutException:
                logger.warning(
                    "[AI分析] 超时(%s), 退避 %ds", current_model, BACKOFF_SCHEDULE[attempt])
                time.sleep(BACKOFF_SCHEDULE[attempt])
            except httpx.RequestError as e:
                logger.warning(
                    "[AI分析] 网络错误(%s): %s, 退避 %ds",
                    current_model, e, BACKOFF_SCHEDULE[attempt])
                time.sleep(BACKOFF_SCHEDULE[attempt])
            except Exception as e:
                logger.warning("[AI分析] 异常(%s): %s, 退避 %ds",
                               current_model, e, BACKOFF_SCHEDULE[attempt])
                time.sleep(BACKOFF_SCHEDULE[attempt])

        logger.error("[AI分析] 已达最大重试次数 %s，放弃 (末模型=%s)", max_retries, current_model)
        return None, current_model

    @staticmethod
    def _is_fatal_model_error(resp: httpx.Response) -> bool:
        """判断 HTTP 响应是否表示该模型已失效，应轮换到下一个。

        触发轮换的条件：
        - 500/503 Internal Server Error（模型服务端故障）
        - 返回的 error.message 包含 "from provider"、"upstream"、"rate-limited"
        - 返回模型已知已下线错误代码
        """
        if resp.status_code in (500, 502, 503):
            return True
        if resp.status_code >= 400:
            try:
                err_msg = str(resp.json().get('error', {}))
                fatal_keywords = ['from provider', 'upstream', 'rate-limited',
                                  'temporarily', 'not available', 'free is temporarily']
                for kw in fatal_keywords:
                    if kw in err_msg.lower():
                        return True
            except Exception:
                pass
        return False

    @staticmethod
    def _log_rate_limit(resp: httpx.Response, attempt: int, max_retries: int):
        try:
            err = resp.json().get('error', {})
            err_code = err.get('code', '')
            if err_code == '1305':
                logger.warning(
                    f"[AI分析] 拥挤1305({err.get('message','')}) 退避 {attempt+1}/{max_retries}")
                return
        except Exception:
            pass
        logger.warning(
            f"[AI分析] 限流(429), 退避 {BACKOFF_SCHEDULE[attempt]}s ({attempt+1}/{max_retries})")


# ── 批量分析 ──

def _save_analysis(stock: dict, result: dict, run_id: str):
    """把 AI 分析结果写入 screening_result 与 stock_analysis_history。"""
    from src.models.database import ScreeningResultDAO, StockAnalysisHistoryDAO

    analysis_json = json.dumps(result, ensure_ascii=False)
    strategy = result.get('investment_strategy', '')
    if isinstance(strategy, dict):
        strategy = json.dumps(strategy, ensure_ascii=False)
    trade = result.get('trade_strategy', {})
    trade_json = json.dumps(trade, ensure_ascii=False) if isinstance(trade, dict) else str(trade)

    ScreeningResultDAO().update_ai_analysis(
        run_id, stock['code'], analysis_json, strategy, trade_json)
    StockAnalysisHistoryDAO().save(
        stock['code'], run_id, stock.get('score'),
        analysis_json, trade_json)


def _save_failure(stock: dict, run_id: str):
    """记录一次失败尝试（写入空记录，避免短时间内重复尝试）。"""
    from src.models.database import StockAnalysisHistoryDAO
    StockAnalysisHistoryDAO().save(
        stock['code'], run_id, stock.get('score'), '{}', '{}')


def analyze_batch(stocks: list[dict], run_id: str,
                  interval_seconds: int = 60,
                  on_progress: Optional[Callable[[int, int, int], None]] = None
                  ) -> tuple[int, int]:
    """批量分析股票并持久化结果。

    Args:
        stocks: 待分析股票列表（需含 code/name/score 等字段）
        run_id: 运行批次 ID
        interval_seconds: 每只股票之间的冷却间隔（默认 60s，避免限流）
        on_progress: 进度回调 (analyzed_ok, analyzed_failed, current_index)

    Returns:
        (成功数, 失败数)
    """
    from src.config import load_config
    analyzer = AiAnalyzer(load_config().get('ai', {}))
    total = len(stocks)
    if total == 0:
        return 0, 0
    if not analyzer.configured:
        logger.error("[AI分析] 无 API Key，跳过全部")
        for i, s in enumerate(stocks):
            _save_failure(s, run_id)
        return 0, total

    logger.info(f"[AI分析] 开始分析 {total} 只股票...")
    analyzed_ok = 0
    analyzed_failed = 0

    for idx, stock in enumerate(stocks):
        code, name = stock.get('code'), stock.get('name')
        logger.info(f"[AI分析] ({idx+1}/{total}) {code} {name}...")
        if on_progress:
            on_progress(analyzed_ok, analyzed_failed, idx)

        result = analyzer.analyze_stock(stock)
        if result:
            _save_analysis(stock, result, run_id)
            analyzed_ok += 1
            logger.info(f"  ✅ {analyzed_ok}/{total}")
        else:
            _save_failure(stock, run_id)
            analyzed_failed += 1
            logger.warning(f"  ✗ Failed {analyzed_failed}")

        # 股票之间冷却
        if idx < total - 1:
            time.sleep(interval_seconds)

    if on_progress:
        on_progress(analyzed_ok, analyzed_failed, total)
    logger.info(
        f"[AI分析] 完成: 成功 {analyzed_ok}/{total}（失败 {analyzed_failed}）")
    return analyzed_ok, analyzed_failed
