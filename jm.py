"""JMComic 下载模块

功能：
  - 下载 JMComic 漫画
  - 合成 PDF 文件
  - 通过私聊发送文件
  - 下载进度回调
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable

import jmcomic
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from jmcomic import Feature
from jmcomic.jm_exception import (
    MissingAlbumPhotoException,
    PartialDownloadFailedException,
    RequestRetryAllFailException,
)

# 存储目录：AstrBot 数据目录/JMDownload
JM_ROOT = Path(get_astrbot_data_path()) / "JMDownload"

# ID 匹配模式
ID_PATTERN = re.compile(r"\d{3,12}")

# 文件名非法字符
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 进度回调类型：(当前进度, 总数, 消息)
ProgressCallback = Callable[[int, int, str], None]


def _parse_cookies(raw: str) -> dict[str, str]:
    """解析 Cookie 字符串"""
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key:
            cookies[key.strip()] = value.strip()
    return cookies


def normalize_album_id(raw: str) -> str | None:
    """标准化专辑 ID

    Args:
        raw: 原始输入，支持 "123456" 或 "JM123456" 格式。

    Returns:
        标准化后的 ID，无效则返回 None。
    """
    candidate = str(raw or "").strip()
    if candidate[:2].lower() == "jm":
        candidate = candidate[2:].strip()
    return candidate if ID_PATTERN.fullmatch(candidate) else None


def _safe_pdf_name(album_id: str, title: str) -> str:
    """生成安全的 PDF 文件名"""
    cleaned = INVALID_FILENAME_CHARS.sub("_", str(title)).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)[:70]
    return f"JM{album_id}-{cleaned}.pdf" if cleaned else f"JM{album_id}.pdf"


def _build_option(config: dict, download_dir: Path) -> jmcomic.JmOption:
    """构建 jmcomic 下载选项"""
    timeout = int(config.get("jm_timeout", 20))
    retry_times = int(config.get("jm_retry_times", 3))
    image_threads = int(config.get("jm_image_threads", 16))
    photo_threads = int(config.get("jm_photo_threads", 4))

    metadata: dict = {"timeout": timeout}

    # Cookie
    cookie_values = _parse_cookies(str(config.get("jm_cookies", "") or ""))
    if cookie_values:
        metadata["cookies"] = cookie_values

    # 代理
    proxy = str(config.get("jm_proxy", "") or "").strip()
    if proxy:
        if "://" not in proxy:
            proxy = f"http://{proxy}"
        metadata["proxies"] = {"http": proxy, "https": proxy}

    option_dict: dict = {
        "client": {
            "impl": "api",
            "retry_times": retry_times,
            "postman": {"meta_data": metadata},
        },
        "dir_rule": {
            "rule": "Bd / JM{Aid}-{Atitle}",
            "base_dir": str(download_dir),
        },
        "download": {
            "cache": True,
            "image": {"decode": True, "suffix": ".jpg"},
            "threading": {"image": image_threads, "photo": photo_threads},
        },
    }
    return jmcomic.JmOption.construct(option_dict)


def _find_pdf(pdf_dir: Path, album_id: str) -> Path:
    """查找生成的 PDF 文件"""
    expected = pdf_dir / f"{album_id}.pdf"
    if expected.is_file():
        return expected

    candidates = [p for p in pdf_dir.glob("*.pdf") if p.is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    raise FileNotFoundError("PDF 转换没有产生输出文件")


def _validate_pdf(pdf_path: Path) -> None:
    """验证 PDF 文件有效性"""
    if not pdf_path.is_file() or pdf_path.stat().st_size < 1024:
        raise ValueError("生成的 PDF 文件为空或过小")
    with pdf_path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise ValueError("生成文件不是有效的 PDF")


class JMDownloader:
    """JMComic 下载管理器"""

    def __init__(self, config: dict):
        self._config = config
        self._semaphore = asyncio.Semaphore(int(config.get("jm_max_concurrent", 1)))
        JM_ROOT.mkdir(parents=True, exist_ok=True)

    async def get_album_info(self, album_id: str) -> dict:
        """获取漫画信息

        Args:
            album_id: 漫画 ID。

        Returns:
            漫画信息字典。
        """
        try:
            option = _build_option(self._config, JM_ROOT / "temp")
            # 使用 jmcomic 的 client 获取专辑信息
            client = option.copy().new_jm_client()
            album = client.get_album_detail(album_id)
            return {
                "id": str(album.id),
                "name": str(album.name),
                "author": str(getattr(album, 'author', '未知')),
                "chapter_count": len(album),
                "image_count": sum(len(photo) for photo in album),
                "tags": list(getattr(album, 'tags', [])),
            }
        except Exception as e:
            # 如果获取失败，尝试使用另一种方式
            try:
                import jmcomic as jm
                client = jm.JmOption.default().new_jm_client()
                album = client.get_album_detail(album_id)
                return {
                    "id": str(album.id),
                    "name": str(album.name),
                    "author": str(getattr(album, 'author', '未知')),
                    "chapter_count": len(album),
                    "image_count": sum(len(photo) for photo in album),
                    "tags": list(getattr(album, 'tags', [])),
                }
            except Exception:
                return {
                    "id": album_id,
                    "name": "未知",
                    "chapter_count": 0,
                    "image_count": 0,
                    "tags": [],
                    "error": str(e),
                }

    async def download(
        self,
        album_id: str,
        send_file: bool = True,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        """下载漫画并生成 PDF

        Args:
            album_id: 漫画 ID。
            send_file: 是否返回文件路径用于发送。
            progress_callback: 进度回调函数 (current, total, message)。

        Returns:
            包含以下字段的字典：
            - success: 是否成功
            - message: 提示消息
            - pdf_path: PDF 文件路径（仅 send_file=True 且成功时）
            - pdf_name: PDF 文件名（仅 send_file=True 且成功时）
            - file_size_mb: 文件大小（MB）
            - album_info: 漫画信息字典
        """
        # 检查是否启用
        if not self._config.get("jm_enabled", True):
            return {"success": False, "message": "JM 下载功能当前已关闭"}

        def _report(current: int, total: int, msg: str):
            """报告进度"""
            if progress_callback:
                try:
                    progress_callback(current, total, msg)
                except Exception:
                    pass

        async with self._semaphore:
            # 创建下载目录（保留文件，不清理）
            job_dir = JM_ROOT / f"jm{album_id}"
            job_dir.mkdir(parents=True, exist_ok=True)
            download_dir = job_dir / "images"
            pdf_dir = job_dir / "pdf"
            download_dir.mkdir(parents=True, exist_ok=True)
            pdf_dir.mkdir(parents=True, exist_ok=True)

            # 获取漫画信息
            album_info = await self.get_album_info(album_id)

            try:
                _report(0, 0, "准备下载...")
                option = _build_option(self._config, download_dir)

                # 在线程池中执行同步下载
                total_images = album_info.get("image_count", 0)
                _report(0, total_images, "开始下载图片...")

                def _download_with_progress():
                    """带进度监控的下载"""
                    import threading
                    import time as _time

                    # 启动进度监控线程
                    stop_event = threading.Event()

                    def _monitor_progress():
                        """监控下载目录中的文件数量"""
                        _time.sleep(2)  # 等待下载开始
                        while not stop_event.is_set():
                            try:
                                # 统计已下载的图片数量
                                image_count = 0
                                for dirpath, dirnames, filenames in os.walk(download_dir):
                                    for f in filenames:
                                        if f.endswith(('.jpg', '.webp', '.png')):
                                            image_count += 1
                                if image_count > 0:
                                    _report(image_count, total_images, f"已下载 {image_count}/{total_images} 张图片")
                            except Exception:
                                pass
                            _time.sleep(3)  # 每3秒检查一次

                    monitor_thread = threading.Thread(target=_monitor_progress, daemon=True)
                    monitor_thread.start()

                    try:
                        result = jmcomic.download_album(
                            album_id,
                            option,
                            extra=Feature.export_pdf(
                                pdf_dir=str(pdf_dir),
                                filename_rule="Aid",
                                delete_original_file=False,  # 不删除原图
                            ),
                        )
                        return result
                    finally:
                        stop_event.set()

                result = await asyncio.to_thread(_download_with_progress)

                _report(total_images, total_images, "下载完成，正在生成 PDF...")

                album = result.detail
                resolved_id = str(album.id)
                pdf_path = _find_pdf(pdf_dir, resolved_id)
                _validate_pdf(pdf_path)
                file_size_mb = pdf_path.stat().st_size / (1024 * 1024)
                pdf_name = _safe_pdf_name(resolved_id, str(album.name))

                # 更新漫画信息
                album_info.update({
                    "id": resolved_id,
                    "name": str(album.name),
                    "image_count": sum(len(photo) for photo in album),
                })

                response = {
                    "success": True,
                    "message": f"下载完成，共 {album_info['image_count']} 张图片，PDF {file_size_mb:.2f} MB",
                    "album_id": resolved_id,
                    "album_name": str(album.name),
                    "album_info": album_info,
                    "file_size_mb": file_size_mb,
                    "image_dir": str(download_dir),
                }

                if send_file:
                    response["pdf_path"] = str(pdf_path)
                    response["pdf_name"] = pdf_name

                _report(total_images, total_images, "全部完成")
                return response

            except MissingAlbumPhotoException:
                return {
                    "success": False,
                    "message": f"没有找到 JM{album_id}，请检查 ID 或配置 cookies",
                }
            except RequestRetryAllFailException:
                return {
                    "success": False,
                    "message": "JMComic 站点连接失败，请稍后重试或配置代理",
                }
            except PartialDownloadFailedException:
                return {
                    "success": False,
                    "message": "部分图片下载失败，请稍后重试",
                }
            except Exception as e:
                return {
                    "success": False,
                    "message": f"JM{album_id} 下载失败（{type(e).__name__}）",
                }
            # 不清理文件，保留下载内容

    def cleanup_files(self, album_id: str) -> None:
        """清理指定漫画的下载文件"""
        job_dir = JM_ROOT / f"jm{album_id}"
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
