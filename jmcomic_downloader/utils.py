"""
工具函数

提供 JMComic 下载管理器使用的各种工具函数。
"""

from __future__ import annotations

import re
from pathlib import Path

# 常量定义
# 漫画 ID 匹配模式：3-12 位数字
ID_PATTERN = re.compile(r"\d{3,12}")

# 文件名非法字符模式
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def parse_cookies(raw: str) -> dict[str, str]:
    """解析 Cookie 字符串为字典格式
    
    Args:
        raw: Cookie 字符串，格式为 "key=value; key2=value2"
        
    Returns:
        解析后的 Cookie 字典
    """
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key:
            cookies[key.strip()] = value.strip()
    return cookies


def normalize_album_id(raw: str) -> str | None:
    """标准化漫画专辑 ID
    
    Args:
        raw: 原始输入的漫画 ID
        
    Returns:
        标准化后的纯数字 ID，无效则返回 None
    """
    candidate = str(raw or "").strip()
    if candidate[:2].lower() == "jm":
        candidate = candidate[2:].strip()
    return candidate if ID_PATTERN.fullmatch(candidate) else None


def safe_pdf_name(album_id: str, title: str) -> str:
    """生成安全的 PDF 文件名
    
    Args:
        album_id: 漫画 ID
        title: 漫画标题
        
    Returns:
        安全的 PDF 文件名
    """
    cleaned = INVALID_FILENAME_CHARS.sub("_", str(title)).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)[:70]
    return f"JM{album_id}-{cleaned}.pdf" if cleaned else f"JM{album_id}.pdf"


def count_images(directory: Path) -> int:
    """统计目录中的图片数量
    
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


def validate_pdf(pdf_path: Path) -> None:
    """验证 PDF 文件是否有效
    
    Args:
        pdf_path: PDF 文件路径
        
    Raises:
        ValueError: 文件无效或过小
    """
    if not pdf_path.is_file() or pdf_path.stat().st_size < 1024:
        raise ValueError("生成的 PDF 文件为空或过小")
    with pdf_path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise ValueError("生成文件不是有效的 PDF")


def find_pdf(pdf_dir: Path, album_id: str) -> Path:
    """在目录中查找生成的 PDF 文件
    
    Args:
        pdf_dir: PDF 目录路径
        album_id: 漫画 ID
        
    Returns:
        找到的 PDF 文件路径
        
    Raises:
        FileNotFoundError: 目录中没有 PDF 文件
    """
    expected = pdf_dir / f"{album_id}.pdf"
    if expected.is_file():
        return expected
    
    candidates = [p for p in pdf_dir.glob("*.pdf") if p.is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    raise FileNotFoundError("PDF 转换没有产生输出文件")