# tgo-widget-miniprogram

TGO 访客聊天微信小程序组件。开发者 3 步完成接入。

## 快速接入

### 1. 安装

```bash
npm install tgo-widget-miniprogram
```

在微信开发者工具中点击 **工具 → 构建 npm**。

### 2. 页面配置

```json
// pages/chat/chat.json
{
  "usingComponents": {
    "tgo-chat": "tgo-widget-miniprogram/chat/index"
  },
  "navigationBarTitleText": "在线客服"
}
```

### 3. 使用组件

```xml
<!-- pages/chat/chat.wxml -->
<tgo-chat api-key="ak_live_xxx" api-base="https://your-api.com" />
```

### 跳转到客服页

```js
wx.navigateTo({ url: '/pages/chat/chat' })
```

## 域名白名单

在小程序管理后台配置以下域名：

| 类型 | 域名 |
|------|------|
| request 合法域名 | API 服务域名 |
| socket 合法域名 | WuKongIM WSS 域名 |
| uploadFile 合法域名 | API 服务域名 |
| downloadFile 合法域名 | API 服务域名 |

## 组件属性

| 属性 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `api-key` | String | 是 | — | 平台 API Key |
| `api-base` | String | 是 | — | API 服务地址 |
| `theme-color` | String | 否 | `#2f80ed` | 主题色 |
| `lang` | String | 否 | `zh` | 语言（zh/en） |
| `visitor-name` | String | 否 | — | 访客名称 |
| `visitor-avatar` | String | 否 | — | 访客头像 URL |
| `custom-attrs` | Object | 否 | — | 自定义访客属性 |

## 功能支持

文字业务身份通过组件 `setBusinessCustomerSessionProvider` 方法接入；格式、生命周期和未验收项见[客户业务 SDK 接入说明](../../docs/business-customer-sdk-integration.md)。不要把业务凭据放入组件属性或 `setData`。

- 文本消息收发
- AI 流式回复（实时逐字显示）
- 图片消息发送与预览
- 语音消息接收与播放（播放前取得当前访客的附件访问链接；暂不支持录音发送）
- 历史消息加载（上拉加载更多）
- 系统消息展示（客服接入/转接/结束）
- Markdown 渲染（加粗、斜体、链接、列表、代码块）
- 多语言支持（中文/英文）
- IM 断线自动重连

## 开发

```bash
# 构建 miniprogram_dist
npm run build
```

构建产物位于 `miniprogram_dist/`，微信开发者工具通过 `package.json` 的 `miniprogram` 字段读取此目录。
