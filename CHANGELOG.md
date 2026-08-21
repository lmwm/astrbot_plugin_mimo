## v2.1.1 (2026-XX-XX)

### 🐛 修复
- 移除无效的 `disable_reset` 配置（AstrBot 不支持此属性）

---

## v2.1.0 (2026-XX-XX)

### 🔧 优化
- 消息模板从全局配置移至每个账号的 `template` 字段
- 每个账号现在可以有独立的消息输出格式
- 模板留空时自动使用默认模板

### 📝 配置变更
- 移除全局 `mimo_template` 和 `wasu_template` 配置项
- MiMo 账号新增 `template` 字段（高级设置中）
- 华数账号新增 `template` 字段（高级设置中）

---

## v2.0.0 (2026-XX-XX)

### 🆕 新增功能
- 插件更名为「资源查询」，支持多平台查询
- 新增华数广电平台支持（流量/通话/余额查询）
- 统一的查询框架，便于扩展新平台
- 新增 `base.py` 基础框架模块
- 新增 `wasu.py` 华数广电查询模块

### 🔧 重构
- 重构指令系统，使用 `/query` 统一入口
- 重构配置结构，MiMo 和华数账号分开管理
- 更新 `_conf_schema.json`，支持华数账号配置

### 📝 指令变更
- `/mimo` → `/query mimo`
- `/query wasu` — 华数广电查询
- `/query update` — 检查更新

### ⚠️ 注意事项
- 配置结构变更，需要重新配置账号
- 原 `/mimo` 指令已弃用，请使用 `/query mimo`

---

## v1.7.0 (2026-08-21)

- 余额消息支持模板，使用 template.txt 自定义格式
- 新增默认模板 template.txt
- 模板变量：{label} {balance} {gift_balance} {input_token} {output_token} {cache_token} {monthly_cost} {total_cost}
- 限额项（TPM/RPM/并发）有变化时自动追加

## v1.6.4 (2026-08-21)

- 移除 constants.py，常量直接硬编码在各模块中
- 环境变量配置移至 .env 文件（MIMO_GH_PROXY、MIMO_DEVICE_ID、MIMO_UA）
- 新增 .env.example 示例文件

## v1.6.3 (2026-08-21)

- 常量全部硬编码，仅 proxy/device_id/ua 走环境变量

## v1.6.2 (2026-08-21)

- 精简环境变量：仅保留 MIMO_GH_PROXY、MIMO_DEVICE_ID、MIMO_UA
- 移除 defaults.json 加载逻辑，统一使用环境变量

## v1.6.1 (2026-08-21)

- 常量改为环境变量优先，保留兜底默认值
- 支持的环境变量：MIMO_ACCOUNT_BASE、MIMO_BALANCE_URL、MIMO_USAGE_URL、MIMO_PLUGIN_VERSION、MIMO_PLUGIN_NAME、MIMO_REPO_OWNER、MIMO_REPO_NAME、MIMO_GITHUB_API、MIMO_GH_PROXY、MIMO_DEVICE_ID、MIMO_UA

## v1.6.0 (2026-08-16)

- 代码重构：拆分为独立模块
  - constants.py：常量、默认值
  - http_utils.py：HTTP 工具（cookie、opener、代理、重试）
  - mi_account.py：小米登录（MiAccount、异常类）
  - query.py：API 查询、报告格式化、限额记录
  - updater.py：自动更新（检查、下载、重载）
  - main.py：插件入口（指令处理）
- 新增 __init__.py 作为包标识

## v1.5.2 (2026-08-16)

- 分隔线长度从 22 缩短为 16

## v1.5.1 (2026-08-15)

- 修复 passToken 被误清的问题：仅在 code=70016 时确认过期并清空
- 其他非 0 code（网络/临时错误）保留 passToken 不清空

## v1.5.0 (2026-08-15)

- 账号密码不再是必须项，仅在 passToken 失效时作为可选后备
- 优先级：serviceToken → passToken → account+password
- 全部失败时提示“令牌过期，请使用 /mimo login 重新登录”
- _ensure_account 和 _re_login_account 逻辑统一

## v1.4.6 (2026-08-15)

- 查询失败时（含 serviceToken 过期、返回无效数据）自动通过 passToken 重新获取 serviceToken 并重试
- 重试条件扩大：不再仅限于 401 错误，任何无效响应均触发重登录
- serviceToken 过期问题的彻底修复

## v1.4.4 (2026-08-13)

- TPM/RPM/并发改为与上次查询结果对比，有变化时才显示
- 限额数据持久化到 last_limits.json

## v1.4.3 (2026-08-13)

- 余额单位统一移到末尾（206.98元）
- TPM/RPM/并发无值时自动隐藏

## v1.4.2 (2026-08-13)

- 移除子项图标，统一简洁风格
- 标签列固定宽度，数值列自动对齐
- 使用半角空格 rpad 替代全角空格，兼容所有平台
- 分隔线改为半角 ─

## v1.4.1 (2026-08-13)

- 默认代理改为 https://gh-proxy.cn/（GitHub 代理加速）
- 代理实现改为 URL 前缀拼接方式（适配 gh-proxy 类服务）
- 查询结果格式优化：左对齐，子项缩进 2 个中文字符
- 字段标签与值之间使用全角空格对齐

## v1.4.0 (2026-08-13)

- 自动更新支持网络代理（WebUI 可配置 HTTP/HTTPS/SOCKS5 代理地址）
- 自动更新添加指数退避重试机制（默认 3 次，可配置）
- 所有指令响应前先发送「正在处理」提示，避免用户无意义等待
- 新增 proxy / update_max_retries 配置项
- _new_opener 支持代理参数
- 新增 _retry 通用重试工具函数

## v1.3.0 (2026-08-13)

- **修复 `_query_one` 中使用未定义变量 `ua` 的 BUG**（serviceToken 过期重新登录时会崩溃）
- 新增 `_re_login_account` 方法：serviceToken 过期时自动重新登录并重试查询
- 查询后自动保存账号配置，确保重新获取的凭据持久化
- 移除 `query_mimo` 中遗留的调试 print 输出
- 新增 `_is_valid_response` 工具函数
- 代码重组：按职责分区（凭据管理 / 查询 / 更新 / 指令）
- 版本升至 1.3.0

## v1.2.6 (2026-08-07)

- 移除不必要的 sdkVersion cookie
- 更新 README 文档

- 移除查询 URL 中多余的 ?userId= 参数（cookie 中的 userId 已足够）

- 全局 device_id / UA 为空时自动从 defaults.json 读取并填入配置

- 添加 defaults.json 兜底默认值文件，所有配置为空时自动使用
- 修复空字符串配置不触发 fallback 的问题

- 自动登录失败时显示具体错误原因（密码错误/网络错误），而非笼统的"无有效凭据"

- 账号未指定 device_id/ua 时自动补全全局配置值并持久化
- 配置说明中默认值换行显示

## v1.2.0 (2026-08-07)

- 每个账号支持单独设置设备标识（device_id）和 User-Agent，优先于全局配置
- 新增账号名称（name）字段，优先显示名称而非账号
- 移除脚本中的默认常量，默认值统一在配置 schema 中定义

## v1.1.0 (2026-08-07)

- 配置了账号密码时自动登录，无需手动执行 /mimo login
- 仅在未配置凭据时才提示登录
- OTP 验证码场景给出明确提示

## v1.0.9 (2026-08-06)

- 账号配置改用 template_list 类型（WebUI 有添加按钮和模板）
- 移除 update_source 配置（不需要）
- 更新功能固定使用默认仓库

## v1.0.8 (2026-08-06)

- 新增自定义更新源配置（WebUI 可视化配置）
- 支持配置 GitHub 仓库地址、分支
- 支持启动时自动检查更新
- 支持定时检查更新间隔
- /mimo update 从配置的仓库获取更新

## v1.0.7 (2026-08-06)

- 用量报告每行不超过 18 个中文字符（适配窄屏）
- 每个账号单独发送一条消息
- 大数字自动简化显示（万/亿）
- 移除 format_report_detail（统一为一种格式）

## v1.0.6 (2026-08-06)

- /mimo update 更新后自动重载插件（通过 Dashboard API）
- 无需手动在 WebUI 重载

## v1.0.5 (2026-08-06)

- 新增 /mimo update 指令：检查并更新插件
- 通过 GitHub API 获取最新版本，自动下载并替换插件文件
- 版本号统一为 PLUGIN_VERSION 常量

## v1.0.4 (2026-08-06)

- metadata.yaml 添加 display_name、support_platforms、astrbot_version 字段
- metadata.yaml 添加详细注释
- 添加 logo.png
- 代码通过 ruff 格式化检查
- 自定义异常类替代通用 Exception（LoginError、StsError）
- 修复类型注解（str = None → str | None = None）
- 移除未使用的导入和变量

## v1.0.3 (2026-08-05)

- 支持多账号管理
- 新增指令：/mimo list（列出账号）、/mimo del（删除账号）、/mimo <序号>（查询指定账号）
- /mimo 查询所有账号用量
- /mimo login 支持添加新账号（不覆盖已有账号）
- 配置改为 accounts 列表结构

## v1.0.2 (2026-08-05)

- 凭据存储改为配置文件（WebUI 可视化管理），移除 KV 存储
- 新增 userId/passToken/serviceToken 配置项（自动填入，无需手动填写）
- 配置中无凭据时自动触发账号密码登录
- 修复 `Context` 对象无 `update_config` 属性的错误

## v1.0.1 (2026-08-05)

- 初始版本
- 支持 /mimo 查询 MiMo 平台用量
- 支持 /mimo login 交互式登录（含 OTP 验证码）
- 三级凭据优先级：serviceToken > passToken > account+password
- urllib 实现，零外部依赖
