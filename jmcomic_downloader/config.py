"""
配置管理

管理 JMComic 下载管理器的配置参数。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DownloaderConfig:
    """下载器配置"""
    cookies: str = ""
    proxy: str = ""
    timeout: int = 20
    retry_times: int = 3
    image_threads: int = 16
    photo_threads: int = 4


@dataclass
class ConverterConfig:
    """PDF转换器配置"""
    pdf_quality: int = 95
    pdf_dpi: int = 150
    delete_original: bool = False


@dataclass
class CacheConfig:
    """缓存配置"""
    enabled: bool = True
    max_size_mb: int = 1024
    cleanup_days: int = 30


@dataclass
class ManagerConfig:
    """管理器配置"""
    enabled: bool = True
    send_file: bool = True
    max_concurrent: int = 1
    download_dir: Optional[str] = None
    
    downloader: DownloaderConfig = None
    converter: ConverterConfig = None
    cache: CacheConfig = None
    
    def __post_init__(self):
        if self.downloader is None:
            self.downloader = DownloaderConfig()
        if self.converter is None:
            self.converter = ConverterConfig()
        if self.cache is None:
            self.cache = CacheConfig()
    
    @classmethod
    def from_dict(cls, data: dict) -> "ManagerConfig":
        """从字典创建配置"""
        config = cls()
        
        # 基本配置
        config.enabled = data.get("jm_enabled", True)
        config.send_file = data.get("jm_send_file", True)
        config.max_concurrent = int(data.get("jm_max_concurrent", 1))
        config.download_dir = data.get("download_dir")
        
        # 下载器配置
        config.downloader = DownloaderConfig(
            cookies=data.get("jm_cookies", ""),
            proxy=data.get("jm_proxy", ""),
            timeout=int(data.get("jm_timeout", 20)),
            retry_times=int(data.get("jm_retry_times", 3)),
            image_threads=int(data.get("jm_image_threads", 16)),
            photo_threads=int(data.get("jm_photo_threads", 4)),
        )
        
        # PDF转换器配置
        config.converter = ConverterConfig()
        
        # 缓存配置
        config.cache = CacheConfig()
        
        return config
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "jm_enabled": self.enabled,
            "jm_send_file": self.send_file,
            "jm_max_concurrent": self.max_concurrent,
            "jm_cookies": self.downloader.cookies,
            "jm_proxy": self.downloader.proxy,
            "jm_timeout": self.downloader.timeout,
            "jm_retry_times": self.downloader.retry_times,
            "jm_image_threads": self.downloader.image_threads,
            "jm_photo_threads": self.downloader.photo_threads,
            "download_dir": self.download_dir,
        }