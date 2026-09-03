"""
数据模型定义

定义 JMComic 下载器中使用的数据结构。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AlbumInfo:
    """漫画信息"""
    id: str
    name: str
    author: str = "未知"
    chapter_count: int = 0
    image_count: int = 0
    tags: list[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class DownloadResult:
    """下载结果"""
    success: bool
    message: str
    album_id: str = ""
    album_name: str = ""
    album_info: Optional[AlbumInfo] = None
    pdf_path: Optional[str] = None
    pdf_name: Optional[str] = None
    file_size_mb: float = 0.0
    image_count: int = 0
    from_cache: bool = False
    image_dir: Optional[str] = None
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        result = {
            "success": self.success,
            "message": self.message,
            "album_id": self.album_id,
            "album_name": self.album_name,
            "file_size_mb": self.file_size_mb,
            "image_count": self.image_count,
            "from_cache": self.from_cache,
        }
        
        if self.album_info:
            result["album_info"] = {
                "id": self.album_info.id,
                "name": self.album_info.name,
                "author": self.album_info.author,
                "chapter_count": self.album_info.chapter_count,
                "image_count": self.album_info.image_count,
                "tags": self.album_info.tags,
            }
        
        if self.pdf_path:
            result["pdf_path"] = self.pdf_path
        if self.pdf_name:
            result["pdf_name"] = self.pdf_name
        if self.image_dir:
            result["image_dir"] = self.image_dir
            
        return result