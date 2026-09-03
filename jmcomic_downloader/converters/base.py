"""
转换器基类

定义转换器的抽象接口。
"""

from abc import ABC, abstractmethod
from pathlib import Path


class BaseConverter(ABC):
    """转换器基类"""
    
    @abstractmethod
    def convert(self, image_dir: Path, output_path: Path) -> Path:
        """将图片转换为 PDF
        
        Args:
            image_dir: 图片目录
            output_path: 输出 PDF 路径
            
        Returns:
            生成的 PDF 文件路径
        """
        pass