# JSON-RPC Remote Control Protocol

> 当前内置 `tgo-device-agent` 是 Go 客户端，提供 `fs_read`、`fs_write`、`fs_edit` 和 `shell_exec`。本文后续 Peekaboo/macOS 截图和点击示例是原协议示意，不代表当前内置客户端支持屏幕操作。

Peekaboo 支持作为 JSON-RPC 2.0 代理客户端连接到远程控制服务器，接收并执行自动化命令。这使得远程控制 macOS 桌面自动化成为可能。

## 架构概览

```
┌─────────────────┐                    ┌─────────────────┐
│  Control Server │◄──── TCP ────────►│  Peekaboo Agent │
│  (Your Server)  │   JSON-RPC 2.0    │  (peekaboo rpc) │
└─────────────────┘                    └─────────────────┘
        │                                      │
        │  1. Agent connects                   │
        │  2. Agent authenticates              │
        │  3. Server sends commands ──────────►│
        │  4. Agent executes & responds ◄──────│
        │                                      │
```

## 快速开始

## 协议规范

使用标准 JSON-RPC 2.0 协议，消息以换行符 (`\n`) 分隔。

### 连接流程

```
1. Agent 建立 TCP 连接
2. Agent 发送 auth 请求
3. Server 返回认证结果
4. 进入消息循环：Server 发请求，Agent 返响应
```

### 认证

支持两种认证方式：
- **首次注册**: 使用一次性绑定码 (`bindCode`)，设备将被注册到关联的项目
- **重新连接**: 使用设备令牌 (`deviceToken`)，已注册设备的重连认证

#### 首次注册 (使用 bindCode)

**Device → Server:**

```json
{
  "jsonrpc": "2.0",
  "method": "auth",
  "params": {
    "bindCode": "ABC123",
    "deviceInfo": {
      "name": "My MacBook Pro",
      "version": "3.0.0",
      "os": "macOS",
      "osVersion": "14.0",
      "screenResolution": "1920x1080"
    }
  },
  "id": 0
}
```

**Server → Device (成功):**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "status": "ok",
    "deviceId": "550e8400-e29b-41d4-a716-446655440000",
    "deviceToken": "<project-key>",
    "projectId": "660e8400-e29b-41d4-a716-446655440001",
    "message": "Device registered successfully"
  },
  "id": 0
}
```

> **重要**: 设备必须保存返回的 `deviceToken`，用于后续重新连接。

#### 重新连接 (使用 deviceToken)

**Device → Server:**

```json
{
  "jsonrpc": "2.0",
  "method": "auth",
  "params": {
    "deviceToken": "<project-key>",
    "deviceInfo": {
      "name": "My MacBook Pro",
      "version": "3.0.0"
    }
  },
  "id": 0
}
```

**Server → Device (成功):**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "status": "ok",
    "deviceId": "550e8400-e29b-41d4-a716-446655440000",
    "projectId": "660e8400-e29b-41d4-a716-446655440001",
    "message": "Reconnected successfully"
  },
  "id": 0
}
```

#### 认证失败

**Server → Device (无效绑定码):**

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32001,
    "message": "Authentication failed: invalid or expired bind code"
  },
  "id": 0
}
```

**Server → Device (无效设备令牌):**

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32001,
    "message": "Authentication failed: invalid device token"
  },
  "id": 0
}
```

#### 认证参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `bindCode` | string | 二选一 | 6位绑定码，首次注册时使用 |
| `deviceToken` | string | 二选一 | 设备令牌，重连时使用 |
| `deviceInfo.name` | string | 是 | 设备名称 |
| `deviceInfo.version` | string | 是 | 客户端版本号 |
| `deviceInfo.os` | string | 首次必填 | 操作系统名称 |
| `deviceInfo.osVersion` | string | 否 | 操作系统版本 |
| `deviceInfo.screenResolution` | string | 否 | 屏幕分辨率 (如 "1920x1080") |

#### 认证响应说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | 固定为 "ok" |
| `deviceId` | string | 设备 UUID，用于后续操作 |
| `deviceToken` | string | 设备令牌，仅首次注册返回，设备需保存用于重连 |
| `projectId` | string | 设备所属项目 ID |
| `message` | string | 人类可读的状态消息 |

## 支持的方法

### `policy/apply` - 应用只读业务范围

企业管理员从本企业设备的文件场景草稿发布只读策略后，服务端在已认证连接上发送 `policy/apply`。当前版本只支持固定工具 `fs_read`：路径必须位于已保存目录，调用必须提供 `limit`（1 至 `max_lines`），`offset` 为 0 至 `max_offset`，编码只允许 UTF-8。其他工具和额外参数在策略生效后拒绝。

请求字段为 `revision`、`tool_name`（固定 `fs_read`）、`allowed_directory`（设备上的绝对路径）、`max_lines`、`max_offset`、`digest`。摘要算法为这些 UTF-8 字节的 SHA-256 小写十六进制表示；下式的 `\n` 表示实际换行字节，并包含末尾换行：

```text
tgo-read-policy-v1\n{revision}\nfs_read\n{allowed_directory}\n{max_lines}\n{max_offset}\n
```

设备端先验证摘要，再把目录与本机 `work_root`、`allowed_paths`、`denied_paths` 取交集，并确认目录存在。成功时只回传相同的 `revision` 和 `digest`。服务端仅在两者精确一致时显示 `active`；断线、旧客户端不支持此方法、超时或应用失败时显示 `pending`，并拒绝该设备的工具调用。重新连接后必须重新应用，旧确认不会沿用。设备与服务端之间的原始 TCP `tools/list` 只报告客户端实现的工具，不能当作已授权动作列表。

企业管理员可调用 `POST /v1/devices/{device_id}/read-policy/test`，提供策略目录内测试文件的绝对路径、4～100 字符的无敏感标记及读取行数。服务端仅在当前连接已确认策略时调用 `fs_read`，比较标记后保存通过/失败、策略摘要、连接编号和测试时间，不保存或返回文件内容。`verified` 仅在当前连接、摘要及通过记录都一致时为 `true`；重连或重新发布后必须重测。网关同名接口位于 `/v1/device-control/devices/{device_id}/read-policy/test`，仅企业管理员可调用。

对已发布策略的设备，对外 MCP `tools/list` 仅在当前连接已确认策略且读取测试通过时返回 `fs_read`；其它客户端工具不会提供给 AI 员工。REST 工具列表在待确认或待测试时返回 HTTP 409，MCP 返回 JSON-RPC 错误。`tools/call` 再次检查相同状态、动作名称和路径/行数范围；列表可见不能替代执行入口的检查。

AI 员工绑定时保存当前策略摘要。构建工具时重新读取策略，摘要变化即拒绝装载；AI 服务签发的设备访问令牌还携带该摘要，设备服务在列表和每次调用时比对。策略重发后须重新测试并显式解除、重新绑定员工；对未发布策略的旧设备，摘要为 `null`，保留原绑定行为。现阶段只读文件场景采用此固定引用；工作流设备节点、脚本和写入动作尚未完成对应绑定与审批门禁。

目前仅提供收紧范围的策略发布，没有放宽或删除策略的管理入口。草稿与策略都不能扩大客户端本机 sandbox；客户端确认不等于目录内文件实际读取成功，读取测试也仅证明指定测试文件在当次连接可读。脚本和写入动作仍需人工确认门禁与真实设备测试后才能开放业务模板。

### `tools/list` - 列出可用工具

返回所有可用的自动化工具及其参数定义。

**Server → Agent:**

```json
{
  "jsonrpc": "2.0",
  "method": "tools/list",
  "params": {},
  "id": 1
}
```

**Agent → Server:**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "tools": [
      {
        "name": "see",
        "description": "Capture UI state with optional annotation",
        "inputSchema": {
          "type": "object",
          "properties": {
            "app_target": { "type": "string", "description": "Target app name or bundle ID" },
            "annotate": { "type": "boolean", "description": "Add element annotations" }
          }
        }
      },
      {
        "name": "click",
        "description": "Click on an element or coordinates",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": { "type": "string", "description": "Element query or ID" },
            "coords": { "type": "string", "description": "x,y coordinates" }
          }
        }
      }
    ]
  },
  "id": 1
}
```

### `tools/call` - 执行工具

调用指定的自动化工具。

**Server → Agent:**

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "see",
    "arguments": {
      "app_target": "Safari",
      "annotate": true
    }
  },
  "id": 2
}
```

**Agent → Server (成功):**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Captured Safari window with 15 UI elements"
      },
      {
        "type": "image",
        "data": "iVBORw0KGgoAAAANSUhEUgAA...",
        "mimeType": "image/png"
      }
    ],
    "isError": false
  },
  "id": 2
}
```

**Agent → Server (工具执行错误):**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Error: Application Safari is not running"
      }
    ],
    "isError": true
  },
  "id": 2
}
```

### `ping` - 健康检查

用于心跳检测和连接保活。

**Server → Agent:**

```json
{
  "jsonrpc": "2.0",
  "method": "ping",
  "id": 3
}
```

**Agent → Server:**

```json
{
  "jsonrpc": "2.0",
  "result": {
    "pong": true,
    "timestamp": 1706428800
  },
  "id": 3
}
```

## 可用工具列表

| 工具名 | 描述 | 关键参数 |
|--------|------|----------|
| `see` | 捕获 UI 状态并标注元素 | `app_target`, `annotate`, `path` |
| `image` | 截图 | `mode`, `app_target`, `retina` |
| `click` | 点击元素或坐标 | `query`, `on`, `coords`, `double`, `right` |
| `type` | 输入文本 | `text`, `clear`, `delay_ms` |
| `scroll` | 滚动 | `direction`, `ticks`, `on` |
| `hotkey` | 快捷键组合 | `keys` |
| `swipe` | 滑动手势 | `from`, `to`, `duration` |
| `drag` | 拖拽 | `from`, `to`, `modifiers` |
| `move` | 移动光标 | `to`, `coords` |
| `app` | 应用管理 | `action`, `name`, `bundle_id` |
| `window` | 窗口管理 | `action`, `app`, `title` |
| `menu` | 菜单操作 | `action`, `app`, `path` |
| `dock` | Dock 操作 | `action`, `app` |
| `dialog` | 对话框处理 | `action`, `button` |
| `space` | 桌面空间管理 | `action`, `index` |
| `clipboard` | 剪贴板操作 | `action`, `text` |
| `paste` | 粘贴 | `text`, `target` |
| `list` | 列出信息 | `type` |
| `permissions` | 权限检查 | `action` |
| `sleep` | 延迟 | `ms` |
| `analyze` | AI 分析 | `prompt`, `image` |

## 错误码

| 错误码 | 名称 | 描述 |
|--------|------|------|
| `-32700` | Parse error | 无效的 JSON |
| `-32600` | Invalid Request | 无效的 JSON-RPC 请求 |
| `-32601` | Method not found | 方法不存在 |
| `-32602` | Invalid params | 无效的参数 |
| `-32603` | Internal error | 内部错误 |
| `-32001` | Auth failed | 认证失败 |
| `-32002` | Tool not found | 工具不存在 |
| `-32003` | Tool execution failed | 工具执行失败 |
| `-32004` | Connection closed | 连接已关闭 |

## 客户端连接示例

### Python 客户端示例

```python
import socket
import json

def connect_to_server(host="localhost", port=9876, device_token=None, bind_code=None):
    """连接到 TGO Device Control 服务器"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    print(f"Connected to {host}:{port}")

    # 构建认证请求
    auth_params = {
        "deviceInfo": {
            "name": "My MacBook Pro",
            "version": "3.0.0",
            "os": "macOS",
            "osVersion": "14.0",
            "screenResolution": "1920x1080"
        }
    }

    if device_token:
        auth_params["deviceToken"] = device_token
    elif bind_code:
        auth_params["bindCode"] = bind_code
    else:
        raise ValueError("Either device_token or bind_code is required")

    auth_request = {
        "jsonrpc": "2.0",
        "method": "auth",
        "params": auth_params,
        "id": 0
    }

    sock.send(json.dumps(auth_request).encode() + b"\n")
    response = json.loads(sock.recv(4096).decode())

    if "error" in response:
        print(f"Auth failed: {response['error']['message']}")
        sock.close()
        return None

    result = response["result"]
    print(f"Authenticated! Device ID: {result['deviceId']}")

    # 首次注册时保存 device_token
    if "deviceToken" in result:
        print(f"Save this token for reconnection: {result['deviceToken']}")

    return sock

if __name__ == "__main__":
    # 首次注册 (从 Web UI 获取绑定码)
    # sock = connect_to_server(bind_code="ABC123")

    # 重新连接 (使用保存的设备令牌)
    sock = connect_to_server(device_token="your-saved-device-token")
```

> **注意**: 以上是客户端示例。服务端由 TGO Device Control 服务实现，使用绑定码/设备令牌进行认证。

## 断线重连

Agent 默认启用自动重连，使用指数退避策略：

1. 初始延迟：`--retry-delay` 秒（默认 1 秒）
2. 每次失败后延迟翻倍
3. 最大延迟：30 秒
4. 最大重试次数：`--max-retries`（0 表示无限重试）

禁用重连：

```bash
peekaboo rpc connect --host server.example.com --token "xxx" --no-reconnect
```

## 安全建议

1. **绑定码安全**: 绑定码有效期默认为 5 分钟，且为一次性使用
2. **设备令牌保护**: 设备应安全存储 deviceToken，避免泄露
3. **限制网络访问**: 通过防火墙限制可连接的 IP 地址
4. **使用 TLS**: 在生产环境中，通过 TLS 隧道（如 stunnel）加密连接
5. **监控连接**: 记录所有连接尝试和命令执行
6. **设备管理**: 可通过 Web UI 随时撤销设备的访问权限

## 与 MCP 的区别

| 特性 | MCP (`peekaboo mcp`) | JSON-RPC (`peekaboo rpc`) |
|------|----------------------|---------------------------|
| 连接方向 | 客户端连接 Peekaboo | Peekaboo 连接服务器 |
| 传输协议 | stdio (管道) | TCP socket |
| 主要用途 | Claude Desktop/Cursor 集成 | 远程自动化控制 |
| 认证 | 无（本地进程） | 绑定码 / 设备令牌 |
| 重连 | 不适用 | 支持自动重连 |

## 故障排除

### 连接失败

```
Error: Connection failed: Failed to resolve host
```

- 检查主机名是否正确
- 确认网络连接正常
- 验证防火墙设置

### 绑定码认证失败

```
Error: Authentication failed: invalid or expired bind code
```

- 确认绑定码未过期（有效期 5 分钟）
- 确认绑定码未被使用（一次性使用）
- 从 Web UI 重新生成绑定码

### 设备令牌认证失败

```
Error: Authentication failed: invalid device token
```

- 确认设备令牌正确
- 检查设备是否已被管理员从项目中移除
- 使用新的绑定码重新注册设备

### 工具执行失败

```
Error: Tool execution failed: ...
```

- 确认 macOS 权限已授予（`peekaboo permissions status`）
- 检查目标应用是否正在运行
- 查看详细错误信息

## TGO Windows 单窗口屏幕协议 v1

新增 `screen/status` 与 `screen/policy/apply`，用于已认证连接内核对当前本机授权实例，并确认服务端与本机范围的交集。参数、摘要、重复/失败/重连语义与验证边界见[共享协议](../../../docs/device-screen-policy-protocol.md)。它不扩展 `policy/apply` 的只读文件权限，也不代表正式 MCP 屏幕业务已开放；持久发布、测试及员工执行绑定仍需完成。

## TGO 已审核脚本目录 v1

在已认证 TCP 连接中，Control 可发送 `scripts/list`，参数省略或为 `{}`，必须包含请求 ID。额外参数、`null` 和数组返回 `-32602`。客户端目录不可用返回 `-32003`，错误不包含本机路径；旧客户端返回 `-32601`，不能将其解释为空目录或执行能力已就绪。

成功结果为 `{ "protocol_version": 1, "scripts": [] }`；有定义时，每项包含：

| 字段 | 语义与限制 |
|---|---|
| `script_id` / `title` / `version` | 稳定业务编号、中文名称和本机审核版本；编号最多 48 个 ASCII 字符，版本最多 40 字符 |
| `grant_digest` | 64 位小写 SHA-256，本机审核定义的不可读摘要；绑定正文、解释器、目录、参数映射、结果和限制 |
| `effect` | `read_only` 或 `write` 的本机审核声明；不会自行建立操作系统级权限隔离 |
| `parameters` / `result_fields` | 最多 16 个业务输入、1～32 个结果字段；必须为数组，空输入为 `[]` |
| `timeout_seconds` | 本机上限 1～120 秒；目录项不代表远程执行已开放 |
| `max_output_bytes` | 本机上限 256～65536 字节 |
| `max_runs` | 本机上限 1～100 次；本机账本已计数，服务端持久发布仍待接线 |

字段定义包含 `name`、`kind`、显式布尔 `required`。整数必须有最小/最大值，范围在 JSON 安全整数之内；字符串必须有 `max_length`（1～4096），可有最多 32 个不重复的 `choices`；布尔值不能带其他类型约束。字段和目录编号不重复，未知字段、隐式类型转换和额外本机路径字段由 Control 拒绝。

本机仅从配置 `approved_scripts` 读取明确 `approved: true` 的定义，固定 Python/Node/PowerShell 解释器与 SHA-256，脚本扩展名匹配 runner，脚本/工作目录遵循现有本机路径限制。对外不传 `script_path`、`interpreter`、`working_directory`、`arguments` 或源码。目录不能远程新增批准。下文的策略方法不执行脚本，没有新增可执行 MCP 工具。

`GET /v1/devices/{device_id}/script-catalog` 是受内部签名项目/设备身份保护的查询入口。Control 在请求前后核验数据库归属和连接实例，不缓存为启用凭证。掉线/重连/不支持返回 HTTP 409，不合法的目录为 502，企业/设备身份约束沿用管理接口。

共享 `ScriptPolicy` 摘要为 SHA-256 UTF-8 文本：`tgo-approved-script-policy-v1\n` 后依次为 `protocol_version`、`revision`、`script_id`、`grant_digest`、`timeout_seconds`、`max_output_bytes`、`max_runs`，每项十进制或原字符串并以 LF 结束，摘要字段本身不参与。版本 1、修订 3、`daily_report`、64 个 `a`、20、4096、2 的固定摘要为 `c67ba11a5189a797b80e7e00fc943219c91bd270aba8929db2227b4d388fe057`。发布及执行协议见下文；摘要相同本身不代表业务验收通过。

### 本机执行核心与远程开放状态（2026-10-27）

Go 客户端已增加包内的只读脚本执行核心，实际验证 Python、Node 与 PowerShell。审核代码通过标准输入传递；PowerShell 使用固定 bootstrap 与类型化参数，文本不拼成命令。正常退出后仍需验证完整 JSON 结果、定义与取消状态，失败或超限不返回业务字段。此阶段最初只有包内执行；之后新增的只读 RPC 见末节，`scripts/list` 仍只查询目录，MCP 不开放脚本工具。

Windows 10/Server 2016+ 进程在创建时经 Job List 归入本次 Job，并只继承三个管道句柄；根进程完成或取消时终止遗留子进程，执行器退出后由关闭 Job 句柄触发终止。机制依据：[UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)、[Job 限制](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)。这不是文件或网络的操作系统级权限隔离；其他平台尚未完成实际退出与故障验收。

Windows 包内执行器现已增加启动时的解释器摘要复核及文件/目录只读锁；其他平台的受保护启动未实现，带审核摘要的业务执行会失败。导入依赖与操作系统权限隔离不在该锁的保证内。

包内还增加认证加密的执行账本：相同编号绑定固定策略、输入和上下文摘要；先占用预算再执行，重开后复用终态结果，未确认记录不重新执行；策略预算与本机审核版本总预算均不因重启或重复发布重置。错键、错企业/设备或损坏不重建为空状态。Windows 互斥和替换依据：[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)。这不是断电或同身份旧快照回滚的验收证明。

认证后初始化、策略确认、Control 持久发布/撤销及限定的只读执行 RPC 已陆续接通，详见后续章节。管理页面、员工/工作流消费和业务启用仍待接通，不因为本机函数或策略确认存在就对外列出可执行工具。

### 已认证连接的脚本状态与策略确认（2026-10-27）

本机配置 `script_execution_enabled` 默认 `false`。开启后，成功认证返回的企业/设备 UUID 用于初始化独立私有状态；`script_state_directory` 只能由本机配置，默认位于设备 Token 文件旁的 `script-state` 子目录。路径不能为磁盘根或包含业务工作区。业务文件工具禁止读取此目录和 Token 文件；目录无效时仍保留 Token 禁读，且不会回退开放 Shell。

Windows 使用当前用户 DPAPI、固定用途及企业/设备身份保护随机账本密钥，不弹出系统交互，不使用机器共享密钥。身份或密文不匹配、现有身份缺少账本、现有账本缺少身份时均停止初始化，不重置历史。其他系统没有密钥保管实现，开启后的状态为不可用；不以明文降级。

`scripts/status` 接受省略参数或 `{}`。结果字段：

| 字段 | 语义 |
|---|---|
| `protocol_version` | 固定整数 `1` |
| `state` | `disabled`、`unavailable`、`awaiting_policy`、`active` 或 `closed` |
| `session_id` | 仅等待策略或已确认状态携带；32 位小写十六进制，成功认证后随机生成，重连更换 |
| `reason` | 低基数原因码，不含私有路径、密钥或本地脚本错误正文 |

`active` 只表示本次连接已确认策略，不是业务测试通过或员工可启用。初始化失败记录在本机日志，远程状态不会泄露私有错误。

`scripts/policy/apply` 必须携带请求 ID，参数为 `{ "session_id": "本连接状态返回的编号", "policy": { ...ScriptPolicy } }`。消息不超过 4096 字节；未知字段、缺项、`null`、错误类型和尾随数据被拒绝。编号不属于本连接时拒绝且不改变当前有效策略。当前连接的非法更新停用旧脚本策略。策略还须匹配当前本机审核摘要、只读声明及不超过本机的次数、时间和输出上限，原子保存后才确认。

确认结果严格包含 `protocol_version`、同一个 `session_id`、`revision`、`digest`。重复相同策略不取消正在进行的本机工作；变更策略、断开连接或切换只读文件策略会取消活动脚本并禁止返回晚到的业务字段。重连清除活动确认，但不清除加密账本中的次数与编号。旧连接的清理不能卸载新连接。

本节说明状态与配置协议。当前客户端另支持末节的 `scripts/run`、`scripts/receipt/get` 和 `scripts/cancel`；旧客户端缺少这些方法时不会被视为可执行。`tools/list` 不出现脚本或兜底 Shell，Control 业务执行开关默认关闭，员工/工作流消费与页面仍未开放。

### Control 持久发布、撤销与重连恢复（2026-10-27）

迁移 `0013_device_script_policy` 创建 `dc_device_script_policies`，每设备保存一个当前脚本策略及修订、审核摘要、执行上限、更新时间和撤销时间。数据库约束同时限制修订、次数、超时、输出及启用/撤销关系。新表不保存源码、本机路径、参数正文、密钥或“测试通过”标记；连接确认只保存在当前 TCP 连接中。

内部管理接口沿用签名项目/设备范围，不能用 query 中的企业 ID 替换签名身份；AI 签名身份不能写配置：

| 方法与路径 | 用途 |
|---|---|
| `GET /v1/devices/{device_id}/script-status` | 获取本机状态、Control 连接实例及客户端随机编号 |
| `GET /v1/devices/{device_id}/script-policy` | 读取持久配置并重核当前状态和本机审核目录 |
| `PUT /v1/devices/{device_id}/script-policy` | 按预期修订发布已审核只读脚本范围 |
| `POST /v1/devices/{device_id}/script-policy/revoke` | 按预期修订保留撤销历史并关闭对应连接 |

发布请求严格包含 `expected_policy_revision`、`connection_instance_id`、`session_id`、`script_id`、`grant_digest`、`timeout_seconds`、`max_output_bytes`、`max_runs`；不接受 `command`、代码、路径、结果或客户端提供的通过状态。先核对设备归属、修订、当前连接/本机随机编号和新读到的审核目录，只接受 `read_only` 且不大于本机上限的范围。

文件、屏幕和脚本的发布均对同一个设备行加锁；任一其他模式仍启用时返回 `409`，须先显式撤销。保存后重新取得持久修订，确认未被其他发布/撤销替换才下发 TCP 策略。确认同时核对当前连接对象、客户端随机编号、修订、摘要与再次读取的 `active` 状态。错误确认、连接替换、持久版本变化或保存失败不能保留活动确认；必要时关闭本次确切连接，不关闭替代连接。查询发现本机审核文件改变时，原服务端确认转为 `pending`。

持久记录和当前连接都符合时响应 `active`，未确认/断线为 `pending`，撤销为 `revoked`；`verified` 固定 `false`。撤销增加修订和时间，不删历史；相同预期修订的过期请求返回 `409`。新迁移只允许空表降级，非空表拒绝丢弃发布或撤销历史。

认证注册阶段即设置脚本模式门禁，防止恢复确认前回退到通用工具。认证成功后从数据库重新加载范围，核对其他模式，再用新客户端随机编号确认；原连接的确认不复用。已撤销且未切换到其他模式的记录保留禁用标记，重连不自动恢复配置。当前 MCP 列表和调用在脚本模式下明确拒绝通用业务执行，直至正式执行授权、回执、测试和固定绑定接通。

已用随机隔离 PostgreSQL schema 验证迁移、数据库会话并发、模式互斥、签名 HTTP 与撤销；另用真实 Windows Go 客户端接入实际 `TcpRpcServer`，验证 Token 认证、持久发布、审核文件变化、进程重启后新编号确认、撤销断线及自动重连后的门禁。HTTP 在 ASGI 测试进程内，TCP 为真实随机本机端口；这批配置测试没有执行脚本正文或修改常驻业务库。后续实际执行与取消证据见末节；仍需管理网关/页面和业务验收，不代表公开网络 TLS、设备安装包或生产验收。

### 绑定业务身份的只读执行、回执和定向取消（2026-10-28）

Control 的 `SCRIPT_BUSINESS_EXECUTION_ENABLED` 默认 `false`，设备本机的 `script_execution_enabled` 也默认 `false`。以下能力只用于已经批准的只读脚本，不支持下发源码、路径、解释器或命令；没有向 MCP 注册可执行工具。

三个 RPC 均要求当前认证连接和请求 ID。公共查询身份严格包含以下字段，不接受缺项、`null`、未知字段或尾随内容：

| 字段 | 约束 |
|---|---|
| `protocol_version` | 整数 `1` |
| `session_id` | 当前客户端随机连接编号，32 位小写十六进制 |
| `execution_id` | 小写 UUID；可信调用方为一次业务执行固定，重试时不重新生成 |
| `policy_digest` | 64 位小写十六进制，绑定固定策略修订 |
| `parameters_digest` | 64 位小写十六进制，绑定本机审核版本和实际业务输入 |
| `context_digest` | 64 位小写十六进制，绑定企业、设备、租约会话、员工、可选工作流执行和固定策略 |

| RPC | 输入与行为 |
|---|---|
| `scripts/run` | 公共身份加 `timeout_seconds`（1～120，不能超过策略）及 `parameters`；后者只允许审核字段中的平面字符串、整数、布尔值。策略和输入摘要在领取本机执行编号前复核。 |
| `scripts/receipt/get` | 只读公共身份；不存在为 `not_found`，执行中或未确认状态为 `unknown`。不新建执行、不消耗次数、不重跑。当前策略和完整原业务身份必须匹配才可读取结果。 |
| `scripts/cancel` | 公共身份；只取消该完整身份，回复原身份加严格布尔 `signaled`。`true` 仅表示已向活动任务发出取消信号，不证明已经退出；应继续等待运行结果或查询回执。 |

运行和查询回执返回 `protocol_version`、`execution_id`、三个摘要、`status`、可选低基数 `failure`、`replayed`，仅完整成功时带 `fields`。不返回本机路径、进程错误正文或部分结果。状态包括 `succeeded`、`failed`、`canceled`、`timed_out`、`output_exceeded`、`rejected`、`start_failed`、`unknown`；仅查询允许 `not_found`。结果还必须通过当前审核的字段、类型和输出上限校验。

定向取消不要求旧任务仍是当前活动策略，但必须匹配原连接编号和活动执行的所有身份字段。为防止并发 RPC 的调度顺序导致“取消先到、运行后启动”，同一连接会记住精确的取消身份；最多保留 256 个，不驱逐旧取消记录，容量耗尽时拒绝后续启动，重连后由新连接编号隔离。该标记不占用持久业务次数。晚到取消不能取消同设备上的另一编号、另一输入或新策略任务。变更策略、撤销和断开连接仍会停止所属任务。

Control 内部接口为 `POST /v1/devices/{device_id}/script-executions/{execution_id}` 与同路径 `GET`。签名必须是原 `tgo-ai` 会话身份，携带匹配的设备、会话及 `script_policy_digest`；员工和工作流执行从持久会话取得，不能由业务参数伪造。管理令牌不能运行脚本。旧签名缺少新增摘要时仍能访问原有接口，但不能访问新执行路径。

首次派发先核对本机取消协议支持及审核目录，再通过设备行锁原子登记执行编号、输入/上下文摘要、固定策略、一个会话步骤和次数，提交成功后才发送 TCP。迁移 `0014_device_script_executions` 不保存参数或结果正文，只保存状态、归属、摘要和时间。策略预算与本机审核版本总预算分别受限。已有编号只能核对输入后查询；派发或完成保存结果未知时不重新运行。非空执行历史拒绝迁移降级。

运行期间检查持久策略和租约，失效时发送原身份的取消请求。无法确认取消时，只有原连接对象、随机编号和策略仍一致才关闭连接；不关闭替代连接或新策略。HTTP 调用被取消时同样清理所属执行，下一次查询可恢复终态。服务端只在原范围仍有效、当前审核未变化、回执身份与结果摘要均一致时临时返回业务字段；其他情况返回元数据。

真实 Windows Agent、实际 Token 认证/随机回环 TCP、隔离 PostgreSQL 和签名 ASGI HTTP 已验证：配置恢复；执行成功、同编号只运行一次、改输入拒绝、次数上限、完成保存丢失后的查询恢复、审核变化隐藏结果、客户端重启后恢复和撤销后的门禁；会话到期及 HTTP 调用取消均停止自有测试进程，保留原连接且重复请求不再执行。HTTP 取消用例还从本机回执恢复了服务端未保存的取消终态。数据、进程、监听和测试配置均为自有隔离对象，不代表商户业务或生产验收。

管理员网关、原设备向导的审核元数据配置，以及 AI 会话内的类型化签名调用层已接入，详见下文。员工的脚本配置绑定已持久化，但业务测试/评审后的执行许可、固定工作流关联和 Ego 页面运行验收仍待完成。回执正文期限、保管键备份/恢复/轮换及旧快照恢复仍待实现；其他操作系统的受保护启动和密钥保管继续失败关闭，不以编译成功证明可运行。正式业务开关继续保持关闭。

### 管理页面代理与 AI 消费者（2026-10-28）

管理页面使用 tgo-api 当前 `API_V1_STR` 前缀下的 `/device-control/devices/{device_id}`，以下路径均要求登录管理员：

| 方法及后缀 | 用途 |
|---|---|
| `GET /script-catalog` | 本机已审核目录 |
| `GET /script-status` | 当前连接及本机随机编号 |
| `GET /script-policy` | 当前持久策略和配置状态 |
| `PUT /script-policy` | 按预期修订发布只读范围 |
| `POST /script-policy/revoke` | 按预期修订撤销范围 |

企业取自登录员工，服务签名同时绑定设备。代理核验返回的归属、摘要、修订和限制，不提供通用 RPC 或公开脚本执行转发；拒绝输入及上游错误不得带回业务值或内部正文。原报表向导只配置本机审核的元数据，不接收脚本源码、命令、解释器或路径；活动策略仍是 `verified: false`，不代表业务已测试。

AI 的 `ApprovedScriptExecution` 必须在现有 `track_device_execution` 会话内准备。审核元数据及参数在准备时复制为固定快照，签名只带本会话与 `script_policy_digest`；不能同时携带文件读取策略。执行至多尝试一次 `POST`，同对象后续操作及恢复操作只发原编号 `GET`；超时和取消不允许自动重新派发。返回前按当前会话、完整回执身份、审核输出字段及字节预算复核；会话失效后的晚到响应不能返回业务字段。持久运行编号、员工/流程绑定及业务评审仍须在正式入口中补齐。

独立 AI Python 进程、Control 原 HTTP 路由、隔离 PostgreSQL 与真实 Windows Go Agent 已完成一次实际联调：执行一次、重复查询不执行、特殊字符字段完整、员工/工作流及会话身份一致、一个持久步骤且计数为一次，正常结束会话。该证据使用自有测试脚本、临时回环监听和随机 schema，无真实模型或商户数据，不构成正式员工启用、页面或生产验收。对应开发状态见项目开发计划 4.2.250～4.2.251。

### 员工脚本配置与业务许可的区别（2026-10-28）

管理员可通过 API 版本前缀下 `/ai/agents/{agent_id}/device-script-binding` 的 `GET` / `PUT` 读取或保存停用员工的配置，同路径 `/requests/{request_id}` 提供原请求查询。AI 所有者使用 `/api/v1/agents/` 前缀的对应私有路由，实时核验配置人，从 Control 读取设备的当前审核目录和已确认策略。配置只固定员工、设备、脚本、审核/策略摘要及修订，返回 `scope=employee_device_script_configuration`、`can_enable=false`，不包含执行或评审通过标志。

保存、修订和原请求回执在同一 AI 数据库事务中完成。原请求重复只返回原结果，不能替换内容；恢复结果可能是历史配置，下一次修改仍需读取当前修订。解除保留历史。文件读取绑定与脚本配置双向互斥，未经过独立脚本验证的关联阻断员工启用；管理身份不能因此调用 `scripts/run`。实际执行仍须使用原设备会话和固定 `script_policy_digest`。

关联请求必须携带当前选择的 `expected_policy_digest`；服务端从 Control 重新取得实际策略并比较，已变化时拒绝，不会替用户改选新策略。期望摘要只是并发一致性条件，不是授权；实际保存的脚本和摘要仍来自经过核验的服务端配置。解除时不提供设备或期望策略摘要。

迁移 `g5b6c7d8e9f0` 新增 AI 自有配置/原请求表，已有记录时拒绝降级；没有将这些表放入 Control 或跨服务直接读库。独立 AI 进程已通过实际 Control HTTP 读取审核元数据并完成现有 Windows 执行/查询链路验证；这不代表新配置绑定已通过正式员工业务试验。剩余次数查询见下一节；独立试验、人工评审、启用门禁、工作流/运行记录和页面入口继续按开发计划 4.2.252～4.2.253 推进。

### 持久执行次数查询（2026-10-28）

新增只读 RPC `scripts/budget/get`，不加入通用 `tools/list`。请求严格包含 `protocol_version: 1`、当前连接的 32 位十六进制 `session_id` 和 64 位 `policy_digest`；缺少、`null`、额外字段或过期连接拒绝。只有本连接已确认的脚本策略可以查询，本机重新核对审核文件和解释器；调用方不能提供脚本、本机审批摘要、上限、用量或源码。

结果包含相同的版本、连接编号、策略摘要，以及本机取得的 `grant_digest`、`policy_max_runs`、`policy_used`、`grant_max_runs`、`grant_used`。已预留但未确认、执行失败和取消的工作仍计次；查询不执行、不增减计数。持久账本保证进程重开和重连不重置用量，同一本机审批的额度跨策略修订累计。原账本恢复隔离和历史密钥生命周期仍按既有未完成项处理，读取次数不取代这些措施。

Control 提供项目/设备签名只读接口 `GET /v1/devices/{device_id}/script-budget`，响应另含设备/企业、Control 连接实例、`observed_at` 和双方计数：

| 字段 | 含义 |
|---|---|
| `server_policy_used` / `server_grant_used` | 服务端同范围所有持久预留，包含尚未派发与未知结果 |
| `device_policy_used` / `device_grant_used` | 本机账本当前策略及审批的预留次数 |
| `policy_max_runs` / `grant_max_runs` | 当前策略上限与实际本机审批上限 |
| `remaining_runs` | 双方各项上限减去用量后的最小值，仅为观察快照 |
| `scope` / `can_enable` | 固定 `script_execution_budget_observation` / `false` |

Control 核对设备归属、当前持久策略、其他模式、实际连接、确认及审核目录；设备响应及最终数据库计数后均核对连接变化。数量为严格整数，不能为负数、超过上限或违反策略/审批关系。旧客户端不支持 RPC、未确认/已撤销、断线、文件改变或账本无法验证时返回错误，不生成零用量。请求不需要执行会话签名，也不赋予执行权限；执行仍要求原有固定设备会话及策略签名、原子预留和业务开关。

AI 通过既有客户端读取并核验精确企业/设备/策略/审核摘要、时间和数量关系；过期、未来时间、重定向、伪造许可及上游错误拒绝。实际独立 AI→Control HTTP→真实 TCP→Windows Agent 验证了余量从 1 到 0、双方用量从 0 到 1及重复查询不重跑。当前没有管理网关或页面的次数展示，独立员工试验、评审、启用与固定工作流接入仍待实现。详细范围和工程限制见项目计划 4.2.253。

### 员工独立脚本试验及管理端（2026-10-28）

后续实现见开发计划 4.2.254～4.2.255；本节更新上一节的员工试验进度，不新增或改变 Go/Control RPC。AI 在派发前持久保存原设备执行编号、员工固定配置修订与受 HMAC 保护的元数据快照，通过原执行接口运行受审核的只读脚本，按类型和预期摘要核对真实回执。原编号查询使用不含测试正文的 `ScriptReceiptReference`；该引用不能发起 POST，未知结果不会另建执行编号。测试仍消耗实际次数，回执丢失不会返还预算。

API 提供管理员身份下 `/v1/ai/agents/{agent_id}/device-script-trials` 的 POST，以及 `/requests/{request_id}`、`/{report_id}` 的 GET；可信身份经既有 AI 客户端传递，浏览器不能提供执行会话签名或任意 RPC。原员工编辑页新增可收起的“设备业务脚本（可选）”，复用现有配置绑定 API。输入、预期及结果正文不进入 AI 试验表或浏览器恢复记录；页面展示比对结论、适用状态和报告记录的剩余次数，独立的实时预算管理接口尚未接入。

试验报告固定 `scope=employee_device_script_trial`、`assessment=not_reviewed`、`can_enable=false`。`matched` 仅表示本次类型化结果与固定预期一致；配置/连接变化、过期、撤销、剩余次数和人工评审仍须独立核验。租约结束后可能只能查询回执元数据而不能恢复结果正文，不能把这种情况判为核验成功。HTTP 传输层也可能重发相同 POST，防止重复业务执行依靠持久原编号及一次派发，不仅依靠页面按钮。

实际 AI→Control→Windows 客户端的独立试验与旧调用回归均通过；管理端另有合成 HTTP 504、GET 续查及刷新恢复的 Ego 控件/布局验证，两者不是一次完整真实登录端到端验收。Ego 截图仍受最小化窗口限制；常驻库迁移、真实页面/软件、人工评审、启用、正式员工/固定工作流及统一记录仍待完成。没有新增可供管理端直接执行任意脚本的入口，也没有据试验报告开启正式业务。

### 原脚本请求准备与停止确认（2026-10-28）

开发计划 4.2.256 增加 AI 持久请求准备和取消状态，不增加新的 Go/Control RPC。管理网关提供原请求 `/state` 查询及 `/cancel` 提交，取消仅引用原请求和绑定修订。准备中的取消阻止后续派发；已派发时通过原可信执行身份结束既有租约，Control 按现有租约检查停止实际脚本。

“取消已记录”“租约已结束”和“设备已停止”是不同证据。AI 将已派发取消记为 `requested`；原身份和摘要核验后的 `canceled` 回执才能记为 `confirmed`，其他真实结束回执为 `finished`。查询不到记录、超时、租约结束应答或关闭页面均不证明停止，次数不返还。独立 AI→真实 HTTP/TCP→Windows Go 的运行中取消已实际通过：先观察脚本进度，再取消并确认进度停止、同编号不重执行及认证连接保留；这不是正式页面或真实商户软件验收。

### 固定业务用例与结果证据（2026-10-28）

开发计划 4.2.257 补充 AI 层的不可变用例版本及受控结果留存，不改变 Go/Control RPC。管理员可在原员工编辑页填写已审核的业务字段，显式保存用例；保存只创建版本，不执行脚本。修改按原用例修订创建新版本，旧版本及其原执行证据保留。运行已保存版本仅提交版本标识、原请求及绑定修订，服务端读取固定输入和预期，仍沿用原试验请求准备、一次派发、取消、租约与实际次数预算。

管理员网关新增以下路径，前缀为 `/v1/ai/agents/{agent_id}`：

| 方法与路径 | 用途 |
|---|---|
| `GET /device-script-cases` | 分页读取最新版本元数据 |
| `POST /device-script-cases` | 保存新用例或新版本，不执行 |
| `GET /device-script-cases/requests/{request_id}` | 查询原保存请求，不重新提交 |
| `GET /device-script-cases/requests/{request_id}/state` | 查询原保存准备及明确终态，仅元数据 |
| `POST /device-script-cases/requests/{request_id}/cancel` | 取消原保存准备，阻止迟到请求落库 |
| `GET /device-script-cases/versions/{version_id}` | 受控读取固定输入和预期 |
| `POST /device-script-cases/versions/{version_id}/trials` | 使用固定版本运行一次试验 |
| `GET /device-script-trials/{report_id}/evidence` | 受控读取原用例与实际结果 |

成功正文响应使用 `Cache-Control: no-store`；网关继续核对当前管理员、企业、员工、原请求和版本，拒绝不完整或伪造的许可。用例校验错误的输入、动态字典键及额外字段名均从响应和日志中移除。正文只保存在当前页面的临时状态，刷新后需重新鉴权读取，不进入共享状态或浏览器恢复存储。

只有显式保存用例的试验才留存输入、预期与实际结果正文。AI 的 `j8e9f0a1b2c3` 迁移增加两张独立证据表；原试验表继续仅保存元数据。正文使用 AES-GCM 加密，附加认证数据绑定企业、操作者、员工、记录编号与用途；用例摘要还绑定原版本和配置。实际回执在原租约结束前核验并加密留存，之后读取证据不重新执行设备。临时试验返回 `not_retained`；待确认、不可读取和过期分别展示，不生成虚构结果。

配置项 `SCRIPT_TRIAL_BODY_RETENTION_DAYS` 默认 90 天；`SCRIPT_TRIAL_CLEANUP_INTERVAL_SECONDS` 默认 3600 秒。维护任务按批清理过期密文，保留编号、摘要及审计元数据。`SCRIPT_TRIAL_EVIDENCE_KEY` 可指定证据加密密钥，未配置时使用足够强的 `SECRET_KEY`；占位或过短密钥拒绝保存。备份须包含受保护的原加密及签名密钥，轮换会影响旧记录的可读性和适用性，不能将完整性失败当作评审通过。

用例、运行包装及证据仍固定 `can_enable=false`。自动比对和可读证据不授予正式执行许可；人工评审、有效期/撤销、当前预算门禁、员工与固定工作流调用及统一记录仍待接通。常驻库尚未执行本迁移，不能仅凭代码或隔离测试在正式员工上启用脚本。

开发计划 4.2.258 增加 AI 迁移 `k9f0a1b2c3d4`，在设备校验前持久化保存请求，固定输入和预期仍只存于原加密用例表。保存状态分为 `preparing/saved/failed/cancelled`；准备期限默认 60 秒，超期查询会结为失败。取消请求只提交 `request_id/binding_id/binding_revision/case_id/expected_revision`，路径与正文原编号必须一致。取消可以先到并留下持久围栏；已保存的版本则返回实际回执，不执行删除或撤销保存。新版本与成功回执原子提交，旧已保存用例按实际记录恢复。两条接口沿用当前管理员、企业和配置权限以及 `no-store`，不增加 Go/Control RPC。

未知结果（包括查询返回 404）不能当作保存失败或自动重新提交。页面只有收到明确失败、取消或已保存回执后才能解除原等待；取消仍在等待的保存 POST 时，迟到响应不能覆盖当前终态。含账本历史时降级迁移拒绝删除围栏；上线前须备份并协调 AI/API/Web 版本。常驻业务库尚未迁移，不把隔离页面验收当作正式登录页面或生产验收。
