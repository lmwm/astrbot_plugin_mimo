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

    def _get_config_path(self) -> Path:
        """获取配置根目录"""
        config_path = self._get_data_path() / "config"
        config_path.mkdir(parents=True, exist_ok=True)
        return config_path

    def _get_platform_path(self, platform: str) -> Path:
        """获取指定平台的配置目录"""
        platform_path = self._get_config_path() / platform
        platform_path.mkdir(parents=True, exist_ok=True)
        return platform_path

    def _get_jm_config_path(self) -> Path:
        """获取 JM 下载配置目录"""
        jm_path = self._get_config_path() / "jm"
        jm_path.mkdir(parents=True, exist_ok=True)
        return jm_path

    # ── 文件名生成 ──

    def _get_account_filename(self, acc: dict) -> str:
        """获取账号配置文件名（格式：名称.json）

        Args:
            acc: 账号配置字典。

        Returns:
            配置文件名。
        """
        name = acc.get("name", "").strip()
        if not name:
            # 使用账号或手机号作为名称
            name = acc.get("account") or acc.get("phone") or "unnamed"
        # 清理文件名中的非法字符
        name = "".join(c for c in name if c.isalnum() or c in "-_\u4e00-\u9fff")
        if not name:
            name = "unnamed"
        return f"{name}.json"

    # ── 账号读写 ──

    def get_all_accounts(self) -> list:
        """获取所有账号（包括模板）

        Returns:
            账号配置列表。
        """
        accounts = []
        config_path = self._get_config_path()

        # 遍历所有平台目录
        for platform_dir in config_path.iterdir():
            if not platform_dir.is_dir():
                continue
            platform = platform_dir.name
            # 跳过非平台目录
            if platform not in ("mimo", "wasu"):
                continue

            # 读取该平台下的所有 json 文件
            for json_file in platform_dir.glob("*.json"):
                try:
                    acc = json.loads(json_file.read_text(encoding="utf-8"))
                    if isinstance(acc, dict):
                        acc["platform"] = platform
                        acc["_config_file"] = json_file.name
                        acc["_config_dir"] = str(platform_dir)
                        # 读取对应的模板文件
                        template_file = platform_dir / json_file.name.replace(".json", ".txt")
                        if template_file.exists():
                            acc["template"] = template_file.read_text(encoding="utf-8")
                        elif not acc.get("template"):
                            acc["template"] = self._get_default_template(platform)
                        accounts.append(acc)
                except (json.JSONDecodeError, OSError):
                    continue

        # 兼容旧版本：从旧目录迁移
        self._migrate_old_accounts(accounts)

        # 为没有名称的账号自动填充默认名称并保存
        if self._fill_default_names(accounts):
            self.save_all_accounts(accounts)

        return accounts

    def save_all_accounts(self, accounts: list):
        """保存所有账号到单独的配置文件和模板文件

        Args:
            accounts: 账号配置列表。
        """
        config_path = self._get_config_path()

        # 第零步：为没有名称的账号自动生成默认名称
        self._fill_default_names(accounts)

        # 第一步：按平台分组，收集新账号的文件名（不写入磁盘）
        platform_files: dict[str, set[str]] = {}
        acc_file_pairs: list[tuple[dict, str, str]] = []  # (acc, platform, filename)

        for acc in accounts:
            platform = acc.get("platform", "unknown")
            filename = self._get_account_filename(acc)
            # 处理文件名冲突：追加数字后缀
            if platform not in platform_files:
                platform_files[platform] = set()
            original = filename
            counter = 2
            while filename in platform_files[platform]:
                stem = original.rsplit(".", 1)[0]
                filename = f"{stem}_{counter}.json"
                counter += 1
            platform_files[platform].add(filename)
            acc_file_pairs.append((acc, platform, filename))

        # 第二步：写入所有新文件
        for acc, platform, filename in acc_file_pairs:
            platform_dir = self._get_platform_path(platform)
            filepath = platform_dir / filename
            save_acc = {k: v for k, v in acc.items() if not k.startswith("_") and k != "template" and k != "platform"}
            filepath.write_text(
                json.dumps(save_acc, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            template = acc.get("template", "")
            template_filename = filename.replace(".json", ".txt")
            template_filepath = platform_dir / template_filename
            template_filepath.write_text(template, encoding="utf-8")

        # 第三步：删除不在新列表中的旧文件
        for platform_dir in config_path.iterdir():
            if not platform_dir.is_dir():
                continue
            platform = platform_dir.name
            if platform not in platform_files:
                # 删除空平台目录
                if not any(platform_dir.iterdir()):
                    platform_dir.rmdir()
                continue
            valid_filenames = platform_files[platform]
            for old_file in platform_dir.glob("*.json"):
                if old_file.name not in valid_filenames:
                    old_file.unlink(missing_ok=True)
                    template_file = platform_dir / old_file.name.replace(".json", ".txt")
                    if template_file.exists():
                        template_file.unlink(missing_ok=True)
            # 删除空平台目录
            if not any(platform_dir.iterdir()):
                platform_dir.rmdir()

    def _fill_default_names(self, accounts: list) -> bool:
        """为没有名称的账号自动生成默认名称（如：账号001）

        所有平台共享同一个序号计数器，最大为 999。

        Args:
            accounts: 账号配置列表（原地修改）。

        Returns:
            是否有账号被修改。
        """
        changed = False

        # 统计所有平台已有的名称（共享序号）
        max_counter = 0
        for acc in accounts:
            name = acc.get("name", "").strip()
            if name and name.startswith("账号"):
                try:
                    num = int(name[2:])
                    max_counter = max(max_counter, num)
                except ValueError:
                    pass

        # 为没有名称的账号生成默认名称（共享序号，最大 999）
        counter = max_counter
        for acc in accounts:
            if not acc.get("name", "").strip():
                counter += 1
                if counter > 999:
                    counter = 1  # 超过 999 后从 001 开始循环
                acc["name"] = f"账号{counter:03d}"
                changed = True

        return changed

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

    def _migrate_old_accounts(self, accounts: list):
        """兼容旧版本：从旧目录结构迁移"""
        data_path = self._get_data_path()

        # 迁移旧的 accounts.json
        old_accounts_file = data_path / "accounts.json"
        if old_accounts_file.exists():
            try:
                old_data = json.loads(old_accounts_file.read_text(encoding="utf-8"))
                if isinstance(old_data, list):
                    for acc in old_data:
                        if isinstance(acc, dict):
                            accounts.append(acc)
                old_accounts_file.unlink()
            except (json.JSONDecodeError, OSError):
                pass

        # 迁移旧的单独配置文件（格式：平台_名称.json）
        for old_file in data_path.glob("*.json"):
            if old_file.name == "accounts.json" or old_file.name == "var_config.json":
                continue
            try:
                # 从文件名解析平台
                parts = old_file.name.split("_", 1)
                if len(parts) == 2:
                    platform = parts[0]
                    if platform in ("mimo", "wasu"):
                        acc = json.loads(old_file.read_text(encoding="utf-8"))
                        if isinstance(acc, dict):
                            acc["platform"] = platform
                            accounts.append(acc)
                        # 删除旧文件
                        old_file.unlink(missing_ok=True)
                        template_file = data_path / old_file.name.replace(".json", ".txt")
                        if template_file.exists():
                            template_file.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                continue

        # 迁移旧目录结构（config 下直接有 mimo_xxx.json 文件）
        config_path = self._get_config_path()
        for old_file in config_path.glob("*.json"):
            if old_file.name == "var_config.json":
                continue
            try:
                parts = old_file.name.split("_", 1)
                if len(parts) == 2:
                    platform = parts[0]
                    if platform in ("mimo", "wasu"):
                        acc = json.loads(old_file.read_text(encoding="utf-8"))
                        if isinstance(acc, dict):
                            acc["platform"] = platform
                            accounts.append(acc)
                        old_file.unlink(missing_ok=True)
                        template_file = config_path / old_file.name.replace(".json", ".txt")
                        if template_file.exists():
                            template_file.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                continue

        # 如果有迁移的账号，保存到新结构
        if accounts:
            self.save_all_accounts(accounts)

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
