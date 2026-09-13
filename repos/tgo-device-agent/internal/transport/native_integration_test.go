package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/tools"
)

// This opt-in test requires credentials for a dedicated disposable API project.
// It uses the real native device server, never a customer's device or files.
func TestNativeDeviceLifecycle(t *testing.T) {
	if os.Getenv("TGO_DEVICE_NATIVE_TEST") != "1" {
		t.Skip("owned native fixture not configured")
	}
	staffHeader := os.Getenv("TGO_DEVICE_TEST_STAFF_HEADER")
	deviceHeader := os.Getenv("TGO_DEVICE_TEST_SERVICE_HEADER")
	project := os.Getenv("TGO_DEVICE_TEST_PROJECT")
	if staffHeader == "" || deviceHeader == "" || project == "" {
		t.Fatal("owned credentials required")
	}
	name := fmt.Sprintf("native-go-proof-%d", time.Now().UnixNano())
	client := &http.Client{Timeout: 5 * time.Second}
	request := func(method, path, header string, input interface{}, output interface{}) int {
		t.Helper()
		body, err := json.Marshal(input)
		if err != nil {
			t.Fatal(err)
		}
		req, err := http.NewRequest(method, path, bytes.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", header)
		req.Header.Set("Content-Type", "application/json")
		response, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		if output != nil && response.StatusCode < 400 {
			if err := json.NewDecoder(response.Body).Decode(output); err != nil {
				t.Fatal(err)
			}
		} else {
			io.Copy(io.Discard, response.Body)
		}
		return response.StatusCode
	}
	const base = "http://127.0.0.1:18000/v1/device-control/devices"
	type deviceInfo struct {
		ID        string `json:"id"`
		ProjectID string `json:"project_id"`
		Name      string `json:"device_name"`
		Status    string `json:"status"`
	}
	// Registered before client cleanups, so clients stop before owned-device deletion.
	t.Cleanup(func() {
		var listing struct {
			Devices []deviceInfo `json:"devices"`
		}
		if request("GET", base, staffHeader, nil, &listing) != 200 {
			t.Error("owned device cleanup lookup failed")
			return
		}
		for _, device := range listing.Devices {
			if device.Name == name && device.ProjectID == project {
				if request("DELETE", base+"/"+device.ID, staffHeader, nil, nil) != 200 {
					t.Error("owned device cleanup failed")
				}
			}
		}
	})
	var bind struct {
		BindCode string `json:"bind_code"`
	}
	if status := request("POST", base+"/bind-code", staffHeader, nil, &bind); status != 200 {
		t.Fatalf("bind-code status %d", status)
	}
	root := t.TempDir()
	workRoot := filepath.Join(root, "sandbox")
	if err := os.Mkdir(workRoot, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "outside.txt"), []byte("owned outside sentinel"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(workRoot, "large.txt"), []byte(strings.Repeat("x", 100000)), 0600); err != nil {
		t.Fatal(err)
	}
	settings := config.DefaultConfig()
	settings.ServerHost = "127.0.0.1"
	settings.ServerPort = 9876
	settings.DeviceName = name
	settings.BindCode = bind.BindCode
	settings.TokenFile = filepath.Join(root, "device_token")
	settings.WorkRoot = workRoot
	settings.ReconnectInitialDelay = 20 * time.Millisecond
	settings.ReconnectMaxDelay = 40 * time.Millisecond
	settings.MaxReconnectAttempts = 2
	start := func(cfg *config.Config) (context.CancelFunc, <-chan error) {
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan error, 1)
		go func() { defer close(done); done <- NewClient(cfg, tools.NewRegistry(cfg)).Run(ctx) }()
		t.Cleanup(func() {
			cancel()
			select {
			case <-done:
			case <-time.After(time.Second):
				t.Error("native client failed to stop")
			}
		})
		return cancel, done
	}
	cancel, done := start(settings)
	var deviceID string
	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		var listing struct {
			Devices []deviceInfo `json:"devices"`
		}
		if request("GET", base, staffHeader, nil, &listing) != 200 {
			t.Fatal("device list failed")
		}
		for _, device := range listing.Devices {
			if device.Name == name && device.ProjectID == project && device.Status == "online" {
				deviceID = device.ID
			}
		}
		if deviceID != "" {
			break
		}
		time.Sleep(25 * time.Millisecond)
	}
	if deviceID == "" {
		t.Fatal("native client did not register")
	}
	rpc := func(method string, params interface{}) protocol.Response {
		t.Helper()
		payload, _ := protocol.NewRequest(7, method, params)
		var response protocol.Response
		if status := request("POST", "http://127.0.0.1:8085/mcp/"+deviceID, deviceHeader, payload, &response); status != 200 {
			t.Fatalf("MCP status %d", status)
		}
		if response.Error != nil {
			t.Fatalf("RPC error code %d", response.Error.Code)
		}
		return response
	}
	var definitions protocol.ToolsListResult
	if err := json.Unmarshal(rpc("tools/list", nil).Result, &definitions); err != nil {
		t.Fatal(err)
	}
	if len(definitions.Tools) != 4 {
		t.Fatalf("expected four real built-in tools, got %d", len(definitions.Tools))
	}
	call := func(name string, args map[string]interface{}) *protocol.ToolCallResult {
		t.Helper()
		var result protocol.ToolCallResult
		if err := json.Unmarshal(rpc("tools/call", protocol.ToolCallParams{Name: name, Arguments: args}).Result, &result); err != nil {
			t.Fatal(err)
		}
		return &result
	}
	if result := call("fs_read", map[string]interface{}{"path": "large.txt"}); result.IsError || len(result.Content[0].Text) != 100000 {
		t.Fatal("large read failed")
	}
	if result := call("fs_read", map[string]interface{}{"path": "../outside.txt"}); !result.IsError {
		t.Fatal("sandbox boundary was bypassed")
	}
	if result := call("fs_write", map[string]interface{}{"path": "note.txt", "content": "owned first"}); result.IsError {
		t.Fatal("owned file write failed")
	}
	if result := call("fs_edit", map[string]interface{}{"path": "note.txt", "old_string": "first", "new_string": "second"}); result.IsError {
		t.Fatal("owned file edit failed")
	}
	if result := call("fs_read", map[string]interface{}{"path": "note.txt"}); result.IsError || result.Content[0].Text != "owned second" {
		t.Fatal("edited content mismatch")
	}
	t.Log("native binding, tool discovery, 100KB read, confined write/edit and boundary denial passed")

	// A clean stop must unblock the real idle TCP reader without a forced kill.
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("idle native client stop hung")
	}
	reconnect := *settings
	reconnect.BindCode = ""
	reconnect.DeviceToken = ""
	reconnect.LoadTokenFromFile()
	if reconnect.DeviceToken == "" {
		t.Fatal("registration token was not persisted")
	}
	_, reconnectDone := start(&reconnect)
	deadline = time.Now().Add(5 * time.Second)
	for {
		var info deviceInfo
		request("GET", base+"/"+deviceID, staffHeader, nil, &info)
		if info.Status == "online" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("saved-token reconnect failed")
		}
		time.Sleep(25 * time.Millisecond)
	}
	if result := call("fs_read", map[string]interface{}{"path": "note.txt"}); result.IsError {
		t.Fatal("reconnected read failed")
	}
	if request("DELETE", base+"/"+deviceID, staffHeader, nil, nil) != 200 {
		t.Fatal("device deletion failed")
	}
	select {
	case err := <-reconnectDone:
		if err == nil {
			t.Fatal("revoked client should exhaust authentication retries")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("revoked token still reconnecting")
	}
	if request("GET", base+"/"+deviceID, staffHeader, nil, nil) != 404 {
		t.Fatal("deleted device still accessible")
	}
	t.Log("graceful shutdown, saved-token reconnect, deletion and token revocation passed")
}
