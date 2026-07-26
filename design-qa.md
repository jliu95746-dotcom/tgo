# 输入框自适应 Design QA

## 对比目标

- Source visual truth: `E:\SystemCache\Administrator\Temp\codex-clipboard-3bf99951-f8d1-46f1-9286-688a523073f2.png`
- Implementation screenshot: `E:\智能客服系统\.tmp\native-dev\input-adaptive-assist-full.png`
- Source pixels: 1251 x 149（用户提供的深色模式输入区裁图）
- Implementation pixels: 1164 x 655（浏览器按 CSS 像素归一化后的完整页面截图）
- CSS viewport: 1164 x 654
- Browser devicePixelRatio: 1.375；截图输出已归一化为接近 1:1 CSS 像素
- State: 已选中一个真实访客会话，并切换到辅助模式；输入 8 行草稿进行可见性验证

## Full-view comparison evidence

- 原图中的两行固定输入框会裁掉后续内容；实现图中 8 行草稿全部显示，发送提示和发送按钮仍位于输入框下方。
- 实现图保留了输入区上方的历史消息区域，输入区在正常文档流中占位，没有覆盖聊天内容。
- 页面现有工具栏、辅助模式提示、拟人技能选择和发送控件均保持原有布局和样式。

## Focused region comparison evidence

- 重点检查实现图右下角的消息输入区；文字、输入框边界、辅助提示和发送控件在原始截图尺寸下均可辨认，无需额外放大裁图。
- DOM 实测：8 行草稿时 textarea 高 208px、`overflow-y: hidden`，内容完整可见。
- DOM 实测：30 行草稿时 textarea 高 260px、scrollHeight 736px、`overflow-y: auto`，超长内容改为内部滚动。
- DOM 实测：超长草稿时历史消息区 `messagesBottom=235.36px`，输入区 `footerTop=235.36px`，两者相邻且无重叠。
- 窄窗口实测：浏览器内部视口 818 x 636 时，8 行草稿完整显示；历史消息区 `messagesBottom=319.64px`，输入区 `footerTop=319.64px`，仍无重叠。

## Findings

- 无 P0/P1/P2 问题。
- 字体与排版：沿用现有 Inter/系统中文字体、14px 正文和 24px 行高；长文本换行清楚。
- 间距与布局：输入框随内容增长；输入区作为 `flex` 正常占位元素，历史消息区同步让出空间。
- 颜色与视觉令牌：保持项目现有浅色/深色主题令牌；原图为深色模式、验证环境为浅色模式，该差异不属于本次功能改动。
- 图片与资源：该组件没有新增或替换图片资源。
- 文案：辅助模式提示、占位文案、快捷键提示和发送按钮文案均未改变。
- 控制台：没有发现由输入框改动引起的错误；现有日志仍包含测试开始时 API 未启动以及 plugin runtime 未运行产生的连接错误，与本次布局改动无关。

## Comparison history

- 初始问题：固定两行 textarea 导致长草稿被裁切，底部输入区存在覆盖历史记录的视觉风险。
- 修复：根据 `scrollHeight` 自动调整高度；最高取视口高度 40% 和 320px 中的较小值；超过上限后内部滚动；输入区改为正常占位并禁止收缩。
- 修复后证据：8 行和 30 行两组浏览器实测均通过，未发现需要继续修复的 P0/P1/P2 问题。

## Implementation checklist

- [x] 普通长草稿完整展开
- [x] 超长草稿内部滚动
- [x] 窗口尺寸变化时重新计算高度
- [x] 清空内容后恢复最小高度
- [x] 输入区不覆盖历史聊天记录
- [x] 辅助模式控件和发送操作保持可用

final result: passed
