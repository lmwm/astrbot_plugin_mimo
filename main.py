"""
资源查询 AstrBot 插件（多平台支持）

支持平台：
  - MiMo：小米 MiMo 平台用量查询
  - 华数广电：流量/通话/余额查询

指令：
  /query                    — 查询所有平台
  /query mimo               — 查询所有 MiMo 账号
  /query mimo <序号>        — 查询指定 MiMo 账号
  /query mimo login         — MiMo 登录
  /query mimo list          — 列出所有 MiMo 账号
  /query mimo del <序号>    — 删除 MiMo 账号
  /query wasu               — 查询所有华数账号
  /query wasu login         — 华数登录
  /query wasu list          — 列出所有华数账号
  /query wasu del <序号>    — 删除华数账号
"""

import asyncio
import os
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from .base import QueryResult
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
from .wasu import WasuPlatform

_PLUGIN_NAME = "astrbot_plugin_resource_query"
_PLUGIN_VERSION = "2.0.0"


@register(_PLUGIN_NAME, "资源查询", "多平台资源查询插件（MiMo/华数广电）", _PLUGIN_VERSION)
class ResourceQueryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._plugin_dir = Path(__file__).parent
        self._limits = LimitTracker(self._plugin_dir)

        # 初始化华数平台
        self._wasu = WasuPlatform()

    # ── MiMo 配置读取 ──

    def _get_device_id(self) -> str:
        return "wb_MIQUERY000001"

    def _get_ua(self) -> str:
        return "APP/com.xiaomi.mihome APPV/11.3.203 iosPassportSDK/4.2.50 iOS/26.3.1"

    def _resolve_device_id(self, acc: dict) -> str:
        return acc.get("device_id") or self._get_device_id()

    def _resolve_ua(self, acc: dict) -> str:
        return acc.get("ua") or self._get_ua()

    def _get_mimo_accounts(self) -> list:
        return self.config.get("mimo_accounts") or []

    def _save_mimo_accounts(self, accounts: list):
        self.config["mimo_accounts"] = accounts
        self.config.save_config()

    def _find_account_index(self, accounts: list, account: str) -> int:
        for i, acc in enumerate(accounts):
            if acc.get("account") == account:
                return i
        return -1

    # ── 华数配置读取 ──

    def _get_wasu_accounts(self) -> list:
        return self.config.get("wasu_accounts") or []

    def _save_wasu_accounts(self, accounts: list):
        self.config["wasu_accounts"] = accounts
        self.config.save_config()

    # ── MiMo 凭据管理 ──

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
            self._save_mimo_accounts(self._get_mimo_accounts())

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

        acc["_login_error"] = "令牌过期，请使用 /query mimo login 重新登录"
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

        acc["_login_error"] = "令牌过期，请使用 /query mimo login 重新登录"
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

    # ── MiMo 查询 ──

    async def _query_one_mimo(self, acc: dict) -> dict:
        """查询单个 MiMo 账号，失败时自动重登录并重试"""
        if acc.pop("_otp_required", False):
            return {"error": "需要短信验证，请使用 /query mimo login 账号 密码 手动登录"}

        login_error = acc.pop("_login_error", "")
        if login_error:
            return {"error": login_error}

        service_token = acc.get("serviceToken", "")
        user_id = acc.get("userId", "")
        ua = self._resolve_ua(acc)

        if not service_token or not user_id:
            return {"error": "无有效凭据，请使用 /query mimo login 账号 密码 登录"}

        results = await query_mimo(service_token, user_id, ua)

        if not is_auth_error(results) and is_valid_response(results):
            return results

        acc = self._re_login_account(acc)
        if acc.get("serviceToken"):
            results = await query_mimo(acc["serviceToken"], user_id, ua)

        return results

    # ================== 主指令 ==================

    @filter.command("query")
    async def query_cmd(self, event: AstrMessageEvent):
        """/query — 资源查询主指令"""
        args = event.get_message_str().strip().split()

        # /query — 显示帮助
        if len(args) == 1:
            yield event.plain_result(
                "📊 资源查询插件\n"
                "────────────────\n"
                "用法:\n"
                "  /query mimo — 查询 MiMo 用量\n"
                "  /query wasu — 查询华数广电\n"
                "  /query update — 检查更新\n\n"
                "输入 /query <平台> 查看更多命令"
            )
            return

        platform = args[1].lower()

        if platform == "mimo":
            async for r in self._handle_mimo(event, args[2:]):
                yield r
        elif platform == "wasu":
            async for r in self._handle_wasu(event, args[2:]):
                yield r
        elif platform == "update":
            yield event.plain_result("🔄 正在检查更新...")
            async for r in self._handle_update(event):
                yield r
        else:
            yield event.plain_result(f"❌ 未知平台: {platform}\n支持: mimo, wasu")

    # ================== MiMo 子命令 ==================

    async def _handle_mimo(self, event: AstrMessageEvent, args: list):
        """处理 MiMo 相关命令"""
        accounts = self._get_mimo_accounts()

        # /query mimo — 查询所有账号
        if not args:
            if not accounts:
                yield event.plain_result(
                    "❌ 还没有配置 MiMo 账号\n使用 /query mimo login 账号 密码 添加"
                )
                return

            yield event.plain_result("🔍 正在查询所有 MiMo 账号...")
            loop = asyncio.get_event_loop()
            for i in range(len(accounts)):
                accounts[i] = await loop.run_in_executor(
                    None, self._ensure_account, accounts[i]
                )
            self._save_mimo_accounts(accounts)

            for i, acc in enumerate(accounts):
                results = await self._query_one_mimo(acc)
                self._save_mimo_accounts(accounts)
                label = self._wasu.get_account_label(acc, i)
                if "error" in results:
                    yield event.plain_result(f"📋 {label}\n❌ {results['error']}")
                else:
                    prev = self._limits.get_prev(acc)
                    yield event.plain_result(format_report(results, label, prev, self._plugin_dir))
                    usage = results.get("usage", {}).get("data", {})
                    self._limits.update(acc, usage.get("accountRateLimit", {}))
            return

        sub_cmd = args[0].lower()

        # /query mimo login
        if sub_cmd == "login":
            yield event.plain_result("🔑 正在处理 MiMo 登录...")
            async for r in self._handle_mimo_login(event, accounts, args[1:]):
                yield r
            return

        # /query mimo list
        if sub_cmd == "list":
            if not accounts:
                yield event.plain_result("还没有配置 MiMo 账号")
                return
            lines = [f"📋 共 {len(accounts)} 个 MiMo 账号:"]
            for i, acc in enumerate(accounts):
                has_st = bool(acc.get("serviceToken"))
                has_pt = bool(acc.get("passToken"))
                status = "✅" if has_st else ("⚠️ 无serviceToken" if has_pt else "❌ 未登录")
                lines.append(
                    f"  {i + 1}. {acc.get('name') or acc.get('account') or f'账号{i+1}'} | userId: {acc.get('userId', '无')} | {status}"
                )
            yield event.plain_result("\n".join(lines))
            return

        # /query mimo del <序号>
        if sub_cmd == "del":
            if len(args) < 2 or not args[1].isdigit():
                yield event.plain_result("用法: /query mimo del <序号>")
                return
            idx = int(args[1]) - 1
            if 0 <= idx < len(accounts):
                removed = accounts.pop(idx)
                self._save_mimo_accounts(accounts)
                yield event.plain_result(f"✅ 已删除: {removed.get('name') or removed.get('account')}")
            else:
                yield event.plain_result(f"❌ 序号 {args[1]} 不存在")
            return

        # /query mimo <序号> — 查询指定账号
        if sub_cmd.isdigit():
            idx = int(sub_cmd) - 1
            if 0 <= idx < len(accounts):
                acc = accounts[idx]
                yield event.plain_result("🔍 正在查询...")
                loop = asyncio.get_event_loop()
                acc = await loop.run_in_executor(None, self._ensure_account, acc)
                self._save_mimo_accounts(accounts)
                results = await self._query_one_mimo(acc)
                self._save_mimo_accounts(accounts)
                label = acc.get("name") or acc.get("account") or f"账号{idx + 1}"
                if "error" in results:
                    yield event.plain_result(f"📋 {label}\n❌ {results['error']}")
                else:
                    prev = self._limits.get_prev(acc)
                    yield event.plain_result(format_report(results, label, prev, self._plugin_dir))
                    usage = results.get("usage", {}).get("data", {})
                    self._limits.update(acc, usage.get("accountRateLimit", {}))
            else:
                yield event.plain_result(f"❌ 序号 {idx + 1} 不存在，共 {len(accounts)} 个账号")
            return

        yield event.plain_result(
            "MiMo 用法:\n"
            "  /query mimo — 查询所有账号\n"
            "  /query mimo <序号> — 查询指定账号\n"
            "  /query mimo login — 查看/登录账号\n"
            "  /query mimo list — 列出账号\n"
            "  /query mimo del <序号> — 删除账号"
        )

    async def _handle_mimo_login(self, event: AstrMessageEvent, accounts: list, args: list):
        """处理 MiMo 登录命令"""
        # /query mimo login — 显示所有账号状态
        if not args:
            if not accounts:
                yield event.plain_result("还没有配置账号\n用法: /query mimo login 账号 密码")
                return
            lines = [f"📋 共 {len(accounts)} 个账号:"]
            for i, acc in enumerate(accounts):
                has_st = bool(acc.get("serviceToken"))
                has_pt = bool(acc.get("passToken"))
                lines.append(
                    f"  {i + 1}. {acc.get('name') or acc.get('account') or f'账号{i+1}'} "
                    f"{'✅' if has_st else ('⚠️' if has_pt else '❌')}"
                )
            lines.append("\n用法: /query mimo login 账号 密码（添加或更新）")
            yield event.plain_result("\n".join(lines))
            return

        # /query mimo login passtoken <账号> <userId> <token>
        if len(args) == 4 and args[0] == "passtoken":
            account, user_id, pass_token = args[1], args[2], args[3]
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
            self._save_mimo_accounts(accounts)
            yield event.plain_result(f"✅ {account} passToken 设置成功\n  userId: {user_id}")
            return

        # /query mimo login account password
        if len(args) >= 2:
            account, password = args[0], args[1]

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
                self._save_mimo_accounts(accounts)
                yield event.plain_result(f"✅ {account} 登录成功!\n  userId: {result['userId']}")
                return
            except OtpRequired:
                yield event.plain_result(f"📱 {account} 需要短信验证，验证码已发送\n请直接回复 6 位验证码：")
            except LoginError as e:
                yield event.plain_result(f"❌ {account} 登录失败: {e}")
                return
            except (OSError, StsError) as e:
                yield event.plain_result(f"❌ {account} 网络错误: {e}")
                return

            # OTP 会话等待
            @session_waiter(timeout=120, record_history_chains=False)
            async def otp_waiter(controller: SessionController, otp_event: AstrMessageEvent):
                code = otp_event.message_str.strip()
                if not code.isdigit() or len(code) != 6:
                    await otp_event.send(otp_event.plain_result("❌ 请输入 6 位数字验证码"))
                    controller.keep(timeout=120, reset_timeout=True)
                    return
                await otp_event.send(otp_event.plain_result("🔑 验证码已收到，正在登录..."))
                try:
                    result = await loop.run_in_executor(None, lambda: _try_login(otp_code=code))
                    accounts[idx].update(result)
                    self._save_mimo_accounts(accounts)
                    await otp_event.send(otp_event.plain_result(f"✅ {account} 登录成功!\n  userId: {result['userId']}"))
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
            "  /query mimo login — 查看所有账号\n"
            "  /query mimo login 账号 密码 — 添加/更新账号\n"
            "  /query mimo login passtoken 账号 userId token — 设置 passToken"
        )

    # ================== 华数子命令 ==================

    async def _handle_wasu(self, event: AstrMessageEvent, args: list):
        """处理华数广电相关命令"""
        accounts = self._get_wasu_accounts()

        # /query wasu — 查询所有账号
        if not args:
            if not accounts:
                yield event.plain_result(
                    "❌ 还没有配置华数账号\n使用 /query wasu login 添加"
                )
                return

            yield event.plain_result("🔍 正在查询华数广电...")
            for i, acc in enumerate(accounts):
                result = await self._wasu.query(acc)
                label = self._wasu.get_account_label(acc, i)
                result.account_name = label
                yield event.plain_result(result.to_text())
            return

        sub_cmd = args[0].lower()

        # /query wasu login
        if sub_cmd == "login":
            yield event.plain_result("🔑 正在处理华数登录...")
            async for r in self._handle_wasu_login(event, accounts, args[1:]):
                yield r
            return

        # /query wasu list
        if sub_cmd == "list":
            if not accounts:
                yield event.plain_result("还没有配置华数账号")
                return
            lines = [f"📋 共 {len(accounts)} 个华数账号:"]
            for i, acc in enumerate(accounts):
                lines.append(
                    f"  {i + 1}. {self._wasu.get_account_label(acc, i)} | 手机号: {acc.get('phone', '无')}"
                )
            yield event.plain_result("\n".join(lines))
            return

        # /query wasu del <序号>
        if sub_cmd == "del":
            if len(args) < 2 or not args[1].isdigit():
                yield event.plain_result("用法: /query wasu del <序号>")
                return
            idx = int(args[1]) - 1
            if 0 <= idx < len(accounts):
                removed = accounts.pop(idx)
                self._save_wasu_accounts(accounts)
                yield event.plain_result(f"✅ 已删除: {self._wasu.get_account_label(removed)}")
            else:
                yield event.plain_result(f"❌ 序号 {args[1]} 不存在")
            return

        # /query wasu <序号> — 查询指定账号
        if sub_cmd.isdigit():
            idx = int(sub_cmd) - 1
            if 0 <= idx < len(accounts):
                acc = accounts[idx]
                yield event.plain_result("🔍 正在查询...")
                result = await self._wasu.query(acc)
                label = self._wasu.get_account_label(acc, idx)
                result.account_name = label
                yield event.plain_result(result.to_text())
            else:
                yield event.plain_result(f"❌ 序号 {idx + 1} 不存在，共 {len(accounts)} 个账号")
            return

        yield event.plain_result(
            "华数用法:\n"
            "  /query wasu — 查询所有账号\n"
            "  /query wasu <序号> — 查询指定账号\n"
            "  /query wasu login — 查看/登录账号\n"
            "  /query wasu list — 列出账号\n"
            "  /query wasu del <序号> — 删除账号"
        )

    async def _handle_wasu_login(self, event: AstrMessageEvent, accounts: list, args: list):
        """处理华数登录命令"""
        # /query wasu login — 显示所有账号状态
        if not args:
            if not accounts:
                yield event.plain_result("还没有配置账号\n用法: /query wasu login user_key token phone sign")
                return
            lines = [f"📋 共 {len(accounts)} 个账号:"]
            for i, acc in enumerate(accounts):
                lines.append(
                    f"  {i + 1}. {self._wasu.get_account_label(acc, i)} | 手机号: {acc.get('phone', '无')}"
                )
            lines.append("\n用法: /query wasu login user_key token phone sign")
            yield event.plain_result("\n".join(lines))
            return

        # /query wasu login user_key token phone sign
        if len(args) >= 4:
            user_key, token, phone = args[0], args[1], args[2]
            sign = args[3] if len(args) > 3 else ""

            # 查找或创建账号
            idx = -1
            for i, acc in enumerate(accounts):
                if acc.get("phone") == phone:
                    idx = i
                    break

            if idx < 0:
                accounts.append({"phone": phone})
                idx = len(accounts) - 1

            accounts[idx]["user_key"] = user_key
            accounts[idx]["token"] = token
            accounts[idx]["phone"] = phone
            accounts[idx]["sign"] = sign

            self._save_wasu_accounts(accounts)
            yield event.plain_result(f"✅ 华数账号 {phone} 配置成功")
            return

        yield event.plain_result(
            "用法:\n"
            "  /query wasu login — 查看所有账号\n"
            "  /query wasu login user_key token phone sign — 添加/更新账号"
        )

    # ================== 更新 ==================

    async def _handle_update(self, event: AstrMessageEvent):
        """处理更新命令"""
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
