"""华数广电 - 流量/通话/余额查询模块"""

import asyncio
import json
from urllib.request import Request

from .base import BasePlatform, QueryResult
from .http_utils import new_opener

_BASE_URL = "https://ups.wasu.cn"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 16; 23127PN0CC Build/BP2A.250605.031.A3; wv) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.181 "
                  "Mobile Safari/537.36 XWEB/1500047 MMWEBSDK/20260502 MMWEBID/9289 "
                  "MicroMessenger/8.0.76.3141(0x28004C3C) WeChat/arm64 Weixin "
                  "NetType/5G Language/zh_CN ABI/arm64 MiniProgramEnv/android",
    "Referer": "https://servicewechat.com/wxdd03803da05c5f59/192/page-frame.html",
    "emp-id": "0000",
    "siteId": "30003",
    "charset": "utf-8",
    "content-type": "application/json;charset=utf-8",
}


def _fmt_gb(val) -> str:
    """格式化流量为 GB"""
    return f"{int(val) / 1024 / 1024:.2f} GB"


def _fmt_yuan(val) -> str:
    """格式化金额为元"""
    return f"¥{int(val) / 100:.2f}"


class WasuResult(QueryResult):
    """华数广电查询结果"""

    def _format_data(self) -> str:
        """格式化华数广电查询结果"""
        data = self.data
        lines = [
            f"📺 {self.platform} - {self.account_name}",
            "────────────────",
        ]

        # 余额信息
        if "balance" in data:
            bal = data["balance"]
            lines.append(f"💰 账户余额: {bal.get('balance', '?')}")
            lines.append(f"   当月话费: {bal.get('month_fee', '?')}")
            lines.append(f"   欠费: {bal.get('arrears', '?')}")

        # 流量信息
        if "traffic" in data:
            traffic = data["traffic"]
            lines.append(f"\n📶 本月累计使用: {traffic.get('total_used', '?')}")
            lines.append(f"   总流量: {traffic.get('total', '?')} | 已用: {traffic.get('used', '?')} | 剩余: {traffic.get('remain', '?')}")

            # 详细流量
            for item in traffic.get("items", []):
                tag = "结转" if item.get("is_carry") else ""
                lines.append(f"     · {item['name']} {tag}: {item['total']} (已用 {item['used']} / 剩 {item['remain']})")

        # 语音信息
        if "voice" in data:
            for item in data["voice"]:
                lines.append(f"\n📞 语音: {item['name']}: {item['total']}分钟 | 剩余 {item['remain']}分钟")

        # 查询时间
        if "query_time" in data:
            lines.append(f"\n🕐 查询时间: {data['query_time']}")

        return "\n".join(lines)


class WasuPlatform(BasePlatform):
    """华数广电平台"""

    @property
    def platform_name(self) -> str:
        return "华数广电"

    @property
    def platform_icon(self) -> str:
        return "📺"

    async def query(self, account: dict) -> QueryResult:
        """查询华数广电账号"""
        user_key = account.get("user_key", "")
        token = account.get("token", "")
        phone = account.get("phone", "")
        sign = account.get("sign", "")

        if not user_key or not token or not phone:
            return WasuResult(
                success=False,
                platform=self.platform_name,
                account_name=self.get_account_label(account),
                data={},
                error="缺少必要参数（user_key, token, phone）"
            )

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._do_query, user_key, token, phone, sign
            )
            return WasuResult(
                success=True,
                platform=self.platform_name,
                account_name=self.get_account_label(account),
                data=data
            )
        except Exception as e:
            return WasuResult(
                success=False,
                platform=self.platform_name,
                account_name=self.get_account_label(account),
                data={},
                error=str(e)
            )

    def _do_query(self, user_key: str, token: str, phone: str, sign: str) -> dict:
        """执行查询（同步）"""
        def _post(path: str, payload: dict) -> dict:
            body = json.dumps(payload, separators=(",", ":"))
            opener, _ = new_opener()
            req = Request(
                _BASE_URL + path,
                data=body.encode("utf-8"),
                headers={**_HEADERS, "x-sign": sign},
                method="POST"
            )
            with opener.open(req, timeout=10) as r:
                return json.loads(r.read())["data"]

        payload = {"userKey": user_key, "token": token, "phoneNo": phone}

        # 查询话费余额
        fee = _post("/msm-local-hub/api/v3/gd/query/fee", payload)

        # 查询流量/通话资源
        res = _post("/msm-local-hub/api/v3/gd/query/resource", payload)

        # 解析余额
        balance_data = {
            "balance": _fmt_yuan(fee.get("BALANCE", 0)),
            "month_fee": _fmt_yuan(fee.get("CURREAL_FEE", 0)),
            "arrears": _fmt_yuan(fee.get("SPAY_FEE", 0)),
        }

        # 解析流量
        ext = res.get("USER_EXT_RES_LIST", [{}])[0]
        data_items = [r for r in res.get("USER_RES_LIST", []) if r.get("ITEM_TYPE_CODE") == "3"]
        voice_items = [r for r in res.get("USER_RES_LIST", []) if r.get("ITEM_TYPE_CODE") == "2"]

        total_high = sum(int(r.get("HIGH_FEE", 0)) for r in data_items)
        total_bal = sum(int(r.get("BALANCE", 0)) for r in data_items)
        total_used = total_high - total_bal

        traffic_items = []
        for r in data_items:
            used = int(r.get("HIGH_FEE", 0)) - int(r.get("BALANCE", 0))
            traffic_items.append({
                "name": r.get("DISCNT_NAME", ""),
                "total": _fmt_gb(int(r.get("HIGH_FEE", 0))),
                "used": _fmt_gb(used),
                "remain": _fmt_gb(int(r.get("BALANCE", 0))),
                "is_carry": "结转" in r.get("DISCNT_NAME", ""),
            })

        traffic_data = {
            "total_used": _fmt_gb(ext.get("ADDUP_TOTAL_VALUE", 0)),
            "total": _fmt_gb(total_high),
            "used": _fmt_gb(total_used),
            "remain": _fmt_gb(total_bal),
            "items": traffic_items,
        }

        # 解析语音
        voice_data = []
        for r in voice_items:
            voice_data.append({
                "name": r.get("DISCNT_NAME", ""),
                "total": r.get("HIGH_FEE", "0"),
                "remain": r.get("BALANCE", "0"),
            })

        return {
            "balance": balance_data,
            "traffic": traffic_data,
            "voice": voice_data,
            "query_time": fee.get("X_SYSDATE", ""),
        }
