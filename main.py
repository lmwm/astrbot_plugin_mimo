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
import json
import os
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
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
_PLUGIN_VERSION = "2.1.0"

# 默认设备标识和 User-Agent
_DEFAULT_DEVICE_ID = os.getenv("MIMO_DEVICE_ID", "wb_MIQUERY000001")
_DEFAULT_UA = os.getenv("MIMO_UA", "APP/com.xiaomi.mihome APPV/11.3.203 iosPassportSDK/4.2.50 iOS/26.3.1")


@register(_PLUGIN_NAME, "资源查询", "多平台资源查询插件（MiMo/华数广电）", _PLUGIN_VERSION)
class ResourceQueryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._plugin_dir = Path(__file__).parent
        self._limits = LimitTracker(self._plugin_dir)
        self._wasu = WasuPlatform()

        # 为现有账号填充默认 device_id 和 ua
        self._fill_default_fields()

        # 注册 Pages API
        context.register_web_api(
            f"/{_PLUGIN_NAME}/config",
            self.get_config,
            ["GET"],
            "获取插件配置",
        )
        context.register_web_api(
            f"/{_PLUGIN_NAME}/config",
            self.save_config,
            ["POST"],
            "保存插件配置",
        )

    def _fill_default_fields(self):
        """为缺少 device_id 和 ua 的账号填充默认值"""
        accounts = self._get_all_accounts()
        changed = False
        # 使用配置中的默认值，如果没有则使用环境变量
        default_device_id = self.config.get("device_id") or _DEFAULT_DEVICE_ID
        default_ua = self.config.get("ua") or _DEFAULT_UA
        for acc in accounts:
            if acc.get("platform") == "mimo":
                if not acc.get("device_id"):
                    acc["device_id"] = default_device_id
                    changed = True
                if not acc.get("ua"):
                    acc["ua"] = default_ua
                    changed = True
        if changed:
            self._save_all_accounts(accounts)

    # ── Pages API ──

    async def get_config(self):
        """获取配置"""
        from astrbot.api.web import json_response
        return json_response({
            "accounts": self._get_all_accounts(),
        })

    async def save_config(self):
        """保存配置"""
        from astrbot.api.web import json_response, request
        payload = await request.json(default={})
        if "accounts" in payload:
            self._save_all_accounts(payload["accounts"])
        return json_response({"status": "ok"})

    # ── 账号管理 ──

    def _get_data_path(self) -> Path:
        """获取插件数据目录"""
        data_path = Path(get_astrbot_data_path()) / "plugin_data" / self.name
        data_path.mkdir(parents=True, exist_ok=True)
        return data_path

    def _get_account_filename(self, acc: dict) -> str:
        """获取账号配置文件名"""
        platform = acc.get("platform", "unknown")
        if platform == "mimo":
            identifier = acc.get("account") or acc.get("name") or "unknown"
        else:
            identifier = acc.get("phone") or acc.get("name") or "unknown"
        # 清理文件名中的非法字符
        identifier = "".join(c for c in identifier if c.isalnum() or c in "-_")
        return f"{platform}_{identifier}.json"

    def _get_all_accounts(self) -> list:
        """获取所有账号"""
        accounts = []
        data_path = self._get_data_path()
        # 读取所有 json 文件（排除 accounts.json）
        for json_file in data_path.glob("*.json"):
            if json_file.name == "accounts.json":
                continue
            try:
                acc = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(acc, dict):
                    acc["_config_file"] = json_file.name  # 记录配置文件名
                    accounts.append(acc)
            except (json.JSONDecodeError, OSError):
                continue
        # 兼容旧版本：从 accounts.json 读取并迁移
        old_file = data_path / "accounts.json"
        if old_file.exists():
            try:
                old_data = json.loads(old_file.read_text(encoding="utf-8"))
                if isinstance(old_data, list):
                    for acc in old_data:
                        if isinstance(acc, dict):
                            filename = self._get_account_filename(acc)
                            filepath = data_path / filename
                            if not filepath.exists():
                                filepath.write_text(
                                    json.dumps(acc, ensure_ascii=False, indent=2),
                                    encoding="utf-8"
                                )
                                acc["_config_file"] = filename
                                accounts.append(acc)
                    old_file.unlink()  # 删除旧文件
            except (json.JSONDecodeError, OSError):
                pass
        return accounts

    def _save_all_accounts(self, accounts: list):
        """保存所有账号到单独的配置文件"""
        data_path = self._get_data_path()
        # 为每个账号保存到单独文件
        for acc in accounts:
            filename = self._get_account_filename(acc)
            filepath = data_path / filename
            # 移除内部字段
            save_acc = {k: v for k, v in acc.items() if not k.startswith("_")}
            filepath.write_text(
                json.dumps(save_acc, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    def _get_mimo_accounts(self) -> list:
        """获取 MiMo 账号"""
        return [acc for acc in self._get_all_accounts() if acc.get("platform") == "mimo"]

    def _get_mimo_account_indices(self) -> list:
        """获取 MiMo 账号在总列表中的索引"""
        all_accounts = self._get_all_accounts()
        return [(i, acc) for i, acc in enumerate(all_accounts) if acc.get("platform") == "mimo"]

    def _get_wasu_accounts(self) -> list:
        """获取华数账号"""
        return [acc for acc in self._get_all_accounts() if acc.get("platform") == "wasu"]

    def _get_wasu_account_indices(self) -> list:
        """获取华数账号在总列表中的索引"""
        all_accounts = self._get_all_accounts()
        return [(i, acc) for i, acc in enumerate(all_accounts) if acc.get("platform") == "wasu"]

    def _find_mimo_account_index(self, account: str) -> int:
        """查找 MiMo 账号在 MiMo 列表中的索引"""
        mimo_accounts = self._get_mimo_accounts()
        for i, acc in enumerate(mimo_accounts):
            if acc.get("account") == account:
                return i
        return -1

    # ── MiMo 凭据管理 ──

    def _ensure_account(self, acc: dict) -> dict:
        """确保账号有可用的凭据。优先级：serviceToken > passToken > account+password"""
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

    def _re_login_account(self, acc: dict) -> dict:
        """查询失败后重新登录。优先级：passToken > account+password"""
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

    def _login_account(self, acc: dict, otp_code: str | None = None) -> dict:
        """完整登录流程，返回凭据 dict"""
        mi = MiAccount(acc.get("device_id", ""), acc.get("ua", ""))
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
        ua = acc.get("ua", "")

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
        mimo_indices = self._get_mimo_account_indices()

        # /query mimo — 查询所有账号
        if not args:
            if not mimo_indices:
                yield event.plain_result(
                    "❌ 还没有配置 MiMo 账号\n使用 /query mimo login 账号 密码 添加"
                )
                return

            yield event.plain_result("🔍 正在查询所有 MiMo 账号...")
            all_accounts = self._get_all_accounts()
            loop = asyncio.get_event_loop()

            for idx, acc in mimo_indices:
                all_accounts[idx] = await loop.run_in_executor(None, self._ensure_account, acc)
            self._save_all_accounts(all_accounts)

            for idx, acc in mimo_indices:
                results = await self._query_one_mimo(acc)
                self._save_all_accounts(all_accounts)
                label = acc.get("name") or acc.get("account") or f"账号{idx + 1}"
                if "error" in results:
                    yield event.plain_result(f"📋 {label}\n❌ {results['error']}")
                else:
                    prev = self._limits.get_prev(acc)
                    template = acc.get("template") or None
                    yield event.plain_result(format_report(results, label, prev, self._plugin_dir, template))
                    usage = results.get("usage", {}).get("data", {})
                    self._limits.update(acc, usage.get("accountRateLimit", {}))
            return

        sub_cmd = args[0].lower()

        # /query mimo login
        if sub_cmd == "login":
            yield event.plain_result("🔑 正在处理 MiMo 登录...")
            async for r in self._handle_mimo_login(event, args[1:]):
                yield r
            return

        # /query mimo list
        if sub_cmd == "list":
            if not mimo_indices:
                yield event.plain_result("还没有配置 MiMo 账号")
                return
            lines = [f"📋 共 {len(mimo_indices)} 个 MiMo 账号:"]
            for i, (idx, acc) in enumerate(mimo_indices):
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
            del_idx = int(args[1]) - 1
            if 0 <= del_idx < len(mimo_indices):
                all_accounts = self._get_all_accounts()
                real_idx, acc = mimo_indices[del_idx]
                all_accounts.pop(real_idx)
                self._save_all_accounts(all_accounts)
                yield event.plain_result(f"✅ 已删除: {acc.get('name') or acc.get('account')}")
            else:
                yield event.plain_result(f"❌ 序号 {args[1]} 不存在")
            return

        # /query mimo <序号> — 查询指定账号
        if sub_cmd.isdigit():
            query_idx = int(sub_cmd) - 1
            if 0 <= query_idx < len(mimo_indices):
                real_idx, acc = mimo_indices[query_idx]
                yield event.plain_result("🔍 正在查询...")
                loop = asyncio.get_event_loop()
                all_accounts = self._get_all_accounts()
                all_accounts[real_idx] = await loop.run_in_executor(None, self._ensure_account, acc)
                acc = all_accounts[real_idx]
                self._save_all_accounts(all_accounts)
                results = await self._query_one_mimo(acc)
                self._save_all_accounts(all_accounts)
                label = acc.get("name") or acc.get("account") or f"账号{query_idx + 1}"
                template = acc.get("template") or None
                if "error" in results:
                    yield event.plain_result(f"📋 {label}\n❌ {results['error']}")
                else:
                    prev = self._limits.get_prev(acc)
                    yield event.plain_result(format_report(results, label, prev, self._plugin_dir, template))
                    usage = results.get("usage", {}).get("data", {})
                    self._limits.update(acc, usage.get("accountRateLimit", {}))
            else:
                yield event.plain_result(f"❌ 序号 {query_idx + 1} 不存在，共 {len(mimo_indices)} 个账号")
            return

        yield event.plain_result(
            "MiMo 用法:\n"
            "  /query mimo — 查询所有账号\n"
            "  /query mimo <序号> — 查询指定账号\n"
            "  /query mimo login — 查看/登录账号\n"
            "  /query mimo list — 列出账号\n"
            "  /query mimo del <序号> — 删除账号"
        )

    async def _handle_mimo_login(self, event: AstrMessageEvent, args: list):
        """处理 MiMo 登录命令"""
        mimo_indices = self._get_mimo_account_indices()

        # /query mimo login — 显示所有账号状态
        if not args:
            if not mimo_indices:
                yield event.plain_result("还没有配置账号\n用法: /query mimo login 账号 密码")
                return
            lines = [f"📋 共 {len(mimo_indices)} 个账号:"]
            for i, (idx, acc) in enumerate(mimo_indices):
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
            all_accounts = self._get_all_accounts()
            mimo_accounts = self._get_mimo_accounts()
            find_idx = self._find_mimo_account_index(account)

            if find_idx < 0:
                # 新建账号
                acc = {"platform": "mimo", "account": account}
                mimo_accounts.append(acc)
            else:
                acc = mimo_accounts[find_idx]

            acc["userId"] = user_id
            acc["passToken"] = pass_token
            acc["serviceToken"] = ""

            loop = asyncio.get_event_loop()
            acc = await loop.run_in_executor(None, self._ensure_account, acc)

            # 更新到总列表
            all_accounts = self._get_all_accounts()
            for i, a in enumerate(all_accounts):
                if a.get("platform") == "mimo" and a.get("account") == account:
                    all_accounts[i] = acc
                    break
            else:
                all_accounts.append(acc)

            self._save_all_accounts(all_accounts)
            yield event.plain_result(f"✅ {account} passToken 设置成功\n  userId: {user_id}")
            return

        # /query mimo login account password
        if len(args) >= 2:
            account, password = args[0], args[1]
            all_accounts = self._get_all_accounts()
            mimo_accounts = self._get_mimo_accounts()
            find_idx = self._find_mimo_account_index(account)

            if find_idx < 0:
                acc = {"platform": "mimo", "account": account}
                mimo_accounts.append(acc)
            else:
                acc = mimo_accounts[find_idx]

            acc["account"] = account
            acc["password"] = password

            loop = asyncio.get_event_loop()

            def _try_login(otp_code=None):
                return self._login_account(acc, otp_code=otp_code)

            try:
                result = await loop.run_in_executor(None, _try_login)
                acc.update(result)

                # 更新到总列表
                all_accounts = self._get_all_accounts()
                for i, a in enumerate(all_accounts):
                    if a.get("platform") == "mimo" and a.get("account") == account:
                        all_accounts[i] = acc
                        break
                else:
                    all_accounts.append(acc)

                self._save_all_accounts(all_accounts)
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
                    acc.update(result)

                    # 更新到总列表
                    all_accounts = self._get_all_accounts()
                    for i, a in enumerate(all_accounts):
                        if a.get("platform") == "mimo" and a.get("account") == account:
                            all_accounts[i] = acc
                            break
                    else:
                        all_accounts.append(acc)

                    self._save_all_accounts(all_accounts)
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
        wasu_indices = self._get_wasu_account_indices()

        # /query wasu — 查询所有账号
        if not args:
            if not wasu_indices:
                yield event.plain_result(
                    "❌ 还没有配置华数账号\n使用 /query wasu login 添加"
                )
                return

            yield event.plain_result("🔍 正在查询华数广电...")
            for i, (idx, acc) in enumerate(wasu_indices):
                template = acc.get("template") or None
                result = await self._wasu.query(acc, template)
                label = acc.get("name") or acc.get("phone") or f"华数账号{i+1}"
                result.account_name = label
                yield event.plain_result(result.to_text())
            return

        sub_cmd = args[0].lower()

        # /query wasu login
        if sub_cmd == "login":
            yield event.plain_result("🔑 正在处理华数登录...")
            async for r in self._handle_wasu_login(event, args[1:]):
                yield r
            return

        # /query wasu list
        if sub_cmd == "list":
            if not wasu_indices:
                yield event.plain_result("还没有配置华数账号")
                return
            lines = [f"📋 共 {len(wasu_indices)} 个华数账号:"]
            for i, (idx, acc) in enumerate(wasu_indices):
                lines.append(
                    f"  {i + 1}. {acc.get('name') or acc.get('phone') or f'华数账号{i+1}'} | 手机号: {acc.get('phone', '无')}"
                )
            yield event.plain_result("\n".join(lines))
            return

        # /query wasu del <序号>
        if sub_cmd == "del":
            if len(args) < 2 or not args[1].isdigit():
                yield event.plain_result("用法: /query wasu del <序号>")
                return
            del_idx = int(args[1]) - 1
            if 0 <= del_idx < len(wasu_indices):
                all_accounts = self._get_all_accounts()
                real_idx, acc = wasu_indices[del_idx]
                all_accounts.pop(real_idx)
                self._save_all_accounts(all_accounts)
                yield event.plain_result(f"✅ 已删除: {acc.get('name') or acc.get('phone')}")
            else:
                yield event.plain_result(f"❌ 序号 {args[1]} 不存在")
            return

        # /query wasu <序号> — 查询指定账号
        if sub_cmd.isdigit():
            query_idx = int(sub_cmd) - 1
            if 0 <= query_idx < len(wasu_indices):
                idx, acc = wasu_indices[query_idx]
                yield event.plain_result("🔍 正在查询...")
                template = acc.get("template") or None
                result = await self._wasu.query(acc, template)
                label = acc.get("name") or acc.get("phone") or f"华数账号{query_idx+1}"
                result.account_name = label
                yield event.plain_result(result.to_text())
            else:
                yield event.plain_result(f"❌ 序号 {query_idx + 1} 不存在，共 {len(wasu_indices)} 个账号")
            return

        yield event.plain_result(
            "华数用法:\n"
            "  /query wasu — 查询所有账号\n"
            "  /query wasu <序号> — 查询指定账号\n"
            "  /query wasu login — 查看/登录账号\n"
            "  /query wasu list — 列出账号\n"
            "  /query wasu del <序号> — 删除账号"
        )

    async def _handle_wasu_login(self, event: AstrMessageEvent, args: list):
        """处理华数登录命令"""
        wasu_indices = self._get_wasu_account_indices()

        # /query wasu login — 显示所有账号状态
        if not args:
            if not wasu_indices:
                yield event.plain_result("还没有配置账号\n用法: /query wasu login user_key token phone sign")
                return
            lines = [f"📋 共 {len(wasu_indices)} 个账号:"]
            for i, (idx, acc) in enumerate(wasu_indices):
                lines.append(
                    f"  {i + 1}. {acc.get('name') or acc.get('phone') or f'华数账号{i+1}'} | 手机号: {acc.get('phone', '无')}"
                )
            lines.append("\n用法: /query wasu login user_key token phone sign")
            yield event.plain_result("\n".join(lines))
            return

        # /query wasu login user_key token phone sign
        if len(args) >= 4:
            user_key, token, phone = args[0], args[1], args[2]
            sign = args[3] if len(args) > 3 else ""

            all_accounts = self._get_all_accounts()

            # 查找已有账号
            find_idx = -1
            for i, acc in enumerate(all_accounts):
                if acc.get("platform") == "wasu" and acc.get("phone") == phone:
                    find_idx = i
                    break

            if find_idx < 0:
                # 新建账号
                acc = {
                    "platform": "wasu",
                    "phone": phone,
                    "user_key": user_key,
                    "token": token,
                    "sign": sign,
                }
                all_accounts.append(acc)
            else:
                all_accounts[find_idx]["user_key"] = user_key
                all_accounts[find_idx]["token"] = token
                all_accounts[find_idx]["sign"] = sign

            self._save_all_accounts(all_accounts)
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
            yield event.plain_result(f"✅ 已是最新版本 v{check['current']}")
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
