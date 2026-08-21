# astrbot_plugin_mimo

小米 MiMo 平台用量查询 AstrBot 插件

## 功能

- 查询 MiMo 平台余额、Token 用量、费用和限额
- 多账号支持，每个账号可独立配置设备标识和 User-Agent
- 支持 serviceToken / passToken / 账号密码三级凭据自动降级
- 凭据自动持久化，重启后无需重新登录
- 配置了账号密码时自动登录，无需手动操作
- **交互式 OTP 验证码输入**（在聊天中完成登录）
- 支持自定义账号名称显示

## 指令

| 指令 | 说明 |
|------|------|
| `/mimo` | 查询所有账号用量 |
| `/mimo <序号>` | 查询指定账号用量 |
| `/mimo login` | 查看当前凭据状态 |
| `/mimo login <账号> <密码>` | 登录（支持 OTP 交互） |
| `/mimo login passtoken <账号> <userId> <token>` | 设置 passToken |
| `/mimo list` | 列出所有账号 |
| `/mimo del <序号>` | 删除指定账号 |
| `/mimo update` | 检查并更新插件 |

## 首次使用

### 方式 1：WebUI 配置（推荐）

在 AstrBot WebUI 插件配置中填写账号和密码，直接发送 `/mimo` 即可自动登录查询。

### 方式 2：交互式登录

```
你: /mimo login 13800138000 你的密码
机器人: ✅ 登录成功!
        userId: 123456789
```

如果需要短信验证：

```
机器人: 📱 需要短信验证，验证码已发送
        请直接回复 6 位验证码：
你: 539705
机器人: ✅ 登录成功!
```

### 方式 3：设置 passToken

```
你: /mimo login passtoken 账号名 123456789 V1:xxxx...
机器人: ✅ 账号名 passToken 设置成功
        userId: 123456789
```

## 凭据优先级

```
serviceToken → passToken → account+password
```

- `serviceToken`：短期令牌（数小时），自动获取并缓存，过期自动刷新
- `passToken`：长期令牌（数月），serviceToken 过期时自动使用
- `account+password`：仅首次登录或 passToken 过期时使用

### 查询所需 Cookie

| Cookie | 来源 | 是否必须 |
|--------|------|:--------:|
| `serviceToken` | STS 换取 | ✅ |
| `userId` | 登录返回 | ✅ |

## 配置（WebUI）

### 全局配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| device_id | 全局设备标识 | wb_MIQUERY000001 |
| ua | 全局 User-Agent | APP/com.xiaomi.mihome... |

### 账号配置（每个账号独立）

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

## 部署

```bash
# 复制到 AstrBot 插件目录
cp -r astrbot_plugin_mimo /path/to/AstrBot/data/plugins/

# 重启 AstrBot 或在 WebUI 热重载插件
```
