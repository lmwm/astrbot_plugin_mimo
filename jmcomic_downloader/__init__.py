"""
JMComic 下载管理器

一个模块化的 JMComic 漫画下载管理系统，提供以下功能：
  - 下载管理：从 JMComic 网站下载漫画图片
  - PDF 转换：将下载的图片转换为 PDF 文件
  - 缓存管理：本地缓存和信息管理
  - 消息适配：支持不同平台的消息发送

架构设计：
  JMManager（管理器）
    ├── Downloader（下载器）
    ├── PDFConverter（PDF转换器）
    ├── CacheManager（缓存管理器）
    └── MessageAdapter（消息适配器）

使用示例：
    from jmcomic_downloader import JMManager
    
    # 初始化管理器
    manager = JMManager(config={
        "jm_proxy": "http://127.0.0.1:7890",
        "jm_cookies": "csrf=abc123"
    })
    
    # 下载漫画
    result = await manager.download("123456")
    if result.success:
        print(f"PDF 路径: {result.pdf_path}")
"""

from .manager import JMManager
from .models import DownloadResult, AlbumInfo, ProgressInfo
from .config import ManagerConfig

__version__ = "2.0.0"
__all__ = ["JMManager", "DownloadResult", "AlbumInfo", "ProgressInfo", "ManagerConfig"]