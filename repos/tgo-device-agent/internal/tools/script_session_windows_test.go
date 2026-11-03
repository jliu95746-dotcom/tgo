//go:build windows && (amd64 || arm64)

package tools

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/approvedscript"
	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func TestScriptSessionInitializesOnlyAfterBindingAndProtectsPrivateFiles(t *testing.T) {
	root := t.TempDir()
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	cfg.TokenFile = filepath.Join(root, "token")
	cfg.ScriptExecutionEnabled = true
	cfg.ScriptStateDirectory = filepath.Join(root, "private")
	r := NewRegistry(cfg)
	if _, err := os.Stat(cfg.ScriptStateDirectory); !os.IsNotExist(err) {
		t.Fatal("registry initialized private state before authentication")
	}
	if len(r.ListTools()) != 0 {
		t.Fatal("script mode fell back to generic tools before policy confirmation")
	}
	owner := approvedscript.SessionOwner{ProjectID: "11111111-1111-4111-8111-111111111111", DeviceID: "22222222-2222-4222-8222-222222222222"}
	id, closeFirst := r.BeginScriptConnection(context.Background(), owner)
	defer closeFirst()
	if id == "" || r.ScriptStatus(id).State != "awaiting_policy" {
		t.Fatalf("status: %#v", r.ScriptStatus(id))
	}
	private := filepath.Join(cfg.ScriptStateDirectory, "identity.json")
	if _, err := r.sb.ResolvePath(private); err == nil {
		t.Fatal("business sandbox could read key file")
	}
	if _, err := r.sb.ResolvePath(strings.ToUpper(private)); err == nil {
		t.Fatal("case alias escaped private state denial")
	}
	if _, err := r.sb.ResolvePath(cfg.TokenFile); err == nil {
		t.Fatal("business sandbox could read device credential")
	}
	next, closeNext := r.BeginScriptConnection(context.Background(), owner)
	defer closeNext()
	if next == id || next == "" {
		t.Fatal("connection binding did not change")
	}
	closeFirst()
	if r.ScriptStatus(next).State != "awaiting_policy" {
		t.Fatal("stale connection cleanup detached replacement")
	}
	if _, err := r.ApplyScriptPolicy(id, protocol.ScriptPolicyApply{SessionID: id}); err == nil {
		t.Fatal("stale connection changed current script policy")
	}
}

func TestDisabledScriptSessionKeepsLegacyToolsAndWritesNothing(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.WorkRoot = t.TempDir()
	cfg.TokenFile = filepath.Join(cfg.WorkRoot, "token")
	cfg.ScriptStateDirectory = filepath.Join(cfg.WorkRoot, "private")
	r := NewRegistry(cfg)
	id, close := r.BeginScriptConnection(context.Background(), approvedscript.SessionOwner{})
	defer close()
	if id != "" || r.ScriptStatus(id).State != "disabled" || len(r.ListTools()) != 4 {
		t.Fatal("legacy mode changed")
	}
	if _, err := os.Stat(cfg.ScriptStateDirectory); !os.IsNotExist(err) {
		t.Fatal("disabled feature wrote private files")
	}
}

func TestInvalidScriptStateStillProtectsDeviceCredential(t *testing.T) {
	cfg := config.DefaultConfig()
	cfg.WorkRoot = t.TempDir()
	cfg.TokenFile = filepath.Join(cfg.WorkRoot, "token")
	cfg.ScriptExecutionEnabled = true
	cfg.ScriptStateDirectory = cfg.WorkRoot // Invalid: private state contains business data.
	r := NewRegistry(cfg)
	if r.ScriptStatus("").State != "unavailable" {
		t.Fatal("invalid state path became usable")
	}
	if _, err := r.sb.ResolvePath(cfg.TokenFile); err == nil {
		t.Fatal("invalid private directory disabled credential protection")
	}
}
