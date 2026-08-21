"""API 查询、报告格式化、限额记录"""

import asyncio
import json
from pathlib import Path
from urllib.request import Request

from .http_utils import inject_cookie, new_opener, parse_resp

_BALANCE_URL = "https://platform.xiaomimimo.com/api/v1/balance"
_USAGE_URL = "https://platform.xiaomimimo.com/api/v1/usage"

_DEFAULT_TEMPLATE = """📋 {label}
────────────────
  余额        {balance}元
  赠送        {gift_balance}元
  输入        {input_token}
  输出        {output_token}
  缓存        {cache_token}
  本月费用    {monthly_cost}元
  累计费用    {total_cost}元"""


def fmt_num(n):
    """格式化数字：大数用万/亿简化"""
    n = int(n or 0)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return f"{n:,}"


def _load_template(plugin_dir: Path | None) -> str:
    """加载模板文件，不存在则使用默认模板"""
    if plugin_dir:
        tpl_path = plugin_dir / "template.txt"
        if tpl_path.exists():
            try:
                return tpl_path.read_text(encoding="utf-8")
            except OSError:
                pass
    return _DEFAULT_TEMPLATE


async def query_mimo(service_token: str, user_id: str, ua: str) -> dict:
    """查询 MiMo 平台余额和用量，返回原始 API 响应"""

    def _query():
        opener, jar = new_opener()
        for n, v, d in [
            ("userId", user_id, "platform.xiaomimimo.com"),
            ("serviceToken", service_token, "platform.xiaomimimo.com"),
        ]:
            inject_cookie(jar, n, v, domain=d)
        results = {}
        for key, url in [("balance", _BALANCE_URL), ("usage", _USAGE_URL)]:
            try:
                with opener.open(
                    Request(url, headers={"User-Agent": ua}),
                    timeout=15,
                ) as r:
                    results[key] = parse_resp(r.read())
            except (OSError, TimeoutError) as e:
                results[key] = {"code": -1, "error": str(e)}
        return results

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _query)


def is_auth_error(results: dict) -> bool:
    """检测 API 响应是否为认证失败（401 / token 相关错误）"""
    for v in results.values():
        if not isinstance(v, dict):
            continue
        if v.get("code") == 401:
            return True
        msg = str(v.get("message", "")).lower()
        if "auth" in msg or "token" in msg:
            return True
    return False


def is_valid_response(results: dict) -> bool:
    """检测 API 响应是否包含有效数据（非空/非错误）"""
    for v in results.values():
        if not isinstance(v, dict):
            continue
        if v.get("code") == -1:
            return False
        if v.get("code") not in (0, None):
            return False
    return True


def format_report(
    results: dict,
    label: str = "",
    prev_limit: dict | None = None,
    plugin_dir: Path | None = None,
    template: str | None = None,
) -> str:
    """使用模板格式化用量报告"""
    bal = results.get("balance", {}).get("data", {})
    usage = results.get("usage", {}).get("data", {})
    tok = usage.get("tokenUsage", {})
    cost = usage.get("costUsage", {})
    limit = usage.get("accountRateLimit", {})

    # 限额项：仅在与上次查询不同时追加
    tpm = int(limit.get("tpm") or 0)
    rpm = int(limit.get("rpm") or 0)
    concurrency = limit.get("concurrency")
    prev = prev_limit or {}
    limit_changed = (
        tpm != int(prev.get("tpm") or 0)
        or rpm != int(prev.get("rpm") or 0)
        or concurrency != prev.get("concurrency")
    )
    limit_lines = ""
    if limit_changed:
        limit_lines = (
            f"\n  TPM         {fmt_num(tpm)}"
            f"\n  RPM         {fmt_num(rpm)}"
            f"\n  并发        {concurrency or '-'}"
        )

    # 使用传入的模板，否则从文件加载
    tpl = template if template else _load_template(plugin_dir)
    return tpl.format(
        label=label or "MiMo用量",
        balance=bal.get("balance", "?"),
        gift_balance=bal.get("giftBalance", "?"),
        input_token=fmt_num(tok.get("inputToken", 0)),
        output_token=fmt_num(tok.get("outputToken", 0)),
        cache_token=fmt_num(tok.get("cacheToken", 0)),
        monthly_cost=cost.get("currentMonthCost", "?"),
        total_cost=cost.get("totalCost", "?"),
        tpm=fmt_num(tpm),
        rpm=fmt_num(rpm),
        concurrency=concurrency or "-",
    ) + limit_lines


class LimitTracker:
    """限额数据持久化，用于对比上次查询结果"""

    def __init__(self, plugin_dir: Path):
        self._path = plugin_dir / "last_limits.json"
        self._data: dict = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get_prev(self, acc: dict) -> dict:
        """获取账号上次查询的限额"""
        key = acc.get("userId") or acc.get("account", "")
        return self._data.get(key, {})

    def update(self, acc: dict, limit: dict):
        """更新账号的限额记录"""
        key = acc.get("userId") or acc.get("account", "")
        if key:
            self._data[key] = {
                "tpm": limit.get("tpm", 0),
                "rpm": limit.get("rpm", 0),
                "concurrency": limit.get("concurrency"),
            }
            self._save()

    def _save(self):
        try:
            self._path.write_text(json.dumps(self._data, ensure_ascii=False))
        except OSError:
            pass
