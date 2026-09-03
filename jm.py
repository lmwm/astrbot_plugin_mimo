"""
JMComic 漫画下载模块（适配器）

本模块是 jmcomic_downloader 管理器的适配器，提供与 AstrBot 插件兼容的接口。

主要功能：
  - 将 jmcomic_downloader 管理器适配到 AstrBot 插件
  - 提供与原有接口兼容的方法
  - 处理 AstrBot 数据路径和配置

架构设计：
  JMManager（管理器）
    ├── Downloader（下载器）
    ├── PDFConverter（PDF转换器）
    └── CacheManager（缓存管理器）

使用示例：
  downloader = JMDownloader(config_path)
  result = await downloader.download("123456")
  if result["success"]:
      print(f"PDF 路径: {result['pdf_path']}")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

# 导入管理器
from .jmcomic_downloader import JMManager
from .jmcomic_downloader.utils import normalize_album_id

# 类型定义
ProgressCallback = Callable[[int, int, str], None]


class JMDownloader:
    """JMComic 漫画下载管理器（适配器）
    
    负责将 jmcomic_downloader 管理器适配到 AstrBot 插件。
    """
    
    def __init__(self, config_path: Path):
        """初始化 JM 下载管理器
        
        Args:
            config_path: JM 配置目录路径（如 config/jm/）
        """
        self._config_path = config_path
        self._config_path.mkdir(parents=True, exist_ok=True)
        
        # 加载配置
        self._config = self._load_config()
        
        # 初始化管理器
        self._manager = self._create_manager()
    
    def _load_config(self) -> dict:
        """从配置文件加载 JM 下载配置
        
        Returns:
            配置字典
        """
        config_file = self._config_path / "config.json"
        
        # 默认配置
        default_config = {
            "jm_enabled": True,
            "jm_send_file": True,
            "jm_cookies": "",
            "jm_proxy": "",
            "jm_timeout": 20,
            "jm_retry_times": 3,
            "jm_image_threads": 16,
            "jm_photo_threads": 4,
            "jm_max_concurrent": 1,
        }
        
        # 从文件加载配置并合并
        if config_file.exists():
            try:
                saved = json.loads(config_file.read_text(encoding="utf-8"))
                default_config.update(saved)
            except (json.JSONDecodeError, OSError):
                pass
        
        return default_config
    
    def _create_manager(self) -> JMManager:
        """创建管理器实例
        
        Returns:
            JMManager 实例
        """
        # 计算下载目录：AstrBot 数据目录/JMDownload
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        download_dir = Path(get_astrbot_data_path()) / "JMDownload"
        
        return JMManager(
            config=self._config,
            download_dir=str(download_dir),
        )
    
    def reload_config(self):
        """重新加载配置文件"""
        self._config = self._load_config()
        self._manager.update_config(self._config)
    
    def check_local(self, album_id: str) -> dict | None:
        """检查本地是否有已下载的内容
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            如果本地有内容，返回包含信息的字典，否则返回 None
        """
        return self._manager.check_local(album_id)
    
    async def get_album_info(self, album_id: str) -> dict:
        """获取漫画信息
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画信息字典
        """
        info = await self._manager.get_album_info(album_id)
        return info.to_dict()
    
    async def download(
        self,
        album_id: str,
        send_file: bool = True,
        progress_callback: ProgressCallback | None = None,
        force_redownload: bool = False,
    ) -> dict:
        """下载漫画并生成 PDF
        
        Args:
            album_id: 漫画 ID
            send_file: 是否返回文件路径用于发送
            progress_callback: 进度回调函数
            force_redownload: 是否强制重新下载
            
        Returns:
            包含下载结果的字典
        """
        # 包装进度回调函数
        def _progress_wrapper(info):
            if progress_callback:
                try:
                    progress_callback(info.current, info.total, info.message)
                except Exception:
                    pass
        
        result = await self._manager.download(
            album_id=album_id,
            progress_callback=_progress_wrapper,
            force_redownload=force_redownload,
        )
        
        # 转换为原有格式
        return result.to_dict()
    
    def cleanup_files(self, album_id: str) -> None:
        """清理指定漫画的所有下载文件
        
        Args:
            album_id: 漫画 ID
        """
        self._manager.cleanup_files(album_id)


# 重新导出 normalize_album_id 以保持接口兼容
__all__ = ["JMDownloader", "normalize_album_id"]