//go:build windows && (amd64 || arm64)

package approvedscript

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func TestBoundReceiptCannotLeakAnotherBusinessContext(t *testing.T) {
	c, g, grant := runnerFixture(t, "python", "print('{\"day\":12,\"include\":false}')")
	s := openFixtureSession(t, c, filepath.Join(t.TempDir(), "private"))
	status := s.Status()
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: g.ScriptID, GrantDigest: grant, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	p.Digest = protocol.ScriptPolicyDigest(p)
	if _, err := s.Apply(status.SessionID, p); err != nil {
		t.Fatal(err)
	}
	values := map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}
	prepared, err := c.Prepare(g.ScriptID, grant, values)
	if err != nil {
		t.Fatal(err)
	}
	q := protocol.ScriptReceiptQuery{ProtocolVersion: 1, SessionID: status.SessionID, ExecutionID: "33333333-3333-4333-8333-333333333333", PolicyDigest: p.Digest, ParametersDigest: prepared.ParametersDigest, ContextDigest: strings.Repeat("c", 64)}
	if result := s.Receipt(context.Background(), q); result.Status != "not_found" {
		t.Fatalf("lookup: %#v", result)
	}
	if result := s.RunReadOnly(context.Background(), q.SessionID, q.ExecutionID, q.ContextDigest, values); result.Status != "succeeded" {
		t.Fatalf("run: %#v", result)
	}
	if result := s.Receipt(context.Background(), q); result.Status != "succeeded" || string(result.Fields["day"]) != "12" || !result.Replayed {
		t.Fatalf("receipt: %#v", result)
	}
	other := q
	other.ContextDigest = strings.Repeat("d", 64)
	if result := s.Receipt(context.Background(), other); result.Status != "rejected" || len(result.Fields) != 0 {
		t.Fatal("wrong context read a result")
	}
	s.Close()
	if result := s.Receipt(context.Background(), q); result.Status != "rejected" || len(result.Fields) != 0 {
		t.Fatal("closed connection read a result")
	}
}

func TestBoundExecutionChecksPolicyAndInputBeforeClaiming(t *testing.T) {
	c, g, grant := runnerFixture(t, "python", "from pathlib import Path\np=Path('count')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nprint('{\"day\":12,\"include\":false}')")
	s := openFixtureSession(t, c, filepath.Join(t.TempDir(), "private"))
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: g.ScriptID, GrantDigest: grant, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	p.Digest = protocol.ScriptPolicyDigest(p)
	id := s.Status().SessionID
	if _, err := s.Apply(id, p); err != nil {
		t.Fatal(err)
	}
	values := map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}
	prepared, err := c.Prepare(g.ScriptID, grant, values)
	if err != nil {
		t.Fatal(err)
	}
	q := protocol.ScriptReceiptQuery{ProtocolVersion: 1, SessionID: id, ExecutionID: "33333333-3333-4333-8333-333333333333", PolicyDigest: p.Digest, ParametersDigest: prepared.ParametersDigest, ContextDigest: strings.Repeat("c", 64)}
	request := protocol.ScriptExecuteRequest{ScriptReceiptQuery: q, Parameters: values, TimeoutSeconds: 10}
	wrong := request
	wrong.PolicyDigest = strings.Repeat("d", 64)
	if result := s.ExecuteReadOnly(context.Background(), wrong); result.Status != "rejected" {
		t.Fatal("wrong policy was executed")
	}
	wrong = request
	wrong.ParametersDigest = strings.Repeat("e", 64)
	if result := s.ExecuteReadOnly(context.Background(), wrong); result.Status != "rejected" {
		t.Fatal("different inputs were executed")
	}
	if _, err := os.Stat(filepath.Join(g.WorkingDirectory, "count")); !os.IsNotExist(err) {
		t.Fatal("rejected request ran code")
	}
	for attempt := 0; attempt < 2; attempt++ {
		result := s.ExecuteReadOnly(context.Background(), request)
		if result.Status != "succeeded" || result.ContextDigest != q.ContextDigest || result.PolicyDigest != p.Digest || result.ParametersDigest != q.ParametersDigest || result.Replayed != (attempt == 1) {
			t.Fatalf("bound run: %#v", result)
		}
	}
	value, err := os.ReadFile(filepath.Join(g.WorkingDirectory, "count"))
	if err != nil || string(value) != "1" {
		t.Fatal("same execution was repeated")
	}
}

func TestCancellationBeforeDispatchCannotStartALateExecution(t *testing.T) {
	c, g, grant := runnerFixture(t, "python", "from pathlib import Path\nPath('late-run').touch()\nprint('{\"day\":12,\"include\":false}')")
	s := openFixtureSession(t, c, filepath.Join(t.TempDir(), "private"))
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: g.ScriptID, GrantDigest: grant, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	p.Digest = protocol.ScriptPolicyDigest(p)
	id := s.Status().SessionID
	if _, err := s.Apply(id, p); err != nil {
		t.Fatal(err)
	}
	values := map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}
	prepared, err := c.Prepare(g.ScriptID, grant, values)
	if err != nil {
		t.Fatal(err)
	}
	q := protocol.ScriptReceiptQuery{ProtocolVersion: 1, SessionID: id, ExecutionID: "33333333-3333-4333-8333-333333333333", PolicyDigest: p.Digest, ParametersDigest: prepared.ParametersDigest, ContextDigest: strings.Repeat("c", 64)}
	if s.Cancel(q) {
		t.Fatal("an unstarted process was reported as signaled")
	}
	request := protocol.ScriptExecuteRequest{ScriptReceiptQuery: q, Parameters: values, TimeoutSeconds: 10}
	if result := s.ExecuteReadOnly(context.Background(), request); result.Status != "canceled" || len(result.Fields) != 0 {
		t.Fatalf("late execution: %#v", result)
	}
	if _, err := os.Stat(filepath.Join(g.WorkingDirectory, "late-run")); !os.IsNotExist(err) {
		t.Fatal("canceled queued execution started")
	}
	// Canceling one identity does not consume the only available run.
	request.ExecutionID = "44444444-4444-4444-8444-444444444444"
	if result := s.ExecuteReadOnly(context.Background(), request); result.Status != "succeeded" {
		t.Fatalf("unrelated execution blocked: %#v", result)
	}
}
