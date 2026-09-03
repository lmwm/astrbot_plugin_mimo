"""
JM 管理器

协调各个功能模块，提供统一的接口。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from .cache.manager import CacheManager
from .config import ManagerConfig
from .converters.pdf import PDFConverter
from .downloaders.jmcomic import JMComicDownloader
from .models import AlbumInfo, DownloadResult, ProgressInfo, ProgressCallback
from .utils import normalize_album_id, safe_pdf_name


class JMManager:
    """JM 管理器
    
    协调下载器、转换器和缓存管理器，提供统一的漫画下载接口。
    
    使用示例：
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
    
    def __init__(
        self,
        config: Optional[dict] = None,
        download_dir: Optional[str] = None,
    ):
        """初始化管理器
        
        Args:
            config: 配置字典
            download_dir: 下载目录路径
        """
        # 加载配置
        if config is None:
            config = {}
        self._config = ManagerConfig.from_dict(config)
        
        # 设置下载目录
        if download_dir:
            self._download_dir = Path(download_dir)
        elif self._config.download_dir:
            self._download_dir = Path(self._config.download_dir)
        else:
            self._download_dir = Path.cwd() / "JMDownload"
        
        # 确保下载目录存在
        self._download_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化各个模块
        self._downloader = JMComicDownloader(self._config.downloader)
        self._converter = PDFConverter(self._config.converter)
        self._cache = CacheManager(self._download_dir, self._config.cache)
        
        # 并发信号量
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent)
    
    @property
    def download_dir(self) -> Path:
        """获取下载目录路径"""
        return self._download_dir
    
    @property
    def config(self) -> ManagerConfig:
        """获取配置"""
        return self._config
    
    def update_config(self, config: dict) -> None:
        """更新配置
        
        Args:
            config: 新的配置字典
        """
        self._config = ManagerConfig.from_dict(config)
        self._downloader = JMComicDownloader(self._config.downloader)
        self._converter = PDFConverter(self._config.converter)
        self._cache = CacheManager(self._download_dir, self._config.cache)
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent)
    
    def check_local(self, album_id: str) -> Optional[dict]:
        """检查本地是否有已下载的内容
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            如果本地有内容，返回包含信息的字典，否则返回 None
        """
        return self._cache.check_local(album_id)
    
    async def get_album_info(self, album_id: str) -> AlbumInfo:
        """获取漫画信息
        
        优先从本地缓存读取，如果没有则从网络获取。
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画信息对象
        """
        # 先检查本地缓存
        local_info = self._cache.load_info(album_id)
        if local_info and local_info.name and local_info.name != "未知":
            return local_info
        
        # 本地没有，从网络获取
        info = await self._downloader.get_album_info(album_id)
        self._cache.save_info(album_id, info)
        return info
    
    async def download(
        self,
        album_id: str,
        progress_callback: Optional[ProgressCallback] = None,
        force_redownload: bool = False,
    ) -> DownloadResult:
        """下载漫画并生成 PDF
        
        Args:
            album_id: 漫画 ID
            progress_callback: 进度回调函数
            force_redownload: 是否强制重新下载
            
        Returns:
            下载结果对象
        """
        # 检查功能是否启用
        if not self._config.enabled:
            return DownloadResult(
                success=False,
                message="JM 下载功能当前已关闭",
            )
        
        # 标准化漫画 ID
        normalized_id = normalize_album_id(album_id)
        if normalized_id is None:
            return DownloadResult(
                success=False,
                message=f"无效的漫画 ID: {album_id}",
            )
        
        def _report(info: ProgressInfo):
            if progress_callback:
                try:
                    progress_callback(info)
                except Exception:
                    pass
        
        # 第一步：检查本地缓存
        if not force_redownload:
            local = self.check_local(normalized_id)
            if local and local["has_pdf"]:
                album_info = local["album_info"]
                if not album_info:
                    album_info = await self.get_album_info(normalized_id)
                    self._cache.save_info(normalized_id, album_info)
                
                return DownloadResult(
                    success=True,
                    message=f"使用本地缓存，共 {local['image_count']} 张图片，PDF {local['pdf_size_mb']:.2f} MB",
                    album_id=normalized_id,
                    album_name=album_info.name,
                    album_info=album_info,
                    pdf_path=local["pdf_path"],
                    pdf_name=local["pdf_name"],
                    file_size_mb=local["pdf_size_mb"],
                    image_count=local["image_count"],
                    image_dir=local.get("album_dir"),
                    from_cache=True,
                )
        
        # 第二步：下载漫画
        async with self._semaphore:
            try:
                _report(ProgressInfo(message="准备下载..."))
                
                # 下载图片
                album_dir, album_info = await self._downloader.download(
                    normalized_id,
                    self._download_dir,
                    progress_callback=_report,
                )
                
                _report(ProgressInfo(
                    current=album_info.image_count,
                    total=album_info.image_count,
                    message="下载完成，正在生成 PDF..."
                ))
                
                # 生成 PDF
                pdf_name = safe_pdf_name(normalized_id, album_info.name)
                pdf_path = album_dir / pdf_name
                
                # 如果 PDF 已存在，先删除
                if pdf_path.exists():
                    pdf_path.unlink()
                
                # 转换为 PDF
                self._converter.convert(album_dir, pdf_path)
                
                # 计算文件大小
                file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
                
                # 保存漫画信息
                self._cache.save_info(normalized_id, album_info)
                
                _report(ProgressInfo(
                    current=album_info.image_count,
                    total=album_info.image_count,
                    message="全部完成"
                ))
                
                return DownloadResult(
                    success=True,
                    message=f"下载完成，共 {album_info.image_count} 张图片，PDF {file_size_mb:.2f} MB",
                    album_id=normalized_id,
                    album_name=album_info.name,
                    album_info=album_info,
                    pdf_path=str(pdf_path),
                    pdf_name=pdf_name,
                    file_size_mb=file_size_mb,
                    image_count=album_info.image_count,
                    image_dir=str(album_dir),
                    from_cache=False,
                )
            
            except Exception as e:
                return DownloadResult(
                    success=False,
                    message=f"JM{normalized_id} 下载失败（{type(e).__name__}）",
                )
    
    def cleanup_files(self, album_id: str) -> None:
        """清理指定漫画的所有下载文件
        
        Args:
            album_id: 漫画 ID
        """
        self._cache.cleanup(album_id)