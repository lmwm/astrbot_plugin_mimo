"""
配置管理

管理 JMComic 下载器的配置参数。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DownloaderConfig:
    """下载器配置"""
    
    # 基本配置
    enabled: bool = True
    send_file: bool = True
    
    # 网络配置
    cookies: str = ""
    proxy: str = ""
    timeout: int = 20
    retry_times: int = 3
    
    # 并发配置
    image_threads: int = 16
    photo_threads: int = 4
    max_concurrent: int = 1
    
    # 存储配置
    download_dir: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: dict) -> "DownloaderConfig":
        """从字典创建配置"""
        return cls(
            enabled=data.get("jm_enabled", True),
            send_file=data.get("jm_send_file", True),
            cookies=data.get("jm_cookies", ""),
            proxy=data.get("jm_proxy", ""),
            timeout=int(data.get("jm_timeout", 20)),
            retry_times=int(data.get("jm_retry_times", 3)),
            image_threads=int(data.get("jm_image_threads", 16)),
            photo_threads=int(data.get("jm_photo_threads", 4)),
            max_concurrent=int(data.get("jm_max_concurrent", 1)),
            download_dir=data.get("download_dir"),
        )
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "jm_enabled": self.enabled,
            "jm_send_file": self.send_file,
            "jm_cookies": self.cookies,
            "jm_proxy": self.proxy,
            "jm_timeout": self.timeout,
            "jm_retry_times": self.retry_times,
            "jm_image_threads": self.image_threads,
            "jm_photo_threads": self.photo_threads,
            "jm_max_concurrent": self.max_concurrent,
            "download_dir": self.download_dir,
        }