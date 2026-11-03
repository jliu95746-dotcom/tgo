# 商户客户身份接入说明

> 2026-10-12；对应实施进度3.34。以下身份配置、交换、查询元数据及撤销接口已有实现。身份会话不是员工启用或业务执行授权；正式订单查询接线继续按D2-B/D推进。

## 1. 谁提供真实客户身份

商户服务端在客户登录后，从自己的已认证会话读取客户编号，再签发身份声明。客户自己填写的客户编号、手机号、邮箱、`platform_open_id`或访客附加属性不作为业务身份依据。

平台验证签名、企业、连接、渠道、访客、规则版本及有效期，保存加密客户绑定，返回独立业务会话凭据。正式调用必须携带并重新核验这份凭据；不能根据访客编号自动选取其他会话的绑定。

商户登录实现和实际接口仍需业务方联调确认，平台不能从一份签名推断商户正确实施了登录校验。

## 2. 管理员配置

本机管理端：`http://127.0.0.1:5173/ai/tools?tab=connections`。在真实连接卡片打开“客户身份验证”：

1. 选择同企业已启用的客户渠道。
2. 配置独立的、至少32字节的随机商户签名密钥。密钥仅保存在商户服务端及平台加密保管中；不写入客户网页、流程定义、提示词、日志或仓库。
3. 设置业务会话最长有效期，60～3600秒，默认900秒。
4. 保存后将界面显示的规则版本配置到商户服务端。

原密钥不会回显；编辑时留空沿用，输入新值轮换。每次保存更新规则版本，旧身份会话需重新验证。业务连接配置修订变化后，需核对商户/身份规则并重新保存。

| 接口 | 身份要求 | 作用 |
|---|---|---|
| `GET /v1/business-connections/{id}/customer-identity-policy` | 当前企业管理员 | 读取无密钥配置及版本 |
| `PUT /v1/business-connections/{id}/customer-identity-policy` | 当前管理员及配置写权限 | 创建、修改、轮换或停用；用`expected_revision`防覆盖 |
| `GET /v1/business-connections/{id}/customer-identities` | 当前企业管理员 | 读取身份会话状态；不返回客户编号或凭据 |
| `POST /v1/business-connections/{id}/customer-identities/{binding_id}/revoke` | 当前企业管理员 | 按原修订撤销身份；可用于停止继续核验 |

新策略`expected_revision=0`；已有策略使用读取的当前修订。保存结果未知时先读取当前配置，不盲目再次轮换密钥；读取最新配置不保证它就是某次未知写入的回执。

## 3. 商户签名协议

声明是UTF-8 JSON对象，所有字段必需，拒绝未知或重复字段。

| 字段 | 类型与来源 |
|---|---|
| `purpose` | 固定`business_customer_identity` |
| `project_id` | 平台企业UUID，由商户服务端固定配置 |
| `connection_id` | 当前企业业务连接UUID，由服务端固定配置 |
| `platform_id` | 已授权渠道UUID |
| `visitor_id` | 本次客服访客UUID；仅用于限定接待上下文，不证明商户身份 |
| `customer_id` | 商户已认证会话中的客户编号，1～128字符，符合平台标识规则 |
| `nonce` | 每次新验证生成的UUID；重试保留原值及原内容 |
| `policy_revision` | 当前规则版本，正整数 |
| `issued_at` | Unix时间，单位秒，整数 |
| `expires_at` | Unix时间，单位秒；晚于签发，不超过策略最长有效期 |

签名格式：

```text
payload = base64url(UTF8(JSON对象))，无末尾等号
signature = HMAC-SHA256(商户密钥, UTF8("tgo-customer-identity-v1:" + payload))，小写十六进制
assertion = "v1." + payload + "." + signature
```

签名直接覆盖编码后的正文，因此不要求不同语言生成同样的JSON键顺序。平台按解析后的声明内容保存受保护的摘要，不保存原签名声明或明文客户编号。

第一次交换要求签发时间在服务端当前时间前60秒、后30秒内，且声明未过期。已有相同`nonce`的成功记录可在原有效期内恢复；不延期。不同内容、已撤销、已过期或旧规则不能用相同编号重新激活。

### 服务端Node示例

此函数只能由商户后端调用。`verifiedCustomerId`必须来自后端已认证会话；固定配置不得由客户请求覆盖。

```js
import { createHmac, randomUUID } from 'node:crypto';

export function createCustomerAssertion(verifiedCustomerId, config, visitorContext) {
  if (!verifiedCustomerId || Buffer.byteLength(config.signingKey, 'utf8') < 32) {
    throw new Error('Customer identity signing is not configured');
  }
  const issuedAt = Math.floor(Date.now() / 1000);
  const claims = {
    purpose: 'business_customer_identity',
    project_id: config.projectId,
    connection_id: config.connectionId,
    platform_id: visitorContext.platformId,
    visitor_id: visitorContext.visitorId,
    customer_id: verifiedCustomerId,
    nonce: randomUUID(),
    policy_revision: config.policyRevision,
    issued_at: issuedAt,
    expires_at: issuedAt + Math.min(config.sessionSeconds, 900),
  };
  const payload = Buffer.from(JSON.stringify(claims), 'utf8').toString('base64url');
  const signature = createHmac('sha256', config.signingKey)
    .update('tgo-customer-identity-v1:' + payload, 'utf8')
    .digest('hex');
  return { assertion: 'v1.' + payload + '.' + signature, nonce: claims.nonce };
}
```

示例只说明协议，不代表实际商户登录或接口已验证。重试使用上次完整声明，不能再次调用函数换`nonce`；需要新验证时才生成新声明。

## 4. 交换与凭据使用

```text
POST /v1/business-connections/{connection_id}/customer-identities/exchange
Content-Type: application/json
正文字段：merchant_assertion；网站首次归属同时提供实际持有的 visitor_access_token
```

此接口验证商户签名，不以平台普通登录Token作为替代。首次网站归属同时校验真实访客持有因素，签名正确也不能采用一个不持有的已知访客 ID。推荐商家只签声明、由聊天客户端把原访问秘密发给平台交换，长期秘密不交商家回调。服务端渠道通过已认证内部 `/internal/channel-visitors/{connection_id}/customer-identities/exchange` 核验原签名；公开不能输入 `trusted_channel` 绕过。成功返回绑定编号、企业/连接/渠道/访客、修订、有效期及`session_token`；响应`Cache-Control: no-store`。回复`scope=business_customer_identity`、`can_enable=false`。

- 声明是短期身份证明，业务会话Token是独立的平台签名凭据。商户签名密钥、普通员工JWT及Plugin配置测试Token不能代签业务会话。
- 业务会话不含明文客户编号。平台以私有保管密钥派生独立签名密钥，并按原绑定与当前规则核验；商户没有该签名密钥。
- 客户凭据仅放请求内存/受保护会话，不能写入对话文本、LLM提示词、普通本地缓存、URL或审计正文。
- 配置/密钥轮换、规则停用、身份撤销、到期、企业/渠道/访客不可用或连接配置变化都会使原绑定不可核验。
- 撤销和取消不会声称已经撤回远端发生的操作；正式业务执行还需要自己的授权、结果检查和动作回执。

### 4.1 访客文字咨询接入（实施 3.44）

商户后端根据已登录的真实用户及经过核验的访客/平台关系签发原声明，再交换 `session_token`。不得直接相信浏览器随意填写的商户客户编号或其他访客编号。拿到会话后，原访客文字 API 请求增加以下可选字段（下例仅为字段示意）：

```json
{
  "api_key": "PLATFORM_API_KEY",
  "from_uid": "CURRENT_PLATFORM_USER",
  "source_message_id": "CURRENT_MESSAGE_ID",
  "message": "查询我的订单",
  "stream": true,
  "customer_sessions": [
    {
      "connection_id": "CONNECTION_UUID",
      "customer_session": "SESSION_TOKEN_FROM_SIGNED_EXCHANGE"
    }
  ]
}
```

发送到 `POST /v1/chat/completion`；同步 `stream=false` 与后台 `wukongim_only=true` 使用同一字段。不得把 token 放在 `extra`、消息文本、URL、提示词、长期浏览器存储或执行日志。最多 20 个不同连接；当前此字段只用于文字请求，媒体业务上下文仍待接入。API 请求 DTO 的公开序列化会省略这组秘密，发送方应在受保护的请求构造中显式提供上述原始字段。

API 从已经保存的原请求及唯一打开的真实访客会话签发最多 300 秒的内存上下文，且不延长商户原会话；绑定原文本指纹、原请求与回复尝试。它经独立私有头传给 AI，私有所有者核验当前访客/渠道/会话/尝试及客户绑定，发布前再次核验。它不包含明文商户客户编号、不进入 LLM/公开响应；后台或请求失效时不能自行重建客户证明。

**当前边界：**上述访客 API、私有校验及 AI 准备接线已实现；员工正式订单/物流工具、连接操作授权和实际商户/SDK/其他渠道仍待完成。有效客户会话或这份上下文不表示订单岗位可启用，也不表示查询已执行。旧按普通平台编号/可编辑属性查询订单的路径已停止。

### 4.2 正式员工查询的当前边界（3.45）

3.46 已补网页、小程序与访客 CLI 的文字凭据传输；接入及准确边界见[客户业务 SDK 说明](business-customer-sdk-integration.md)。回调与凭据失效不等于商户账号切换后的完整历史/IM 隔离，后者仍为待开发 P0。

访客文字客户上下文现已接入员工实际 Agno 工具。模型仅提供订单号；原员工、服务渠道、连接/操作、当前业务评审、商户客户身份和真实会话由服务器核验。查询前后、返回 API 和答复发布均检查原依据，查询期间关闭会话、转人工、替换回复尝试或撤销权限会阻止结果继续发布。订单/客户凭据不进入通用工具日志，流式工具事件不重复传播完整订单号。

正式私有协议为 AI → API `/internal/ai/business-execution/query` → Plugin `/business/execution/query`；适配器通过 API 私有连接解析和 AI `/api/v1/agents/business-execution/authorize` 核验原员工授权。平台 API Key、浏览器登录或模型参数不能直接访问这些接口；公开 API 没有注册内部解析/授权路由。配置测试和员工测试仍不能替代当前正式评审，演示连接不能正式查询。

岗位仅在正式协议可用、订单与物流两项当前业务测试/评审全部通过且其他依赖完整时允许进入原启用确认流程，不自动启用。真实商户和真实模型完整查询仍待验收；SDK、员工辅助、其他应用/官方渠道及持久任务入口还需逐项接线，未带可信客户证明的入口继续拒绝订单/物流工具。前节 3.44 的未接线说明保留为历史，以本节和剩余计划 4.2.12 为当前边界。

### 4.3 固定客户归属及访问接线状态（3.47）

同一项目/访客/业务连接固定一个已核验客户，绑定到期或撤销不释放归属；签名正确也不能改成另一客户，退出永久结束后应建立新访客。历史无归属记录先拒绝解析，收到新有效签名后核对历史同一客户；超过 200 条、混合客户或当前访客超过 20 个连接时要求新访客，不自动确认旧记录。

`0051_visitor_business_scope` 增加归属表和访客访问字段，已在本地备份、实际恢复核对后升级；有保护记录禁止降级删除。新访问检查和结束服务已有测试，但注册/历史/附件/SSE/IM 与 SDK 换号尚未接入，不能据此认定现有访客数据已完全隔离。当前详细开发顺序见[剩余计划 4.2.14](business-scenarios-remaining-development-plan.md)，正式令牌/SDK 参数仍按现有公开契约。

## 5. 交付与回退

身份基础迁移为API`0050_business_customer_identity`，创建签名策略及客户绑定表；后续 `0051_visitor_business_scope` 增加固定归属和访客访问字段。秘钥、客户编号分别使用独立AAD命名空间加密。交换和修改按连接/策略/访客锁及唯一`nonce`处理，跨进程重试读取原记录。

有记录时迁移降级拒绝删表。回退代码保留表、身份回执及API保管密钥；更换保管密钥先制定重加密/恢复方案，不能直接替换丢弃。

原身份基础、访客上下文和 3.45 正式工具已有自动及本机 PostgreSQL/HTTP 证据；没有真实商户登录/订单、正式模型完整查询、正式浏览器或生产验收。下一步补其他入口/SDK/队列与真实执行证据，再接固定发布版本、统一运行记录和澜月样板；旧全局业务查询路径不会因此取得授权。
