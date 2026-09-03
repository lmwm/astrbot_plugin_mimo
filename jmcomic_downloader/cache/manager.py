"""
缓存管理器

管理漫画的本地缓存和信息。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from ..config import CacheConfig
from ..models import AlbumInfo
from ..utils import count_images, safe_pdf_name


class CacheManager:
    """缓存管理器
    
    管理漫画的本地缓存，包括：
    - 漫画信息缓存
    - PDF 文件缓存
    - 图片目录管理
    """
    
    def __init__(self, cache_dir: Path, config: Optional[CacheConfig] = None):
        """初始化缓存管理器
        
        Args:
            cache_dir: 缓存根目录
            config: 缓存配置
        """
        self._cache_dir = cache_dir
        self._config = config or CacheConfig()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_album_dir(self, album_id: str) -> Path:
        """获取漫画缓存目录
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画目录路径
        """
        return self._cache_dir / f"JM{album_id}"
    
    def _get_info_file(self, album_id: str) -> Path:
        """获取漫画信息文件路径
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            信息文件路径
        """
        return self._get_album_dir(album_id) / "info.json"
    
    def save_info(self, album_id: str, info: AlbumInfo) -> None:
        """保存漫画信息到本地文件
        
        Args:
            album_id: 漫画 ID
            info: 漫画信息对象
        """
        info_file = self._get_info_file(album_id)
        info_file.parent.mkdir(parents=True, exist_ok=True)
        info_file.write_text(
            json.dumps(info.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def load_info(self, album_id: str) -> Optional[AlbumInfo]:
        """从本地加载漫画信息
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画信息对象，文件不存在则返回 None
        """
        info_file = self._get_info_file(album_id)
        if info_file.exists():
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
                return AlbumInfo(**data)
            except Exception:
                pass
        return None
    
    def check_local(self, album_id: str) -> Optional[dict]:
        """检查本地是否有已下载的内容
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            如果本地有内容，返回包含信息的字典，否则返回 None
        """
        album_dir = self._get_album_dir(album_id)
        if not album_dir.exists():
            return None
        
        # 检查 PDF 文件
        pdf_files = list(album_dir.glob(f"JM{album_id}*.pdf"))
        
        # 兼容旧目录结构
        if not pdf_files:
            old_pdf_dir = album_dir / "pdf"
            if old_pdf_dir.exists():
                old_pdf_files = list(old_pdf_dir.glob("*.pdf"))
                if old_pdf_files:
                    for old_file in old_pdf_files:
                        album_info = self.load_info(album_id)
                        album_name = album_info.name if album_info else ""
                        if album_name and album_name != "未知":
                            new_name = safe_pdf_name(album_id, album_name)
                        else:
                            new_name = old_file.name
                        new_path = album_dir / new_name
                        if not new_path.exists():
                            shutil.move(str(old_file), str(new_path))
                        else:
                            old_file.unlink()
                    shutil.rmtree(old_pdf_dir, ignore_errors=True)
                    pdf_files = list(album_dir.glob(f"JM{album_id}*.pdf"))
        
        # 迁移旧图片目录
        old_images_dir = album_dir / "images"
        if old_images_dir.exists():
            for item in old_images_dir.iterdir():
                dest = album_dir / item.name
                if not dest.exists():
                    shutil.move(str(item), str(dest))
            try:
                old_images_dir.rmdir()
            except OSError:
                pass
        
        has_pdf = len(pdf_files) > 0
        pdf_path = pdf_files[0] if pdf_files else None
        image_count = count_images(album_dir)
        album_info = self.load_info(album_id)
        
        if has_pdf or image_count > 0:
            return {
                "has_pdf": has_pdf,
                "pdf_path": str(pdf_path) if pdf_path else None,
                "pdf_name": pdf_path.name if pdf_path else None,
                "pdf_size_mb": pdf_path.stat().st_size / (1024 * 1024) if pdf_path else 0,
                "image_count": image_count,
                "album_info": album_info,
                "album_dir": str(album_dir),
            }
        return None
    
    def cleanup(self, album_id: str) -> None:
        """清理指定漫画的所有缓存文件
        
        Args:
            album_id: 漫画 ID
        """
        album_dir = self._get_album_dir(album_id)
        if album_dir.exists():
            shutil.rmtree(album_dir, ignore_errors=True)