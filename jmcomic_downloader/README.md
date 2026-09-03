# JMComic 下载管理器

一个模块化的 JMComic 漫画下载管理系统，采用管理器架构设计，各个功能模块独立且可插拔。

## 架构设计

```
JMManager（管理器）
├── Downloader（下载器）
│   └── JMComicDownloader - 从 JMComic 网站下载漫画图片
├── PDFConverter（PDF转换器）
│   └── PDFConverter - 将图片转换为 PDF 文件
└── CacheManager（缓存管理器）
    └── CacheManager - 管理本地缓存和信息
```

## 目录结构

```
jmcomic_downloader/
├── __init__.py          # 模块初始化，导出管理器
├── manager.py           # JM管理器，协调各个功能
├── config.py            # 配置管理
├── models.py            # 数据模型
├── utils.py             # 工具函数
├── README.md            # 使用文档
├── downloaders/         # 下载器模块
│   ├── __init__.py
│   ├── base.py          # 下载器基类
│   └── jmcomic.py       # JMComic下载器实现
├── converters/          # 转换器模块
│   ├── __init__.py
│   ├── base.py          # 转换器基类
│   └── pdf.py           # PDF转换器实现
└── cache/               # 缓存管理模块
    ├── __init__.py
    └── manager.py       # 缓存管理器
```

## 功能特性

### 下载器（Downloader）
- 从 JMComic 网站下载漫画图片
- 支持代理和 Cookie 配置
- 支持并发下载控制
- 支持进度回调

### PDF转换器（PDFConverter）
- 将下载的图片转换为 PDF 文件
- 支持多种图片格式（JPG、PNG、WebP）
- 自动排序图片
- 验证生成的 PDF 文件

### 缓存管理器（CacheManager）
- 管理漫画信息缓存
- 管理 PDF 文件缓存
- 自动迁移旧目录结构
- 支持缓存清理

## 使用示例

### 基本使用

```python
import asyncio
from jmcomic_downloader import JMManager

async def main():
    # 初始化管理器
    manager = JMManager(config={
        "jm_proxy": "http://127.0.0.1:7890",
        "jm_cookies": "csrf=abc123",
    })
    
    # 下载漫画
    result = await manager.download("123456")
    
    if result.success:
        print(f"下载成功！")
        print(f"PDF 路径: {result.pdf_path}")
        print(f"文件大小: {result.file_size_mb:.2f} MB")
        print(f"图片数量: {result.image_count}")
    else:
        print(f"下载失败: {result.message}")

asyncio.run(main())
```

### 带进度回调的使用

```python
import asyncio
from jmcomic_downloader import JMManager, ProgressInfo

def progress_callback(info: ProgressInfo):
    """进度回调函数"""
    if info.total > 0:
        print(f"进度: {info.percent}% - {info.message}")

async def main():
    manager = JMManager()
    
    result = await manager.download(
        "123456",
        progress_callback=progress_callback,
    )
    
    if result.success:
        print(f"PDF 路径: {result.pdf_path}")

asyncio.run(main())
```

### 检查本地缓存

```python
async def main():
    manager = JMManager()
    
    # 检查本地是否有缓存
    local = manager.check_local("123456")
    if local:
        print(f"本地已有缓存")
        print(f"PDF 路径: {local['pdf_path']}")
        print(f"图片数量: {local['image_count']}")
    else:
        print("本地没有缓存")

asyncio.run(main())
```

### 获取漫画信息

```python
async def main():
    manager = JMManager()
    
    # 获取漫画信息（优先从缓存读取）
    info = await manager.get_album_info("123456")
    
    print(f"漫画名称: {info.name}")
    print(f"作者: {info.author}")
    print(f"章节数: {info.chapter_count}")
    print(f"图片数: {info.image_count}")

asyncio.run(main())
```

## 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `jm_enabled` | bool | `True` | 是否启用下载功能 |
| `jm_send_file` | bool | `True` | 下载后是否发送文件 |
| `jm_cookies` | str | `""` | JMComic Cookie |
| `jm_proxy` | str | `""` | 代理地址 |
| `jm_timeout` | int | `20` | 请求超时（秒） |
| `jm_retry_times` | int | `3` | 重试次数 |
| `jm_image_threads` | int | `16` | 图片下载并发数 |
| `jm_photo_threads` | int | `4` | 章节下载并发数 |
| `jm_max_concurrent` | int | `1` | 同时下载漫画数量 |
| `download_dir` | str | `None` | 下载目录路径 |

## 数据模型

### AlbumInfo（漫画信息）
| 属性 | 类型 | 说明 |
|------|------|------|
| `id` | str | 漫画 ID |
| `name` | str | 漫画名称 |
| `author` | str | 作者 |
| `chapter_count` | int | 章节数量 |
| `image_count` | int | 图片数量 |
| `tags` | list[str] | 标签列表 |

### DownloadResult（下载结果）
| 属性 | 类型 | 说明 |
|------|------|------|
| `success` | bool | 是否成功 |
| `message` | str | 提示消息 |
| `album_id` | str | 漫画 ID |
| `album_name` | str | 漫画名称 |
| `album_info` | AlbumInfo | 漫画信息对象 |
| `pdf_path` | str | PDF 文件路径 |
| `pdf_name` | str | PDF 文件名 |
| `file_size_mb` | float | 文件大小（MB） |
| `image_count` | int | 图片数量 |
| `from_cache` | bool | 是否来自本地缓存 |
| `image_dir` | str | 图片目录路径 |

### ProgressInfo（进度信息）
| 属性 | 类型 | 说明 |
|------|------|------|
| `current` | int | 当前进度 |
| `total` | int | 总数 |
| `message` | str | 进度消息 |
| `percent` | int | 进度百分比（只读） |

## 扩展开发

### 添加新的下载器

1. 继承 `BaseDownloader` 基类
2. 实现 `download()` 和 `get_album_info()` 方法
3. 在管理器中注册新的下载器

```python
from jmcomic_downloader.downloaders.base import BaseDownloader

class NewDownloader(BaseDownloader):
    async def download(self, album_id, download_dir, progress_callback=None):
        # 实现下载逻辑
        pass
    
    async def get_album_info(self, album_id):
        # 实现获取信息逻辑
        pass
```

### 添加新的转换器

1. 继承 `BaseConverter` 基类
2. 实现 `convert()` 方法
3. 在管理器中注册新的转换器

```python
from jmcomic_downloader.converters.base import BaseConverter

class NewConverter(BaseConverter):
    def convert(self, image_dir, output_path):
        # 实现转换逻辑
        pass
```

## 注意事项

1. 首次使用需要配置代理或 Cookie 才能访问 JMComic 网站
2. 下载的漫画会缓存到本地，避免重复下载
3. 可以通过 `force_redownload=True` 强制重新下载
4. 下载目录默认为当前工作目录下的 `JMDownload` 文件夹
5. 各个功能模块独立，可以单独使用或替换