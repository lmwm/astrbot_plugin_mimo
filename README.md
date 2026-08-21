# 资源查询插件 (astrbot_plugin_resource_query)

多平台资源查询 AstrBot 插件，支持小米 MiMo 用量查询、华数广电流量/话费查询等。

## 支持平台

| 平台 | 功能 | 状态 |
|------|------|------|
| 小米 MiMo | 余额、Token 用量、费用、限额查询 | ✅ 已支持 |
| 华数广电 | 流量、通话、余额查询 | ✅ 已支持 |

## 功能特性

### MiMo 平台
- 查询余额、Token 用量、费用和限额
- 多账号支持，每个账号可独立配置
- 支持 serviceToken / passToken / 账号密码三级凭据自动降级
- 凭据自动持久化，重启后无需重新登录
- 交互式 OTP 验证码输入

### 华数广电
- 查询流量使用情况
- 查询通话分钟数
- 查询话费余额
- 支持多账号管理

## 指令

### 通用指令

| 指令 | 说明 |
|------|------|
| `/query` | 显示帮助信息 |
| `/query update` | 检查并更新插件 |

### MiMo 指令

| 指令 | 说明 |
|------|------|
| `/query mimo` | 查询所有 MiMo 账号 |
| `/query mimo <序号>` | 查询指定 MiMo 账号 |
| `/query mimo login` | 查看当前凭据状态 |
| `/query mimo login <账号> <密码>` | 登录（支持 OTP 交互） |
| `/query mimo login passtoken <账号> <userId> <token>` | 设置 passToken |
| `/query mimo list` | 列出所有账号 |
| `/query mimo del <序号>` | 删除指定账号 |

### 华数广电指令

| 指令 | 说明 |
|------|------|
| `/query wasu` | 查询所有华数账号 |
| `/query wasu <序号>` | 查询指定华数账号 |
| `/query wasu login` | 查看当前账号 |
| `/query wasu login <user_key> <token> <phone> [<sign>]` | 添加/更新账号 |
| `/query wasu list` | 列出所有账号 |
| `/query wasu del <序号>` | 删除指定账号 |

## 首次使用

### MiMo 配置

#### 方式 1：WebUI 配置（推荐）

在 AstrBot WebUI 插件配置中填写账号和密码，直接发送 `/query mimo` 即可自动登录查询。

#### 方式 2：交互式登录

```
你: /query mimo login 13800138000 你的密码
机器人: ✅ 登录成功!
        userId: 123456789
```

### 华数广电配置

1. 打开华数广电微信小程序
2. 获取 `user_key`、`token`、`phone`、`sign` 参数
3. 使用指令配置：

```
你: /query wasu login your_user_key your_token 13800138000 your_sign
机器人: ✅ 华数账号 13800138000 配置成功
```

或在 WebUI 插件配置中直接填写。

## 配置（WebUI）

### MiMo 全局配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| device_id | 全局设备标识 | wb_MIQUERY000001 |
| ua | 全局 User-Agent | APP/com.xiaomi.mihome... |

### MiMo 账号配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| name | 账号显示名称 | 空（显示账号） |
| account | 小米账号 | 空 |
| password | 小米账号密码 | 空 |
| device_id | 设备标识（留空用全局） | 空 |
| ua | User-Agent（留空用全局） | 空 |
| userId | 用户 ID（自动填入） | 空 |
| passToken | 通行令牌（自动填入） | 空 |
| serviceToken | serviceToken（自动填入） | 空 |

### 华数广电账号配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| name | 账号显示名称 | 空（显示手机号） |
| user_key | User Key | 空 |
| token | Token | 空 |
| phone | 手机号 | 空 |
| sign | 签名 | 空 |

### 其他配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| proxy | GitHub 更新代理 | https://gh-proxy.cn/ |
| update_max_retries | 更新重试次数 | 3 |

## 部署

```bash
# 复制到 AstrBot 插件目录
cp -r astrbot_plugin_mimo /path/to/AstrBot/data/plugins/

# 重启 AstrBot 或在 WebUI 热重载插件
```

## 扩展开发

本插件采用模块化设计，添加新平台只需：

1. 继承 `BasePlatform` 基类
2. 实现 `query()` 方法
3. 在 `main.py` 中注册新平台

```python
from .base import BasePlatform, QueryResult

class NewPlatform(BasePlatform):
    @property
    def platform_name(self) -> str:
        return "新平台"

    @property
    def platform_icon(self) -> str:
        return "🆕"

    async def query(self, account: dict) -> QueryResult:
        # 实现查询逻辑
        pass
```
