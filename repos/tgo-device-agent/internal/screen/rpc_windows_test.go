//go:build windows && amd64

package screen

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"image/png"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

type ownedRPCPeer struct {
	conn   net.Conn
	reader *json.Decoder
	next   int
}

func (p *ownedRPCPeer) request(t *testing.T, method string, params interface{}) protocol.Response {
	t.Helper()
	p.next++
	request, _ := protocol.NewRequest(p.next, method, params)
	p.conn.SetDeadline(time.Now().Add(5 * time.Second))
	if err := json.NewEncoder(p.conn).Encode(request); err != nil {
		t.Fatal(err)
	}
	for {
		var result protocol.Response
		if err := p.reader.Decode(&result); err != nil {
			t.Fatal(err)
		}
		if result.ID != nil {
			var responseID int
			if result.JSONRPC != "2.0" || json.Unmarshal(*result.ID, &responseID) != nil || responseID != p.next {
				t.Fatal("owned RPC response identity mismatch")
			}
			return result
		}
	}
}
func rpcResult[T any](t *testing.T, response protocol.Response) T {
	t.Helper()
	if response.Error != nil {
		t.Fatalf("owned RPC returned %d: %s", response.Error.Code, response.Error.Message)
	}
	var result T
	if err := json.Unmarshal(response.Result, &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func acceptOwnAgent(t *testing.T, listener net.Listener) *ownedRPCPeer {
	t.Helper()
	listener.(*net.TCPListener).SetDeadline(time.Now().Add(5 * time.Second))
	connection, err := listener.Accept()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { connection.Close() })
	connection.SetDeadline(time.Now().Add(5 * time.Second))
	reader := json.NewDecoder(bufio.NewReader(connection))
	var auth protocol.Request
	if err := reader.Decode(&auth); err != nil || auth.Method != "auth" {
		t.Fatal("owned agent did not authenticate", err)
	}
	response, _ := protocol.NewResponse(auth.ID, protocol.AuthResult{Status: "ok", DeviceID: "00000000-0000-0000-0000-000000000001", ProjectID: "00000000-0000-0000-0000-000000000002"})
	if err := json.NewEncoder(connection).Encode(response); err != nil {
		t.Fatal(err)
	}
	return &ownedRPCPeer{connection, reader, 100}
}
func rpcScreenView(t *testing.T, peer *ownedRPCPeer) Capture {
	t.Helper()
	result := rpcResult[protocol.ToolCallResult](t, peer.request(t, "tools/call", protocol.ToolCallParams{Name: "screen_screenshot", Arguments: map[string]interface{}{}}))
	if result.IsError || len(result.Content) != 2 {
		t.Fatal("owned screenshot was not delivered")
	}
	var capture Capture
	if err := json.Unmarshal([]byte(result.Content[0].Text), &capture); err != nil {
		t.Fatal(err)
	}
	data, err := base64.StdEncoding.DecodeString(result.Content[1].Data)
	if err != nil {
		t.Fatal(err)
	}
	config, err := png.DecodeConfig(bytes.NewReader(data))
	if err != nil || config.Width != capture.Width || config.Height != capture.Height {
		t.Fatal("owned TCP PNG dimensions are inconsistent")
	}
	capture.PNG = data
	return capture
}

func TestNativeOwnedScreenPolicyRPC(t *testing.T) {
	if os.Getenv("TGO_SCREEN_RPC_ACCEPTANCE") != "owned-fixture-only" {
		t.Skip("requires explicit owned TCP client acceptance")
	}
	program := os.Getenv("TGO_SCREEN_RPC_AGENT_EXE")
	if !filepath.IsAbs(program) {
		t.Fatal("compiled agent path is required")
	}
	fixture := ownedFixture(t)
	if err := activateSelected(context.Background(), fixture.handle); err != nil {
		t.Fatal(err)
	}
	targetProgram, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { listener.Close() })
	directory := t.TempDir()
	settings := struct {
		ServerHost           string `json:"server_host"`
		ServerPort           int    `json:"server_port"`
		DeviceToken          string `json:"device_token"`
		TokenFile            string `json:"token_file"`
		WorkRoot             string `json:"work_root"`
		MaxReconnectAttempts int    `json:"max_reconnect_attempts"`
		Screen               Grant  `json:"screen"`
	}{"127.0.0.1", listener.Addr().(*net.TCPAddr).Port, "owned-screen-rpc-synthetic-token", filepath.Join(directory, "unused-token"), directory, 2,
		Grant{Handle: uint64(fixture.handle), Executable: targetProgram, MaxActions: 3, AllowText: false, ClickRegions: []Region{{X: 20, Y: 50, Width: 360, Height: 40}}}}
	config, _ := json.Marshal(settings)
	configPath := filepath.Join(directory, "agent.json")
	if err := os.WriteFile(configPath, config, 0600); err != nil {
		t.Fatal(err)
	}
	log, err := os.Create(filepath.Join(directory, "agent.log"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { log.Close() })
	ctx, cancel := context.WithCancel(context.Background())
	command := exec.CommandContext(ctx, program, "--config", configPath)
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	command.Stderr, command.Stdout = log, log
	if err := command.Start(); err != nil {
		cancel()
		t.Fatal(err)
	}
	finished := make(chan error, 1)
	go func() { finished <- command.Wait(); close(finished) }()
	stopped := false
	stop := func() error {
		if stopped {
			return nil
		}
		cancel()
		select {
		case <-finished:
			stopped = true
			return nil
		case <-time.After(3 * time.Second):
			return errors.New("owned agent did not stop")
		}
	}
	t.Cleanup(func() {
		if err := stop(); err != nil {
			t.Error(err)
		}
	})
	peer := acceptOwnAgent(t, listener)
	statusResponse := peer.request(t, "screen/status", map[string]interface{}{})
	status := rpcResult[protocol.ScreenGrantStatus](t, statusResponse)
	if !status.Active || len(status.GrantID) != 32 || status.UsedActions != 0 {
		logged, _ := os.ReadFile(log.Name())
		t.Log(strings.ReplaceAll(string(logged), settings.DeviceToken, "[redacted]"))
		t.Fatal("fresh local grant not reported")
	}
	if bytes.Contains(statusResponse.Result, []byte(targetProgram)) || bytes.Contains(statusResponse.Result, []byte("window_handle")) {
		t.Fatal("local window identity escaped the client")
	}
	var fields map[string]json.RawMessage
	if err = json.Unmarshal(statusResponse.Result, &fields); err != nil {
		t.Fatal(err)
	}
	allowed := map[string]bool{"protocol_version": true, "active": true, "grant_id": true, "width": true, "height": true, "dpi": true, "allow_click": true, "allow_text": true, "click_regions": true, "max_actions": true, "used_actions": true}
	for field := range fields {
		if !allowed[field] {
			t.Fatal("unexpected local grant field", field)
		}
	}
	listed := rpcResult[protocol.ToolsListResult](t, peer.request(t, "tools/list", nil))
	if len(listed.Tools) != 1 || listed.Tools[0].Name != "screen_stop" {
		t.Fatal("unpublished screen mode exposed tools")
	}
	policy := protocol.ScreenPolicy{ProtocolVersion: 1, Revision: 1, GrantID: status.GrantID, MaxActions: 3, ClickRegions: []protocol.ScreenRegion{}}
	policy.Digest = protocol.ScreenPolicyDigest(policy)
	ack := rpcResult[protocol.ScreenPolicyAck](t, peer.request(t, "screen/policy/apply", policy))
	if ack.GrantID != policy.GrantID || ack.Digest != policy.Digest {
		t.Fatal("ack not bound to the local grant")
	}
	listed = rpcResult[protocol.ToolsListResult](t, peer.request(t, "tools/list", nil))
	if len(listed.Tools) != 2 {
		t.Fatal("read-only screen policy discovery is incorrect")
	}
	for _, tool := range listed.Tools {
		if tool.Name != "screen_screenshot" && tool.Name != "screen_stop" {
			t.Fatal("unexpected tool", tool.Name)
		}
	}
	_ = rpcScreenView(t, peer)
	bad := map[string]interface{}{}
	encoded, _ := json.Marshal(policy)
	_ = json.Unmarshal(encoded, &bad)
	bad["window_handle"] = 1
	if response := peer.request(t, "screen/policy/apply", bad); response.Error == nil {
		t.Fatal("unknown screen policy field accepted")
	}
	denied := rpcResult[protocol.ToolCallResult](t, peer.request(t, "tools/call", protocol.ToolCallParams{Name: "screen_screenshot", Arguments: map[string]interface{}{}}))
	if !denied.IsError {
		t.Fatal("malformed update left prior policy active")
	}
	_ = rpcResult[protocol.ScreenPolicyAck](t, peer.request(t, "screen/policy/apply", policy))
	delete(bad, "window_handle")
	bad["allow_click"] = nil
	if response := peer.request(t, "screen/policy/apply", bad); response.Error == nil {
		t.Fatal("null permission accepted")
	}
	denied = rpcResult[protocol.ToolCallResult](t, peer.request(t, "tools/call", protocol.ToolCallParams{Name: "screen_screenshot", Arguments: map[string]interface{}{}}))
	if !denied.IsError {
		t.Fatal("null update left previous policy active")
	}
	_ = rpcResult[protocol.ScreenPolicyAck](t, peer.request(t, "screen/policy/apply", policy))
	previous := rpcScreenView(t, peer)
	policy.Revision = 2
	policy.AllowClick = true
	policy.ClickRegions = []protocol.ScreenRegion{{X: 20, Y: 50, Width: 100, Height: 40}}
	policy.Digest = protocol.ScreenPolicyDigest(policy)
	_ = rpcResult[protocol.ScreenPolicyAck](t, peer.request(t, "screen/policy/apply", policy))
	click := func(view string) protocol.ToolCallResult {
		return rpcResult[protocol.ToolCallResult](t, peer.request(t, "tools/call", protocol.ToolCallParams{Name: "screen_click", Arguments: map[string]interface{}{"view_id": view, "x": 30, "y": 60}}))
	}
	if !click(previous.ViewID).IsError {
		t.Fatal("previous policy screenshot was reusable")
	}
	current := rpcScreenView(t, peer)
	if clicked := click(current.ViewID); clicked.IsError {
		bound, _ := inspectNativeWindow(context.Background(), fixture.handle, true)
		point := uint64(uint32(bound.X+30)) | uint64(uint32(bound.Y+60))<<32
		hit := call(user32, "WindowFromPoint", uintptr(point))
		root := call(user32, "GetAncestor", hit, 2)
		t.Logf("own target is foreground=%t; click hits own target=%t", call(user32, "GetForegroundWindow") == fixture.handle, root == fixture.handle)
		if root := os.Getenv("TGO_SCREEN_NATIVE_REPORT_DIR"); filepath.IsAbs(root) {
			_ = os.MkdirAll(root, 0700)
			_ = os.WriteFile(filepath.Join(root, "diagnostic-before-click.png"), current.PNG, 0600)
		}
		if len(clicked.Content) > 0 {
			t.Fatal("limited owned click failed:", clicked.Content[0].Text)
		}
		t.Fatal("limited owned click failed without a reason")
	}
	if !click(current.ViewID).IsError {
		t.Fatal("click was replayed")
	}
	status = rpcResult[protocol.ScreenGrantStatus](t, peer.request(t, "screen/status", nil))
	if !status.Active || status.UsedActions != 1 {
		t.Fatal("input count was reset or grant ended early")
	}
	_ = rpcResult[protocol.ScreenPolicyAck](t, peer.request(t, "screen/policy/apply", policy))
	status = rpcResult[protocol.ScreenGrantStatus](t, peer.request(t, "screen/status", nil))
	if status.UsedActions != 1 {
		t.Fatal("duplicate policy replenished consumed actions")
	}
	peer.conn.Close()
	peer = acceptOwnAgent(t, listener)
	status = rpcResult[protocol.ScreenGrantStatus](t, peer.request(t, "screen/status", nil))
	if status.Active || status.GrantID != "" {
		t.Fatal("reconnect restored an old local grant")
	}
	if response := peer.request(t, "screen/policy/apply", policy); response.Error == nil {
		t.Fatal("reconnect accepted the old grant")
	}
	if err = stop(); err != nil {
		t.Fatal(err)
	}
	if err = fixture.close(); err != nil {
		t.Fatal(err)
	}
	log.Close()
	logged, err := os.ReadFile(log.Name())
	if err != nil || strings.Contains(string(logged), settings.DeviceToken) {
		t.Fatal("agent log check failed")
	}
	if root := os.Getenv("TGO_SCREEN_NATIVE_REPORT_DIR"); root != "" {
		if !filepath.IsAbs(root) {
			t.Fatal("report directory must be absolute")
		}
		if err = os.MkdirAll(root, 0700); err != nil {
			t.Fatal(err)
		}
		report := struct {
			VerificationCompleted bool     `json:"verification_completed"`
			VerifiedAtUTC         string   `json:"verified_at_utc"`
			Peer                  string   `json:"peer"`
			ImageSHA256           string   `json:"image_sha256"`
			Checks                []string `json:"checks"`
		}{true, time.Now().UTC().Format(time.RFC3339Nano), "synthetic owned loopback TCP peer; not the Device Control service", "", []string{"fresh_opaque_grant", "pending_discovery_only_stop", "same_grant_ack", "narrow_tool_discovery", "actual_png_over_tcp", "unknown_field_revokes_old_policy", "null_permission_rejected", "revision_invalidates_view", "actual_owned_click_over_tcp", "click_replay_rejected", "duplicate_does_not_reset_actions", "reconnect_revokes_local_grant", "old_grant_cannot_reapply", "owned_agent_and_window_closed", "token_not_in_agent_log"}}
		if err = os.WriteFile(filepath.Join(root, "rpc-before-click.png"), current.PNG, 0600); err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(current.PNG)
		report.ImageSHA256 = hex.EncodeToString(digest[:])
		data, _ := json.MarshalIndent(report, "", "  ")
		if err = os.WriteFile(filepath.Join(root, "rpc-report.json"), data, 0600); err != nil {
			t.Fatal(err)
		}
	}
}
