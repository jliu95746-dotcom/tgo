package tools

import (
	"context"
	"github.com/tgoai/tgo-device-agent/internal/config"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func TestUnavailableLocalScreenRequestNeverFallsBackToLegacyTools(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.WorkRoot = t.TempDir()
	cfg.Screen.Handle = 1
	cfg.Screen.Executable = "" // Rejected before reading any real window.
	registry := NewRegistry(cfg)
	if len(registry.ListTools()) != 0 {
		t.Fatal("failed local screen setup exposed legacy tools")
	}
	if !registry.CallTool(context.Background(), "shell_exec", map[string]interface{}{"command": "echo denied"}).IsError {
		t.Fatal("failed local scope allowed a command")
	}
}

func screenPolicyFor(t *testing.T, registry *Registry, revision, limit int) protocol.ScreenPolicy {
	t.Helper()
	status := registry.ScreenGrantStatus(context.Background())
	if !status.Active || status.GrantID == "" {
		t.Fatal("local screen grant unavailable")
	}
	policy := protocol.ScreenPolicy{ProtocolVersion: 1, Revision: revision, GrantID: status.GrantID, MaxActions: limit}
	policy.Digest = protocol.ScreenPolicyDigest(policy)
	return policy
}
func TestScreenPolicyRequiresLocalGrantAndLimitsOnlyScreenTools(t *testing.T) {
	r, backend, _ := screenRegistry(t)
	policy := screenPolicyFor(t, r, 1, 2)
	ack, err := r.ApplyScreenPolicy(context.Background(), policy)
	if err != nil || ack.Digest != policy.Digest || ack.GrantID != policy.GrantID {
		t.Fatal("matching local grant was not acknowledged", err)
	}
	for _, tool := range r.ListTools() {
		if tool.Name != "screen_screenshot" && tool.Name != "screen_stop" {
			t.Fatal("policy exposed an unapproved tool", tool.Name)
		}
	}
	for _, name := range []string{"fs_read", "fs_write", "shell_exec", "screen_click", "screen_type_text"} {
		if !r.CallTool(context.Background(), name, map[string]interface{}{}).IsError {
			t.Fatal("out of policy tool was allowed", name)
		}
	}
	if backend.clicks != 0 || backend.texts != 0 {
		t.Fatal("rejected tools caused input")
	}
}
func TestScreenPolicyCannotReplaceLocalGrantOrExpandLocalScope(t *testing.T) {
	for name, mutate := range map[string]func(*protocol.ScreenPolicy){
		"other_grant":   func(p *protocol.ScreenPolicy) { p.GrantID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" },
		"larger_budget": func(p *protocol.ScreenPolicy) { p.MaxActions = 6 },
		"outside_region": func(p *protocol.ScreenPolicy) {
			p.AllowClick = true
			p.ClickRegions = []protocol.ScreenRegion{{X: 15, Y: 15, Width: 2, Height: 2}}
		},
		"unbound_region": func(p *protocol.ScreenPolicy) { p.ClickRegions = []protocol.ScreenRegion{{Width: 1, Height: 1}} },
		"protocol":       func(p *protocol.ScreenPolicy) { p.ProtocolVersion = 2 },
		"zero_revision":  func(p *protocol.ScreenPolicy) { p.Revision = 0 },
	} {
		t.Run(name, func(t *testing.T) {
			r, backend, _ := screenRegistry(t)
			policy := screenPolicyFor(t, r, 1, 2)
			mutate(&policy)
			policy.Digest = protocol.ScreenPolicyDigest(policy)
			if _, err := r.ApplyScreenPolicy(context.Background(), policy); err == nil {
				t.Fatal("expanded local grant accepted")
			}
			if !r.CallTool(context.Background(), "screen_screenshot", map[string]interface{}{}).IsError {
				t.Fatal("failed policy reused an older grant")
			}
			if backend.clicks != 0 {
				t.Fatal("failed policy clicked")
			}
		})
	}
}
func TestScreenPolicyDuplicateDoesNotResetBudgetAndRevisionInvalidatesView(t *testing.T) {
	r, backend, _ := screenRegistry(t)
	policy := screenPolicyFor(t, r, 1, 1)
	policy.AllowClick = true
	policy.ClickRegions = []protocol.ScreenRegion{{X: 0, Y: 0, Width: 10, Height: 10}}
	policy.Digest = protocol.ScreenPolicyDigest(policy)
	if _, err := r.ApplyScreenPolicy(context.Background(), policy); err != nil {
		t.Fatal(err)
	}
	oldView := captureID(t, r)
	if _, err := r.ApplyScreenPolicy(context.Background(), policy); err != nil {
		t.Fatal(err)
	}
	policy.Revision++
	policy.Digest = protocol.ScreenPolicyDigest(policy)
	if _, err := r.ApplyScreenPolicy(context.Background(), policy); err != nil {
		t.Fatal(err)
	}
	if !r.CallTool(context.Background(), "screen_click", map[string]interface{}{"view_id": oldView, "x": 2, "y": 2}).IsError {
		t.Fatal("old revision's view was accepted")
	}
	newView := captureID(t, r)
	if r.CallTool(context.Background(), "screen_click", map[string]interface{}{"view_id": newView, "x": 2, "y": 2}).IsError {
		t.Fatal("allowed click rejected")
	}
	if _, err := r.ApplyScreenPolicy(context.Background(), policy); err == nil {
		t.Fatal("duplicate acknowledgement replenished exhausted budget")
	}
	if registryStatus := r.ScreenGrantStatus(context.Background()); registryStatus.Active || registryStatus.GrantID != "" {
		t.Fatal("exhausted local grant was still advertised")
	}
	if backend.clicks != 1 {
		t.Fatal("unexpected click count")
	}
}
func TestScreenPolicyRollbackAndWrongDigestInvalidateAppliedState(t *testing.T) {
	r, _, _ := screenRegistry(t)
	policy := screenPolicyFor(t, r, 2, 3)
	if _, err := r.ApplyScreenPolicy(context.Background(), policy); err != nil {
		t.Fatal(err)
	}
	policy.Revision = 1
	policy.Digest = protocol.ScreenPolicyDigest(policy)
	if _, err := r.ApplyScreenPolicy(context.Background(), policy); err == nil {
		t.Fatal("revision rollback accepted")
	}
	if !r.CallTool(context.Background(), "screen_screenshot", map[string]interface{}{}).IsError {
		t.Fatal("rollback left previous policy usable")
	}
	policy.Revision = 3
	policy.Digest = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	if _, err := r.ApplyScreenPolicy(context.Background(), policy); err == nil {
		t.Fatal("wrong digest accepted")
	}
}
