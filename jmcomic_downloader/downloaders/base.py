"""
下载器基类

定义下载器的抽象接口。
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..models import AlbumInfo, ProgressInfo


class BaseDownloader(ABC):
    """下载器基类"""
    
    @abstractmethod
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
        """
        pass
    
    @abstractmethod
    async def get_album_info(self, album_id: str) -> AlbumInfo:
        """获取漫画信息
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画信息对象
        """
        pass