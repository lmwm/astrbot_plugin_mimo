"""
下载器模块

提供漫画图片下载功能。
"""

from .base import BaseDownloader
from .jmcomic import JMComicDownloader

__all__ = ["BaseDownloader", "JMComicDownloader"]