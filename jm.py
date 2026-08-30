"""JMComic 下载模块

功能：
  - 下载 JMComic 漫画
  - 合成 PDF 文件
  - 通过私聊发送文件
  - 下载进度回调
  - 本地缓存支持
"""

from __future__ import annotations

import asyncio
import json
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


def _count_images(directory: Path) -> int:
    """统计目录中的图片数量"""
    count = 0
    if directory.exists():
        for f in directory.rglob("*"):
            if f.suffix.lower() in ('.jpg', '.webp', '.png', '.jpeg'):
                count += 1
    return count


class JMDownloader:
    """JMComic 下载管理器"""

    def __init__(self, config: dict):
        self._config = config
        self._semaphore = asyncio.Semaphore(int(config.get("jm_max_concurrent", 1)))
        JM_ROOT.mkdir(parents=True, exist_ok=True)

    def _get_album_dir(self, album_id: str) -> Path:
        """获取漫画存储目录"""
        return JM_ROOT / f"jm{album_id}"

    def _get_info_file(self, album_id: str) -> Path:
        """获取漫画信息文件路径"""
        return self._get_album_dir(album_id) / "info.json"

    def _save_info(self, album_id: str, info: dict) -> None:
        """保存漫画信息到本地"""
        info_file = self._get_info_file(album_id)
        info_file.parent.mkdir(parents=True, exist_ok=True)
        info_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_info(self, album_id: str) -> dict | None:
        """从本地加载漫画信息"""
        info_file = self._get_info_file(album_id)
        if info_file.exists():
            try:
                return json.loads(info_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def check_local(self, album_id: str) -> dict | None:
        """检查本地是否有已下载的内容

        Args:
            album_id: 漫画 ID。

        Returns:
            如果本地有内容，返回包含以下字段的字典：
            - has_pdf: 是否有 PDF 文件
            - pdf_path: PDF 文件路径
            - pdf_name: PDF 文件名
            - image_count: 图片数量
            - album_info: 漫画信息
            否则返回 None。
        """
        album_dir = self._get_album_dir(album_id)
        if not album_dir.exists():
            return None

        pdf_dir = album_dir / "pdf"
        image_dir = album_dir / "images"

        # 检查 PDF
        pdf_files = list(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []
        has_pdf = len(pdf_files) > 0
        pdf_path = pdf_files[0] if pdf_files else None

        # 统计图片
        image_count = _count_images(image_dir)

        # 加载信息
        album_info = self._load_info(album_id) or {}

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

    async def get_album_info(self, album_id: str) -> dict:
        """获取漫画信息（优先从本地缓存读取）

        Args:
            album_id: 漫画 ID。

        Returns:
            漫画信息字典。
        """
        # 先检查本地缓存
        local_info = self._load_info(album_id)
        if local_info and local_info.get("name") and local_info["name"] != "未知":
            return local_info

        # 本地没有，从网络获取
        try:
            option = _build_option(self._config, JM_ROOT / "temp")
            client = option.new_jm_client()
            album = client.get_album_detail(album_id)
            info = {
                "id": str(album.id),
                "name": str(album.name),
                "author": str(getattr(album, 'author', '未知')),
                "chapter_count": len(album),
                "image_count": sum(len(photo) for photo in album),
                "tags": list(getattr(album, 'tags', [])),
            }
            # 保存到本地
            self._save_info(album_id, info)
            return info
        except Exception:
            return {
                "id": album_id,
                "name": "未知",
                "chapter_count": 0,
                "image_count": 0,
                "tags": [],
            }

    async def download(
        self,
        album_id: str,
        send_file: bool = True,
        progress_callback: ProgressCallback | None = None,
        force_redownload: bool = False,
    ) -> dict:
        """下载漫画并生成 PDF

        Args:
            album_id: 漫画 ID。
            send_file: 是否返回文件路径用于发送。
            progress_callback: 进度回调函数 (current, total, message)。
            force_redownload: 是否强制重新下载。

        Returns:
            包含以下字段的字典：
            - success: 是否成功
            - message: 提示消息
            - pdf_path: PDF 文件路径
            - pdf_name: PDF 文件名
            - file_size_mb: 文件大小（MB）
            - album_info: 漫画信息字典
            - from_cache: 是否来自本地缓存
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

        # 检查本地缓存
        if not force_redownload:
            local = self.check_local(album_id)
            if local and local["has_pdf"]:
                album_info = local["album_info"]
                if not album_info.get("name") or album_info["name"] == "未知":
                    album_info = await self.get_album_info(album_id)
                    self._save_info(album_id, album_info)

                return {
                    "success": True,
                    "message": f"使用本地缓存，共 {local['image_count']} 张图片，PDF {local['pdf_size_mb']:.2f} MB",
                    "album_id": album_id,
                    "album_name": album_info.get("name", "未知"),
                    "album_info": album_info,
                    "file_size_mb": local["pdf_size_mb"],
                    "pdf_path": local["pdf_path"],
                    "pdf_name": local["pdf_name"],
                    "image_dir": local.get("album_dir"),
                    "from_cache": True,
                }

        async with self._semaphore:
            # 创建下载目录
            album_dir = self._get_album_dir(album_id)
            album_dir.mkdir(parents=True, exist_ok=True)
            download_dir = album_dir / "images"
            pdf_dir = album_dir / "pdf"
            download_dir.mkdir(parents=True, exist_ok=True)
            pdf_dir.mkdir(parents=True, exist_ok=True)

            # 获取漫画信息
            album_info = await self.get_album_info(album_id)
            total_images = album_info.get("image_count", 0)

            try:
                _report(0, total_images, "准备下载...")
                option = _build_option(self._config, download_dir)

                # 在线程池中执行同步下载
                _report(0, total_images, "开始下载图片...")

                def _download_with_progress():
                    """带进度监控的下载"""
                    import threading
                    import time as _time

                    stop_event = threading.Event()

                    def _monitor_progress():
                        """监控下载目录中的文件数量"""
                        _time.sleep(2)
                        while not stop_event.is_set():
                            try:
                                image_count = _count_images(download_dir)
                                if image_count > 0:
                                    _report(image_count, total_images, f"已下载 {image_count}/{total_images} 张图片")
                            except Exception:
                                pass
                            _time.sleep(3)

                    monitor_thread = threading.Thread(target=_monitor_progress, daemon=True)
                    monitor_thread.start()

                    try:
                        result = jmcomic.download_album(
                            album_id,
                            option,
                            extra=Feature.export_pdf(
                                pdf_dir=str(pdf_dir),
                                filename_rule="Aid",
                                delete_original_file=False,
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
                self._save_info(resolved_id, album_info)

                response = {
                    "success": True,
                    "message": f"下载完成，共 {album_info['image_count']} 张图片，PDF {file_size_mb:.2f} MB",
                    "album_id": resolved_id,
                    "album_name": str(album.name),
                    "album_info": album_info,
                    "file_size_mb": file_size_mb,
                    "pdf_path": str(pdf_path),
                    "pdf_name": pdf_name,
                    "image_dir": str(download_dir),
                    "from_cache": False,
                }

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

    def cleanup_files(self, album_id: str) -> None:
        """清理指定漫画的下载文件"""
        album_dir = self._get_album_dir(album_id)
        if album_dir.exists():
            shutil.rmtree(album_dir, ignore_errors=True)
