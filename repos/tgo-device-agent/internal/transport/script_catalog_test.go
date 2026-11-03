package transport

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func TestScriptCatalogIsCurrentMetadataOverAuthenticatedTCP(t *testing.T) {
	var scriptPath string
	f := startConfiguredFixture(t, func(cfg *config.Config) {
		scriptPath = filepath.Join(cfg.WorkRoot, "daily.py")
		runtimePath := filepath.Join(cfg.WorkRoot, "runtime.exe")
		code, runtime := []byte("print('catalog only')"), []byte("test runtime, not executed")
		for path, data := range map[string][]byte{scriptPath: code, runtimePath: runtime} {
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
		}
		digest := func(data []byte) string { h := sha256.Sum256(data); return hex.EncodeToString(h[:]) }
		cfg.ApprovedScripts = []protocol.ScriptGrant{{Approved: true, ScriptID: "daily_report", Title: "业务报表", Version: "v1", Runner: "python", ScriptPath: scriptPath, ContentSHA256: digest(code), Interpreter: runtimePath, InterpreterSHA256: digest(runtime), WorkingDirectory: cfg.WorkRoot, Effect: "read_only", ResultFields: []protocol.ScriptParameter{{Name: "ready", Kind: "boolean", Required: true}}, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}}
	}, nil)
	conn := f.accept(t, true)
	call := func(params json.RawMessage) protocol.Response {
		t.Helper()
		request, _ := protocol.NewRequest(81, "scripts/list", nil)
		request.Params = params
		if err := json.NewEncoder(conn).Encode(request); err != nil {
			t.Fatal(err)
		}
		var reply protocol.Response
		if err := json.NewDecoder(conn).Decode(&reply); err != nil {
			t.Fatal(err)
		}
		return reply
	}
	reply := call(json.RawMessage(`{}`))
	var catalog protocol.ScriptCatalog
	if reply.Error != nil || json.Unmarshal(reply.Result, &catalog) != nil || len(catalog.Scripts) != 1 {
		t.Fatalf("catalog unavailable: %#v", reply)
	}
	if catalog.Scripts[0].Parameters == nil || strings.Contains(string(reply.Result), f.client.cfg.WorkRoot) {
		t.Fatal("catalog has null parameters or private paths")
	}
	for _, tool := range f.client.registry.ListTools() {
		if strings.HasPrefix(tool.Name, "script") {
			t.Fatal("discovery alone exposed an executable script tool")
		}
	}
	for _, params := range []string{`null`, `[]`, `{"script_path":"private.py"}`, `{"command":"whoami"}`} {
		if reply := call(json.RawMessage(params)); reply.Error == nil || reply.Error.Code != protocol.ErrInvalidParams {
			t.Fatalf("unexpected discovery params accepted: %s", params)
		}
	}
	if err := os.WriteFile(scriptPath, []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	reply = call(json.RawMessage(`{}`))
	if reply.Error == nil || strings.Contains(reply.Error.Message, f.client.cfg.WorkRoot) {
		t.Fatal("stale catalog accepted or private path exposed")
	}
}

func TestEmptyAndInvalidLocalCatalogsAreDistinguishable(t *testing.T) {
	for _, invalid := range []bool{false, true} {
		f := startConfiguredFixture(t, func(cfg *config.Config) {
			if invalid {
				cfg.ApprovedScripts = []protocol.ScriptGrant{{Approved: true, ScriptID: "bad"}}
			}
		}, nil)
		conn := f.accept(t, true)
		request, _ := protocol.NewRequest(82, "scripts/list", nil)
		if err := json.NewEncoder(conn).Encode(request); err != nil {
			t.Fatal(err)
		}
		var reply protocol.Response
		if err := json.NewDecoder(conn).Decode(&reply); err != nil {
			t.Fatal(err)
		}
		if invalid {
			if reply.Error == nil {
				t.Fatal("invalid local grant silently became an empty catalog")
			}
		} else if reply.Error != nil || string(reply.Result) != `{"protocol_version":1,"scripts":[]}` {
			t.Fatalf("wrong empty catalog: %s", reply.Result)
		}
	}
}
