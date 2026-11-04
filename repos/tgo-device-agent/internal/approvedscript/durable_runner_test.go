package approvedscript

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func durableFixture(t *testing.T) (*Catalog, *executionJournal, scriptExecutionRequest, []byte) {
	t.Helper()
	c, g, hash := runnerFixture(t, "python", "from pathlib import Path\nimport json\np=Path('counter')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\nprint(json.dumps({'day':12,'include':False}))\n")
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		t.Fatal(err)
	}
	j, err := newJournal(g.WorkingDirectory, journalScope{ProjectID: "11111111-1111-4111-8111-111111111111", DeviceID: "22222222-2222-4222-8222-222222222222"}, key, c.guard)
	if err != nil {
		t.Fatal(err)
	}
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: g.ScriptID, GrantDigest: hash, TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 2}
	p.Digest = protocol.ScriptPolicyDigest(p)
	if err := j.applyPolicy(context.Background(), p); err != nil {
		t.Fatal(err)
	}
	request := executionRequest(p)
	return c, j, request, key
}

func TestDurableScriptReplaysRealResultWithoutRepeatingWork(t *testing.T) {
	c, j, request, key := durableFixture(t)
	parameters := map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}
	first := c.runDurableReadOnly(context.Background(), j, request.ExecutionID, request.Policy, request.ContextDigest, parameters)
	if first.Receipt.Status != "succeeded" || first.Replayed {
		t.Fatalf("first run: %#v", first)
	}
	second := c.runDurableReadOnly(context.Background(), reopenJournal(t, j, key), request.ExecutionID, request.Policy, request.ContextDigest, parameters)
	if second.Receipt.Status != "succeeded" || !second.Replayed || string(second.Receipt.Fields["day"]) != "12" {
		t.Fatalf("replay: %#v", second)
	}
	data, err := os.ReadFile(filepath.Join(j.root, "counter"))
	if err != nil || string(data) != "1" {
		t.Fatal("duplicate request repeated a real script")
	}
	parameters["day"] = json.RawMessage(`13`)
	changed := c.runDurableReadOnly(context.Background(), j, request.ExecutionID, request.Policy, request.ContextDigest, parameters)
	if !errors.Is(changed.Err, errJournalConflict) || len(changed.Receipt.Fields) != 0 {
		t.Fatalf("different input reused old result: %#v", changed)
	}
}

func TestDurableScriptDoesNotStartWithoutReservationOrRepeatUnknown(t *testing.T) {
	for _, stage := range []string{"claim", "finish"} {
		t.Run(stage, func(t *testing.T) {
			c, j, request, key := durableFixture(t)
			calls := 0
			j.writeOverride = func(data []byte) error {
				calls++
				if (stage == "claim" && calls == 1) || (stage == "finish" && calls == 2) {
					return os.ErrPermission
				}
				return j.atomicSave(data)
			}
			parameters := map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}
			result := c.runDurableReadOnly(context.Background(), j, request.ExecutionID, request.Policy, request.ContextDigest, parameters)
			if result.Err == nil || len(result.Receipt.Fields) != 0 {
				t.Fatal("persistence failure returned a business result")
			}
			path := filepath.Join(j.root, "counter")
			if stage == "claim" {
				if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
					t.Fatal("script started before claim was durable")
				}
				return
			}
			if result.Receipt.Status != "unknown" {
				t.Fatalf("lost completion not marked unknown: %#v", result)
			}
			replay := c.runDurableReadOnly(context.Background(), reopenJournal(t, j, key), request.ExecutionID, request.Policy, request.ContextDigest, parameters)
			if replay.Receipt.Status != "unknown" || !replay.Replayed {
				t.Fatalf("unknown execution restarted: %#v", replay)
			}
			data, err := os.ReadFile(path)
			if err != nil || strings.TrimSpace(string(data)) != "1" {
				t.Fatal("unconfirmed script ran twice")
			}
		})
	}
}
