package tools

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"log/slog"
	"strings"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/screen"
)

type screenTool struct {
	name       string
	controller *screen.Controller
}

func isScreenTool(name string) bool { return strings.HasPrefix(name, "screen_") }
func (r *Registry) registerScreen(grant screen.Grant) {
	if grant.Handle == 0 {
		return
	}
	// A requested screen-only scope must not fall back to legacy file/command
	// access when local window or capture initialization is unavailable.
	r.screenPolicyRequired = true
	backend, err := screen.NewNativeBackend(grant)
	if err != nil {
		slog.Warn("screen grant unavailable on this platform")
		return
	}
	controller, err := screen.NewController(grant, backend)
	if err != nil {
		slog.Warn("screen local window validation failed")
		return
	}
	if _, err = controller.Screenshot(context.Background()); err != nil {
		slog.Warn("screen local capture validation failed", "reason", err.Error())
		return
	}
	r.screenController = controller
	names := []string{"screen_screenshot", "screen_stop"}
	if len(grant.ClickRegions) > 0 {
		names = append(names, "screen_click")
	}
	if grant.AllowText {
		names = append(names, "screen_type_text")
	}
	for _, name := range names {
		r.Register(&screenTool{name: name, controller: controller})
	}
}
func (r *Registry) WatchLocalScreenStop(ctx context.Context) {
	if r.screenController != nil {
		go r.screenController.WatchStop(ctx)
		if err := screen.ShowLocalStatus(ctx, r.screenController); err != nil {
			r.screenController.Stop()
			slog.Warn("本机授权状态窗口不可用，屏幕授权已停止")
		}
	}
}
func (t *screenTool) Name() string { return t.name }
func (t *screenTool) Definition() protocol.ToolDefinition {
	properties := map[string]interface{}{}
	required := []string{}
	description := "仅截图本机已明确绑定的窗口；返回一次性 view_id 与 PNG。不会截图整个桌面。按住 Esc 可停止授权。"
	if t.name == "screen_click" || t.name == "screen_type_text" {
		properties["view_id"] = map[string]interface{}{"type": "string", "minLength": 32, "maxLength": 32}
		required = append(required, "view_id")
	}
	if t.name == "screen_click" {
		for _, name := range []string{"x", "y"} {
			properties[name] = map[string]interface{}{"type": "integer", "minimum": 0, "maximum": 8191}
			required = append(required, name)
		}
		description = "使用刚取得的 view_id，在本机批准的窗口区域内单击。窗口内容、焦点、尺寸或权限变化会停止授权；不得自动重试。"
	}
	if t.name == "screen_type_text" {
		properties["text"] = map[string]interface{}{"type": "string", "minLength": 1, "maxLength": 256}
		required = append(required, "text")
		description = "仅向本机允许的 Windows 普通 Edit 控件输入文本；不支持密码控件、浏览器字段、换行或快捷键。输入送达不代表业务办理完成。"
	}
	if t.name == "screen_stop" {
		description = "立即停止本次本机窗口授权。恢复需要设备使用者在本机重新启动并核对授权。"
	}
	return protocol.ToolDefinition{Name: t.name, Description: description, InputSchema: map[string]interface{}{"type": "object", "properties": properties, "required": required, "additionalProperties": false}}
}
func (t *screenTool) Execute(ctx context.Context, args map[string]interface{}) *protocol.ToolCallResult {
	allowed := map[string]bool{}
	if t.name == "screen_click" {
		allowed = map[string]bool{"view_id": true, "x": true, "y": true}
	}
	if t.name == "screen_type_text" {
		allowed = map[string]bool{"view_id": true, "text": true}
	}
	for key := range args {
		if !allowed[key] {
			return protocol.TextResult("Error: 屏幕参数不受支持", true)
		}
	}
	if ctx.Err() != nil {
		return protocol.TextResult("Error: 屏幕调用已取消", true)
	}
	if t.name == "screen_stop" {
		t.controller.Stop()
		return protocol.TextResult("本机窗口授权已停止。", false)
	}
	if t.name == "screen_screenshot" {
		capture, err := t.controller.Screenshot(ctx)
		if err != nil {
			return protocol.TextResult("Error: "+err.Error(), true)
		}
		metadata, _ := json.Marshal(capture)
		return &protocol.ToolCallResult{Content: []protocol.ContentItem{{Type: "text", Text: string(metadata)}, {Type: "image", Data: base64.StdEncoding.EncodeToString(capture.PNG), MimeType: "image/png"}}, IsError: false}
	}
	view, ok := args["view_id"].(string)
	if !ok || len(view) != 32 {
		return protocol.TextResult("Error: 需要本次截图的 view_id", true)
	}
	if t.name == "screen_click" {
		x, xOK := policyIntArg(args["x"])
		y, yOK := policyIntArg(args["y"])
		if !xOK || !yOK {
			return protocol.TextResult("Error: 点击坐标需要整数", true)
		}
		if err := t.controller.Click(ctx, view, x, y); err != nil {
			return protocol.TextResult("Error: "+err.Error(), true)
		}
		return protocol.TextResult("点击已送达；请重新截图核对结果，不得据此认定业务已完成。", false)
	}
	text, ok := args["text"].(string)
	if !ok {
		return protocol.TextResult("Error: 需要纯文本输入", true)
	}
	if err := t.controller.Type(ctx, view, text); err != nil {
		return protocol.TextResult("Error: "+err.Error(), true)
	}
	return protocol.TextResult("文本输入已送达；请重新截图核对结果，未执行提交或快捷键。", false)
}
