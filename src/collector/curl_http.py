"""
系统 curl HTTP 客户端
绕过 sing-box TLS 指纹检测，使用系统 curl 命令行
"""

import subprocess
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def curl_get(url: str, timeout: int = 30) -> Optional[str]:
    """
    使用系统 curl 发起 GET 请求
    返回响应文本，失败返回 None
    """
    try:
        result = subprocess.run(
            [
                'curl', '-s', '--connect-timeout', str(timeout // 2),
                '--max-time', str(timeout),
                '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                '-H', 'Referer: https://quote.eastmoney.com/',
                url,
            ],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        else:
            logger.warning(f"[curl] 请求失败: returncode={result.returncode}, stderr={result.stderr[:100] if result.stderr else ''}")
            return None
    except subprocess.TimeoutExpired:
        logger.warning(f"[curl] 请求超时: {url[:60]}...")
        return None
    except Exception as e:
        logger.error(f"[curl] 异常: {e}")
        return None


def curl_get_json(url: str, timeout: int = 30) -> Optional[dict]:
    """GET 请求并解析 JSON"""
    text = curl_get(url, timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"[curl] JSON 解析失败: {text[:100]}")
        return None
