"""资源查询插件 - 基础框架"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class QueryResult:
    """查询结果统一格式"""
    success: bool
    platform: str  # 平台标识
    account_name: str  # 账号名称
    data: dict  # 查询数据
    error: str = ""  # 错误信息

    def to_text(self) -> str:
        """转换为文本格式"""
        if not self.success:
            return f"📋 {self.platform} - {self.account_name}\n❌ {self.error}"
        return self._format_data()

    @abstractmethod
    def _format_data(self) -> str:
        """格式化数据（子类实现）"""
        pass


class BasePlatform(ABC):
    """平台查询基类"""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称"""
        pass

    @property
    @abstractmethod
    def platform_icon(self) -> str:
        """平台图标"""
        pass

    @abstractmethod
    async def query(self, account: dict) -> QueryResult:
        """查询单个账号"""
        pass

    def get_account_label(self, account: dict, index: int = 0) -> str:
        """获取账号显示名称"""
        return account.get("name") or account.get("account") or f"{self.platform_name}账号{index + 1}"
