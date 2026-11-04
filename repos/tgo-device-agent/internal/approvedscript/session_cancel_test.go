package approvedscript

import (
	"context"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func TestCancellationRequiresExactExecutionScope(t *testing.T) {
	query := protocol.ScriptReceiptQuery{ProtocolVersion: 1, SessionID: strings.Repeat("a", 32), ExecutionID: "33333333-3333-4333-8333-333333333333", PolicyDigest: strings.Repeat("b", 64), ParametersDigest: strings.Repeat("c", 64), ContextDigest: strings.Repeat("d", 64)}
	first, cancelFirst := context.WithCancel(context.Background())
	defer cancelFirst()
	second, cancelSecond := context.WithCancel(context.Background())
	defer cancelSecond()
	other := query
	other.ExecutionID = "44444444-4444-4444-8444-444444444444"
	session := &Session{ctx: context.Background(), id: query.SessionID, active: map[uint64]context.CancelFunc{1: cancelFirst, 2: cancelSecond}, activeScopes: map[uint64]protocol.ScriptReceiptQuery{1: query, 2: other}}
	for _, field := range []string{"protocol", "session", "execution", "policy", "parameters", "context"} {
		wrong := query
		switch field {
		case "protocol":
			wrong.ProtocolVersion = 2
		case "session":
			wrong.SessionID = strings.Repeat("e", 32)
		case "execution":
			wrong.ExecutionID = "55555555-5555-4555-8555-555555555555"
		case "policy":
			wrong.PolicyDigest = strings.Repeat("e", 64)
		case "parameters":
			wrong.ParametersDigest = strings.Repeat("e", 64)
		case "context":
			wrong.ContextDigest = strings.Repeat("e", 64)
		}
		if session.Cancel(wrong) || first.Err() != nil || second.Err() != nil {
			t.Fatalf("%s mismatch canceled unrelated work", field)
		}
	}
	// No current policy is required to stop an old, exactly bound execution.
	if !session.Cancel(query) || first.Err() == nil || second.Err() != nil {
		t.Fatal("cancellation did not stop only the requested execution")
	}
	delete(session.active, 1)
	delete(session.activeScopes, 1)
	if session.Cancel(query) || second.Err() != nil {
		t.Fatal("late cancellation affected another execution")
	}
}
