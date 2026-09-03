"""
JMComic 漫画下载模块

本模块提供 JMComic 漫画的下载、PDF 生成和本地缓存管理功能。

主要功能：
  - 从 JMComic 网站下载漫画图片
  - 将下载的图片合并为 PDF 文件
  - 支持本地缓存，避免重复下载
  - 支持下载进度回调
  - 支持代理和 Cookie 配置

目录结构：
  AstrBot数据目录/JMDownload/
  ├── jm123456/                    ← 漫画目录
  │   ├── JM123456-漫画名/         ← 图片目录
  │   │   ├── 0001.jpg
  │   │   ├── 0002.jpg
  │   │   └── ...
  │   ├── JM123456-漫画名.pdf      ← PDF 文件
  │   └── info.json                ← 漫画信息缓存
  └── jm789012/
      └── ...

使用示例：
  downloader = JMDownloader(config_path)
  result = await downloader.download("123456")
  if result["success"]:
      print(f"PDF 路径: {result['pdf_path']}")
"""

# ============================================================================
# 导入模块
# ============================================================================

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Callable

# jmcomic 库：用于访问 JMComic 网站和下载漫画
import jmcomic
# AstrBot 数据路径工具
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
# jmcomic 的 PDF 导出功能
from jmcomic import Feature
# jmcomic 的异常类
from jmcomic.jm_exception import (
    MissingAlbumPhotoException,      # 漫画不存在
    PartialDownloadFailedException,  # 部分图片下载失败
    RequestRetryAllFailException,    # 网络请求重试全部失败
)

# ============================================================================
# 常量定义
# ============================================================================

# 漫画存储根目录：AstrBot 数据目录/JMDownload
# 所有下载的漫画都存储在这个目录下
JM_ROOT = Path(get_astrbot_data_path()) / "JMDownload"

# 漫画 ID 匹配模式：3-12 位数字
# 用于验证用户输入的漫画 ID 是否有效
ID_PATTERN = re.compile(r"\d{3,12}")

# 文件名非法字符模式
# 用于清理文件名中的非法字符，确保文件名在各操作系统上都有效
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 进度回调函数类型定义
# 参数：(当前进度, 总数, 消息)
ProgressCallback = Callable[[int, int, str], None]


# ============================================================================
# 工具函数
# ============================================================================


def _parse_cookies(raw: str) -> dict[str, str]:
    """解析 Cookie 字符串为字典格式

    将形如 "key1=value1; key2=value2" 的 Cookie 字符串解析为字典。

    Args:
        raw: Cookie 字符串，格式为 "key=value; key2=value2"

    Returns:
        解析后的 Cookie 字典，如 {"key1": "value1", "key2": "value2"}

    示例：
        >>> _parse_cookies("csrf=abc123; remember_web=xyz")
        {'csrf': 'abc123', 'remember_web': 'xyz'}
    """
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key:
            cookies[key.strip()] = value.strip()
    return cookies


def normalize_album_id(raw: str) -> str | None:
    """标准化漫画专辑 ID

    支持多种输入格式：
      - 纯数字：123456
      - 带 JM 前缀：JM123456 或 jm123456

    Args:
        raw: 原始输入的漫画 ID

    Returns:
        标准化后的纯数字 ID，无效则返回 None

    示例：
        >>> normalize_album_id("JM123456")
        '123456'
        >>> normalize_album_id("123456")
        '123456'
        >>> normalize_album_id("abc")
        None
    """
    candidate = str(raw or "").strip()
    # 移除 JM 前缀（不区分大小写）
    if candidate[:2].lower() == "jm":
        candidate = candidate[2:].strip()
    # 验证是否为有效的数字 ID
    return candidate if ID_PATTERN.fullmatch(candidate) else None


def _safe_pdf_name(album_id: str, title: str) -> str:
    """生成安全的 PDF 文件名

    清理文件名中的非法字符，确保文件名在各操作系统上都有效。

    Args:
        album_id: 漫画 ID
        title: 漫画标题

    Returns:
        安全的 PDF 文件名，格式为 "JM{ID}-{标题}.pdf"

    示例：
        >>> _safe_pdf_name("123456", "我的漫画")
        'JM123456-我的漫画.pdf'
        >>> _safe_pdf_name("123456", '漫画/特殊*字符')
        'JM123456-漫画_特殊_字符.pdf'
    """
    # 替换非法字符为下划线
    cleaned = INVALID_FILENAME_CHARS.sub("_", str(title)).strip(" ._")
    # 合并多个空格为一个，并限制长度为 70 字符
    cleaned = re.sub(r"\s+", " ", cleaned)[:70]
    return f"JM{album_id}-{cleaned}.pdf" if cleaned else f"JM{album_id}.pdf"


def _build_option(config: dict, download_dir: Path) -> jmcomic.JmOption:
    """构建 jmcomic 下载选项

    根据用户配置创建 jmcomic 库的下载选项对象。

    Args:
        config: 配置字典，包含以下字段：
            - jm_timeout: 请求超时时间（秒）
            - jm_retry_times: 重试次数
            - jm_image_threads: 图片下载并发数
            - jm_photo_threads: 章节下载并发数
            - jm_cookies: Cookie 字符串
            - jm_proxy: 代理地址
        download_dir: 下载目录路径

    Returns:
        jmcomic 选项对象
    """
    # 从配置中读取参数，使用默认值
    timeout = int(config.get("jm_timeout", 20))
    retry_times = int(config.get("jm_retry_times", 3))
    image_threads = int(config.get("jm_image_threads", 16))
    photo_threads = int(config.get("jm_photo_threads", 4))

    # 构建请求元数据
    metadata: dict = {"timeout": timeout}

    # 配置 Cookie（用于访问需要登录的内容）
    cookie_values = _parse_cookies(str(config.get("jm_cookies", "") or ""))
    if cookie_values:
        metadata["cookies"] = cookie_values

    # 配置代理（用于网络受限环境）
    proxy = str(config.get("jm_proxy", "") or "").strip()
    if proxy:
        # 确保代理地址包含协议前缀
        if "://" not in proxy:
            proxy = f"http://{proxy}"
        metadata["proxies"] = {"http": proxy, "https": proxy}

    # 构建 jmcomic 选项字典
    option_dict: dict = {
        # 客户端配置
        "client": {
            "impl": "api",           # 使用 API 方式访问
            "retry_times": retry_times,  # 重试次数
            "postman": {"meta_data": metadata},  # 请求元数据
        },
        # 目录规则：漫画存放在 download_dir/JM{ID}-{标题}/ 目录下
        "dir_rule": {
            "rule": "Bd / JM{Aid}-{Atitle}",
            "base_dir": str(download_dir),
        },
        # 下载配置
        "download": {
            "cache": True,  # 启用缓存，避免重复下载
            "image": {"decode": True, "suffix": ".jpg"},  # 图片解码为 JPG
            "threading": {"image": image_threads, "photo": photo_threads},  # 并发数
        },
    }
    return jmcomic.JmOption.construct(option_dict)


def _find_pdf(pdf_dir: Path, album_id: str) -> Path:
    """在目录中查找生成的 PDF 文件

    查找策略：
    1. 优先查找名为 {album_id}.pdf 的文件
    2. 如果有多个 PDF，返回最新的一个
    3. 如果没有找到 PDF，抛出异常

    Args:
        pdf_dir: PDF 目录路径
        album_id: 漫画 ID

    Returns:
        找到的 PDF 文件路径

    Raises:
        FileNotFoundError: 目录中没有 PDF 文件
    """
    # 优先查找预期的文件名
    expected = pdf_dir / f"{album_id}.pdf"
    if expected.is_file():
        return expected

    # 查找所有 PDF 文件
    candidates = [p for p in pdf_dir.glob("*.pdf") if p.is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        # 返回修改时间最新的 PDF
        return max(candidates, key=lambda p: p.stat().st_mtime)
    raise FileNotFoundError("PDF 转换没有产生输出文件")


def _validate_pdf(pdf_path: Path) -> None:
    """验证 PDF 文件是否有效

    验证条件：
    1. 文件存在且大小至少 1024 字节
    2. 文件以 PDF 魔数 (%PDF-) 开头

    Args:
        pdf_path: PDF 文件路径

    Raises:
        ValueError: 文件无效或过小
    """
    # 检查文件大小
    if not pdf_path.is_file() or pdf_path.stat().st_size < 1024:
        raise ValueError("生成的 PDF 文件为空或过小")
    # 检查 PDF 魔数
    with pdf_path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise ValueError("生成文件不是有效的 PDF")


def _count_images(directory: Path) -> int:
    """统计目录中的图片数量

    递归查找目录中所有常见图片格式的文件。

    Args:
        directory: 要统计的目录路径

    Returns:
        图片文件数量
    """
    count = 0
    if directory.exists():
        for f in directory.rglob("*"):
            if f.suffix.lower() in ('.jpg', '.webp', '.png', '.jpeg'):
                count += 1
    return count


# ============================================================================
# JMDownloader 主类
# ============================================================================


class JMDownloader:
    """JMComic 漫画下载管理器

    负责漫画的下载、PDF 生成、本地缓存管理等功能。

    使用示例：
        # 初始化
        downloader = JMDownloader(Path("config/jm"))

        # 检查本地缓存
        local = downloader.check_local("123456")
        if local:
            print("已有缓存")

        # 下载漫画
        result = await downloader.download("123456")
        if result["success"]:
            print(f"PDF: {result['pdf_path']}")
    """

    def __init__(self, config_path: Path):
        """初始化 JM 下载管理器

        Args:
            config_path: JM 配置目录路径（如 config/jm/）
        """
        # 配置目录
        self._config_path = config_path
        self._config_path.mkdir(parents=True, exist_ok=True)

        # 加载配置
        self._config = self._load_config()

        # 并发信号量：限制同时下载的漫画数量
        self._semaphore = asyncio.Semaphore(int(self._config.get("jm_max_concurrent", 1)))

        # 确保漫画存储根目录存在
        JM_ROOT.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> dict:
        """从配置文件加载 JM 下载配置

        配置文件位置：config_path/config.json

        Returns:
            配置字典，包含所有 JM 相关配置项
        """
        config_file = self._config_path / "config.json"

        # 默认配置
        default_config = {
            "jm_enabled": True,           # 是否启用 JM 下载功能
            "jm_send_file": True,         # 下载后是否发送 PDF 文件
            "jm_cookies": "",             # JMComic Cookie
            "jm_proxy": "",               # 代理地址
            "jm_timeout": 20,             # 请求超时（秒）
            "jm_retry_times": 3,          # 重试次数
            "jm_image_threads": 16,       # 图片下载并发数
            "jm_photo_threads": 4,        # 章节下载并发数
            "jm_max_concurrent": 1,       # 同时下载漫画数量
        }

        # 从文件加载配置并合并
        if config_file.exists():
            try:
                saved = json.loads(config_file.read_text(encoding="utf-8"))
                default_config.update(saved)
            except (json.JSONDecodeError, OSError):
                pass

        return default_config

    def reload_config(self):
        """重新加载配置文件

        当用户通过 Pages 修改配置后，调用此方法刷新配置。
        """
        self._config = self._load_config()
        # 更新并发信号量
        self._semaphore = asyncio.Semaphore(int(self._config.get("jm_max_concurrent", 1)))

    def _get_album_dir(self, album_id: str) -> Path:
        """获取漫画存储目录路径

        Args:
            album_id: 漫画 ID

        Returns:
            漫画目录路径，如 JMDownload/JM123456/
        """
        return JM_ROOT / f"JM{album_id}"

    def _get_pdf_path(self, album_id: str, album_name: str = "") -> Path:
        """获取 PDF 文件路径

        PDF 文件存放在漫画目录下，使用规范的文件名。

        Args:
            album_id: 漫画 ID
            album_name: 漫画名称（用于生成文件名）

        Returns:
            PDF 文件路径
        """
        album_dir = self._get_album_dir(album_id)
        pdf_name = _safe_pdf_name(album_id, album_name) if album_name else f"JM{album_id}.pdf"
        return album_dir / pdf_name

    def _get_info_file(self, album_id: str) -> Path:
        """获取漫画信息文件路径

        漫画信息以 JSON 格式存储，用于缓存漫画的元数据。

        Args:
            album_id: 漫画 ID

        Returns:
            信息文件路径，如 JMDownload/jm123456/info.json
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
        info_file.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_info(self, album_id: str) -> dict | None:
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

    def check_local(self, album_id: str) -> dict | None:
        """检查本地是否有已下载的内容

        检查漫画目录是否存在，以及是否有 PDF 文件或图片。

        Args:
            album_id: 漫画 ID

        Returns:
            如果本地有内容，返回包含以下字段的字典：
            - has_pdf: 是否有 PDF 文件
            - pdf_path: PDF 文件路径
            - pdf_name: PDF 文件名
            - pdf_size_mb: PDF 文件大小（MB）
            - image_count: 图片数量
            - album_info: 漫画信息
            - album_dir: 漫画目录路径
            否则返回 None。
        """
        album_dir = self._get_album_dir(album_id)
        if not album_dir.exists():
            return None

        # 检查漫画目录下的 PDF 文件
        pdf_files = list(album_dir.glob(f"JM{album_id}*.pdf"))

        # ── 兼容旧目录结构 ──
        # 旧版本将 PDF 存放在 pdf 子目录中，需要迁移到漫画目录
        if not pdf_files:
            old_pdf_dir = album_dir / "pdf"
            if old_pdf_dir.exists():
                old_pdf_files = list(old_pdf_dir.glob("*.pdf"))
                if old_pdf_files:
                    # 迁移旧文件到漫画目录
                    for old_file in old_pdf_files:
                        # 生成规范的文件名
                        album_info = self._load_info(album_id) or {}
                        album_name = album_info.get("name", "")
                        if album_name and album_name != "未知":
                            new_name = _safe_pdf_name(album_id, album_name)
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

        # 旧版本将图片存放在 images 子目录中，需要迁移到漫画目录
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

        # 统计图片数量（直接在漫画目录下）
        image_count = _count_images(album_dir)

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

    async def get_album_info(self, album_id: str) -> dict:
        """获取漫画信息

        优先从本地缓存读取，如果没有则从网络获取。

        Args:
            album_id: 漫画 ID

        Returns:
            漫画信息字典，包含以下字段：
            - id: 漫画 ID
            - name: 漫画名称
            - author: 作者
            - chapter_count: 章节数量
            - image_count: 图片数量
            - tags: 标签列表
        """
        # 先检查本地缓存
        local_info = self._load_info(album_id)
        if local_info and local_info.get("name") and local_info["name"] != "未知":
            return local_info

        # 本地没有，从网络获取
        try:
            # 构建临时目录用于获取信息
            option = _build_option(self._config, JM_ROOT / "temp")
            client = option.new_jm_client()
            # 获取漫画详情
            album = client.get_album_detail(album_id)
            info = {
                "id": str(album.id),
                "name": str(album.name),
                "author": str(getattr(album, 'author', '未知')),
                "chapter_count": len(album),
                "image_count": sum(len(photo) for photo in album),
                "tags": list(getattr(album, 'tags', [])),
            }
            # 保存到本地缓存
            self._save_info(album_id, info)
            return info
        except Exception:
            # 获取失败时返回默认信息
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

        这是主要的下载方法，处理完整的下载流程：
        1. 检查本地缓存
        2. 下载漫画图片
        3. 生成 PDF 文件
        4. 整理文件结构

        Args:
            album_id: 漫画 ID
            send_file: 是否返回文件路径用于发送
            progress_callback: 进度回调函数 (current, total, message)
            force_redownload: 是否强制重新下载（忽略本地缓存）

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
        # 检查功能是否启用
        if not self._config.get("jm_enabled", True):
            return {"success": False, "message": "JM 下载功能当前已关闭"}

        def _report(current: int, total: int, msg: str):
            """报告下载进度"""
            if progress_callback:
                try:
                    progress_callback(current, total, msg)
                except Exception:
                    pass

        # ── 第一步：检查本地缓存 ──
        if not force_redownload:
            local = self.check_local(album_id)
            if local and local["has_pdf"]:
                # 本地已有 PDF，直接返回
                album_info = local["album_info"]
                # 如果本地信息不完整，尝试获取
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

        # ── 第二步：下载漫画 ──
        # 使用信号量控制并发
        async with self._semaphore:
            # 创建下载目录
            album_dir = self._get_album_dir(album_id)
            album_dir.mkdir(parents=True, exist_ok=True)
            # 临时 PDF 目录（jmcomic 生成 PDF 的位置）
            temp_pdf_dir = album_dir / "pdf"
            temp_pdf_dir.mkdir(parents=True, exist_ok=True)

            # 获取漫画信息
            album_info = await self.get_album_info(album_id)
            total_images = album_info.get("image_count", 0)

            try:
                _report(0, total_images, "准备下载...")
                # 构建下载选项
                option = _build_option(self._config, album_dir)

                # ── 第三步：执行下载 ──
                # 使用线程池执行同步下载，避免阻塞事件循环
                _report(0, total_images, "开始下载图片...")

                def _download_with_progress():
                    """带进度监控的下载（在单独线程中运行）"""
                    import threading
                    import time as _time

                    # 停止事件，用于通知监控线程停止
                    stop_event = threading.Event()

                    def _monitor_progress():
                        """监控下载进度（在后台线程中运行）"""
                        _time.sleep(2)  # 等待下载开始
                        while not stop_event.is_set():
                            try:
                                # 统计已下载的图片数量
                                image_count = _count_images(album_dir)
                                if image_count > 0:
                                    _report(image_count, total_images, f"已下载 {image_count}/{total_images} 张图片")
                            except Exception:
                                pass
                            _time.sleep(3)  # 每 3 秒更新一次进度

                    # 启动进度监控线程
                    monitor_thread = threading.Thread(target=_monitor_progress, daemon=True)
                    monitor_thread.start()

                    try:
                        # 调用 jmcomic 下载漫画并导出 PDF
                        result = jmcomic.download_album(
                            album_id,
                            option,
                            extra=Feature.export_pdf(
                                pdf_dir=str(temp_pdf_dir),
                                filename_rule="Aid",  # 使用漫画 ID 作为文件名
                                delete_original_file=False,  # 保留原始图片
                            ),
                        )
                        return result
                    finally:
                        # 通知监控线程停止
                        stop_event.set()

                # 在线程池中执行下载
                result = await asyncio.to_thread(_download_with_progress)

                _report(total_images, total_images, "下载完成，正在生成 PDF...")

                # ── 第四步：整理文件 ──
                album = result.detail
                resolved_id = str(album.id)

                # 从临时目录找到生成的 PDF
                temp_pdf_path = _find_pdf(temp_pdf_dir, resolved_id)
                # 验证 PDF 有效性
                _validate_pdf(temp_pdf_path)

                # 生成规范的文件名并移动到漫画目录
                pdf_name = _safe_pdf_name(resolved_id, str(album.name))
                final_pdf_path = album_dir / pdf_name

                # 如果目标文件已存在，先删除
                if final_pdf_path.exists():
                    final_pdf_path.unlink()

                # 移动 PDF 到漫画目录
                shutil.move(str(temp_pdf_path), str(final_pdf_path))

                # 计算文件大小
                file_size_mb = final_pdf_path.stat().st_size / (1024 * 1024)

                # 清理临时 PDF 目录
                if temp_pdf_dir.exists():
                    shutil.rmtree(temp_pdf_dir, ignore_errors=True)

                # 更新漫画信息缓存
                album_info.update({
                    "id": resolved_id,
                    "name": str(album.name),
                    "image_count": sum(len(photo) for photo in album),
                })
                self._save_info(resolved_id, album_info)

                # 构建返回结果
                response = {
                    "success": True,
                    "message": f"下载完成，共 {album_info['image_count']} 张图片，PDF {file_size_mb:.2f} MB",
                    "album_id": resolved_id,
                    "album_name": str(album.name),
                    "album_info": album_info,
                    "file_size_mb": file_size_mb,
                    "pdf_path": str(final_pdf_path),
                    "pdf_name": pdf_name,
                    "image_dir": str(album_dir),
                    "from_cache": False,
                }

                _report(total_images, total_images, "全部完成")
                return response

            # ── 异常处理 ──
            except MissingAlbumPhotoException:
                # 漫画不存在或需要登录
                return {
                    "success": False,
                    "message": f"没有找到 JM{album_id}，请检查 ID 或配置 cookies",
                }
            except RequestRetryAllFailException:
                # 网络请求失败
                return {
                    "success": False,
                    "message": "JMComic 站点连接失败，请稍后重试或配置代理",
                }
            except PartialDownloadFailedException:
                # 部分图片下载失败
                return {
                    "success": False,
                    "message": "部分图片下载失败，请稍后重试",
                }
            except Exception as e:
                # 其他未知错误
                return {
                    "success": False,
                    "message": f"JM{album_id} 下载失败（{type(e).__name__}）",
                }

    def cleanup_files(self, album_id: str) -> None:
        """清理指定漫画的所有下载文件

        删除漫画目录及其所有内容。

        Args:
            album_id: 漫画 ID
        """
        album_dir = self._get_album_dir(album_id)
        if album_dir.exists():
            shutil.rmtree(album_dir, ignore_errors=True)
