"""
工具函数

提供 JMComic 下载器使用的各种工具函数。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

# 类型定义
ProgressCallback = Callable[[int, int, str], None]

# 常量定义
# 漫画 ID 匹配模式：3-12 位数字
ID_PATTERN = re.compile(r"\d{3,12}")

# 文件名非法字符模式
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def parse_cookies(raw: str) -> dict[str, str]:
    """解析 Cookie 字符串为字典格式
    
    将形如 "key1=value1; key2=value2" 的 Cookie 字符串解析为字典。
    
    Args:
        raw: Cookie 字符串，格式为 "key=value; key2=value2"
        
    Returns:
        解析后的 Cookie 字典，如 {"key1": "value1", "key2": "value2"}
        
    示例：
        >>> parse_cookies("csrf=abc123; remember_web=xyz")
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


def safe_pdf_name(album_id: str, title: str) -> str:
    """生成安全的 PDF 文件名
    
    清理文件名中的非法字符，确保文件名在各操作系统上都有效。
    
    Args:
        album_id: 漫画 ID
        title: 漫画标题
        
    Returns:
        安全的 PDF 文件名，格式为 "JM{ID}-{标题}.pdf"
        
    示例：
        >>> safe_pdf_name("123456", "我的漫画")
        'JM123456-我的漫画.pdf'
        >>> safe_pdf_name("123456", '漫画/特殊*字符')
        'JM123456-漫画_特殊_字符.pdf'
    """
    # 替换非法字符为下划线
    cleaned = INVALID_FILENAME_CHARS.sub("_", str(title)).strip(" ._")
    # 合并多个空格为一个，并限制长度为 70 字符
    cleaned = re.sub(r"\s+", " ", cleaned)[:70]
    return f"JM{album_id}-{cleaned}.pdf" if cleaned else f"JM{album_id}.pdf"


def count_images(directory: Path) -> int:
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


def validate_pdf(pdf_path: Path) -> None:
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


def find_pdf(pdf_dir: Path, album_id: str) -> Path:
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