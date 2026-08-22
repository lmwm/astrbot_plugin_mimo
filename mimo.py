"""小米 MiMo 平台 - 用量查询与登录模块

整合了 MiMo 平台的所有功能：
  - 账号登录（密码/passToken/OTP）
  - 用量查询（余额/Token/费用）
  - 报告格式化（模板支持）
  - 限额追踪
"""

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request

from .base import BasePlatform, QueryResult
from .http_utils import inject_cookie, new_opener, parse_resp


# ══════════════════════════════════════════
#  异常类
# ══════════════════════════════════════════


class OtpRequired(Exception):
    """需要 OTP 验证码"""


class PassTokenExpired(Exception):
    """passToken 过期"""


class LoginError(Exception):
    """登录过程中的通用错误"""


class StsError(Exception):
    """STS 换取 serviceToken 失败"""


# ══════════════════════════════════════════
#  常量
# ══════════════════════════════════════════

_ACCOUNT_BASE = "https://account.xiaomi.com"
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


# ══════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════


def fmt_num(n) -> str:
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


# ══════════════════════════════════════════
#  登录逻辑：MiAccount
# ══════════════════════════════════════════


class MiAccount:
    """小米账号登录封装"""

    def __init__(self, device_id: str, ua: str):
        self.device_id = device_id
        self.ua = ua

    def login_with_passtoken(self, user_id: str, pass_token: str) -> str:
        """使用 passToken 登录，返回 serviceToken"""
        opener, jar = new_opener()
        for n, v in [
            ("deviceId", self.device_id),
            ("userId", user_id),
            ("passToken", pass_token),
        ]:
            inject_cookie(jar, n, v)
        resp = self._service_login(opener)
        code = resp.get("code")
        if code != 0:
            if code == 70016:
                raise PassTokenExpired(f"passToken 已过期 (code={code})")
            raise StsError(f"serviceLogin 失败 (code={code})")
        if not resp.get("userId"):
            raise PassTokenExpired("passToken 无效")
        return self._sts(opener, jar, resp)

    def login_with_password(
        self, account: str, password: str, otp_code: str | None = None
    ) -> dict:
        """使用账号密码登录，返回含 userId/passToken/serviceToken 的 dict"""
        opener, jar = new_opener()
        for n, v in [("deviceId", self.device_id)]:
            inject_cookie(jar, n, v)
        resp = self._service_login(opener)
        if resp.get("code") == 0:
            st = self._sts(opener, jar, resp)
            return {
                "userId": str(resp["userId"]),
                "passToken": resp.get("passToken", ""),
                "serviceToken": st,
            }
        if not resp.get("qs"):
            raise LoginError(f"serviceLogin 异常: code={resp.get('code')}")
        data = {
            "_json": "true",
            "qs": resp["qs"],
            "sid": resp["sid"],
            "_sign": resp["_sign"],
            "callback": resp["callback"],
            "user": account,
            "hash": hashlib.md5(password.encode()).hexdigest().upper(),
        }
        body = urlencode(data).encode()
        req = Request(
            f"{_ACCOUNT_BASE}/pass/serviceLoginAuth2",
            data=body,
            headers={"User-Agent": self.ua},
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with opener.open(req, timeout=15) as r:
            resp2 = parse_resp(r.read())
        if resp2.get("code") != 0:
            raise LoginError(f"密码认证失败: {resp2.get('desc', resp2)}")
        if resp2.get("notificationUrl"):
            if not otp_code:
                self._trigger_otp(opener, jar, resp2["notificationUrl"])
                raise OtpRequired(resp2["notificationUrl"])
            else:
                self._submit_otp(opener, jar, resp2["notificationUrl"], otp_code)
                resp2 = self._service_login(opener)
                if resp2.get("code") != 0:
                    raise LoginError(f"OTP 验证后登录失败: {resp2}")
        st = self._sts(opener, jar, resp2)
        return {
            "userId": str(resp2.get("userId", "")),
            "passToken": resp2.get("passToken", ""),
            "serviceToken": st,
        }

    def _service_login(self, opener) -> dict:
        """发起 serviceLogin 请求"""
        url = f"{_ACCOUNT_BASE}/pass/serviceLogin?sid=api-platform&_json=true"
        with opener.open(
            Request(url, headers={"User-Agent": self.ua}), timeout=15
        ) as r:
            return parse_resp(r.read())

    def _trigger_otp(self, opener, jar, notification_url):
        """触发 OTP 验证（发送短信/邮件）"""
        if not notification_url.startswith("http"):
            notification_url = _ACCOUNT_BASE + notification_url
        headers = {"User-Agent": self.ua}
        with opener.open(Request(notification_url, headers=headers), timeout=15) as r:
            r.read()
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(notification_url).query)
        context = q.get("context", [""])[0]
        sid_p = q.get("sid", ["api-platform"])[0]
        with opener.open(
            Request(
                f"{_ACCOUNT_BASE}/identity/list?sid={sid_p}&supportedMask=0&_locale=zh_CN&context={context}",
                headers=headers,
            ),
            timeout=15,
        ) as r:
            idata = parse_resp(r.read())
        flag = idata.get("flag", 4)
        method = "Email" if flag == 8 else "Phone"
        with opener.open(
            Request(
                f"{_ACCOUNT_BASE}/identity/auth/verify{method}?_flag={flag}&_json=true",
                headers=headers,
            ),
            timeout=15,
        ) as r:
            r.read()
        if method == "Phone":
            body = urlencode({"retry": "0", "icode": "", "_json": "true"}).encode()
            req = Request(
                f"{_ACCOUNT_BASE}/identity/auth/sendPhoneTicket",
                data=body,
                headers=headers,
                method="POST",
            )
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with opener.open(req, timeout=15) as r:
                r.read()

    def _submit_otp(self, opener, jar, notification_url, code):
        """提交 OTP 验证码"""
        if not notification_url.startswith("http"):
            notification_url = _ACCOUNT_BASE + notification_url
        headers = {"User-Agent": self.ua}
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(notification_url).query)
        context = q.get("context", [""])[0]
        sid_p = q.get("sid", ["api-platform"])[0]
        with opener.open(
            Request(
                f"{_ACCOUNT_BASE}/identity/list?sid={sid_p}&supportedMask=0&_locale=zh_CN&context={context}",
                headers=headers,
            ),
            timeout=15,
        ) as r:
            idata = parse_resp(r.read())
        flag = idata.get("flag", 4)
        method = "Email" if flag == 8 else "Phone"
        body = urlencode(
            {
                "_flag": str(flag),
                "ticket": code.strip(),
                "trust": "false",
                "_json": "true",
            }
        ).encode()
        req = Request(
            f"{_ACCOUNT_BASE}/identity/auth/verify{method}?_dc={int(time.time() * 1000)}",
            data=body,
            headers=headers,
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with opener.open(req, timeout=15) as r:
            r.read()
            for sc in r.headers.get_all("Set-Cookie") or []:
                jar.load(sc)

    def _sts(self, opener, jar, resp) -> str:
        """通过 STS 换取 serviceToken"""
        nonce = resp["nonce"]
        ssecurity = resp["ssecurity"]
        location = resp["location"]
        nsec = f"nonce={nonce}&{ssecurity}"
        sign = base64.b64encode(hashlib.sha1(nsec.encode()).digest()).decode()
        sts_url = f"{location}&clientSign={quote(sign)}"
        with opener.open(
            Request(sts_url, headers={"User-Agent": self.ua}), timeout=15
        ) as r:
            r.read()
        for c in jar:
            if "serviceToken" in c.name:
                return c.value
        raise StsError("STS 响应中未找到 serviceToken")


# ══════════════════════════════════════════
#  查询逻辑
# ══════════════════════════════════════════


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


# ══════════════════════════════════════════
#  结果类
# ══════════════════════════════════════════


class MimoResult(QueryResult):
    """MiMo 查询结果"""

    def __init__(
        self,
        success: bool,
        account_name: str,
        data: dict,
        error: str = "",
        prev_limit: dict | None = None,
        template: str | None = None,
    ):
        super().__init__(success=success, platform="MiMo", account_name=account_name, data=data, error=error)
        self.prev_limit = prev_limit
        self.template = template

    def _format_data(self) -> str:
        """格式化 MiMo 查询结果"""
        bal = self.data.get("balance", {}).get("data", {})
        usage = self.data.get("usage", {}).get("data", {})
        tok = usage.get("tokenUsage", {})
        cost = usage.get("costUsage", {})
        limit = usage.get("accountRateLimit", {})

        # 限额项：仅在与上次查询不同时追加
        tpm = int(limit.get("tpm") or 0)
        rpm = int(limit.get("rpm") or 0)
        concurrency = limit.get("concurrency")
        prev = self.prev_limit or {}
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

        tpl = self.template if self.template else _DEFAULT_TEMPLATE
        return tpl.format(
            label=self.account_name or "MiMo用量",
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


# ══════════════════════════════════════════
#  限额追踪
# ══════════════════════════════════════════


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


# ══════════════════════════════════════════
#  同步凭据操作（供 run_in_executor 使用）
# ══════════════════════════════════════════


def _sync_ensure_account(acc: dict) -> dict:
    """同步：确保账号有可用凭据。优先级：serviceToken > passToken > account+password"""
    service_token = acc.get("serviceToken", "")
    pass_token = acc.get("passToken", "")
    user_id = acc.get("userId", "")

    if service_token and user_id:
        return acc

    if pass_token and user_id:
        mi = MiAccount(acc.get("device_id", ""), acc.get("ua", ""))
        try:
            acc["serviceToken"] = mi.login_with_passtoken(user_id, pass_token)
            return acc
        except PassTokenExpired:
            acc["passToken"] = ""
        except (OSError, StsError):
            pass

    account = acc.get("account", "")
    password = acc.get("password", "")
    if account and password:
        mi = MiAccount(acc.get("device_id", ""), acc.get("ua", ""))
        try:
            result = mi.login_with_password(account, password)
            acc["userId"] = result["userId"]
            acc["passToken"] = result["passToken"]
            acc["serviceToken"] = result["serviceToken"]
            return acc
        except OtpRequired:
            acc["_otp_required"] = True
        except LoginError as e:
            acc["_login_error"] = f"登录失败: {e}"
        except (OSError, StsError) as e:
            acc["_login_error"] = f"网络错误: {e}"
        return acc

    acc["_login_error"] = "令牌过期，请使用 /query mimo login 重新登录"
    return acc


def _sync_re_login_account(acc: dict) -> dict:
    """同步：查询失败后重新登录。优先级：passToken > account+password"""
    acc["serviceToken"] = ""
    pass_token = acc.get("passToken", "")
    user_id = acc.get("userId", "")

    if pass_token and user_id:
        mi = MiAccount(acc.get("device_id", ""), acc.get("ua", ""))
        try:
            acc["serviceToken"] = mi.login_with_passtoken(user_id, pass_token)
            return acc
        except PassTokenExpired:
            acc["passToken"] = ""
        except (OSError, StsError):
            pass

    account = acc.get("account", "")
    password = acc.get("password", "")
    if account and password:
        mi = MiAccount(acc.get("device_id", ""), acc.get("ua", ""))
        try:
            result = mi.login_with_password(account, password)
            acc["userId"] = result["userId"]
            acc["passToken"] = result["passToken"]
            acc["serviceToken"] = result["serviceToken"]
            return acc
        except OtpRequired:
            acc["_otp_required"] = True
        except LoginError as e:
            acc["_login_error"] = f"重新登录失败: {e}"
        except (OSError, StsError) as e:
            acc["_login_error"] = f"网络错误: {e}"
        return acc

    acc["_login_error"] = "令牌过期，请使用 /query mimo login 重新登录"
    return acc


def _sync_login_account(acc: dict, otp_code: str | None = None) -> dict:
    """同步：完整登录流程，返回凭据 dict"""
    mi = MiAccount(acc.get("device_id", ""), acc.get("ua", ""))
    result = mi.login_with_password(acc["account"], acc["password"], otp_code=otp_code)
    return {
        "account": acc["account"],
        "password": acc["password"],
        "userId": result["userId"],
        "passToken": result["passToken"],
        "serviceToken": result["serviceToken"],
    }


# ══════════════════════════════════════════
#  平台类
# ══════════════════════════════════════════


class MimoPlatform(BasePlatform):
    """MiMo 平台查询封装"""

    def __init__(self, plugin_dir: Path):
        self._plugin_dir = plugin_dir
        self.limits = LimitTracker(plugin_dir)

    @property
    def platform_name(self) -> str:
        return "MiMo"

    @property
    def platform_icon(self) -> str:
        return "📋"

    async def ensure_account(self, acc: dict) -> dict:
        """异步：确保账号有可用凭据"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_ensure_account, acc)

    async def re_login_account(self, acc: dict) -> dict:
        """异步：查询失败后重新登录"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_re_login_account, acc)

    def login_account(self, acc: dict, otp_code: str | None = None) -> dict:
        """同步：完整登录流程（在线程中调用）"""
        return _sync_login_account(acc, otp_code)

    async def query_one(self, acc: dict) -> dict:
        """查询单个 MiMo 账号，失败时自动重登录并重试"""
        if acc.pop("_otp_required", False):
            return {"error": "需要短信验证，请使用 /query mimo login 账号 密码 手动登录"}

        login_error = acc.pop("_login_error", "")
        if login_error:
            return {"error": login_error}

        service_token = acc.get("serviceToken", "")
        user_id = acc.get("userId", "")
        ua = acc.get("ua", "")

        if not service_token or not user_id:
            return {"error": "无有效凭据，请使用 /query mimo login 账号 密码 登录"}

        results = await query_mimo(service_token, user_id, ua)

        if not is_auth_error(results) and is_valid_response(results):
            return results

        acc = await self.re_login_account(acc)
        if acc.get("serviceToken"):
            results = await query_mimo(acc["serviceToken"], user_id, ua)

        return results

    async def query(self, account: dict, template: str | None = None) -> MimoResult:
        """查询单个账号"""
        label = self.get_account_label(account)

        result_data = await self.query_one(account)
        if "error" in result_data:
            return MimoResult(
                success=False,
                account_name=label,
                data=result_data,
                error=result_data["error"],
                template=template,
            )

        prev = self.limits.get_prev(account)
        usage = result_data.get("usage", {}).get("data", {})
        self.limits.update(account, usage.get("accountRateLimit", {}))

        return MimoResult(
            success=True,
            account_name=label,
            data=result_data,
            prev_limit=prev,
            template=template,
        )
