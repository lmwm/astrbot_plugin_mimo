"""HTTP 工具：Cookie 管理、opener 创建、代理、重试"""

import json
import time
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from urllib.request import HTTPCookieProcessor, Request, build_opener

_JSON_PREFIX = "&&&START&&&"


class _AcceptAllPolicy(DefaultCookiePolicy):
    """接受所有 Cookie 的策略，用于小米 Passport 登录流程"""

    def set_ok(self, cookie, request):
        return True

    def return_ok(self, cookie, request):
        return True

    def domain_return_ok(self, domain, request):
        return True

    def path_return_ok(self, path, request):
        return True


def new_opener(proxy: str = ""):
    """创建带 Cookie Jar 的 HTTP opener，可选代理支持"""
    jar = CookieJar()
    jar.set_policy(_AcceptAllPolicy())
    handlers = [HTTPCookieProcessor(jar)]
    if proxy:
        from urllib.request import ProxyHandler

        handlers.append(
            ProxyHandler(
                {
                    "http": proxy,
                    "https": proxy,
                }
            )
        )
    return build_opener(*handlers), jar


def proxy_url(url: str, proxy: str = "") -> str:
    """将代理 URL 拼接到目标 URL 前（支持 gh-proxy 等 URL 前缀代理）"""
    if not proxy:
        return url
    proxy = proxy.rstrip("/")
    return f"{proxy}/{url}"


def retry(fn, max_retries: int = 3, delay: float = 2.0):
    """带指数退避的重试包装器"""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except (OSError, TimeoutError) as e:
            last_exc = e
            if attempt < max_retries:
                wait = delay * (2**attempt)
                time.sleep(wait)
    raise last_exc


def inject_cookie(jar, name, value, domain="account.xiaomi.com"):
    """向 Cookie Jar 注入指定 Cookie"""
    jar.set_cookie(
        Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=False,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
    )


def parse_resp(raw: bytes) -> dict:
    """解析小米 Passport API 响应，去除 JSON 前缀"""
    if raw.startswith(_JSON_PREFIX.encode()):
        raw = raw[len(_JSON_PREFIX) :]
    return json.loads(raw) if raw else {}
