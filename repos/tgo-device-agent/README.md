# tgo-device-agent

被控端 Agent — 运行在受管设备上，通过 TCP 长连接接入 `tgo-device-control` 服务，向 AI Agent 提供文件读写、编辑和 Shell 执行能力。

## 功能特性

- **TCP JSON-RPC 2.0** 长连接，与 `tgo-device-control` 实时通信
- **自动认证**: 首次使用 `bindCode` 注册，之后凭 `deviceToken` 自动重连
- **指数退避重连**: 断线自动重连，避免服务端过载
- **4 个内置工具**:
  - `fs_read` – 读取文件内容（支持行号范围）
  - `fs_write` – 创建/覆盖/追加文件
  - `fs_edit` – 精确字符串替换
  - `shell_exec` – 执行 Shell 命令
- **安全沙箱**: 路径白名单、命令黑名单、输出截断、超时控制
- **可扩展**: 通过 `Tool` 接口注册自定义工具
- **Windows 窗口工具（本机选择性开启）**：截图、指定区域点击和普通文本输入；默认关闭，需在设备本机绑定单个前台窗口。

## 快速开始

### 构建

```bash
make build
```

### 首次运行（使用绑定码）

```bash
./bin/tgo-device-agent \
  --server your-control-server:9876 \
  --bind-code ABC123 \
  --work-root /path/to/workspace \
  --log-level info
```

### 后续运行（自动使用已保存的 Token）

```bash
./bin/tgo-device-agent \
  --server your-control-server:9876 \
  --work-root /path/to/workspace
```

### 使用配置文件

```bash
./bin/tgo-device-agent --config configs/config.json
```

## 启动参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--server` | 控制服务地址 `host:port` | `localhost:9876` |
| `--bind-code` | 首次注册绑定码 | – |
| `--name` | 设备显示名称 | 主机名 |
| `--work-root` | 文件操作根目录 | `.` |
| `--log-level` | 日志级别: debug/info/warn/error | `info` |
| `--config` | JSON 配置文件路径 | – |
| `--version` | 显示版本 | – |
| `--screen-select` | 打开本机窗口选择界面，逐项核对截图/区域点击/文本输入授权 | `false` |
| `--screen-window` | 本机指定前台窗口句柄，支持十进制/十六进制；0 关闭屏幕工具 | `0` |
| `--screen-executable` | 该窗口程序的完整路径，必须与实际进程一致 | – |
| `--screen-max-actions` | 本次点击/输入总上限，1～50 次 | `20` |

配置文件在参数解析后载入；除 `--server` 外，文件内同名设置优先。使用 `--screen-select` 时，最终以本机选择界面确认的窗口、权限和操作上限覆盖文件/参数授权；没有确认则退出，不连接服务。

## 认证流程

```
┌──────────────┐                    ┌──────────────────┐
│ device-agent │                    │ device-control   │
└──────┬───────┘                    └────────┬─────────┘
       │  TCP connect                        │
       │ ──────────────────────────────────> │
       │                                     │
       │  auth(bindCode / deviceToken)       │
       │ ──────────────────────────────────> │
       │                                     │
       │  {deviceId, deviceToken, projectId} │
       │ <────────────────────────────────── │
       │                                     │
       │  ← 保存 deviceToken 到本地文件       │
       │                                     │
       │  tools/list (服务端请求)             │
       │ <────────────────────────────────── │
       │                                     │
       │  {tools: [...]}                     │
       │ ──────────────────────────────────> │
       │                                     │
       │  heartbeat (周期性 pong)             │
       │ ──────────────────────────────────> │
```

## 工具 Schema

### fs_read

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 文件路径 |
| `offset` | int | 否 | 起始行号（1-based，负数从尾部计算） |
| `limit` | int | 否 | 读取行数 |

### fs_write

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 文件路径 |
| `content` | string | 是 | 写入内容 |
| `mode` | string | 否 | `overwrite`（默认）或 `append` |
| `create_dirs` | bool | 否 | 是否自动创建父目录（默认 true） |

### fs_edit

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `path` | string | 是 | 文件路径 |
| `old_string` | string | 是 | 要替换的精确文本 |
| `new_string` | string | 是 | 替换后的文本 |
| `replace_all` | bool | 否 | 替换所有匹配（默认 false） |

### shell_exec

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `command` | string | 是 | Shell 命令 |
| `cwd` | string | 否 | 工作目录（默认 work_root） |
| `timeout_sec` | int | 否 | 超时秒数（默认 60，上限 300） |
| `env` | object | 否 | 额外环境变量 |

## Windows 窗口工具

目前只支持 **Windows amd64 的交互桌面**。未填写本机授权时，仍只有原四个工具；其他平台继续支持原文件/命令工具。窗口必须由设备使用者在本机指定，服务端不能通过工具参数更换窗口或程序。

已绑定设备可以用本机选择界面启动，不需要手填窗口句柄：

```powershell
.\bin\tgo-device-agent.exe --server HOST:PORT --screen-select
```

首次绑定仍需按前文使用绑定码；客户端核对已有绑定凭据后才打开选择界面。列表只在本机显示当前可用的桌面应用，默认不选中任何窗口、不允许点击或文本输入。选择后会显示程序完整路径、内容区尺寸；点击需额外填写 X/Y/宽度/高度，文本输入需单独勾选，上限在界面确认。切换窗口或刷新会清空输入权限。确认后仅尝试正常激活所选窗口，Windows 拒绝或窗口/布局变化时不继续授权，不使用强制抢焦点办法。

授权后会显示独立的本机状态窗口，列出目标窗口与程序，并提供“停止授权”。它不置顶、不主动抢走目标窗口的焦点；关闭状态窗口也会停止授权，状态窗口创建失败时拒绝开放屏幕工具。窗口名称、程序路径、句柄和进程信息不会作为身份字段加入结构化工具结果或服务器注册信息；授权画面仍按实际窗口内容返回。本机窗口选择不是远程调用参数，也不会把授权写回配置文件。

状态窗优先放到业务窗口外侧，目标占满显示器、没有空余位置时显示到任务栏，避免遮挡批准区域。此时仍可按住 `Esc`，或从任务栏打开状态窗口后停止。

**还需服务器策略确认：**本机授权只准备能力，不会直接开放输入。服务器需通过 `screen/status` 取得当前不含窗口身份的授权编号，再用 `screen/policy/apply` 确认更小范围；未确认时只发现停止工具。重复确认不重置次数，新版本使旧截图失效，错误更新会清除旧策略。请求了屏幕模式但初始化失败时也不会退回开放文件/命令工具。控制服务已有内部持久发布/撤销接口，但尚未接正式页面、业务测试和员工执行入口，因此现在仅启动客户端不会让线上 AI 自动获得屏幕能力。完整消息定义见[屏幕策略协议](../../docs/device-screen-policy-protocol.md)。

以下是配置片段，`window_handle: 0` 保持关闭。实际使用前，应在本机核对测试软件、前台窗口和允许点击的区域，再填写当前窗口句柄及程序完整路径。坐标相对窗口客户区，不包含标题栏；示例区域不能直接用于未知软件。

```json
{
  "screen": {
    "window_handle": 0,
    "executable": "C:\\BusinessTest\\Example.exe",
    "click_regions": [{"x": 20, "y": 50, "width": 200, "height": 40}],
    "allow_text": false,
    "max_actions": 20
  }
}
```

| 工具 | 本机条件 | 参数与返回 |
|---|---|---|
| `screen_screenshot` | 窗口、程序、进程实例、前台及截图检测全部通过 | 无参数；返回 `view_id`、宽高和 `image/png` 图片内容 |
| `screen_click` | 配置了窗口内点击区域 | `view_id`、整数 `x/y`；仅在批准区域内单击 |
| `screen_type_text` | `allow_text: true` | `view_id`、`text`；仅支持当前聚焦的原生 Windows 普通 `Edit` 控件 |
| `screen_stop` | 已建立本机窗口授权 | 无参数；停止本次授权 |

每次点击或输入前必须重新截图，只能使用刚取得的一次性 `view_id`。执行前再次截图并核对内容；窗口内容、位置、大小、DPI、进程实例或焦点改变会停止授权。图片限制为 PNG 1 MiB、约 400 万像素，无法提供可核验画面的窗口不开放工具。截图在隔离子进程内只绘制所选窗口，再裁切内容区作为 PNG；父进程以 2 秒期限终止并等待超时/取消的自有子进程退出。返回图像不含标题栏，坐标与点击坐标一致；不会退回截取整个桌面。

文本输入限 1～256 个字符，不接受换行、控制键或快捷键；密码、只读、禁用和浏览器/自绘控件不支持。点击和输入只报告输入是否送达，不据此认定提交、保存、查单或其他业务完成。输入结果不回显原文。部分输入未确认时停止，不能自动重试。

**停止方式：**按住本机 `Esc`、调用 `screen_stop` 或退出客户端。重连后的第二个已认证连接也会停止旧窗口授权，需要本机重新核对后启动。已停止授权不会因继续调用或重连自动恢复。锁屏、UAC 安全桌面或权限不足时拒绝操作，不尝试提升权限。使用者手动按住修饰键或鼠标键时也不会发送新的输入。

已发布的只读文件策略仍然只允许 `fs_read`，不会因本机开启窗口授权扩大权限。独立屏幕策略发布/回执已有实现，正式页面、完整文件/屏幕模式切换、业务验证、可编辑录制及完整设备控制服务联调仍需继续；多显示器/混合 DPI、实际物理 Esc/锁屏、其他业务软件还需独立验收。本节描述客户端基础能力，不表示正式屏幕业务模板已经可用。

### 验证

```powershell
go test -count=1 ./...
go vet ./...
go build -o bin/tgo-device-agent.exe ./cmd/agent
```

普通测试默认跳过原生窗口验收。下述显式验收只创建并操作测试进程自己的合成窗口，不读取已有应用；需本机有可交互桌面。若自有窗口没有成为前台，测试会失败而不发送输入。请先结束正在输入的操作，再单独运行：

```powershell
$env:TGO_SCREEN_NATIVE_ACCEPTANCE = 'owned-fixture-only'
$env:TGO_SCREEN_PICKER_ACCEPTANCE = 'owned-fixture-only'
$env:TGO_SCREEN_NATIVE_REPORT_DIR = Join-Path (Get-Location) 'test-results/screen-owned'
try {
    go test -count=1 -v -timeout 30s -run '^TestNativeOwnedWindow' ./internal/screen
} finally {
    Remove-Item Env:TGO_SCREEN_NATIVE_ACCEPTANCE, Env:TGO_SCREEN_PICKER_ACCEPTANCE, Env:TGO_SCREEN_NATIVE_REPORT_DIR -ErrorAction SilentlyContinue
}
```

退出码 0 且本次报告 `verification_completed: true` 才表示这次独立验收通过；原生输入报告须含 `owned_fixture_closed`，窗口选择报告 `picker-report.json` 须含 `all_owned_windows_closed`。三组测试覆盖父/子控件坐标、实际截图/区域点击/中文输入/密码拒绝，以及原生窗口选择、取消、权限重置、越界拒绝、不抢焦点的状态窗口和按钮停止；选择/停止检查使用自有控件的原生消息，不等同物理按键操作。报告记录实际 `window_dpi`，不代替其他 DPI、多屏或 MCP/TCP/设备服务、真实业务软件验收。报告含合成窗口 PNG，应留在本机且不提交仓库。

独立客户端/TCP 验证还需已构建的当前客户端。下述 TCP 对端是仅测试使用的合成本机服务，不是已部署的 Python Device Control：

```powershell
go build -o bin/tgo-device-agent.exe ./cmd/agent
$env:TGO_SCREEN_RPC_ACCEPTANCE = 'owned-fixture-only'
$env:TGO_SCREEN_RPC_AGENT_EXE = (Resolve-Path 'bin/tgo-device-agent.exe').Path
$env:TGO_SCREEN_NATIVE_REPORT_DIR = Join-Path (Get-Location) 'test-results/screen-rpc'
try {
    go test -count=1 -v -timeout 45s -run '^TestNativeOwnedScreenPolicyRPC$' ./internal/screen
} finally {
    Remove-Item Env:TGO_SCREEN_RPC_ACCEPTANCE, Env:TGO_SCREEN_RPC_AGENT_EXE, Env:TGO_SCREEN_NATIVE_REPORT_DIR -ErrorAction SilentlyContinue
}
```

需退出 0 且本次 `rpc-report.json` 包含实际 PNG、受限点击、重放拒绝、次数不重置、重连失效及自有客户端/窗口关闭。`TestCaptureWorkerDeadline` 在普通 Windows 测试中启动明确停驻的自有测试子进程，用生产监督器验证实际期限与退出；发布客户端不含停驻分支。它不代表其他软件都能被绘制或已验证真实应用挂起。

## 安全机制

### 文件沙箱
- 所有文件操作只允许在 `work_root` 及 `allowed_paths` 内执行
- 自动解析符号链接防止路径逃逸
- 阻止写入系统关键路径（`/etc`、`/usr`、`/bin` 等）
- 读写均有大小限制（默认 10 MB）

### 命令安全
- 内置危险命令黑名单（`rm -rf /`、`mkfs`、`dd if=/dev/zero` 等）
- 检测危险模式（`curl | sh`、`chmod -R 777 /` 等）
- 输出截断（默认 1 MB）
- 执行超时（默认 60 秒，上限 5 分钟）

## 故障排查

| 问题 | 排查方向 |
|------|---------|
| 连接超时 | 检查服务地址和端口，防火墙规则 |
| 认证失败 | 确认 bindCode 未过期，或 deviceToken 文件未损坏 |
| 工具不可见 | 检查 device-control 日志中的 `tools/list` 调用 |
| 路径拒绝 | 确认 `--work-root` 设置正确，路径在白名单内 |
| 命令被拦截 | 检查 `blocked_commands` 配置 |

## 项目结构

```
tgo-device-agent/
├── cmd/agent/          # 程序入口
│   └── main.go
├── internal/
│   ├── config/         # 配置加载
│   ├── protocol/       # JSON-RPC 消息类型
│   ├── transport/      # TCP 客户端、重连、心跳
│   ├── tools/          # 工具注册中心、原四工具及可选屏幕工具
│   ├── screen/         # 本机窗口授权、选择/停止界面、Windows 截图与输入
│   └── sandbox/        # 安全沙箱（路径验证、命令过滤）
├── configs/            # 配置示例
├── Dockerfile
├── Makefile
└── README.md
```

## 扩展工具

实现 `tools.Tool` 接口并在 `Registry` 中注册：

```go
type MyTool struct{}

func (t *MyTool) Name() string { return "my_tool" }
func (t *MyTool) Definition() protocol.ToolDefinition { /* ... */ }
func (t *MyTool) Execute(ctx context.Context, args map[string]interface{}) *protocol.ToolCallResult { /* ... */ }

// 在 NewRegistry() 中添加:
// r.Register(&MyTool{})
```

## 与 tgo 生态集成

```
tgo-ai (Agent) ──MCP──> tgo-device-control ──TCP──> tgo-device-agent
    │                         │                          │
    │  bound_device_id        │  MCP 透传                │  工具执行
    │  POST /mcp/{device_id}  │  tools/call              │  fs_read / shell_exec
```
