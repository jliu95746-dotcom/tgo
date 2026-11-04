package tools

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func TestReadPolicyNarrowsToolsAndDirectory(t *testing.T) {
	root := t.TempDir()
	allowed := filepath.Join(root, "approved")
	if err := os.Mkdir(allowed, 0o700); err != nil {
		t.Fatal(err)
	}
	inside := filepath.Join(allowed, "answer.txt")
	outside := filepath.Join(root, "private.txt")
	for _, path := range []string{inside, outside} {
		if err := os.WriteFile(path, []byte("fixture"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	r := NewRegistry(cfg)
	if len(r.ListTools()) != 4 {
		t.Fatal("unrestricted device should list its four built-in tools")
	}
	policy := protocol.ReadPolicy{
		Revision: 1, ToolName: "fs_read", AllowedDirectory: allowed,
		MaxLines: 100, MaxOffset: 1000,
		Digest: protocol.ReadPolicyDigest(1, "fs_read", allowed, 100, 1000),
	}
	ack, err := r.ApplyReadPolicy(policy)
	if err != nil || ack.Digest != policy.Digest || ack.Revision != 1 {
		t.Fatalf("policy was not acknowledged: %#v, %v", ack, err)
	}
	if listed := r.ListTools(); len(listed) != 1 || listed[0].Name != "fs_read" {
		t.Fatalf("read-only policy advertised unavailable tools: %#v", listed)
	}
	readSchema := r.ListTools()[0].InputSchema
	required, ok := readSchema["required"].([]string)
	if !ok || len(required) != 2 || required[0] != "path" || required[1] != "limit" {
		t.Fatalf("read policy did not require path and limit: %#v", readSchema)
	}
	properties, ok := readSchema["properties"].(map[string]interface{})
	if !ok {
		t.Fatalf("read policy omitted argument schemas: %#v", readSchema)
	}
	limit, ok := properties["limit"].(map[string]interface{})
	if !ok || limit["minimum"] != 1 || limit["maximum"] != 100 {
		t.Fatalf("read policy advertised an invalid line range: %#v", limit)
	}
	offset, ok := properties["offset"].(map[string]interface{})
	if !ok || offset["minimum"] != 0 || offset["maximum"] != 1000 {
		t.Fatalf("read policy advertised an invalid offset range: %#v", offset)
	}
	if readSchema["additionalProperties"] != false {
		t.Fatalf("read policy advertised unsupported arguments: %#v", readSchema)
	}
	call := func(name, path string) *protocol.ToolCallResult {
		return r.CallTool(context.Background(), name, map[string]interface{}{"path": path, "limit": 20})
	}
	if result := call("fs_read", inside); result.IsError {
		t.Fatalf("approved read failed: %#v", result)
	}
	for _, path := range []string{outside, filepath.Join(allowed, "..", "private.txt"), "answer.txt"} {
		if result := call("fs_read", path); !result.IsError {
			t.Fatalf("read outside policy succeeded: %s", path)
		}
	}
	link := filepath.Join(allowed, "outside-link.txt")
	if err := os.Symlink(outside, link); err == nil {
		if result := call("fs_read", link); !result.IsError {
			t.Fatal("symlink escaped the active read policy")
		}
	}
	if result := call("shell_exec", inside); !result.IsError {
		t.Fatal("command was allowed by a read-only policy")
	}
	for _, args := range []map[string]interface{}{
		{"path": inside},
		{"path": inside, "limit": 101},
		{"path": inside, "limit": 20, "offset": -1},
		{"path": inside, "limit": 20, "encoding": "utf-16"},
		{"path": inside, "limit": 20, "unknown": true},
	} {
		if result := r.CallTool(context.Background(), "fs_read", args); !result.IsError {
			t.Fatalf("disallowed read parameters succeeded: %#v", args)
		}
	}
	r.ResetPolicyForConnection()
	if listed := r.ListTools(); len(listed) != 0 {
		t.Fatalf("reconnect advertised tools before policy confirmation: %#v", listed)
	}
	if result := call("fs_read", inside); !result.IsError {
		t.Fatal("old policy survived reconnect")
	}
}

func TestReadPolicyRejectsWideningAndStaleDigest(t *testing.T) {
	root := t.TempDir()
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	r := NewRegistry(cfg)
	outside := t.TempDir()
	policy := protocol.ReadPolicy{
		Revision: 1, ToolName: "fs_read", AllowedDirectory: outside,
		MaxLines: 100, MaxOffset: 1000,
		Digest: protocol.ReadPolicyDigest(1, "fs_read", outside, 100, 1000),
	}
	if _, err := r.ApplyReadPolicy(policy); err == nil {
		t.Fatal("server widened the local sandbox")
	}
	policy.AllowedDirectory = root
	if _, err := r.ApplyReadPolicy(policy); err == nil {
		t.Fatal("stale digest was accepted")
	}
	if listed := r.ListTools(); len(listed) != 0 {
		t.Fatalf("rejected policy advertised tools: %#v", listed)
	}
	if result := r.CallTool(context.Background(), "fs_read", map[string]interface{}{"path": root}); !result.IsError {
		t.Fatal("tool remained available after rejected policy")
	}
}
