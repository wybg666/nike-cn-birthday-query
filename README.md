# Nike 中国区生日月份查询 GUI（非官方整理版）

这是从已有反编译材料整理的 Python 桌面查询工具，同时保留命令行入口。对**本人或已获明确授权的账号**完成登录，读取生日月份及注册时间；界面可查看结果，并由用户明确选择导出 CSV。不是 Nike 官方软件，也不是原作者提供的原始工程。

> **接口未在线实测。** 仅完成离线模拟验证，不保证旧登录接口、既有 cookie 计算及缩减后的资料字段今天仍可使用。遇到额外身份验证、限流或接口变化，请停止；本项目不提供绕过新增验证的方法。

## 功能与边界

- 中国大陆手机号账号输入，单次最多 100 个；默认 1 个工作线程，最多 2 个。
- 登录成功后以 **POST** 调用 `/identity/user/v1/{id}/read`，请求 `dob.month`、`registration.timestamp`。
- CSV 只包含账号标识、生日月份、注册时间、状态；不输出密码、会话令牌、完整接口响应或其它个人资料。
- **没有生日修改、改密码、下单或交易功能。** 登录仍可能影响服务端登录记录/会话状态，因此不能把整个流程称为“无副作用”。
- 已去除旧入口、第三方许可服务和硬件上报依赖；不启动额外本地服务。
- 原材料的本地 cookie 计算保留；不下载或执行服务器返回的 JavaScript。
- 双击 `start.bat` 打开本地桌面窗口，**不会启动自动查询**。GUI 需点击开始并确认账号使用授权后才会登录。
- CLI 无参数及双击 `start_cli.bat` 只显示帮助；CLI 只有明确添加 `--execute` 才会登录和查询。

## 环境

- Python **3.11+**，推荐 Python 3.11。依赖对其它新版本的兼容性未全部验证。
- `requests`、`Js2Py`；不需要 Windows 硬件查询依赖。
- 图形界面使用 Python 标准库 `tkinter`，不新增第三方 GUI 依赖。Windows 官方 Python 安装通常包含 Tcl/Tk；若提示缺少 `tkinter`，请在 Python 安装器中启用 Tcl/Tk 组件，而不是执行 `pip install tkinter`。

在项目目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装依赖会访问包索引。**本项目整理时没有安装新依赖、没有发出真实账号请求。** 两个启动脚本优先使用项目内 `.venv\Scripts\python.exe`，没有虚拟环境时依次尝试 `py -3`、`python`。脚本保留控制台错误输出；启动失败后会暂停，方便查看原因。

## 桌面界面使用（推荐）

1. 双击 `start.bat`，或运行 `.\.venv\Scripts\python.exe -B birthday_gui.py`。
2. 点击“导入账号文件”，或点击“粘贴账号文本”后“检查并导入”。每行格式见下文。导入后列表直接明文显示完整 `手机号----密码`，不做掩码；长密码可通过横向滚动查看，账号较多时可纵向滚动。程序不自动保存输入密码；选择文件不会替你复制到项目，也不能代替妥善保管原文件。粘贴输入框同样显示明文，请避免旁人看到，不要分享包含凭据的截图或录屏；程序不负责清理系统剪贴板及其历史。
3. 先进行**离线格式检查**：只检查输入格式和设置，不会登录，不会验证密码正确性。
4. 设置线程、超时、重试次数、每次请求前等待时间；需要代理时填写单一代理，留空直连。代理输入框也直接显示明文。参数范围见下表。
5. 点击“开始查询”，阅读并确认账号使用授权。窗口显示进度和结果；结果表及窗口运行记录直接显示完整账号，不再提供掩码开关。可按月份及状态筛选显示；运行记录仍不包含密码。
6. 停止按钮取消尚未开始的任务，并通知进行中的任务在当前网络调用结束或超时后停止。它不能立即强行终止正在等待的系统网络调用；已完成结果仍可查看。
7. 查询完成或停止后，点击“导出 CSV”并确认隐私提示。导出包含**全部结果，不受界面筛选影响**；输出限定在项目 `data/` 内，已有文件不覆盖。**CSV 含完整手机号**、生日月份和注册时间，属于私密资料，不要上传 GitHub。

GUI 不会在启动时登录，不会自动保存账号输入，也不会自动导出结果。开始新任务会清空旧结果，关闭窗口后未导出的结果也会丢失，请先导出需要的资料。GUI 的运行记录仅在窗口中显示；下方 `data/query.log` 是 CLI 执行时的日志。界面内参数用于当前任务；下文的 `data/config.json` 配置示例主要供 CLI 使用。

## 账号格式与 CLI 首次配置

```powershell
New-Item -ItemType Directory -Force data | Out-Null
Copy-Item accounts.example.txt data/accounts.txt
Copy-Item config.example.json data/config.json
```

编辑 `data/accounts.txt`，每行一个账号，格式如下：

```text
PHONE----PASSWORD
```

将 `PHONE` 替换为本人或授权账号的 **11 位中国大陆手机号，不带 +86**，将 `PASSWORD` 替换为相应密码。不要把占位文本当作真实账号。空行及 `#` 开头的注释会忽略；重复账号及格式错误会拒绝，不会开始查询。

密码来自 GUI 粘贴/导入或 CLI 的本地账号文件；GUI 会按原文显示导入的密码，联网时仅在登录请求中使用，不写入结果 CSV 或日志。**输入文件本身仍是明文私密资料**，请控制目录权限，用完妥善移除；本项目不提供凭据加密保险箱。

`data/config.json` 可不创建，缺省使用以下保守参数：

| 参数 | 默认 | 允许范围 / 说明 |
|---|---:|---|
| `workers` | 1 | 1–2 |
| `timeout_seconds` | 15 | 5–30；连接和读取超时分别使用此值，不是总任务时限 |
| `retry_count` | 0 | 0–1；仅网络异常额外重试，401/403/429 不自动重试 |
| `request_delay_seconds` | 1.0 | 1–30；每个工作线程每次请求前等待，不是全局精确限速 |
| `proxy` | 空字符串 | 留空直连；或单一 `http(s)://[用户:密码@]主机:端口`；没有代理轮换 |

不使用系统环境代理配置；需要代理时请显式填写。代理凭据属于隐私，不要提交真实配置。

## CLI：先离线检查

```powershell
.\.venv\Scripts\python.exe -B birthday_query.py --check
# 或：
.\.venv\Scripts\python.exe -B birthday_query.py --dry-run
```

上述两种模式只检查账号文件和配置格式，**不验证账号密码是否正确、不访问网络、不生成查询结果**。无需安装业务依赖也能运行输入检查。无参数、`--help` 同样不联网。

## CLI：显式查询

确认使用权限及数据处理范围后：

```powershell
.\.venv\Scripts\python.exe -B birthday_query.py --execute
```

可指定 `data/` 内不同文件：

```powershell
.\.venv\Scripts\python.exe -B birthday_query.py --execute --accounts data/accounts.txt --config data/config.json --output data/results.csv
```

输出文件存在时拒绝覆盖。默认使用带时间戳的新文件名。

- `data/results-*.csv`：`account,birthday_month,registration_time,status`。其中 `account` 是**完整手机号**，生日月份和注册时间也属于个人资料；整个 `data/` 都不能上传。
- `data/query.log`：账号掩码及固定状态码，无密码和令牌。
- 注册时间是 **UTC ISO 8601**；未知字段保持空白，不伪造生日。
- `partial_profile` 表示资料字段缺失，其它可用字段仍可能保存。
- `invalid_credentials`、`verification_required`、`rate_limited`、`access_denied` 等为有限状态码，不保留完整服务端错误。
- 输入错误返回码 2；查询有失败项返回码 1；全成功或只缺资料字段返回码 0。
- Ctrl+C 会取消尚未开始的任务，并让当前工作线程在网络读取结束或超时后停止，不再发出后续步骤请求；不能立即强制终止进行中的系统网络调用。CSV 可能已有部分结果。

## 安全说明

TLS 证书校验保持开启；固定 HTTPS 端点，不跟随重定向；响应体大小限制为 2 MiB。网络错误及失败分支关闭会话，线程池统一回收任务。没有后台自动启动或定时执行。

访问令牌仅用于本次会话；解析其中 `prn` 是为了读取服务端指定账号资料，**不把未验证的 JWT 解析视为签名验证或权限判断**。

`data/` 已在 `.gitignore` 中忽略，但 **GitHub 网页拖拽上传不会替你执行本地忽略规则**。请只上传下方源码/模板清单，不要上传 `data/`、账号、真实配置、日志、结果、虚拟环境、原始程序及分析材料。

## 发布文件

```text
birthday_gui.py
gui_runner.py
birthday_query.py
nike_birthday_client.py
safe_log.py
accounts.example.txt
config.example.json
requirements.txt
start.bat
start_cli.bat
.gitignore
README.md
```

离线检查脚本和审计材料不放在这个发布目录中。原材料目录没有修改。

## 许可与来源

尚未为本项目选择开源许可证，也未自动授予对外开源许可。公开仓库不等于任意使用授权。公开前应确认对重建及整理代码拥有再发布权，并保留依法或依约需要保留的来源、版权和第三方许可信息；不能用新许可证覆盖第三方条款。

第三方库各自适用其原许可证。本项目名称仅说明适配对象，不表示与 Nike 存在隶属或认可关系。
