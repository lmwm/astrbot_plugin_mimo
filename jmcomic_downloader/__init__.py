"""
JMComic 下载器 - 独立模块

本模块提供 JMComic 漫画的下载、PDF 生成和本地缓存管理功能。
可以独立于 AstrBot 框架使用。

主要功能：
  - 从 JMComic 网站下载漫画图片
  - 将下载的图片合并为 PDF 文件
  - 支持本地缓存，避免重复下载
  - 支持下载进度回调
  - 支持代理和 Cookie 配置

使用示例：
    from jmcomic_downloader import JMComicDownloader
    
    # 初始化下载器
    downloader = JMComicDownloader(config={
        "jm_proxy": "http://127.0.0.1:7890",
        "jm_cookies": "csrf=abc123"
    })
    
    # 下载漫画
    result = await downloader.download("123456")
    if result["success"]:
        print(f"PDF 路径: {result['pdf_path']}")
"""

from .core import JMComicDownloader
from .models import DownloadResult, AlbumInfo
from .config import DownloaderConfig

__version__ = "1.0.0"
__all__ = ["JMComicDownloader", "DownloadResult", "AlbumInfo", "DownloaderConfig"]