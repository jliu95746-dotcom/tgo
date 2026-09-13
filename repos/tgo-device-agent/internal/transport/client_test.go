package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/tools"
)

type fixtureServer struct {
	listener net.Listener
	client   *Client
	cancel   context.CancelFunc
	done     chan error
}

func startFixture(t *testing.T, customize func(*config.Config, *tools.Registry)) *fixtureServer {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	cfg := config.DefaultConfig()
	cfg.ServerHost = "127.0.0.1"
	cfg.ServerPort = listener.Addr().(*net.TCPAddr).Port
	cfg.DeviceToken = "owned-test-token"
	cfg.TokenFile = filepath.Join(t.TempDir(), "device_token")
	cfg.WorkRoot = t.TempDir()
	cfg.HeartbeatInterval = time.Hour
	cfg.ReconnectInitialDelay = time.Millisecond
	cfg.ReconnectMaxDelay = 5 * time.Millisecond
	registry := tools.NewRegistry(cfg)
	if customize != nil {
		customize(cfg, registry)
	}
	ctx, cancel := context.WithCancel(context.Background())
	fixture := &fixtureServer{listener: listener, client: NewClient(cfg, registry), cancel: cancel, done: make(chan error, 1)}
	go func() { defer close(fixture.done); fixture.done <- fixture.client.Run(ctx) }()
	t.Cleanup(func() {
		cancel()
		listener.Close()
		select {
		case <-fixture.done:
		case <-time.After(time.Second):
			t.Error("client did not shut down")
		}
	})
	return fixture
}

func (f *fixtureServer) accept(t *testing.T, authenticate bool) net.Conn {
	t.Helper()
	f.listener.(*net.TCPListener).SetDeadline(time.Now().Add(3 * time.Second))
	conn, err := f.listener.Accept()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	conn.SetDeadline(time.Now().Add(3 * time.Second))
	if authenticate {
		var request protocol.Request
		if err := json.NewDecoder(conn).Decode(&request); err != nil {
			t.Fatal(err)
		}
		response, _ := protocol.NewResponse(request.ID, protocol.AuthResult{Status: "ok", DeviceID: "fixture-device", ProjectID: "fixture-project"})
		if err := json.NewEncoder(conn).Encode(response); err != nil {
			t.Fatal(err)
		}
	}
	return conn
}

func waitSignal(t *testing.T, ch <-chan struct{}, label string) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(time.Second):
		t.Errorf("timed out: %s", label)
	}
}

func TestCancelInterruptsIdleRead(t *testing.T) {
	f := startFixture(t, nil)
	conn := f.accept(t, true)
	ping, _ := protocol.NewRequest(44, "ping", nil)
	if err := json.NewEncoder(conn).Encode(ping); err != nil {
		t.Fatal(err)
	}
	var pong protocol.Response
	if err := json.NewDecoder(conn).Decode(&pong); err != nil {
		t.Fatal(err)
	}
	time.Sleep(20 * time.Millisecond)
	f.cancel()
	select {
	case err := <-f.done:
		if err != nil {
			t.Errorf("cancel returned error: %v", err)
		}
	case <-time.After(300 * time.Millisecond):
		t.Error("Run remains blocked reading an idle connection after cancellation")
		conn.Close()
	}
}

type blockedTool struct{ started, cancelled, release chan struct{} }

func (b *blockedTool) Name() string { return "fixture_block" }
func (b *blockedTool) Definition() protocol.ToolDefinition {
	return protocol.ToolDefinition{Name: b.Name(), InputSchema: map[string]interface{}{"type": "object"}}
}
func (b *blockedTool) Execute(ctx context.Context, args map[string]interface{}) *protocol.ToolCallResult {
	close(b.started)
	select {
	case <-ctx.Done():
		close(b.cancelled)
	case <-b.release:
		return protocol.TextResult("old-session-result", false)
	}
	<-b.release // Deliberately delay completion after cancellation to simulate slow cleanup.
	return protocol.TextResult("old-session-result", false)
}

func TestDisconnectCancelsToolAndCannotWriteIntoReplacement(t *testing.T) {
	tool := &blockedTool{make(chan struct{}), make(chan struct{}), make(chan struct{})}
	f := startFixture(t, func(_ *config.Config, r *tools.Registry) { r.Register(tool) })
	first := f.accept(t, true)
	request, _ := protocol.NewRequest(91, "tools/call", protocol.ToolCallParams{Name: tool.Name(), Arguments: map[string]interface{}{}})
	if err := json.NewEncoder(first).Encode(request); err != nil {
		t.Fatal(err)
	}
	waitSignal(t, tool.started, "tool started")
	first.Close()
	second := f.accept(t, true)
	waitSignal(t, tool.cancelled, "old tool cancelled on disconnect")
	close(tool.release)
	second.SetReadDeadline(time.Now().Add(150 * time.Millisecond))
	var response protocol.Response
	if err := json.NewDecoder(second).Decode(&response); err == nil {
		t.Errorf("old connection response appeared on replacement: %s", response.Result)
	}
}

func TestAuthRejectsMismatchedResponseID(t *testing.T) {
	f := startFixture(t, func(cfg *config.Config, _ *tools.Registry) { cfg.MaxReconnectAttempts = 1 })
	conn := f.accept(t, false)
	var request protocol.Request
	if err := json.NewDecoder(conn).Decode(&request); err != nil {
		t.Fatal(err)
	}
	wrong := json.RawMessage(`999`)
	response, _ := protocol.NewResponse(&wrong, protocol.AuthResult{Status: "ok", DeviceID: "fixture-device", ProjectID: "fixture-project"})
	json.NewEncoder(conn).Encode(response)
	select {
	case err := <-f.done:
		if err == nil {
			t.Error("mismatched auth ID was accepted")
		}
	case <-time.After(300 * time.Millisecond):
		t.Error("mismatched auth response was not rejected")
		conn.Close()
	}
}

func TestDebugLogsDoNotExposeBindCode(t *testing.T) {
	var output bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&output, &slog.HandlerOptions{Level: slog.LevelDebug})))
	defer slog.SetDefault(previous)
	f := startFixture(t, func(cfg *config.Config, _ *tools.Registry) {
		cfg.DeviceToken = ""
		cfg.BindCode = "OWNED-SECRET-BIND"
		cfg.MaxReconnectAttempts = 1
	})
	conn := f.accept(t, false)
	var request protocol.Request
	if err := json.NewDecoder(conn).Decode(&request); err != nil {
		t.Fatal(err)
	}
	json.NewEncoder(conn).Encode(protocol.NewErrorResponse(request.ID, protocol.ErrAuthFailed, "fixture rejected"))
	select {
	case <-f.done:
	case <-time.After(time.Second):
		t.Fatal("auth did not finish")
	}
	if strings.Contains(output.String(), "OWNED-SECRET-BIND") {
		t.Error("debug logs exposed the bind code")
	}
}

func TestAuthResultMustContainConfirmedIdentity(t *testing.T) {
	for _, raw := range []string{`null`, `{}`, `{"status":"failed","deviceId":"d","projectId":"p"}`, `{"status":"ok","deviceId":"d"}`} {
		t.Run(raw, func(t *testing.T) {
			cfg := config.DefaultConfig()
			cfg.DeviceToken = "owned-test-token"
			client := NewClient(cfg, nil)
			if err := client.processAuthResult(&protocol.Response{Result: json.RawMessage(raw)}); err == nil {
				t.Error("invalid authentication result was accepted")
			}
		})
	}
}
