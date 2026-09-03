"""
PDF 转换器实现

使用 img2pdf 库将图片转换为 PDF。
"""

from pathlib import Path
from typing import Optional

import img2pdf

from ..config import ConverterConfig
from ..utils import validate_pdf
from .base import BaseConverter


class PDFConverter(BaseConverter):
    """PDF 转换器
    
    使用 img2pdf 库将图片转换为 PDF 文件。
    """
    
    def __init__(self, config: Optional[ConverterConfig] = None):
        """初始化转换器
        
        Args:
            config: 转换器配置
        """
        self._config = config or ConverterConfig()
    
    def convert(self, image_dir: Path, output_path: Path) -> Path:
        """将图片转换为 PDF
        
        Args:
            image_dir: 图片目录
            output_path: 输出 PDF 路径
            
        Returns:
            生成的 PDF 文件路径
            
        Raises:
            FileNotFoundError: 图片目录不存在或为空
            ValueError: 生成的 PDF 文件无效
        """
        if not image_dir.exists():
            raise FileNotFoundError(f"图片目录不存在: {image_dir}")
        
        # 收集所有图片文件（递归查找子目录）
        image_files = []
        for ext in ['*.jpg', '*.jpeg', '*.png', '*.webp']:
            image_files.extend(image_dir.rglob(ext))
        
        if not image_files:
            raise FileNotFoundError(f"图片目录为空: {image_dir}")
        
        # 按文件名排序
        image_files.sort(key=lambda p: p.name)
        
        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 转换为 PDF
        with open(output_path, "wb") as f:
            f.write(img2pdf.convert([str(p) for p in image_files]))
        
        # 验证生成的 PDF
        validate_pdf(output_path)
        
        return output_path