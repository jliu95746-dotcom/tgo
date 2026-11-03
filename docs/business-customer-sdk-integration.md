# 客户业务会话 SDK 接入说明

> 当前实施：3.50（2026-10-13）。SDK 是将聊天窗口或调用接口接入业务页面的代码。本文覆盖访客文字消息；签名、权限、员工评审和订单归属继续由服务器核验。

> **3.47 补充：**服务器签名兑换/解析已固定访客客户归属，换客户必须建立新访客；访问检查、历史/IM 与 SDK 换号生命周期尚未完成统一接线。本文原文字参数/用法不变，不能仅更换回调就认定旧历史隔离。详见[当前剩余任务](business-scenarios-remaining-development-plan.md)。

## 1. 准备与返回格式

商家后台先验证当前登录用户，再按照[签名身份协议](business-customer-identity-integration.md)生成短期声明。**网站首次绑定推荐返回签名声明，由 Widget/小程序把自身持有的访客凭据发给平台交换；商家回调不接收长期访客秘密。**网页/小程序回调可以返回下面的严格两字段数组：

```json
[{"connection_id":"业务连接 UUID","merchant_assertion":"商家已签名的短期声明"}]
```

已有正确客户归属时，可继续返回已经交换的会话（CLI 当前也使用此格式）：

```json
[
  {
    "connection_id": "业务连接 UUID",
    "customer_session": "签名交换返回的 session_token"
  }
]
```

最多 20 个不同连接，每项只包含一种身份的两字段，不能同时有签名和会话；声明最多 16384 字符、会话最多 8192 字符。不能提供自声明的 `customer_id`、权限、员工评审或商户签名密钥。前端不生成商户签名，不以邮箱、平台编号或自定义属性代替验证。

商家可在自己的后台实现一个返回上述数组的接口。下面 `/merchant-api/chat-business-sessions` 是接入约定示例，需要商家实现，不是本项目已提供的接口。商家后台必须从自己的已认证会话取得客户身份，不能接受网页声明的客户编号。

## 2. 网页聊天

升级并使用同一批次的 `tgo-widget-sdk.js` 与 Widget 页面资源。

```js
TGOWidget.init({
  apiKey: '平台 API Key',
  async getBusinessCustomerSessions(context, options) {
    const response = await fetch('/merchant-api/chat-business-sessions', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(context),
      signal: options.signal
    })
    if (!response.ok) throw new Error('当前客户尚未验证')
    return response.json()
  }
})
```

`context` 只有实际注册得到的 `visitorId`、`projectId`、`platformId` 和本次 `sourceMessageId`。每次发送前调用回调；回调结果只在本次受保护的发送请求中使用，不能放在访客属性、URL、消息文本、提示词、长期存储或日志中。没有回调时保留普通聊天；回调可以返回空数组，表示当前没有可用的已验证业务会话。

SDK 自动加载后再显式 `init` 设置回调也支持。首次发送先完成状态握手，不能因为回调注册较晚而静默跳过验证。iframe 来源、宿主页来源、请求编号和原访客元数据都必须匹配；凭据只发送到对应控制 iframe 的准确 origin，不使用 `'*'`。

回调变更使用：

```js
TGOWidget.setBusinessCustomerSessionProvider(newProvider)
TGOWidget.setBusinessCustomerSessionProvider(null)
```

替换/清除回调会使旧待返回凭据和旧发送凭据失效，并通知遵守 `signal` 的后台请求停止。`shutdown()` 同样终止旧桥接；不能自动重放未知请求。

**当前边界：**此方法只处理回调及尚未发送的凭据，不代表商户注销、服务端撤销、访客身份重建、IM 订阅或历史消息隔离已完成。尤其不能把“换回调”当作多个真实商户账号共享同一访客会话的安全方案。完整账号隔离是下一项 P0，真实敏感业务接入仍待验收。

## 3. 小程序

在页面 `selectComponent` 得到现有组件后，通过方法传入回调；不要通过 `properties`、`setData` 或 WXML 传入签名密钥/业务凭据。

```js
var chat = this.selectComponent('#customer-chat')
chat.setBusinessCustomerSessionProvider(function (context) {
  // requestMerchantSessions 由接入方实现并校验当前商户登录。
  return requestMerchantSessions(context)
})
```

回调收到的元数据与网页相同，返回同一数组格式。发送前、IM 就绪后及完成请求前校验原聊天身份和回调修订；更换回调、断开或组件卸载后，旧凭据不能继续使用。旧组件释放回调不能移除后设置的新回调。没有回调的普通消息保持现有行为。

IM 已确认送达后，业务身份/AI 请求失败不会把消息标成未送达并触发重发。业务凭据不会进入 IM 消息、聊天状态、组件数据、访客缓存或失败日志。真实微信开发者工具/设备、商户登录和账号切换完整验收仍待完成。

## 4. 访客 CLI

`chat send --customer-sessions-stdin` 从标准输入读取数组，只用于本次 HTTP 请求；不将业务凭据放在命令参数或 `~/.tgo-widget/config.json`。输入限制为 192 KiB。调用者仍须先完成现有访客初始化。

```powershell
# 以下文件仅为本机短期输入示例，需要自行取得有效会话；不要纳入仓库。
Get-Content -LiteralPath 'E:/local-only/customer-sessions.json' -Raw |
  node repos/tgo-widget-cli/dist/index.js chat send --message '查询订单' --no-stream --customer-sessions-stdin
```

省略 `--no-stream` 时继续请求 SSE；携带客户会话的请求先等待服务器明确的成功完成事件，检查完整结果后再输出，避免未完成草稿或分片凭据进入终端。新 `agent_content_chunk`、`agent_response_complete`、`workflow_completed` 与旧内容事件均支持。中断、失败、缺少完成标记、返回凭据或访客配置变化时不输出业务结果；HTTP 错误保留状态码并清除私密正文。

`TGO_WIDGET_CONFIG_PATH` 可以指定独立的普通访客配置文件，用于受控测试；不改变默认配置路径。这个配置只承载既有普通连接/访客设置，不能保存业务会话。MCP `tgo_widget_chat_send` 没有新增凭据参数，模型不接收这组秘密。

### 4.1 注册和内部渠道（3.48）

网站新注册响应 `visitor_access_token` 为当前访客的持有凭据；SDK 的私人注册缓存保存它，`im_token` 继续用于连接。它们不表示已认证商户客户，不进宿主页上下文、URL、聊天文本、模型或普通输出。短 `merchant_assertion`/`customer_session` 只留请求内存；不要把这些短证明存入访客缓存。

重连请求携带真实 `visitor_id`/`platform_open_id` 和持有凭据；已经关联客户还要当前客户证明。失效普通凭据建立新访客，不能仅凭 public ID 恢复；首次新商户关系还要访客持有因素。网页/小程序新访客在签名交换后再次注册受保护 IM，普通没有回调的聊天保持匿名流程。完整换号、历史、媒体、退出和实际 Gateway 仍待接线/验收。

服务端官方渠道使用 `API_INTERNAL_SERVICE_URL` 下的 `/internal/channel-visitors/register` 与现有 `X-SaaS-Service-Token`；不能用公开注册制造外部渠道身份，内部元数据不包含浏览器令牌。真实商户/渠道和真机验收未完成。

## 5. 验证、发布与剩余项

- Web 28、Mini 7、CLI 5，共 40 项针对性/相关回归通过；包含两端来源、首次握手、替换/取消、私密结果、身份变化和原消息送达等测试。
- CLI 构建产物已通过受控真实 HTTP 的 JSON、SSE 与凭据回显拒绝测试；使用合成会话，临时配置及数据已清理。这证明客户端传输，不等于平台签名、真实商户或模型业务查询验收。
- Widget 的 TypeScript/Vite 构建、CLI 的类型检查/构建、小程序的 ES5 构建通过。通用工程脚本中这三个服务通过，其他既有 Python 检查和 Web 缺 `yarn` 的失败仍存在。
- 暂缓真实订单/物流联调；未配置商家签名回调、未启用真实员工、未部署云端。页面风格未改变，不把构建或 VM 事件测试记为正式浏览器/真机验证。
- 下一步先解决商户账号变更时的完整访客/会话/历史/IM 隔离及签名撤销，再补员工辅助、其他应用/OpenAI 兼容/官方渠道和持久任务入口；最后接固定发布版本、统一运行记录和工作流样板。其他原计划与 G0～G5 完整保留。

## 6. 公开结束访问与实时凭据（3.49）

网站自己的访问结束接口：

```http
POST /v1/visitors/access/end
Content-Type: application/json
```

```json
{
  "platform_api_key": "当前网站平台 key",
  "visitor_id": "原访客 UUID",
  "visitor_access_token": "仅由 SDK 持有的原访问秘密"
}
```

成功示例（JSON 字段含义固定，清理状态视实际回执）：

```json
{"status":"ended","upstream_stop_status":"pending","im_disconnect_status":"requested"}
```

- `ended`：原访问已持久关闭，旧归属/绑定、open 会话、running/waiting 记录已终止；结束后的原访问不能恢复。重复提交原参数可查询/重试原清理，不增加修订，也不重新启动任务。
- `upstream_stop_status=pending`：正在等待原回复任务的停止回执，缺记录/服务故障/超时均不算确认。只有原 registry 给出取消/完成回执时才为 `confirmed`；这个字段不代表整个业务发布路径已经开发完。
- `im_disconnect_status=requested`：已请求替换旧上游凭据并踢出原设备，不能仅凭此宣称每个客户端都已断开；请求未确认时为 `pending`。
- 结束只要求自己的访问秘密，不要求仍有效的业务证明；不能用平台 key、公开 ID、商户签名或他人的秘密结束访问。原资源停用仍允许结束自己，不提供数据读取。秘密只放请求正文，响应 `no-store`；不得放 URL、宿主安全上下文、模型或日志。
- 换号/退出/卸载先冻结当前 UI/发送、递增代次、关闭旧订阅与 IM，再以原访客快照结束访问并生成新访客。清理未确认时显示待确认，重查原请求；迟到包不交给新身份。**该段为 3.49 的历史状态；3.51/3.52 的当前源码接线和未验收边界见第 8 节。**

网站访客在非 SaaS 模式也必须使用注册返回的签名 `im_token` 和 `/v1/wukongim/route` 返回的网关，不能取私有上游 HMAC 或凭原 ID直连恢复。每次实时操作检查当前访问修订与原客户引用；令牌不延长原客户证明到期。非网站渠道保持已验证服务端接入。

发布时须配置现有 `SAAS_IM_PUBLIC_URL`（客户端可达的受控 WS/WSS 入口）及 `SAAS_IM_UPSTREAM_URL`（仅 API 可达的消息服务入口），即使普通网站处于非 SaaS 模式。Windows 本机 helper 默认前者 `ws://127.0.0.1:18000/v1/wukongim/ws`、后者 `ws://127.0.0.1:5200`；可用 `NATIVE_IM_PUBLIC_URL`/`NATIVE_IM_UPSTREAM_URL` 指定本机值。这些 localhost 地址只用于开发，不复制成云端地址；部署仍要验证原客户端协议、私有端口与旧凭据断口。本批原生直连认证控制没有明确回执，未记完成。

本批只验证后端与本机网络/SDK 构建；正式浏览器、小程序真机、完整身份生命周期和生产尚未验收。详细状态见[实施进度 3.49](business-scenarios-development-progress.md)和[剩余计划 4.2.16](business-scenarios-remaining-development-plan.md)。

## 7. 只用于原退出的凭据与 CLI（3.50）

新授权注册会返回私密 `visitor_end_token`。它由 API 签名绑定原访客/企业/平台、访问修订和原持有者摘要，默认 30 天；与读取/IM/customer session 分开。后台统一访问检查、注册和 IM 不接受这个用途；旧仅 ID 的历史/媒体等接口仍需继续接 R3，不能据此宣称所有数据入口已经保护。

退出和未知结果重试优先只发：

```json
{"platform_api_key":"原网站平台 key","visitor_id":"原访客 UUID","visitor_end_token":"原注册发出的退出凭据"}
```

先删除活动读取/IM 缓存，再保存 end grant 与原定位用于重试，不保留 `visitor_access_token`、`im_token`、`customer_session` 或商家声明。原访问秘密仍可在一次结束请求中使用以兼容旧调用方；新客户端缺 end grant 的旧缓存不能伪造退出或证明恢复。`pending` 继续待确认，不能写成已停止。grant 过期、未知新注册及存储/恢复异常仍在后续收口。

网页与小程序已提供 `services/visitorLifecycle` 的 `endCachedVisitorAccess`、`retryPendingVisitorEnds`，按原 API/平台保存 end-only 记录；**这段是 3.50 的历史状态；当前 App/store/小程序组件联动见第 8 节，尚未发布生产。**

访客 CLI 的当前可用操作：

```powershell
node repos/tgo-widget-cli/dist/index.js session end
node repos/tgo-widget-cli/dist/index.js session retry-end
node repos/tgo-widget-cli/dist/index.js reset
```

`session end` 清活动读取/IM 字段并请求原服务器结束；`retry-end` 重查原 pending，不能恢复历史/重新启动业务。`reset` 保留 end-only 待确认记录。有效的新 `init` 响应将替换不同原身份时，先结束原配置的访问；无效注册不抹去原配置。未确认状态不会打印成已确认，不输出任何私密 token。新服务器必须先支持此协议；若存在 end-only pending，回退不能只回到不认识 grant 的旧 API。

本轮 API/VM/构建/本机网络与实际 CLI 有证据；网页真浏览器、微信真机、完整账号生命周期、全部私密数据入口和生产尚未验收。详见[进度 3.50](business-scenarios-development-progress.md)和[剩余计划 4.2.17](business-scenarios-remaining-development-plan.md)。


## 8. 组件生命周期接线（3.51/3.52）

网页：已有可信宿主 `STATE` 修订/启用变更订阅，首次注册前捕获并在响应/缓存/IM 初始化时核验；App/store 接变更、pagehide/pageshow 和卸载。先清旧消息、身份、历史、上传、输入和图片预览，再以原 end-only grant 结束旧访问、生成新访客。只有原服务器明确返回 `409` 且 `error.details.reason=visitor_owner_changed` 才按新客户重建；声明重放、网络错误、过期或其他冲突没有这个许可。

小程序：在组件 attached 后用实例方法设置回调，业务凭据不进 attributes、properties、setData、URL 或持久聊天状态。回调函数没有改变但宿主已换账号时，宿主必须显式通知；不要让旧客户的回调返回新客户数据而不改变修订。

```js
// chat 为宿主通过 selectComponent 取得的当前 <tgo-chat> 实例。
chat.setBusinessCustomerSessionProvider(function (context) {
  return getSignedCustomerIdentityForCurrentAccount(context)
})

// 在宿主登录/退出/切换账号完成、当前回调会读新账号时调用。
chat.notifyBusinessCustomerChanged()

// 宿主不再提供业务身份时设置 null；原受保护访问结束后另建访客。
chat.setBusinessCustomerSessionProvider(null)
```

`getSignedCustomerIdentityForCurrentAccount` 是宿主已有的当前账号签名回调，不是 SDK 内置函数；返回格式沿用第 1～4 节。签名应由宿主服务端签发，不能由浏览器/小程序自行声明客户身份。

小程序组件接页面 hide/show、detached 及 API/平台属性改变；旧组件释放不结束新组件。store 先增加 `sessionRevision`、清原 UI/身份/历史/流式状态、解绑原 IM 并中止上传；旧注册、历史、选图、上传和排队回调只属于旧访问，不对新账号生效。清理失败仍拒绝继续，end 回执 pending 仍待确认，不等于上游已经停止。

**3.52 历史证据：**Web **47**、Mini **35** 项源码/VM 竞争及原功能回归通过，构建通过；本机 API/end/双 WS/实际 CLI 有独立证据。没有真实 Web 渲染或微信真机验收，没有发布云端。所有旧仅 ID 的 history/media/file/chat/cancel/SSE 数据入口还需 R3，持久清理恢复/CLI 多进程及原生直连控制也未关闭；不能把本节解读为全隐私边界或生产交付完成。

查看[进度 3.51～3.52](business-scenarios-development-progress.md)和[剩余计划 4.2.19](business-scenarios-remaining-development-plan.md)。


## 9. 历史、附件和上传当前授权（3.53）

仅 platform_api_key、visitor_id 或文件 ID 不再足以读取网站访客私密数据。新 Web/Mini/CLI 在注册完成后，私下从原缓存/config 捕获原 IM grant，以 `Authorization: Bearer <original im_token>` 发送，不放 JSON、查询参数、HTML、properties 或 setData。平台 Key 只标识入口；不能用新的默认访客/另一个账号替换原请求持有者。

| 路径 | 需要的原权限 / 输出 |
|---|---|
| POST /v1/visitors/messages/sync | 原访客签名 grant + 原平台 Key + 自己的 1/251 会话；异步补拉后再检查，无授权/失效 401，批次会话不一致安全 502，成功 no-store |
| POST /v1/chat/upload | 原访客 grant 或当前员工 JWT；读文件/存储 await 后检查，撤权不持久化或返回链接 |
| POST /v1/chat/files/{id}/access | 当前原访客或员工的会话权限，返回单文件短期 access_url/expires_at；无原 IM token |
| GET /v1/chat/files/{id} | 当前原授权，或上述文件专用 file_token；最长五分钟且不超过原授权到期；原访问结束/Key 变化/客户权限撤回后链接拒绝，云读取和字节块发送前复查 |

Web/Mini 的图片/文件和 Markdown 地址只向配置 API 申请续期，历史消息自带域名不作为凭据发送目标。显示状态只保存文件专用链接，不保存读/IM grant；换账号/卸载后旧异步结果丢弃，失效时不回退到平台 Key 下载链接。访问服务器与客户端需作为同批升级，旧 Key-only 客户端先同步协议，不能为兼容恢复旁路。

访客 CLI 的 chat history/upload 验证原 server/Key/visitor/token/channel，服务器或 Key 覆盖不匹配会在网络请求/文件字节发送前拒绝；测试和实际联调使用独立 TGO_WIDGET_CONFIG_PATH，没有改真实用户配置。文件专用 token、end-only token 均不能作为历史或 IM token。

本节仅覆盖本批历史/文件/上传和三种消费者。chat/cancel/SSE/媒体/UI actions/其余私密入口、iOS/Flutter 与正式浏览器/微信真机仍待接线/验收；没有云端发布。开发顺序见[剩余计划 4.2.20](business-scenarios-remaining-development-plan.md)，证据见[进度 3.53](business-scenarios-development-progress.md)。


## 10. 聊天与取消原授权（3.54）

网站聊天必须先完成原访客注册，随后把注册响应的原 `im_token` 仅放在请求头 `Authorization: Bearer <original im_token>` 中。平台 Key 仍用于选择原入口；不把 IM/end/file grant 写入 completion JSON、URL、IM payload 或可见组件状态。

| 路径 / 调用者 | 当前契约 |
|---|---|
| POST /v1/chat/completion 网站文字/媒体 | JSON 中保留原 api_key/from_uid/channel_id，header 发送原 grant；仅原 251 会话。进入上游、写记录、await 返回及每个输出前核验原访问 |
| POST /v1/chat/completions OpenAI 格式 | X-Platform-API-Key + 原 Authorization；user 若有必须属于原访客，缺省使用已注册持有者；无凭据不会隐式创建新访客 |
| POST /v1/ai/runs/cancel-by-client | 原 Authorization + JSON 原 platform_api_key/client_msg_no；只取消原当前客户的同一任务。没有 grant 401、外任务安全 404；HTTP 成功仍要求明确取消回执，pending 不能显示“已停止” |
| 非网站消息渠道 | 使用原平台 Key + 服务端 X-SaaS-Service-Token；Httpx/Slack caller 已同步。内部 token 只能留在后端，不发布到网站或移动端；不能代替网站原访客 grant |

Web 文字、图片/音频回复和取消；Mini 文字/媒体同一 completion 与取消；CLI JSON/SSE 已接以上协议。每个异步响应/消息在采用前核验原身份，凭据回显拒绝。CLI 有客户私密数据或原访客 grant 时只输出核验后的最终答复；普通人工/辅助/排队/转人工输出受理事件，不将其说成 AI 回复成功。没有最终完成的流、失败或拆开的凭据回显不输出答复。

浏览器 EventSource 不支持 POST 自定义 Authorization；本接口使用 fetch POST，流式消费使用 ReadableStream。所有客户端只向配置 API 发送原凭据。服务端与所有受支持客户端需同批升级；iOS/Flutter、其他媒体/UI actions/插件、正式页面/真机仍需接线与验收，不能为旧 Key-only 客户端恢复旁路。

本批验证及下一步见[进度 3.54](business-scenarios-development-progress.md)、[剩余计划 4.2.21](business-scenarios-remaining-development-plan.md)。没有真实商户/模型或云端发布证据；后台原授权在同一进程内传递，持久恢复独立开发。


## 11. 资料显示与活动、员工插件范围（3.55）

| 路径 / 用途 | 当前授权与响应 |
|---|---|
| GET /v1/visitors/{id}/basic 网站 | 原平台 Key + 原 Authorization；只允许当前访客本人，返回 id/name/display_nickname/avatar_url/is_online，不包含 ai_settings 或内部技能字段；成功 no-store |
| GET /v1/visitors/{id}/basic 员工 | 当前员工 JWT，原公司与现有客服归属规则；保持员工基本资料格式，不授予跨企业读取 |
| GET /v1/channels/info 网站 | 原平台 Key + 原 Authorization；自身 1/251 显示、自己的已关联客服或平台明确关联 Agent。公开 name/avatar/channel/entity 字段、extra=null；不能读取其他客户或任意 Agent 配置。Key-only 拒绝，成功 no-store |
| POST /v1/visitors/activities | 原平台 Key + 原 Authorization，body visitor_id 与活动 id 属于原访客；客户端 page_url/title 只作为该访客的活动资料，不配置平台来源 |
| 插件 visitor-panel、chat-toolbar/通用 render/event、tool execute | 保持员工 JWT 用途，原企业与当前客户/会话范围，数据库客户资料为准；客户端 visitor 对象/ID/session 不提供额外权限。上游调用前后和响应字节前复查，网站 grant 不能代替员工登录 |
| POST /v1/chat/ui/action | 已弃用的员工内部 AI 操作，仅 1 类型经过核验的 Agent 会话，agent_id 必须与 channel 一致；后台保留原员工授权。不是新增网站访客动作接口 |

Web 渠道/活动与访客 CLI 已同步请求头及结果当前性检查。渠道请求 URL 不放原读取/IM token，Web 只采用公开显示字段，不回退到 extra 中的内部 metadata；活动 visitorId 必须与原缓存一致。CLI channel info/activity record 必须匹配原 server/Key/visitor/token/channel 配置，缺凭据或服务器覆盖不匹配时不发送请求；迟到/回显的原凭据不输出。

头像及其他媒体/元数据、iOS/Flutter、全部消费者与正式 UI/真机仍有独立任务；原短期 file_token 仍只用于原附件，不能转作头像/资料/聊天 grant。服务端和受支持消费者同批升级，不能恢复 Key-only 旁路。插件调用的权限重查只拦截请求和输出，不等于已确认正在执行的副作用停止；恢复/确认按 R2/D4 继续开发。

证据与后续顺序见[进度 3.55](business-scenarios-development-progress.md)、[剩余计划 4.2.22](business-scenarios-remaining-development-plan.md)。源码、本机测试与正式页面/生产分开；没有本批线上发布。

## 12. 客户本地头像：请求头读取与具体版本（3.56）

| 用途 | 契约 |
|---|---|
| 员工 GET `/v1/visitors/{id}/avatar` | 原员工 Authorization，当前企业与客服归属规则。图片 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`，不匿名开放 |
| 网站访客 GET 同路径 | 原 `Authorization: Bearer <im_token>` + `X-Platform-API-Key`，仅原访客自己的头像。平台 Key 单独、end-only、其他客户与失效授权拒绝 |
| 新上传版本 | staff POST 同路径，JPEG/PNG/GIF/WebP 最大 5MB；URL 保存 `?v=具体文件名`，v 仅图片版本。更新后旧 v 返回 404；旧无 v 记录只在一个明确文件时兼容 |
| 浏览器消费 | 对配置 API fetch header 获取图片 bytes/Blob，再用本地 object URL 显示；原授权不得放图片 URL。不跟随 redirect、不缓存；原身份在请求与读取 body 后均匹配，失败/换号/退出/到期丢弃并 revokeObjectURL |
| 其他消费者 | 若需要客户本地头像，应按相同 header 契约下载图片。Website Widget/Mini 当前不渲染客户头像，iOS/Flutter 及外部集成接线和真机另行验收；不得恢复匿名/Key-only 兼容旁路 |

员工 Web 共用头像组件已接，视觉样式不变。收到的头像 URL 仅作为客户 ID/版本定位，不向其附带的域名发送登录凭据；不采用其 token/file_token/query 授权。页面隐藏、原令牌到期或换号释放/遮住图片；可见时和每 30 秒重新查授权，失败清除，不能宣称已下载图片可被服务端即时远程收回。员工/品牌公开头像与第三方提供的 URL 不在本批本地客户图片范围。

本地读取每个响应块前复查原身份、客户归属、绑定版本和文件状态；上传等待后复查，失败/取消只清理本次新文件，成功换图仅删除原绑定文件。未知旧文件、跨进程竞争、清理失败/进程崩溃恢复继续按 R2 开发。没有新图片凭据发行体系，附件 file_token 不能读取头像。

证据见[进度 3.56](business-scenarios-development-progress.md)、[剩余计划 4.2.23](business-scenarios-remaining-development-plan.md)。服务端与消费者需协调升级；当前只完成本机源码和上述测试，没有本批线上发布或正式浏览器/真机验收。

## 13. 注册最小响应与员工私密资料（3.57）

`POST /v1/visitors/register` 继续使用原网站平台 Key；续期必须带原访客访问凭据，已关联业务客户须提供当前客户证明，原授权结束后不能续期。响应从员工资料模型独立出来，以下是返回字段清单：

- 身份：`id/project_id/platform_id/platform_open_id`。
- 显示：`name/nickname/nickname_zh/display_nickname/avatar_url/is_online`。
- 时间：`created_at/updated_at/first_visit_time/last_visit_time/last_offline_time`。
- 连接和授权：`channel_id/channel_type/im_token/visitor_access_token/visitor_end_token/business_protected/business_access_expires_at`。

员工内部备注、联系方式、企业/职位、来源/IP/系统资料、自定义属性、标签、AI 分析/配置不在注册返回中；客户端提交资料的既有请求契约保留。新模型不继承员工完整模型，未来新增员工字段不会自动进入网站响应。必要凭据仅由经过原网站/企业/Key、访客当前访问/原业务引用校验的注册回应发出，headers/body 前复查，响应 `no-store`。

| 员工入口 | 当前契约 |
|---|---|
| GET `/v1/visitors` | 原员工 Authorization + 当前 `visitors:list`，现有企业/客服范围；返回 ID 批量复查，成功 no-store |
| GET `/v1/visitors/{id}`、`/{id}/basic` 与 `/by-channel` | 原员工 + 当前 `visitors:read`、真实客户/企业/归属；保留完整员工详情格式，无效/缺失/跨企业客户 404，不生成默认客户 |
| GET `/v1/visitors/{id}/humanization-settings` | 同一原员工、读取权限与目标；调用 upstream 前后与输出前复查，无权不能调用 |
| GET `/v1/channels/info` 客户内部 metadata | 员工的客户渠道沿用当前 `visitors:read` 与归属；网站访客仍仅使用第 11 节的公开显示响应，无内部 extra |

Widget 响应类型已同步；现有 Mini/CLI 的缓存和初始化依赖字段保留，实际访客 CLI 初始化/后续数据请求已本机验收。iOS 当前公开字段和 Flutter nullable 资料字段在源码上兼容，完整授权生命周期与它们的构建/真机另行验收；不能将源码兼容算作可发布。

原平台换 Key/停用、客户修订/业务引用失效，不能在等待接入后改用新平台发凭据；员工 token/账号/权限/角色/企业或归属变化会阻止旧回应。已有远端接入、通知或提交的副作用，必须依 R2 的持久清理/回执独立确认。写入/派单/AI 模式、其他内部与媒体入口仍待开发。证据见[进度 3.57](business-scenarios-development-progress.md)、[剩余计划 4.2.24](business-scenarios-remaining-development-plan.md)。本批仅本机验证，没有线上发布。

## 14. 员工客户写入与手动接入（3.58）

沿用员工 `Authorization`，服务端捕获原员工/企业/操作权限与原客户/平台/版本，客户端不能自报扩大范围。当前覆盖八个入口：

| 入口 | 规则 |
|---|---|
| POST `/v1/visitors` | 当前 visitors:create，有效本企业平台；原权限与平台在插入前锁定/重查，重复 open_id 拒绝，不恢复软删除记录 |
| PUT `/{id}/attributes` 与 PATCH `/{id}` | 当前 visitors:update、真实未删除客户、原企业/平台与客服归属；属性不再 upsert/恢复或转未知平台。客户版本变化 409，缺失/外企/删掉/转交范围不符 404 |
| POST `/{id}/enable-ai`、`disable-ai` 与 PUT `/{id}/service-mode` | 同一 update 范围；专用 service-mode 异步技能校验只处理独立对象，检查结束后核验原权限/归属/版本再提交 |
| DELETE `/{id}` | 当前 visitors:delete 和原真实目标，锁内软删除；保留无 body 的 204/no-store，不因自己删掉记录误判响应失败 |
| POST `/{id}/accept` | 当前 visitors:update，既有未接入客户的例外；只分配给原当前员工，原作用域进入实际 transfer 事务及后续 dispatch。已接入/状态变化拒绝，指定员工失效不能回退其他人/LLM |

提交时客户行 `FOR UPDATE`，原员工与生效权限共享锁维持提交阶段的授权。权限/企业/平台/目标/归属变化会拒绝，服务端 updated_at 不同返回 `409 / VISITOR_STATE_CHANGED`；响应和通知前后仍核验原授权与提交版本。客户端应刷新当前客户状态后判断下一步，不能将通知失败/旧回应被拒绝当作原操作必定没提交并盲目重做。

如果撤权发生在分配/修改已提交之后，旧私密回应被拦截，已提交的合法状态保留。远端订阅/通知已经接受、停止和重启恢复的确认，需要 R2 持久回执；本批没有“接口失败即撤销远端”的保证。通用幂等恢复、标签/其他队列/转接/关闭、所有消费者及旧 PATCH 与专用模式的语义校准仍待完成。

实际本机 HTTP/CLI 与 PostgreSQL 证据见[进度 3.58](business-scenarios-development-progress.md)、[剩余计划 4.2.25](business-scenarios-remaining-development-plan.md)。接口形状、聊天帧与现有 UI 风格保持；正式浏览器/微信/iOS/Flutter/原生协议及云端未验收/未发布，真实商户订单/物流继续暂缓。

## 15. 员工客户标签和旧队列原操作范围（3.59）

| 入口 | 当前契约 |
|---|---|
| POST `/v1/tags/visitor-tags` | 原员工Authorization、tags:create、现有客户归属和真实本企业标签。201返回原VisitorTagResponse，无新客户端授权字段；活动重复400，软删除关联不隐式恢复 |
| DELETE `/v1/tags/visitor-tags/{id}` | 原员工tags:delete和原关联/客户范围；204。锁内仍必须是原关联ID/版本，不能删除重建记录 |
| DELETE `/v1/tags/visitors/{visitor_id}/tags/{tag_id}` | 同一删除规则，服务器解析一次并保留原association ID；不能靠同名目标采用后来的关联 |
| POST `/v1/visitor-waiting-queue/accept` | 原员工visitors:update且既有队列入口权限；固定原waiting entry/最新open session/客户平台/版本，未分配/未过期。仅接给原员工，重复404/409或现有拒绝，指定员工失效不回退；成功JSON原形状 |
| POST `/v1/visitor-waiting-queue/{entry_id}/cancel` | 现有管理员/企业规则；当前原等待/原未分配会话，固定原目标版本；取消不能关闭新会话，返回原success/message/entry_id形状 |

成功响应均no-store。撤权401/403，缺失/外企/已删除/客服范围不符404，目标状态/版本变化409 `VISITOR_STATE_CHANGED`；客户端刷新并核验当前结果，避免在已提交但通知/回应失败时盲目重试。提交前固定结果版本，后续refresh不扩大原输出范围；每个外部dispatch及回应边界复查。

归属限制复用已有规则：SaaS普通客服限当前分配客户，管理员及单企业共享模式按现有权限。当前常驻本机false未改；true下归属拒绝使用独立实际HTTP/原JWT/PG验证。没有新角色/登录体系，前端布局不变。标签目录、其余session关闭/转接、后台重试、全部消费者和R2幂等回执仍继续。

证据见[进度3.59](business-scenarios-development-progress.md)、[剩余计划4.2.26](business-scenarios-remaining-development-plan.md)。没有新SQL迁移、Git提交推送或云端发布，正式页面/原生协议/真机和真实商户仍待验收。
