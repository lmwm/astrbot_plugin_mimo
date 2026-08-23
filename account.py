"""
账号管理模块 - 多平台账号的统一管理

职责：
  - 数据目录管理
  - 账号文件的读写（JSON）
  - 模板文件的读写（TXT）
  - 账号列表的增删改查
  - 跨平台的账号筛选
"""

import json
from pathlib import Path

from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class AccountManager:
    """账号统一管理器"""

    def __init__(self, plugin_name: str, plugin_dir: Path):
        self._plugin_name = plugin_name
        self._plugin_dir = plugin_dir

    # ── 数据目录 ──

    def _get_data_path(self) -> Path:
        """获取插件数据目录"""
        data_path = Path(get_astrbot_data_path()) / "plugin_data" / self._plugin_name
        data_path.mkdir(parents=True, exist_ok=True)
        return data_path

    # ── 文件名生成 ──

    def _get_account_filename(self, acc: dict) -> str:
        """获取账号配置文件名（格式：平台_名称.json）

        Args:
            acc: 账号配置字典。

        Returns:
            配置文件名。
        """
        platform = acc.get("platform", "unknown")
        name = acc.get("name", "").strip()
        if not name:
            name = "unnamed"
        # 清理文件名中的非法字符
        name = "".join(c for c in name if c.isalnum() or c in "-_\u4e00-\u9fff")
        if not name:
            name = "unnamed"
        return f"{platform}_{name}.json"

    # ── 账号读写 ──

    def get_all_accounts(self) -> list:
        """获取所有账号（包括模板）"""
        accounts = []
        data_path = self._get_data_path()

        # 读取所有 json 文件（排除 accounts.json）
        for json_file in data_path.glob("*.json"):
            if json_file.name == "accounts.json":
                continue
            try:
                acc = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(acc, dict):
                    acc["_config_file"] = json_file.name
                    # 读取对应的模板文件
                    template_file = data_path / json_file.name.replace(".json", ".txt")
                    if template_file.exists():
                        acc["template"] = template_file.read_text(encoding="utf-8")
                    elif not acc.get("template"):
                        acc["template"] = self._get_default_template(acc.get("platform", ""))
                    accounts.append(acc)
            except (json.JSONDecodeError, OSError):
                continue

        # 兼容旧版本：从 accounts.json 读取并迁移
        self._migrate_old_accounts(data_path, accounts)

        return accounts

    def save_all_accounts(self, accounts: list):
        """保存所有账号到单独的配置文件和模板文件

        Args:
            accounts: 账号配置列表。
        """
        data_path = self._get_data_path()

        # 第零步：为没有名称的账号自动生成默认名称
        self._fill_default_names(accounts)

        # 第一步：收集新账号的文件名（不写入磁盘）
        new_filenames: set[str] = set()
        acc_file_pairs: list[tuple[dict, str]] = []
        for acc in accounts:
            filename = self._get_account_filename(acc)
            # 处理文件名冲突：追加数字后缀
            original = filename
            counter = 2
            while filename in new_filenames:
                stem = original.rsplit(".", 1)[0]
                filename = f"{stem}_{counter}.json"
                counter += 1
            new_filenames.add(filename)
            acc_file_pairs.append((acc, filename))

        # 第二步：写入所有新文件
        for acc, filename in acc_file_pairs:
            filepath = data_path / filename
            save_acc = {k: v for k, v in acc.items() if not k.startswith("_") and k != "template"}
            filepath.write_text(
                json.dumps(save_acc, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            template = acc.get("template", "")
            template_filename = filename.replace(".json", ".txt")
            template_filepath = data_path / template_filename
            template_filepath.write_text(template, encoding="utf-8")

        # 第三步：删除不在新列表中的旧文件
        for old_file in data_path.glob("*.json"):
            if old_file.name == "accounts.json":
                continue
            if old_file.name not in new_filenames:
                old_file.unlink(missing_ok=True)
                template_file = data_path / old_file.name.replace(".json", ".txt")
                if template_file.exists():
                    template_file.unlink(missing_ok=True)

    def _fill_default_names(self, accounts: list):
        """为没有名称的账号自动生成默认名称（如：账号001）

        Args:
            accounts: 账号配置列表（原地修改）。
        """
        # 统计各平台已有的名称
        platform_counters: dict[str, int] = {}
        for acc in accounts:
            platform = acc.get("platform", "unknown")
            name = acc.get("name", "").strip()
            if name and name.startswith("账号"):
                try:
                    num = int(name[2:])
                    platform_counters[platform] = max(platform_counters.get(platform, 0), num)
                except ValueError:
                    pass

        # 为没有名称的账号生成默认名称
        for acc in accounts:
            if not acc.get("name", "").strip():
                platform = acc.get("platform", "unknown")
                counter = platform_counters.get(platform, 0) + 1
                platform_counters[platform] = counter
                acc["name"] = f"账号{counter:03d}"

    def delete_account(self, platform: str, index: int) -> dict | None:
        """删除指定平台的指定账号

        Args:
            platform: 平台名称（mimo/wasu）。
            index: 账号在该平台列表中的序号（从 0 开始）。

        Returns:
            被删除的账号配置，如果序号无效则返回 None。
        """
        platform_accounts = self.get_accounts_by_platform(platform)
        if index < 0 or index >= len(platform_accounts):
            return None

        deleted_acc = platform_accounts[index]
        all_accounts = self.get_all_accounts()

        # 找到在总列表中的真实索引
        real_idx = self._find_real_index(all_accounts, platform, deleted_acc)
        if real_idx < 0:
            return None

        all_accounts.pop(real_idx)
        self.save_all_accounts(all_accounts)
        return deleted_acc

    def _find_real_index(self, all_accounts: list, platform: str, target: dict) -> int:
        """在总列表中查找目标账号的真实索引

        通过比较文件名来定位账号，文件名是账号的稳定标识。

        Args:
            all_accounts: 所有账号列表。
            platform: 平台名称。
            target: 目标账号配置。

        Returns:
            真实索引，未找到返回 -1。
        """
        target_filename = self._get_account_filename(target)
        for i, acc in enumerate(all_accounts):
            if acc.get("platform") != platform:
                continue
            if self._get_account_filename(acc) == target_filename:
                return i
        return -1

    def _migrate_old_accounts(self, data_path: Path, accounts: list):
        """兼容旧版本：从 accounts.json 迁移"""
        old_file = data_path / "accounts.json"
        if not old_file.exists():
            return
        try:
            old_data = json.loads(old_file.read_text(encoding="utf-8"))
            if not isinstance(old_data, list):
                return
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
            old_file.unlink()
        except (json.JSONDecodeError, OSError):
            pass

    # ── 模板管理 ──

    def _get_default_template(self, platform: str) -> str:
        """获取默认模板内容"""
        template_file = self._plugin_dir / "templates" / f"{platform}_default.txt"
        if template_file.exists():
            return template_file.read_text(encoding="utf-8")
        return ""

    # ── 平台筛选 ──

    def get_accounts_by_platform(self, platform: str) -> list:
        """获取指定平台的所有账号"""
        return [acc for acc in self.get_all_accounts() if acc.get("platform") == platform]

    def get_account_indices(self, platform: str) -> list:
        """获取指定平台账号在总列表中的索引 [(index, account), ...]"""
        all_accounts = self.get_all_accounts()
        return [(i, acc) for i, acc in enumerate(all_accounts) if acc.get("platform") == platform]

    def find_account_index(self, platform: str, identifier: str, key: str = "account") -> int:
        """查找账号在指定平台列表中的索引"""
        platform_accounts = self.get_accounts_by_platform(platform)
        for i, acc in enumerate(platform_accounts):
            if acc.get(key) == identifier:
                return i
        return -1