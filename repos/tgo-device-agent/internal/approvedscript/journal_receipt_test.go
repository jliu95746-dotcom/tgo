package approvedscript

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/scriptprocess"
)

func TestJournalMissingHistoryCannotBeResetByPolicyReapply(t *testing.T) {
	j, p, _ := journalFixture(t)
	if _, err := j.claim(context.Background(), executionRequest(p)); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(j.root, "executions.bin")
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := j.applyPolicy(context.Background(), p); !errors.Is(err, errJournalUnavailable) {
		t.Fatal("reapplying policy recreated missing execution history and its budget")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("missing journal was replaced")
	}
}

func TestJournalReceiptLookupDoesNotClaimOrWrite(t *testing.T) {
	j, p, _ := journalFixture(t)
	path := filepath.Join(j.root, "executions.bin")
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	request := executionRequest(p)
	if _, found, err := j.lookup(context.Background(), request.ExecutionID); err != nil || found {
		t.Fatal("absent receipt was not distinguished")
	}
	after, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("lookup changed the journal")
	}
	claim, err := j.claim(context.Background(), request)
	if err != nil || !claim.Fresh {
		t.Fatal("lookup consumed the run budget")
	}
	if receipt, found, err := j.lookup(context.Background(), request.ExecutionID); err != nil || !found || receipt.Status != "unknown" || len(receipt.Fields) != 0 {
		t.Fatal("in-progress receipt was treated as a completed execution")
	}
	if _, err := j.finish(context.Background(), request, claim.Token, evaluation{Outcome: scriptprocess.Succeeded, Fields: map[string]json.RawMessage{"answer": json.RawMessage(`true`)}}); err != nil {
		t.Fatal(err)
	}
	receipt, found, err := j.lookup(context.Background(), request.ExecutionID)
	if err != nil || !found || receipt.Status != "succeeded" || string(receipt.Fields["answer"]) != "true" {
		t.Fatal("completed receipt could not be read")
	}
}

func TestJournalRequiredHistoryCannotBootstrap(t *testing.T) {
	j, _, key := journalFixture(t)
	if err := os.Remove(filepath.Join(j.root, "executions.bin")); err != nil {
		t.Fatal(err)
	}
	if _, err := openJournal(j.root, j.scope, key, j.guard, true); !errors.Is(err, errJournalUnavailable) {
		t.Fatal("a required journal was opened as empty")
	}
}

func TestJournalInputDigestMatchesControlVector(t *testing.T) {
	label, _ := json.Marshal("女包<&> 'quote' " + string(rune(92)))
	values := map[string]json.RawMessage{"count": json.RawMessage(`12`), "enabled": json.RawMessage(`false`), "label": label}
	encoded, err := json.Marshal(values)
	if err != nil {
		t.Fatal(err)
	}
	value := digest([]byte("tgo-approved-script-input-v1\n" + strings.Repeat("a", 64) + "\n" + string(encoded)))
	if value!="5f41350924e7bb44b73eec27a14db4555f4435dd247db84b091f27b497260163" { t.Fatal("input digest differs from the Control contract") }
}
