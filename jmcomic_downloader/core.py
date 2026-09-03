"""
核心下载和 PDF 生成功能

提供 JMComic 漫画的下载、PDF 生成和本地缓存管理功能。
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

import jmcomic
from jmcomic import Feature
from jmcomic.jm_exception import (
    MissingAlbumPhotoException,
    PartialDownloadFailedException,
    RequestRetryAllFailException,
)

from .config import DownloaderConfig
from .models import AlbumInfo, DownloadResult
from .utils import (
    ProgressCallback,
    count_images,
    find_pdf,
    normalize_album_id,
    parse_cookies,
    safe_pdf_name,
    validate_pdf,
)


class JMComicDownloader:
    """JMComic 漫画下载器
    
    提供漫画下载、PDF 生成和本地缓存管理功能。
    
    使用示例：
        # 初始化下载器
        downloader = JMComicDownloader(config={
            "jm_proxy": "http://127.0.0.1:7890",
            "jm_cookies": "csrf=abc123"
        })
        
        # 下载漫画
        result = await downloader.download("123456")
        if result.success:
            print(f"PDF 路径: {result.pdf_path}")
    """
    
    def __init__(
        self,
        config: Optional[dict] = None,
        download_dir: Optional[str] = None,
    ):
        """初始化下载器
        
        Args:
            config: 配置字典，包含下载参数
            download_dir: 下载目录路径，默认为当前目录下的 JMDownload
        """
        # 加载配置
        if config is None:
            config = {}
        self._config = DownloaderConfig.from_dict(config)
        
        # 设置下载目录
        if download_dir:
            self._download_dir = Path(download_dir)
        elif self._config.download_dir:
            self._download_dir = Path(self._config.download_dir)
        else:
            self._download_dir = Path.cwd() / "JMDownload"
        
        # 确保下载目录存在
        self._download_dir.mkdir(parents=True, exist_ok=True)
        
        # 并发信号量
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent)
    
    @property
    def download_dir(self) -> Path:
        """获取下载目录路径"""
        return self._download_dir
    
    def _get_album_dir(self, album_id: str) -> Path:
        """获取漫画存储目录路径
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画目录路径
        """
        return self._download_dir / f"JM{album_id}"
    
    def _get_info_file(self, album_id: str) -> Path:
        """获取漫画信息文件路径
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            信息文件路径
        """
        return self._get_album_dir(album_id) / "info.json"
    
    def _save_info(self, album_id: str, info: dict) -> None:
        """保存漫画信息到本地文件
        
        Args:
            album_id: 漫画 ID
            info: 漫画信息字典
        """
        info_file = self._get_info_file(album_id)
        info_file.parent.mkdir(parents=True, exist_ok=True)
        info_file.write_text(
            json.dumps(info, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    def _load_info(self, album_id: str) -> Optional[dict]:
        """从本地加载漫画信息
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画信息字典，文件不存在则返回 None
        """
        info_file = self._get_info_file(album_id)
        if info_file.exists():
            try:
                return json.loads(info_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None
    
    def _build_option(self, download_dir: Path) -> jmcomic.JmOption:
        """构建 jmcomic 下载选项
        
        Args:
            download_dir: 下载目录路径
            
        Returns:
            jmcomic 选项对象
        """
        # 构建请求元数据
        metadata: dict = {"timeout": self._config.timeout}
        
        # 配置 Cookie
        cookie_values = parse_cookies(self._config.cookies)
        if cookie_values:
            metadata["cookies"] = cookie_values
        
        # 配置代理
        proxy = self._config.proxy.strip()
        if proxy:
            if "://" not in proxy:
                proxy = f"http://{proxy}"
            metadata["proxies"] = {"http": proxy, "https": proxy}
        
        # 构建 jmcomic 选项字典
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
        
        # 检查漫画目录下的 PDF 文件
        pdf_files = list(album_dir.glob(f"JM{album_id}*.pdf"))
        
        # 兼容旧目录结构
        if not pdf_files:
            old_pdf_dir = album_dir / "pdf"
            if old_pdf_dir.exists():
                old_pdf_files = list(old_pdf_dir.glob("*.pdf"))
                if old_pdf_files:
                    # 迁移旧文件到漫画目录
                    for old_file in old_pdf_files:
                        album_info = self._load_info(album_id) or {}
                        album_name = album_info.get("name", "")
                        if album_name and album_name != "未知":
                            new_name = safe_pdf_name(album_id, album_name)
                        else:
                            new_name = old_file.name
                        new_path = album_dir / new_name
                        if not new_path.exists():
                            shutil.move(str(old_file), str(new_path))
                        else:
                            old_file.unlink()
                    # 清理旧目录
                    shutil.rmtree(old_pdf_dir, ignore_errors=True)
                    # 重新检查
                    pdf_files = list(album_dir.glob(f"JM{album_id}*.pdf"))
        
        # 旧版本将图片存放在 images 子目录中
        old_images_dir = album_dir / "images"
        if old_images_dir.exists():
            # 移动子目录到漫画目录
            for item in old_images_dir.iterdir():
                dest = album_dir / item.name
                if not dest.exists():
                    shutil.move(str(item), str(dest))
            # 清理空目录
            try:
                old_images_dir.rmdir()
            except OSError:
                pass
        
        has_pdf = len(pdf_files) > 0
        pdf_path = pdf_files[0] if pdf_files else None
        
        # 统计图片数量
        image_count = count_images(album_dir)
        
        # 加载漫画信息
        album_info = self._load_info(album_id) or {}
        
        # 只有存在 PDF 或图片时才返回结果
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
    
    async def get_album_info(self, album_id: str) -> AlbumInfo:
        """获取漫画信息
        
        优先从本地缓存读取，如果没有则从网络获取。
        
        Args:
            album_id: 漫画 ID
            
        Returns:
            漫画信息对象
        """
        # 先检查本地缓存
        local_info = self._load_info(album_id)
        if local_info and local_info.get("name") and local_info["name"] != "未知":
            return AlbumInfo(
                id=local_info.get("id", album_id),
                name=local_info.get("name", "未知"),
                author=local_info.get("author", "未知"),
                chapter_count=local_info.get("chapter_count", 0),
                image_count=local_info.get("image_count", 0),
                tags=local_info.get("tags", []),
            )
        
        # 本地没有，从网络获取
        try:
            option = self._build_option(self._download_dir / "temp")
            client = option.new_jm_client()
            album = client.get_album_detail(album_id)
            info = AlbumInfo(
                id=str(album.id),
                name=str(album.name),
                author=str(getattr(album, 'author', '未知')),
                chapter_count=len(album),
                image_count=sum(len(photo) for photo in album),
                tags=list(getattr(album, 'tags', [])),
            )
            # 保存到本地缓存
            self._save_info(album_id, {
                "id": info.id,
                "name": info.name,
                "author": info.author,
                "chapter_count": info.chapter_count,
                "image_count": info.image_count,
                "tags": info.tags,
            })
            return info
        except Exception:
            return AlbumInfo(id=album_id, name="未知")
    
    async def download(
        self,
        album_id: str,
        progress_callback: Optional[ProgressCallback] = None,
        force_redownload: bool = False,
    ) -> DownloadResult:
        """下载漫画并生成 PDF
        
        Args:
            album_id: 漫画 ID
            progress_callback: 进度回调函数 (current, total, message)
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
        
        def _report(current: int, total: int, msg: str):
            """报告下载进度"""
            if progress_callback:
                try:
                    progress_callback(current, total, msg)
                except Exception:
                    pass
        
        # 第一步：检查本地缓存
        if not force_redownload:
            local = self.check_local(normalized_id)
            if local and local["has_pdf"]:
                album_info_dict = local["album_info"]
                album_info = AlbumInfo(
                    id=album_info_dict.get("id", normalized_id),
                    name=album_info_dict.get("name", "未知"),
                    author=album_info_dict.get("author", "未知"),
                    chapter_count=album_info_dict.get("chapter_count", 0),
                    image_count=album_info_dict.get("image_count", 0),
                    tags=album_info_dict.get("tags", []),
                )
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
            album_dir = self._get_album_dir(normalized_id)
            album_dir.mkdir(parents=True, exist_ok=True)
            temp_pdf_dir = album_dir / "pdf"
            temp_pdf_dir.mkdir(parents=True, exist_ok=True)
            
            # 获取漫画信息
            album_info = await self.get_album_info(normalized_id)
            total_images = album_info.image_count
            
            try:
                _report(0, total_images, "准备下载...")
                option = self._build_option(album_dir)
                
                # 第三步：执行下载
                _report(0, total_images, "开始下载图片...")
                
                def _download_with_progress():
                    """带进度监控的下载"""
                    stop_event = threading.Event()
                    
                    def _monitor_progress():
                        """监控下载进度"""
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
                        result = jmcomic.download_album(
                            normalized_id,
                            option,
                            extra=Feature.export_pdf(
                                pdf_dir=str(temp_pdf_dir),
                                filename_rule="Aid",
                                delete_original_file=False,
                            ),
                        )
                        return result
                    finally:
                        stop_event.set()
                
                result = await asyncio.to_thread(_download_with_progress)
                
                _report(total_images, total_images, "下载完成，正在生成 PDF...")
                
                # 第四步：整理文件
                album = result.detail
                resolved_id = str(album.id)
                
                temp_pdf_path = find_pdf(temp_pdf_dir, resolved_id)
                validate_pdf(temp_pdf_path)
                
                pdf_name = safe_pdf_name(resolved_id, str(album.name))
                final_pdf_path = album_dir / pdf_name
                
                if final_pdf_path.exists():
                    final_pdf_path.unlink()
                
                shutil.move(str(temp_pdf_path), str(final_pdf_path))
                
                file_size_mb = final_pdf_path.stat().st_size / (1024 * 1024)
                
                if temp_pdf_dir.exists():
                    shutil.rmtree(temp_pdf_dir, ignore_errors=True)
                
                # 更新漫画信息缓存
                album_info_dict = {
                    "id": resolved_id,
                    "name": str(album.name),
                    "author": str(getattr(album, 'author', '未知')),
                    "chapter_count": len(album),
                    "image_count": sum(len(photo) for photo in album),
                    "tags": list(getattr(album, 'tags', [])),
                }
                self._save_info(resolved_id, album_info_dict)
                
                album_info = AlbumInfo(**album_info_dict)
                
                _report(total_images, total_images, "全部完成")
                
                return DownloadResult(
                    success=True,
                    message=f"下载完成，共 {album_info.image_count} 张图片，PDF {file_size_mb:.2f} MB",
                    album_id=resolved_id,
                    album_name=str(album.name),
                    album_info=album_info,
                    pdf_path=str(final_pdf_path),
                    pdf_name=pdf_name,
                    file_size_mb=file_size_mb,
                    image_count=album_info.image_count,
                    image_dir=str(album_dir),
                    from_cache=False,
                )
            
            except MissingAlbumPhotoException:
                return DownloadResult(
                    success=False,
                    message=f"没有找到 JM{normalized_id}，请检查 ID 或配置 cookies",
                )
            except RequestRetryAllFailException:
                return DownloadResult(
                    success=False,
                    message="JMComic 站点连接失败，请稍后重试或配置代理",
                )
            except PartialDownloadFailedException:
                return DownloadResult(
                    success=False,
                    message="部分图片下载失败，请稍后重试",
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
        album_dir = self._get_album_dir(album_id)
        if album_dir.exists():
            shutil.rmtree(album_dir, ignore_errors=True)