package tools

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/screen"
)

type screenBackend struct {
	window screen.Window
	image  []byte
	clicks int
	texts  int
}

func (b *screenBackend) Inspect(context.Context) (screen.Window, error)         { return b.window, nil }
func (b *screenBackend) Capture(context.Context, screen.Window) ([]byte, error) { return b.image, nil }
func (b *screenBackend) Click(context.Context, screen.Window, int, int) error   { b.clicks++; return nil }
func (b *screenBackend) TypeText(context.Context, screen.Window, string) error  { b.texts++; return nil }
func (b *screenBackend) StopRequested() bool                                    { return false }
func screenRegistry(t *testing.T) (*Registry, *screenBackend, *config.Config) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.WorkRoot = t.TempDir()
	r := NewRegistry(cfg)
	img := image.NewRGBA(image.Rect(0, 0, 16, 16))
	img.Set(0, 0, color.White)
	var buffer bytes.Buffer
	_ = png.Encode(&buffer, img)
	b := &screenBackend{window: screen.Window{Handle: 1, ProcessID: 2, ProcessStarted: 3, Executable: filepath.Join(cfg.WorkRoot, "owned.exe"), Width: 16, Height: 16, DPI: 96, Visible: true, Foreground: true, Interactive: true}, image: buffer.Bytes()}
	c, err := screen.NewController(screen.Grant{Handle: 1, Executable: b.window.Executable, MaxActions: 5, AllowText: true, ClickRegions: []screen.Region{{X: 0, Y: 0, Width: 16, Height: 16}}}, b)
	if err != nil {
		t.Fatal(err)
	}
	r.screenController = c
	r.screenPolicyRequired = true
	for _, name := range []string{"screen_screenshot", "screen_click", "screen_type_text", "screen_stop"} {
		r.Register(&screenTool{name: name, controller: c})
	}
	return r, b, cfg
}
func captureID(t *testing.T, r *Registry) string {
	t.Helper()
	result := r.CallTool(context.Background(), "screen_screenshot", map[string]interface{}{})
	if result.IsError || len(result.Content) != 2 || result.Content[1].Type != "image" || result.Content[1].MimeType != "image/png" {
		t.Fatal("missing MCP screenshot")
	}
	decoded, err := base64.StdEncoding.DecodeString(result.Content[1].Data)
	if err != nil {
		t.Fatal(err)
	}
	image, err := png.DecodeConfig(bytes.NewReader(decoded))
	if err != nil || image.Width != 16 {
		t.Fatal("bad MCP image")
	}
	if strings.Contains(result.Content[0].Text, "Executable") || strings.Contains(result.Content[0].Text, "ProcessID") {
		t.Fatal("private window identity exposed")
	}
	var metadata screen.Capture
	if err = json.Unmarshal([]byte(result.Content[0].Text), &metadata); err != nil {
		t.Fatal(err)
	}
	return metadata.ViewID
}
func TestScreenWireIsSingleUseStrictAndDoesNotEchoTypedText(t *testing.T) {
	r, b, _ := screenRegistry(t)
	status := r.ScreenGrantStatus(context.Background())
	policy := protocol.ScreenPolicy{ProtocolVersion: 1, Revision: 1, GrantID: status.GrantID, MaxActions: 5, AllowClick: true, AllowText: true, ClickRegions: status.ClickRegions}
	policy.Digest = protocol.ScreenPolicyDigest(policy)
	if _, err := r.ApplyScreenPolicy(context.Background(), policy); err != nil {
		t.Fatal(err)
	}
	view := captureID(t, r)
	args := map[string]interface{}{"view_id": view, "x": float64(2), "y": float64(2)}
	if r.CallTool(context.Background(), "screen_click", args).IsError {
		t.Fatal("valid click rejected")
	}
	if !r.CallTool(context.Background(), "screen_click", args).IsError || b.clicks != 1 {
		t.Fatal("click replayed")
	}
	view = captureID(t, r)
	text := "synthetic-sensitive-text"
	result := r.CallTool(context.Background(), "screen_type_text", map[string]interface{}{"view_id": view, "text": text})
	if result.IsError || b.texts != 1 || strings.Contains(result.Content[0].Text, text) {
		t.Fatal("type result missing or echoed input")
	}
	if !r.CallTool(context.Background(), "screen_screenshot", map[string]interface{}{"window_handle": 1}).IsError {
		t.Fatal("remote window replacement accepted")
	}
}
func TestScreenGrantStopsAcrossReconnectAndToolsDisappear(t *testing.T) {
	r, b, _ := screenRegistry(t)
	r.ResetPolicyForConnection()
	if !r.screenController.Active() {
		t.Fatal("initial connection consumed local grant")
	}
	r.ResetPolicyForConnection()
	for _, tool := range r.ListTools() {
		if isScreenTool(tool.Name) {
			t.Fatal("stale grant advertised after reconnect")
		}
	}
	if !r.CallTool(context.Background(), "screen_screenshot", map[string]interface{}{}).IsError || b.clicks != 0 {
		t.Fatal("old grant used after reconnect")
	}
}
func TestPublishedFilePolicyDoesNotGainScreenAccess(t *testing.T) {
	r, b, cfg := screenRegistry(t)
	policy := protocol.ReadPolicy{Revision: 1, ToolName: "fs_read", AllowedDirectory: cfg.WorkRoot, MaxLines: 5, MaxOffset: 10}
	policy.Digest = protocol.ReadPolicyDigest(policy.Revision, policy.ToolName, policy.AllowedDirectory, policy.MaxLines, policy.MaxOffset)
	if _, err := r.ApplyReadPolicy(policy); err != nil {
		t.Fatal(err)
	}
	for _, tool := range r.ListTools() {
		if tool.Name != "fs_read" {
			t.Fatal("file policy exposed another tool")
		}
	}
	if !r.CallTool(context.Background(), "screen_screenshot", map[string]interface{}{}).IsError || b.clicks != 0 {
		t.Fatal("file policy was bypassed")
	}
}
func TestLegacyRegistryDoesNotExposeScreenTools(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.WorkRoot = t.TempDir()
	r := NewRegistry(cfg)
	if len(r.ListTools()) != 4 || r.screenController != nil {
		t.Fatal("screen was enabled without a local grant")
	}
}
