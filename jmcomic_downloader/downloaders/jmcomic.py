"""
JMComic 下载器实现

使用 jmcomic 库实现漫画图片下载。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

import jmcomic
from jmcomic.jm_exception import (
    MissingAlbumPhotoException,
    PartialDownloadFailedException,
    RequestRetryAllFailException,
)

from ..config import DownloaderConfig
from ..models import AlbumInfo, ProgressInfo
from ..utils import count_images, parse_cookies
from .base import BaseDownloader


class JMComicDownloader(BaseDownloader):
    """JMComic 下载器
    
    使用 jmcomic 库从 JMComic 网站下载漫画图片。
    """
    
    def __init__(self, config: DownloaderConfig):
        """初始化下载器
        
        Args:
            config: 下载器配置
        """
        self._config = config
    
    def _build_option(self, download_dir: Path) -> jmcomic.JmOption:
        """构建 jmcomic 下载选项
        
        Args:
            download_dir: 下载目录路径
            
        Returns:
            jmcomic 选项对象
        """
        metadata: dict = {"timeout": self._config.timeout}
        
        cookie_values = parse_cookies(self._config.cookies)
        if cookie_values:
            metadata["cookies"] = cookie_values
        
        proxy = self._config.proxy.strip()
        if proxy:
            if "://" not in proxy:
                proxy = f"http://{proxy}"
            metadata["proxies"] = {"http": proxy, "https": proxy}
        
        option_dict: dict = {
            "client": {
                "impl": "api",
                "retry_times": self._config.retry_times,
                "postman": {"meta_data": metadata},
            },
            "dir_rule": {
                "rule": "Bd / JM{Aid}-{Atitle}",
                "base_dir": str(download_dir),
            },
            "download": {
                "cache": True,
                "image": {"decode": True, "suffix": ".jpg"},
                "threading": {
                    "image": self._config.image_threads,
                    "photo": self._config.photo_threads,
                },
            },
        }
        return jmcomic.JmOption.construct(option_dict)
    
    async def get_album_info(self, album_id: str) -> AlbumInfo:
        """获取漫画信息
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画信息对象
        """
        try:
            option = self._build_option(Path("temp"))
            client = option.new_jm_client()
            album = client.get_album_detail(album_id)
            return AlbumInfo(
                id=str(album.id),
                name=str(album.name),
                author=str(getattr(album, 'author', '未知')),
                chapter_count=len(album),
                image_count=sum(len(photo) for photo in album),
                tags=list(getattr(album, 'tags', [])),
            )
        except Exception:
            return AlbumInfo(id=album_id, name="未知")
    
    async def download(
        self,
        album_id: str,
        download_dir: Path,
        progress_callback: Optional[callable] = None,
    ) -> tuple[Path, AlbumInfo]:
        """下载漫画图片
        
        Args:
            album_id: 漫画 ID
            download_dir: 下载目录
            progress_callback: 进度回调函数
            
        Returns:
            下载目录路径和漫画信息的元组
            
        Raises:
            MissingAlbumPhotoException: 漫画不存在
            RequestRetryAllFailException: 网络请求失败
            PartialDownloadFailedException: 部分下载失败
        """
        album_dir = download_dir / f"JM{album_id}"
        album_dir.mkdir(parents=True, exist_ok=True)
        
        album_info = await self.get_album_info(album_id)
        total_images = album_info.image_count
        
        def _report(current: int, total: int, msg: str):
            if progress_callback:
                try:
                    progress_callback(ProgressInfo(current=current, total=total, message=msg))
                except Exception:
                    pass
        
        _report(0, total_images, "准备下载...")
        option = self._build_option(album_dir)
        
        _report(0, total_images, "开始下载图片...")
        
        def _download_with_progress():
            stop_event = threading.Event()
            
            def _monitor_progress():
                time.sleep(2)
                while not stop_event.is_set():
                    try:
                        image_count = count_images(album_dir)
                        if image_count > 0:
                            _report(image_count, total_images, f"已下载 {image_count}/{total_images} 张图片")
                    except Exception:
                        pass
                    time.sleep(3)
            
            monitor_thread = threading.Thread(target=_monitor_progress, daemon=True)
            monitor_thread.start()
            
            try:
                result = jmcomic.download_album(album_id, option)
                return result
            finally:
                stop_event.set()
        
        result = await asyncio.to_thread(_download_with_progress)
        
        _report(total_images, total_images, "下载完成")
        
        return album_dir, album_info