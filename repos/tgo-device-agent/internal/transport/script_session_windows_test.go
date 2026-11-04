//go:build windows && (amd64 || arm64)

package transport

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func acceptScriptFixture(t *testing.T, f *fixtureServer) net.Conn {
	t.Helper()
	conn := f.accept(t, false)
	conn.SetDeadline(time.Now().Add(8 * time.Second))
	var request protocol.Request
	if err := json.NewDecoder(conn).Decode(&request); err != nil {
		t.Fatal(err)
	}
	response, _ := protocol.NewResponse(request.ID, protocol.AuthResult{Status: "ok", ProjectID: "11111111-1111-4111-8111-111111111111", DeviceID: "22222222-2222-4222-8222-222222222222"})
	if err := json.NewEncoder(conn).Encode(response); err != nil {
		t.Fatal(err)
	}
	return conn
}

func scriptFixtureCall(t *testing.T, conn net.Conn, method string, params json.RawMessage) protocol.Response {
	t.Helper()
	request, _ := protocol.NewRequest(85, method, nil)
	request.Params = params
	if err := json.NewEncoder(conn).Encode(request); err != nil {
		t.Fatal(err)
	}
	var response protocol.Response
	if err := json.NewDecoder(conn).Decode(&response); err != nil {
		t.Fatal(err)
	}
	return response
}

func TestAuthenticatedScriptStatusAndReconnectUseDifferentBindings(t *testing.T) {
	f := startConfiguredFixture(t, func(cfg *config.Config) {
		cfg.ScriptExecutionEnabled = true
		cfg.ScriptStateDirectory = filepath.Join(cfg.WorkRoot, "private")
	}, nil)
	first := acceptScriptFixture(t, f)
	reply := scriptFixtureCall(t, first, "scripts/status", json.RawMessage(`{}`))
	var status protocol.ScriptSessionStatus
	if reply.Error != nil || json.Unmarshal(reply.Result, &status) != nil || status.State != "awaiting_policy" {
		t.Fatalf("status: %#v %s", reply, string(reply.Result))
	}
	first.Close()
	second := acceptScriptFixture(t, f)
	reply = scriptFixtureCall(t, second, "scripts/status", json.RawMessage(`{}`))
	var next protocol.ScriptSessionStatus
	if json.Unmarshal(reply.Result, &next) != nil || next.SessionID == status.SessionID || next.SessionID == "" {
		t.Fatal("reconnection reused a script session")
	}
	request, _ := json.Marshal(protocol.ScriptPolicyApply{SessionID: status.SessionID})
	if result := scriptFixtureCall(t, second, "scripts/policy/apply", request); result.Error == nil {
		t.Fatal("old connection policy was accepted")
	}
	for _, raw := range []string{`null`, `[]`, `{"owner":"other"}`} {
		if reply := scriptFixtureCall(t, second, "scripts/status", json.RawMessage(raw)); reply.Error == nil {
			t.Fatal("status accepted configuration input")
		}
	}
	if reply := scriptFixtureCall(t, second, "scripts/run", json.RawMessage(`{}`)); reply.Error == nil || reply.Error.Code != protocol.ErrInvalidParams {
		t.Fatal("execution accepted a request without a bound identity")
	}
}

func TestAuthenticatedScriptPolicyAcknowledgementAndFileModeSwitch(t *testing.T) {
	f := startConfiguredFixture(t, func(cfg *config.Config) {
		cfg.ScriptExecutionEnabled = true
		cfg.ScriptStateDirectory = filepath.Join(cfg.WorkRoot, "private")
		scriptPath := filepath.Join(cfg.WorkRoot, "daily.py")
		runtimePath := filepath.Join(cfg.WorkRoot, "runtime.exe")
		code, runtime := []byte("print('policy fixture only')"), []byte("policy fixture; never executed")
		digest := func(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
		for path, data := range map[string][]byte{scriptPath: code, runtimePath: runtime} {
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
		}
		cfg.ApprovedScripts = []protocol.ScriptGrant{{Approved: true, ScriptID: "daily_report", Title: "业务报表", Version: "v1", Runner: "python", ScriptPath: scriptPath, ContentSHA256: digest(code), Interpreter: runtimePath, InterpreterSHA256: digest(runtime), WorkingDirectory: cfg.WorkRoot, Effect: "read_only", ResultFields: []protocol.ScriptParameter{{Name: "ready", Kind: "boolean", Required: true}}, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}}
	}, nil)
	conn := acceptScriptFixture(t, f)
	status := func() protocol.ScriptSessionStatus {
		t.Helper()
		response := scriptFixtureCall(t, conn, "scripts/status", json.RawMessage(`{}`))
		var value protocol.ScriptSessionStatus
		if response.Error != nil || json.Unmarshal(response.Result, &value) != nil {
			t.Fatal("script status unavailable")
		}
		return value
	}
	initial := status()
	reply := scriptFixtureCall(t, conn, "scripts/list", json.RawMessage(`{}`))
	var catalog protocol.ScriptCatalog
	if reply.Error != nil || json.Unmarshal(reply.Result, &catalog) != nil || len(catalog.Scripts) != 1 {
		t.Fatal("local reviewed catalog unavailable")
	}
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: "daily_report", GrantDigest: catalog.Scripts[0].GrantDigest, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	p.Digest = protocol.ScriptPolicyDigest(p)
	request, _ := json.Marshal(protocol.ScriptPolicyApply{SessionID: initial.SessionID, Policy: p})
	budgetQuery, _ := json.Marshal(protocol.ScriptBudgetQuery{ProtocolVersion: 1, SessionID: initial.SessionID, PolicyDigest: p.Digest})
	if scriptFixtureCall(t, conn, "scripts/budget/get", budgetQuery).Error == nil {
		t.Fatal("unconfirmed connection returned budget")
	}
	apply := func() {
		t.Helper()
		reply := scriptFixtureCall(t, conn, "scripts/policy/apply", request)
		var ack protocol.ScriptPolicyAck
		if reply.Error != nil || json.Unmarshal(reply.Result, &ack) != nil || ack.SessionID != initial.SessionID || ack.Digest != p.Digest || ack.Revision != p.Revision {
			t.Fatalf("unbound acknowledgement: %#v", reply)
		}
		if status().State != "active" {
			t.Fatal("acknowledged policy is inactive")
		}
		if len(f.client.registry.ListTools()) != 0 {
			t.Fatal("script policy exposed generic executable tools")
		}
		reply = scriptFixtureCall(t, conn, "scripts/budget/get", budgetQuery)
		var budget protocol.ScriptBudget
		if reply.Error != nil || json.Unmarshal(reply.Result, &budget) != nil || budget.SessionID != initial.SessionID || budget.PolicyDigest != p.Digest || budget.GrantDigest != p.GrantDigest || budget.PolicyUsed != 0 || budget.GrantUsed != 0 || budget.PolicyMaxRuns != 1 || budget.GrantMaxRuns != 1 {
			t.Fatalf("bound metadata query consumed or changed budget: %#v", reply)
		}
	}
	apply()
	for _, raw := range []string{`null`, `{}`, `[]`, `{"protocol_version":1,"session_id":null,"policy_digest":null}`, string(budgetQuery[:len(budgetQuery)-1]) + `,"command":"private-marker"}`} {
		if reply := scriptFixtureCall(t, conn, "scripts/budget/get", json.RawMessage(raw)); reply.Error == nil || status().State != "active" {
			t.Fatal("malformed budget query accepted or changed active policy")
		}
	}
	stale, _ := json.Marshal(protocol.ScriptPolicyApply{SessionID: "ffffffffffffffffffffffffffffffff", Policy: p})
	if scriptFixtureCall(t, conn, "scripts/policy/apply", stale).Error == nil || status().State != "active" {
		t.Fatal("stale binding changed current policy")
	}
	for _, raw := range []string{`null`, `{}`, `{"session_id":null,"policy":null}`, `{"session_id":"` + initial.SessionID + `","policy":null}`} {
		if scriptFixtureCall(t, conn, "scripts/policy/apply", json.RawMessage(raw)).Error == nil || status().State != "awaiting_policy" {
			t.Fatal("malformed current update retained an active policy")
		}
		apply()
	}
	readPolicy := protocol.ReadPolicy{Revision: 1, ToolName: "fs_read", AllowedDirectory: f.client.cfg.WorkRoot, MaxLines: 10, MaxOffset: 0}
	readPolicy.Digest = protocol.ReadPolicyDigest(readPolicy.Revision, readPolicy.ToolName, readPolicy.AllowedDirectory, readPolicy.MaxLines, readPolicy.MaxOffset)
	readRequest, _ := json.Marshal(readPolicy)
	if scriptFixtureCall(t, conn, "policy/apply", readRequest).Error != nil || status().State != "awaiting_policy" {
		t.Fatal("file mode retained an active script policy")
	}
	if got := f.client.registry.ListTools(); len(got) != 1 || got[0].Name != "fs_read" {
		t.Fatal("file policy did not narrow tools")
	}
	if scriptFixtureCall(t, conn, "scripts/budget/get", budgetQuery).Error == nil {
		t.Fatal("file mode returned active script budget")
	}
	apply()
}
