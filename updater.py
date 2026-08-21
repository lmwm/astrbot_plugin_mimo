"""自动更新：版本检查、下载、重载插件"""

import asyncio
import base64
import datetime
import io
import json
import os
import shutil
import zipfile
from pathlib import Path
from urllib.request import Request

import jwt

from .http_utils import new_opener, proxy_url, retry

_REPO_OWNER = "lmwm"
_REPO_NAME = "astrbot_plugin_resource_query"
_GITHUB_API = f"https://api.github.com/repos/{_REPO_OWNER}/{_REPO_NAME}"


def _get_plugin_version() -> str:
    """从 metadata.yaml 动态读取版本号"""
    try:
        metadata_path = Path(__file__).parent / "metadata.yaml"
        if metadata_path.exists():
            content = metadata_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "0.0.0"


async def check_update(config) -> dict:
    """检查 GitHub 是否有新版本（支持代理和重试）"""
    proxy = config.get("proxy") or os.getenv("MIMO_GH_PROXY", "https://gh-proxy.cn/")
    max_retries = int(config.get("update_max_retries") or 3)
    current_version = _get_plugin_version()

    def _fetch():
        def _do():
            opener, _ = new_opener()
            # 使用 raw.githubusercontent.com 直接获取文件，避免 API 速率限制
            raw_url = f"https://raw.githubusercontent.com/{_REPO_OWNER}/{_REPO_NAME}/main/metadata.yaml"
            url = proxy_url(raw_url, proxy)
            req = Request(
                url,
                headers={
                    "User-Agent": "astrbot-plugin-resource-query-updater",
                },
            )
            with opener.open(req, timeout=15) as r:
                content = r.read().decode("utf-8")

            for line in content.splitlines():
                line = line.strip()
                if line.startswith("version:"):
                    version = line.split(":", 1)[1].strip().strip('"').strip("'")
                    return {
                        "latest": version,
                        "current": current_version,
                        "error": "",
                    }
            return {
                "latest": "",
                "current": current_version,
                "error": "metadata.yaml 中未找到 version",
            }

        try:
            return retry(_do, max_retries)
        except (OSError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            return {"latest": "", "current": current_version, "error": str(e)}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _fetch)
    result["has_update"] = bool(
        result["latest"] and result["latest"] != current_version
    )
    return result


async def do_update(config) -> str:
    """从 GitHub 下载最新版本并替换当前插件文件（支持代理和重试）"""
    proxy = config.get("proxy") or os.getenv("MIMO_GH_PROXY", "https://gh-proxy.cn/")
    max_retries = int(config.get("update_max_retries") or 3)

    def _download_and_extract():
        try:

            def _get_sha():
                opener, _ = new_opener()
                commits_url = proxy_url(
                    f"{_GITHUB_API}/commits/main", proxy
                )
                req = Request(
                    commits_url,
                    headers={
                        "User-Agent": "astrbot-plugin-resource-query-updater",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                with opener.open(req, timeout=15) as r:
                    return json.loads(r.read())["sha"]

            sha = retry(_get_sha, max_retries)

            def _download_zip():
                opener, _ = new_opener()
                zip_url = proxy_url(
                    f"https://github.com/{_REPO_OWNER}/{_REPO_NAME}/archive/{sha}.zip",
                    proxy,
                )
                req = Request(
                    zip_url,
                    headers={"User-Agent": "astrbot-plugin-resource-query-updater"},
                )
                with opener.open(req, timeout=30) as r:
                    return r.read()

            zip_data = retry(_download_zip, max_retries)

            plugin_dir = Path(__file__).parent
            tmp_dir = plugin_dir.parent / f"{_REPO_NAME}_tmp_update"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)

            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                zf.extractall(tmp_dir)

            extracted_dirs = [d for d in tmp_dir.iterdir() if d.is_dir()]
            if not extracted_dirs:
                shutil.rmtree(tmp_dir)
                return "❌ 解压失败：未找到插件目录"
            source_dir = extracted_dirs[0]

            exclude = {".git", ".github", "__pycache__", ".gitignore", "tests"}
            for item in plugin_dir.iterdir():
                if item.name in exclude:
                    continue
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

            for item in source_dir.iterdir():
                if item.name in exclude:
                    continue
                dest = plugin_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            shutil.rmtree(tmp_dir)
            return f"✅ 更新完成！新版本已下载（commit: {sha[:7]}）"

        except (OSError, TimeoutError, zipfile.BadZipFile, shutil.Error) as e:
            return f"❌ 更新失败: {e}"

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _download_and_extract)


async def reload_plugin(context) -> str:
    """调用 AstrBot Dashboard API 重载插件"""

    def _do_reload():
        try:
            dbc = context.get_config().get("dashboard", {})
            host = dbc.get("host", "127.0.0.1")
            if host == "0.0.0.0":
                host = "127.0.0.1"
            port = int(os.environ.get("DASHBOARD_PORT") or dbc.get("port", 6185))
            username = dbc.get("username")
            jwt_secret = dbc.get("jwt_secret")

            if not username or not jwt_secret:
                return "⚠️ Dashboard 未配置，无法自动重载。请手动在 WebUI 重载插件。"

            payload = {
                "username": username,
                "exp": datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(minutes=5),
            }
            token = jwt.encode(payload, jwt_secret, algorithm="HS256")
            if isinstance(token, bytes):
                token = token.decode("utf-8")

            opener, _ = new_opener()
            reload_url = f"http://{host}:{port}/api/plugin/reload"
            body = json.dumps({"name": _REPO_NAME}).encode()
            req = Request(
                reload_url,
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with opener.open(req, timeout=15) as r:
                resp = json.loads(r.read())
                if resp.get("status") == "ok":
                    return "✅ 插件已自动重载，新版本已生效！"
                return f"⚠️ 重载返回: {resp.get('message', resp)}"

        except (OSError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            return f"⚠️ 自动重载失败: {e}。请手动在 WebUI 重载插件。"

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_reload)
