"""
MiMo 平台用量查询 AstrBot 插件（多账号支持）

指令：
  /mimo              — 查询所有账号用量
  /mimo <序号>       — 查询指定账号用量
  /mimo login 账号 密码 — 添加/更新账号并登录
  /mimo list         — 列出所有账号
  /mimo del <序号>   — 删除指定账号
  /mimo update       — 检查并更新插件
"""

import asyncio
import os
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .mi_account import (
    LoginError,
    MiAccount,
    OtpRequired,
    PassTokenExpired,
    StsError,
)
from .query import (
    LimitTracker,
    format_report,
    is_auth_error,
    is_valid_response,
    query_mimo,
)
from .updater import check_update, do_update, reload_plugin

_PLUGIN_NAME = "astrbot_plugin_mimo"
_PLUGIN_VERSION = "1.6.3"


@register(_PLUGIN_NAME, "MiMo", "小米 MiMo 平台用量查询插件（多账号）", _PLUGIN_VERSION)
class MiMoPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._limits = LimitTracker(Path(__file__).parent)
        self._plugin_dir = Path(__file__).parent

        # 全局 device_id / UA 为空时自动填入默认值
        changed = False
        if not self.config.get("device_id"):
            self.config["device_id"] = os.getenv("MIMO_DEVICE_ID", "wb_MIQUERY000001")
            changed = True
        if not self.config.get("ua"):
            self.config["ua"] = os.getenv("MIMO_UA", "APP/com.xiaomi.mihome APPV/11.3.203 iosPassportSDK/4.2.50 iOS/26.3.1")
            changed = True
        if changed:
            self.config.save_config()

    # ── 配置读取 ──

    def _get_device_id(self) -> str:
        return self.config.get("device_id") or os.getenv("MIMO_DEVICE_ID", "wb_MIQUERY000001")

    def _get_ua(self) -> str:
        return self.config.get("ua") or os.getenv("MIMO_UA", "APP/com.xiaomi.mihome APPV/11.3.203 iosPassportSDK/4.2.50 iOS/26.3.1")

    def _resolve_device_id(self, acc: dict) -> str:
        return acc.get("device_id") or self._get_device_id()

    def _resolve_ua(self, acc: dict) -> str:
        return acc.get("ua") or self._get_ua()

    def _get_label(self, acc: dict, idx: int = 0) -> str:
        return acc.get("name") or acc.get("account") or f"账号{idx + 1}"

    def _get_accounts(self) -> list:
        return self.config.get("accounts") or []

    def _save_accounts(self, accounts: list):
        self.config["accounts"] = accounts
        self.config.save_config()

    def _find_account_index(self, accounts: list, account: str) -> int:
        for i, acc in enumerate(accounts):
            if acc.get("account") == account:
                return i
        return -1

    # ── 凭据管理 ──

    def _ensure_account(self, acc: dict) -> dict:
        """确保账号有可用的凭据。优先级：serviceToken > passToken > account+password"""
        changed = False
        if not acc.get("device_id"):
            acc["device_id"] = self._get_device_id()
            changed = True
        if not acc.get("ua"):
            acc["ua"] = self._get_ua()
            changed = True
        if changed:
            self._save_accounts(self._get_accounts())

        service_token = acc.get("serviceToken", "")
        pass_token = acc.get("passToken", "")
        user_id = acc.get("userId", "")

        if service_token and user_id:
            return acc

        if pass_token and user_id:
            mi = MiAccount(acc["device_id"], acc["ua"])
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
            mi = MiAccount(self._resolve_device_id(acc), self._resolve_ua(acc))
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

        acc["_login_error"] = "令牌过期，请使用 /mimo login 重新登录"
        return acc

    def _re_login_account(self, acc: dict) -> dict:
        """查询失败后重新登录。优先级：passToken > account+password"""
        acc["serviceToken"] = ""
        pass_token = acc.get("passToken", "")
        user_id = acc.get("userId", "")

        if pass_token and user_id:
            mi = MiAccount(self._resolve_device_id(acc), self._resolve_ua(acc))
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
            mi = MiAccount(self._resolve_device_id(acc), self._resolve_ua(acc))
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

        acc["_login_error"] = "令牌过期，请使用 /mimo login 重新登录"
        return acc

    def _login_account(self, acc: dict, otp_code: str | None = None) -> dict:
        """完整登录流程，返回凭据 dict"""
        mi = MiAccount(self._resolve_device_id(acc), self._resolve_ua(acc))
        result = mi.login_with_password(acc["account"], acc["password"], otp_code=otp_code)
        return {
            "account": acc["account"],
            "password": acc["password"],
            "userId": result["userId"],
            "passToken": result["passToken"],
            "serviceToken": result["serviceToken"],
        }

    # ── 查询 ──

    async def _query_one(self, acc: dict) -> dict:
        """查询单个账号用量，失败时自动重登录并重试"""
        if acc.pop("_otp_required", False):
            return {"error": "需要短信验证，请使用 /mimo login 账号 密码 手动登录"}

        login_error = acc.pop("_login_error", "")
        if login_error:
            return {"error": login_error}

        service_token = acc.get("serviceToken", "")
        user_id = acc.get("userId", "")
        ua = self._resolve_ua(acc)

        if not service_token or not user_id:
            return {"error": "无有效凭据，请使用 /mimo login 账号 密码 登录"}

        results = await query_mimo(service_token, user_id, ua)

        if not is_auth_error(results) and is_valid_response(results):
            return results

        acc = self._re_login_account(acc)
        if acc.get("serviceToken"):
            results = await query_mimo(acc["serviceToken"], user_id, ua)

        return results

    # ================== 指令 ==================

    @filter.command("mimo")
    async def mimo_cmd(self, event: AstrMessageEvent):
        """/mimo — 查询 | /mimo login | /mimo list | /mimo del | /mimo update"""
        args = event.get_message_str().strip().split()
        accounts = self._get_accounts()

        if len(args) >= 2 and args[1] == "update":
            yield event.plain_result("🔄 正在检查更新...")
            async for r in self._handle_update(event):
                yield r
            return

        if len(args) >= 2 and args[1] == "login":
            yield event.plain_result("🔑 正在处理登录...")
            async for r in self._handle_login(event, accounts):
                yield r
            return

        if len(args) >= 2 and args[1] == "list":
            yield event.plain_result("📋 正在获取账号列表...")
            async for r in self._handle_list(event, accounts):
                yield r
            return

        if len(args) >= 2 and args[1] == "del":
            yield event.plain_result("🗑️ 正在删除...")
            async for r in self._handle_del(event, accounts, args):
                yield r
            return

        # /mimo <序号> — 查询指定账号
        if len(args) >= 2 and args[1].isdigit():
            idx = int(args[1]) - 1
            if 0 <= idx < len(accounts):
                acc = accounts[idx]
                yield event.plain_result("🔍 正在查询...")
                loop = asyncio.get_event_loop()
                acc = await loop.run_in_executor(None, self._ensure_account, acc)
                self._save_accounts(accounts)
                results = await self._query_one(acc)
                self._save_accounts(accounts)
                label = self._get_label(acc, idx)
                if "error" in results:
                    yield event.plain_result(f"📋 {label}\n❌ {results['error']}")
                else:
                    prev = self._limits.get_prev(acc)
                    yield event.plain_result(format_report(results, label, prev, self._plugin_dir))
                    usage = results.get("usage", {}).get("data", {})
                    self._limits.update(acc, usage.get("accountRateLimit", {}))
            else:
                yield event.plain_result(
                    f"❌ 序号 {idx + 1} 不存在，共 {len(accounts)} 个账号"
                )
            return

        # /mimo — 查询所有账号
        if not accounts:
            yield event.plain_result(
                "❌ 还没有配置账号\n使用 /mimo login 账号 密码 添加账号"
            )
            return

        yield event.plain_result("🔍 正在查询所有账号...")
        loop = asyncio.get_event_loop()
        for i in range(len(accounts)):
            accounts[i] = await loop.run_in_executor(
                None, self._ensure_account, accounts[i]
            )
        self._save_accounts(accounts)

        for i, acc in enumerate(accounts):
            results = await self._query_one(acc)
            self._save_accounts(accounts)
            label = self._get_label(acc, i)
            if "error" in results:
                yield event.plain_result(f"📋 {label}\n❌ {results['error']}")
            else:
                prev = self._limits.get_prev(acc)
                yield event.plain_result(format_report(results, label, prev, self._plugin_dir))
                usage = results.get("usage", {}).get("data", {})
                self._limits.update(acc, usage.get("accountRateLimit", {}))

    # ── 子命令处理 ──

    async def _handle_login(self, event: AstrMessageEvent, accounts: list):
        args = event.get_message_str().strip().split()

        # /mimo login — 显示所有账号状态
        if len(args) == 2:
            if not accounts:
                yield event.plain_result("还没有配置账号\n用法: /mimo login 账号 密码")
                return
            lines = [f"📋 共 {len(accounts)} 个账号:"]
            for i, acc in enumerate(accounts):
                has_st = bool(acc.get("serviceToken"))
                has_pt = bool(acc.get("passToken"))
                lines.append(
                    f"  {i + 1}. {self._get_label(acc, i)} "
                    f"{'✅' if has_st else ('⚠️' if has_pt else '❌')}"
                )
            lines.append("\n用法: /mimo login 账号 密码（添加或更新）")
            yield event.plain_result("\n".join(lines))
            return

        # /mimo login passtoken <账号> <userId> <token>
        if len(args) == 6 and args[2] == "passtoken":
            account, user_id, pass_token = args[3], args[4], args[5]
            idx = self._find_account_index(accounts, account)
            if idx < 0:
                acc = {"account": account}
                accounts.append(acc)
                idx = len(accounts) - 1
            else:
                acc = accounts[idx]

            acc["userId"] = user_id
            acc["passToken"] = pass_token
            acc["serviceToken"] = ""

            loop = asyncio.get_event_loop()
            acc = await loop.run_in_executor(None, self._ensure_account, acc)
            accounts[idx] = acc
            self._save_accounts(accounts)
            yield event.plain_result(
                f"✅ {account} passToken 设置成功\n  userId: {user_id}"
            )
            return

        # /mimo login account password
        if len(args) >= 4:
            account, password = args[2], args[3]

            idx = self._find_account_index(accounts, account)
            if idx < 0:
                accounts.append({"account": account})
                idx = len(accounts) - 1

            accounts[idx]["account"] = account
            accounts[idx]["password"] = password

            loop = asyncio.get_event_loop()

            def _try_login(otp_code=None):
                return self._login_account(accounts[idx], otp_code=otp_code)

            try:
                result = await loop.run_in_executor(None, _try_login)
                accounts[idx].update(result)
                self._save_accounts(accounts)
                yield event.plain_result(
                    f"✅ {account} 登录成功!\n  userId: {result['userId']}"
                )
                return
            except OtpRequired:
                yield event.plain_result(
                    f"📱 {account} 需要短信验证，验证码已发送\n请直接回复 6 位验证码："
                )
            except LoginError as e:
                yield event.plain_result(f"❌ {account} 登录失败: {e}")
                return
            except (OSError, StsError) as e:
                yield event.plain_result(f"❌ {account} 网络错误: {e}")
                return

            # OTP 会话等待
            @session_waiter(timeout=120, record_history_chains=False)
            async def otp_waiter(
                controller: SessionController, otp_event: AstrMessageEvent
            ):
                code = otp_event.message_str.strip()
                if not code.isdigit() or len(code) != 6:
                    await otp_event.send(
                        otp_event.plain_result("❌ 请输入 6 位数字验证码")
                    )
                    controller.keep(timeout=120, reset_timeout=True)
                    return
                await otp_event.send(
                    otp_event.plain_result("🔑 验证码已收到，正在登录...")
                )
                try:
                    result = await loop.run_in_executor(
                        None, lambda: _try_login(otp_code=code)
                    )
                    accounts[idx].update(result)
                    self._save_accounts(accounts)
                    await otp_event.send(
                        otp_event.plain_result(
                            f"✅ {account} 登录成功!\n  userId: {result['userId']}"
                        )
                    )
                except (LoginError, OtpRequired, StsError) as e:
                    await otp_event.send(otp_event.plain_result(f"❌ 登录失败: {e}"))
                except (OSError, TimeoutError) as e:
                    await otp_event.send(otp_event.plain_result(f"❌ 网络错误: {e}"))
                controller.stop()

            try:
                await otp_waiter(event)
            except TimeoutError:
                yield event.plain_result("⏰ 验证码等待超时")
            except (LoginError, OtpRequired, StsError) as e:
                yield event.plain_result(f"❌ 登录失败: {e}")
            finally:
                event.stop_event()
            return

        yield event.plain_result(
            "用法:\n"
            "  /mimo login — 查看所有账号\n"
            "  /mimo login 账号 密码 — 添加/更新账号\n"
            "  /mimo login passtoken 账号 userId token — 设置 passToken"
        )

    async def _handle_list(self, event: AstrMessageEvent, accounts: list):
        if not accounts:
            yield event.plain_result("还没有配置账号")
            return
        lines = [f"📋 共 {len(accounts)} 个账号:"]
        for i, acc in enumerate(accounts):
            has_st = bool(acc.get("serviceToken"))
            has_pt = bool(acc.get("passToken"))
            status = "✅" if has_st else ("⚠️ 无serviceToken" if has_pt else "❌ 未登录")
            lines.append(
                f"  {i + 1}. {self._get_label(acc, i)} | userId: {acc.get('userId', '无')} | {status}"
            )
        yield event.plain_result("\n".join(lines))

    async def _handle_del(self, event: AstrMessageEvent, accounts: list, args: list):
        if len(args) < 3 or not args[2].isdigit():
            yield event.plain_result("用法: /mimo del <序号>")
            return
        idx = int(args[2]) - 1
        if 0 <= idx < len(accounts):
            removed = accounts.pop(idx)
            self._save_accounts(accounts)
            yield event.plain_result(f"✅ 已删除: {self._get_label(removed, 0)}")
        else:
            yield event.plain_result(f"❌ 序号 {idx + 1} 不存在")

    async def _handle_update(self, event: AstrMessageEvent):
        """处理 /mimo update 指令"""
        check = await check_update(self.config)
        if check.get("error"):
            yield event.plain_result(f"❌ 检查更新失败: {check['error']}")
            return

        if not check["has_update"]:
            yield event.plain_result(f"✅ 已是最新版本 v{_PLUGIN_VERSION}")
            return

        yield event.plain_result(
            f"🆕 发现新版本 v{check['latest']}（当前 v{check['current']}）\n"
            f"正在下载并安装..."
        )
        result = await do_update(self.config)

        if "✅" in result:
            reload_result = await reload_plugin(self.context)
            yield event.plain_result(f"{result}\n{reload_result}")
        else:
            yield event.plain_result(result)
