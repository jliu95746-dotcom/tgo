//go:build windows && (amd64 || arm64)

package approvedscript

import (
	"context"
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func TestSessionBudgetHasCurrentScopeAndNeverExecutes(t *testing.T) {
	c, g, digest := runnerFixture(t, "python", "import json\nprint(json.dumps({'day':12,'include':False}))")
	root := filepath.Join(t.TempDir(), "private")
	s := openFixtureSession(t, c, root)
	policy := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: g.ScriptID, GrantDigest: digest, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	policy.Digest = protocol.ScriptPolicyDigest(policy)
	q := protocol.ScriptBudgetQuery{ProtocolVersion: 1, SessionID: s.Status().SessionID, PolicyDigest: policy.Digest}
	if _, err := s.Budget(context.Background(), q); err == nil {
		t.Fatal("unconfirmed policy returned budget")
	}
	if _, err := s.Apply(q.SessionID, policy); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		budget, err := s.Budget(context.Background(), q)
		if err != nil || budget.PolicyUsed != 0 || budget.GrantUsed != 0 || budget.PolicyMaxRuns != 1 || budget.GrantMaxRuns != g.MaxRuns || budget.SessionID != q.SessionID {
			t.Fatalf("before run: %#v %v", budget, err)
		}
	}
	args := map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}
	result := s.RunReadOnly(context.Background(), q.SessionID, "33333333-3333-4333-8333-333333333333", strings.Repeat("c", 64), args)
	if result.Status != "succeeded" {
		t.Fatalf("execution: %#v", result)
	}
	budget, err := s.Budget(context.Background(), q)
	if err != nil || budget.PolicyUsed != 1 || budget.GrantUsed != 1 {
		t.Fatalf("after run: %#v %v", budget, err)
	}
	wrong := q
	wrong.PolicyDigest = strings.Repeat("0", 64)
	if _, err := s.Budget(context.Background(), wrong); err == nil {
		t.Fatal("foreign policy accepted")
	}
	s.Close()
	if _, err := s.Budget(context.Background(), q); err == nil {
		t.Fatal("closed connection returned budget")
	}
	next := openFixtureSession(t, c, root)
	if _, err := next.Apply(next.Status().SessionID, policy); err != nil {
		t.Fatal(err)
	}
	if _, err := next.Budget(context.Background(), q); err == nil {
		t.Fatal("old connection returned budget")
	}
	q.SessionID = next.Status().SessionID
	budget, err = next.Budget(context.Background(), q)
	if err != nil || budget.PolicyUsed != 1 || budget.GrantUsed != 1 {
		t.Fatal("reconnect reset observed usage")
	}
	next.Suspend()
	if _, err := next.Budget(context.Background(), q); err == nil {
		t.Fatal("suspended connection returned budget")
	}
}
