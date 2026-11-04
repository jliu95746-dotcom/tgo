//go:build windows && (amd64 || arm64)

package approvedscript

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/sandbox"
)

func openFixtureSession(t *testing.T, c *Catalog, root string) *Session {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	s, err := OpenSession(context.Background(), c, root, SessionOwner{ProjectID: "11111111-1111-4111-8111-111111111111", DeviceID: "22222222-2222-4222-8222-222222222222"}, sandbox.New(cfg))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	return s
}

func TestSessionRequiresFreshPolicyAndPreservesBudgetOnReconnect(t *testing.T) {
	c, g, digest := runnerFixture(t, "python", "import json\nprint(json.dumps({'day':12,'include':False}))")
	root := filepath.Join(t.TempDir(), "private")
	first := openFixtureSession(t, c, root)
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: g.ScriptID, GrantDigest: digest, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	p.Digest = protocol.ScriptPolicyDigest(p)
	status := first.Status()
	if status.State != "awaiting_policy" || len(status.SessionID) != 32 {
		t.Fatalf("status: %#v", status)
	}
	args := map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}
	if result := first.RunReadOnly(context.Background(), status.SessionID, "33333333-3333-4333-8333-333333333333", strings.Repeat("c", 64), args); result.Status != "rejected" {
		t.Fatal("unconfirmed session ran")
	}
	ack, err := first.Apply(status.SessionID, p)
	if err != nil || ack.SessionID != status.SessionID || ack.Digest != p.Digest {
		t.Fatal("policy not confirmed for this session")
	}
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	if result := first.RunReadOnly(canceled, status.SessionID, "33333333-3333-4333-8333-333333333333", strings.Repeat("c", 64), args); result.Status != "canceled" || result.Failure != "caller_canceled" {
		t.Fatalf("canceled caller did not stop before reserving execution: %#v", result)
	}
	if result := first.RunReadOnly(context.Background(), status.SessionID, "33333333-3333-4333-8333-333333333333", strings.Repeat("c", 64), args); result.Status != "succeeded" {
		t.Fatalf("confirmed execution: %#v", result)
	}
	first.Close()
	second := openFixtureSession(t, c, root)
	next := second.Status()
	if next.SessionID == status.SessionID || next.State != "awaiting_policy" {
		t.Fatal("reconnection retained a live policy")
	}
	if _, err := second.Apply(status.SessionID, p); err == nil {
		t.Fatal("old connection's policy request was accepted")
	}
	if _, err := second.Apply(next.SessionID, p); err != nil {
		t.Fatal(err)
	}
	if result := second.RunReadOnly(context.Background(), next.SessionID, "44444444-4444-4444-8444-444444444444", strings.Repeat("c", 64), args); result.Status != "rejected" {
		t.Fatal("reconnection reset the policy run budget")
	}
}

func TestSessionCloseStopsRunningScriptAndDiscardsLateFields(t *testing.T) {
	for _, stop := range []string{"close", "suspend"} {
		t.Run(stop, func(t *testing.T) { testSessionStop(t, stop) })
	}
}

func testSessionStop(t *testing.T, stop string) {
	c, g, digest := runnerFixture(t, "python", "from pathlib import Path\nimport time\nPath('running').touch()\ntime.sleep(30)\nprint('{\"day\":12,\"include\":false}')")
	s := openFixtureSession(t, c, filepath.Join(t.TempDir(), "private"))
	status := s.Status()
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: g.ScriptID, GrantDigest: digest, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	p.Digest = protocol.ScriptPolicyDigest(p)
	if _, err := s.Apply(status.SessionID, p); err != nil {
		t.Fatal(err)
	}
	done := make(chan SessionResult, 1)
	go func() {
		done <- s.RunReadOnly(context.Background(), status.SessionID, "33333333-3333-4333-8333-333333333333", strings.Repeat("c", 64), map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)})
	}()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := os.Stat(filepath.Join(g.WorkingDirectory, "running")); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("script did not start")
		}
		time.Sleep(10 * time.Millisecond)
	}
	if _, err := s.Apply(status.SessionID, p); err != nil {
		t.Fatal(err)
	}
	select {
	case result := <-done:
		t.Fatalf("idempotent policy reapply canceled active work: %#v", result)
	case <-time.After(150 * time.Millisecond):
	}
	if stop == "suspend" {
		s.Suspend()
		if s.Status().State != "awaiting_policy" {
			t.Fatal("suspended session retained a confirmed policy")
		}
	} else {
		s.Close()
	}
	select {
	case result := <-done:
		if result.Status == "succeeded" || len(result.Fields) != 0 {
			t.Fatal("closed session returned late data")
		}
	case <-time.After(4 * time.Second):
		t.Fatal("session did not stop the script")
	}
}
