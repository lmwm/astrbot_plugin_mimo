"""小米账号登录：Passport 认证、OTP 验证、STS 换取"""

import base64
import hashlib
import time
from urllib.parse import quote, urlencode
from urllib.request import Request

from .http_utils import inject_cookie, new_opener, parse_resp

_ACCOUNT_BASE = "https://account.xiaomi.com"


class OtpRequired(Exception):
    """需要 OTP 验证码"""


class PassTokenExpired(Exception):
    """passToken 过期"""


class LoginError(Exception):
    """登录过程中的通用错误"""


class StsError(Exception):
    """STS 换取 serviceToken 失败"""


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
