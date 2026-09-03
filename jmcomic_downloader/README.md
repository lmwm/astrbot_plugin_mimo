# JMComic 下载器

独立的 JMComic 漫画下载和 PDF 生成模块，可以脱离 AstrBot 框架单独使用。

## 功能特性

- 从 JMComic 网站下载漫画图片
- 将下载的图片合并为 PDF 文件
- 支持本地缓存，避免重复下载
- 支持下载进度回调
- 支持代理和 Cookie 配置
- 支持并发下载控制

## 安装

### 依赖项

```bash
pip install jmcomic>=2.7.0,<3 img2pdf>=0.5.1
```

### 使用方式

#### 基本使用

```python
import asyncio
from jmcomic_downloader import JMComicDownloader

async def main():
    # 初始化下载器
    downloader = JMComicDownloader(config={
        "jm_proxy": "http://127.0.0.1:7890",  # 可选：代理配置
        "jm_cookies": "csrf=abc123",           # 可选：Cookie 配置
    })
    
    # 下载漫画
    result = await downloader.download("123456")
    
    if result.success:
        print(f"下载成功！")
        print(f"PDF 路径: {result.pdf_path}")
        print(f"文件大小: {result.file_size_mb:.2f} MB")
        print(f"图片数量: {result.image_count}")
    else:
        print(f"下载失败: {result.message}")

# 运行
asyncio.run(main())
```

#### 带进度回调的使用

```python
import asyncio
from jmcomic_downloader import JMComicDownloader

def progress_callback(current: int, total: int, message: str):
    """进度回调函数"""
    if total > 0:
        percent = int(current / total * 100)
        print(f"进度: {percent}% - {message}")

async def main():
    downloader = JMComicDownloader()
    
    result = await downloader.download(
        "123456",
        progress_callback=progress_callback,
    )
    
    if result.success:
        print(f"PDF 路径: {result.pdf_path}")

asyncio.run(main())
```

#### 强制重新下载

```python
async def main():
    downloader = JMComicDownloader()
    
    # 强制重新下载（忽略本地缓存）
    result = await downloader.download(
        "123456",
        force_redownload=True,
    )
    
    if result.success:
        print(f"PDF 路径: {result.pdf_path}")

asyncio.run(main())
```

#### 检查本地缓存

```python
async def main():
    downloader = JMComicDownloader()
    
    # 检查本地是否有缓存
    local = downloader.check_local("123456")
    if local:
        print(f"本地已有缓存")
        print(f"PDF 路径: {local['pdf_path']}")
        print(f"图片数量: {local['image_count']}")
    else:
        print("本地没有缓存")

asyncio.run(main())
```

#### 获取漫画信息

```python
async def main():
    downloader = JMComicDownloader()
    
    # 获取漫画信息（优先从缓存读取）
    info = await downloader.get_album_info("123456")
    
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

## 目录结构

```
JMDownload/
├── jm123456/                    ← 漫画目录
│   ├── JM123456-漫画名/         ← 图片目录
│   │   ├── 0001.jpg
│   │   ├── 0002.jpg
│   │   └── ...
│   ├── JM123456-漫画名.pdf      ← PDF 文件
│   └── info.json                ← 漫画信息缓存
└── jm789012/
    └── ...
```

## 返回结果

`download()` 方法返回 `DownloadResult` 对象，包含以下属性：

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

## 漫画信息

`AlbumInfo` 对象包含以下属性：

| 属性 | 类型 | 说明 |
|------|------|------|
| `id` | str | 漫画 ID |
| `name` | str | 漫画名称 |
| `author` | str | 作者 |
| `chapter_count` | int | 章节数量 |
| `image_count` | int | 图片数量 |
| `tags` | list[str] | 标签列表 |

## 错误处理

下载失败时，`DownloadResult.message` 会包含具体的错误信息：

- 漫画不存在：`没有找到 JM123456，请检查 ID 或配置 cookies`
- 网络连接失败：`JMComic 站点连接失败，请稍后重试或配置代理`
- 部分下载失败：`部分图片下载失败，请稍后重试`
- 其他错误：`JM123456 下载失败（异常类型）`

## 注意事项

1. 首次使用需要配置代理或 Cookie 才能访问 JMComic 网站
2. 下载的漫画会缓存到本地，避免重复下载
3. 可以通过 `force_redownload=True` 强制重新下载
4. 下载目录默认为当前工作目录下的 `JMDownload` 文件夹