package approvedscript

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/sandbox"
	"github.com/tgoai/tgo-device-agent/internal/scriptprocess"
)

func journalFixture(t *testing.T) (*executionJournal, protocol.ScriptPolicy, []byte) {
	t.Helper()
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	j, err := newJournal(root, journalScope{ProjectID: "11111111-1111-4111-8111-111111111111", DeviceID: "22222222-2222-4222-8222-222222222222"}, key, sandbox.New(cfg))
	if err != nil {
		t.Fatal(err)
	}
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: "daily_report", GrantDigest: strings.Repeat("a", 64), TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	p.Digest = protocol.ScriptPolicyDigest(p)
	if err := j.applyPolicy(context.Background(), p); err != nil {
		t.Fatal(err)
	}
	return j, p, key
}

func executionRequest(p protocol.ScriptPolicy) scriptExecutionRequest {
	return scriptExecutionRequest{ExecutionID: "33333333-3333-4333-8333-333333333333", Policy: p, ParametersDigest: strings.Repeat("b", 64), ContextDigest: strings.Repeat("c", 64), GrantMaxRuns: 2}
}

func reopenJournal(t *testing.T, j *executionJournal, key []byte) *executionJournal {
	t.Helper()
	copy, err := newJournal(j.root, j.scope, key, j.guard)
	if err != nil {
		t.Fatal(err)
	}
	return copy
}

func TestJournalReservesBeforeExecutionAndReplaysAfterReopen(t *testing.T) {
	j, p, key := journalFixture(t)
	request := executionRequest(p)
	claim, err := j.claim(context.Background(), request)
	if err != nil || !claim.Fresh {
		t.Fatalf("claim: %#v %v", claim, err)
	}
	result := evaluation{Outcome: scriptprocess.Succeeded, Fields: map[string]json.RawMessage{"answer": json.RawMessage(`"fixture-private-customer"`)}}
	if _, err := j.finish(context.Background(), request, claim.Token, result); err != nil {
		t.Fatal(err)
	}
	copy := reopenJournal(t, j, key)
	replay, err := copy.claim(context.Background(), request)
	if err != nil || replay.Fresh || replay.Receipt.Status != "succeeded" || string(replay.Receipt.Fields["answer"]) != `"fixture-private-customer"` {
		t.Fatalf("replay: %#v %v", replay, err)
	}
	data, err := os.ReadFile(filepath.Join(j.root, "executions.bin"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(data, []byte("fixture-private-customer")) || bytes.Contains(data, []byte(p.GrantDigest)) {
		t.Fatal("journal persisted plaintext business data or bindings")
	}
	request.ExecutionID = "44444444-4444-4444-8444-444444444444"
	if _, err := copy.claim(context.Background(), request); !errors.Is(err, errJournalBudget) {
		t.Fatalf("restart reset budget: %v", err)
	}
}

func TestJournalUnknownReservationCannotExecuteAgain(t *testing.T) {
	j, p, key := journalFixture(t)
	request := executionRequest(p)
	if _, err := j.claim(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	copy := reopenJournal(t, j, key)
	claim, err := copy.claim(context.Background(), request)
	if err != nil || claim.Fresh || claim.Receipt.Status != "unknown" || len(claim.Receipt.Fields) != 0 {
		t.Fatalf("unconfirmed execution was reclaimed: %#v %v", claim, err)
	}
	request.ParametersDigest = strings.Repeat("d", 64)
	if _, err := copy.claim(context.Background(), request); !errors.Is(err, errJournalConflict) {
		t.Fatalf("changed input accepted: %v", err)
	}
}

func TestJournalRejectsWrongOwnerKeyTamperingAndOldPolicy(t *testing.T) {
	j, p, key := journalFixture(t)
	other := j.scope
	other.ProjectID = "55555555-5555-4555-8555-555555555555"
	if _, err := newJournal(j.root, other, key, j.guard); err == nil {
		t.Fatal("journal reused across owners")
	}
	wrong := append([]byte(nil), key...)
	wrong[0] ^= 1
	if _, err := newJournal(j.root, j.scope, wrong, j.guard); err == nil {
		t.Fatal("wrong key accepted")
	}
	newer := p
	newer.Revision++
	newer.Digest = protocol.ScriptPolicyDigest(newer)
	if err := j.applyPolicy(context.Background(), newer); err != nil {
		t.Fatal(err)
	}
	if err := j.applyPolicy(context.Background(), p); !errors.Is(err, errJournalConflict) {
		t.Fatalf("old policy restored: %v", err)
	}
	if _, err := j.claim(context.Background(), executionRequest(p)); !errors.Is(err, errJournalConflict) {
		t.Fatal("old scope executed")
	}
	path := filepath.Join(j.root, "executions.bin")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	data[len(data)-2] ^= 1
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := newJournal(j.root, j.scope, key, j.guard); err == nil {
		t.Fatal("corrupt journal was reset")
	}
}

func TestJournalPolicyChangeDropsOldCompletionAndChecksClaimToken(t *testing.T) {
	j, p, _ := journalFixture(t)
	request := executionRequest(p)
	claim, err := j.claim(context.Background(), request)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := j.finish(context.Background(), request, strings.Repeat("0", 64), evaluation{Outcome: scriptprocess.Succeeded}); !errors.Is(err, errJournalConflict) {
		t.Fatal("foreign finisher accepted")
	}
	newer := p
	newer.Revision++
	newer.Digest = protocol.ScriptPolicyDigest(newer)
	if err := j.applyPolicy(context.Background(), newer); err != nil {
		t.Fatal(err)
	}
	receipt, err := j.finish(context.Background(), request, claim.Token, evaluation{Outcome: scriptprocess.Succeeded, Fields: map[string]json.RawMessage{"answer": json.RawMessage(`"old"`)}})
	if err != nil || receipt.Status != "canceled" || receipt.Failure != "policy_changed" || len(receipt.Fields) != 0 {
		t.Fatalf("old completion accepted: %#v %v", receipt, err)
	}
}

func TestJournalSerializesRealProcessesAndReleasesCrashLock(t *testing.T) {
	j, p, key := journalFixture(t)
	program, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	commands := []*exec.Cmd{}
	outputs := []*bytes.Buffer{}
	for i := 0; i < 2; i++ {
		command := exec.Command(program, "-test.run=TestJournalHelper", "--", "tgo-journal-helper", j.root)
		command.Stdin = bytes.NewReader(key)
		output := new(bytes.Buffer)
		command.Stdout = output
		command.Stderr = output
		if err := command.Start(); err != nil {
			t.Fatal(err)
		}
		commands = append(commands, command)
		outputs = append(outputs, output)
	}
	fresh := 0
	for index, command := range commands {
		if err := command.Wait(); err != nil {
			t.Fatalf("journal subprocess: %v; %s", err, outputs[index].String())
		}
		if strings.Contains(outputs[index].String(), "JOURNAL_FRESH") {
			fresh++
		}
	}
	if fresh != 1 {
		t.Fatalf("simultaneous processes both acquired the execution: %d", fresh)
	}
	claim, err := reopenJournal(t, j, key).claim(context.Background(), executionRequest(p))
	if err != nil || claim.Fresh || claim.Receipt.Status != "unknown" {
		t.Fatalf("process reservation lost: %#v %v", claim, err)
	}
}

func TestJournalHelper(t *testing.T) {
	index := -1
	for i, arg := range os.Args {
		if arg == "tgo-journal-helper" {
			index = i
			break
		}
	}
	if index < 0 {
		return
	}
	key := make([]byte, 32)
	if _, err := io.ReadFull(os.Stdin, key); err != nil {
		os.Exit(11)
	}
	root := os.Args[index+1]
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	j, err := newJournal(root, journalScope{ProjectID: "11111111-1111-4111-8111-111111111111", DeviceID: "22222222-2222-4222-8222-222222222222"}, key, sandbox.New(cfg))
	if err != nil {
		os.Exit(12)
	}
	if len(os.Args) > index+2 && os.Args[index+2] == "hold" {
		path, err := j.path("executions.lock")
		if err != nil {
			os.Exit(15)
		}
		lock, err := lockJournal(path)
		if err != nil {
			os.Exit(16)
		}
		defer lock.Close()
		if os.WriteFile(filepath.Join(root, "lock-held"), []byte("owned"), 0600) != nil {
			os.Exit(17)
		}
		time.Sleep(30 * time.Second)
		os.Exit(18)
	}
	p := protocol.ScriptPolicy{ProtocolVersion: 1, Revision: 1, ScriptID: "daily_report", GrantDigest: strings.Repeat("a", 64), TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 1}
	p.Digest = protocol.ScriptPolicyDigest(p)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	claim, err := j.claim(ctx, executionRequest(p))
	if err != nil {
		os.Exit(13)
	}
	if claim.Fresh {
		fmt.Println("JOURNAL_FRESH")
	} else {
		fmt.Println("JOURNAL_EXISTING")
	}
	os.Exit(0)
}

func TestJournalCrashReleasesKernelLock(t *testing.T) {
	j, p, key := journalFixture(t)
	program, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	command := exec.Command(program, "-test.run=TestJournalHelper", "--", "tgo-journal-helper", j.root, "hold")
	command.Stdin = bytes.NewReader(key)
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { command.Process.Kill(); command.Wait() }()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := os.Stat(filepath.Join(j.root, "lock-held")); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("helper did not hold the file lock")
		}
		time.Sleep(10 * time.Millisecond)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if _, err := j.claim(ctx, executionRequest(p)); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("parallel process bypassed held lock: %v", err)
	}
	if err := command.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	command.Wait()
	claim, err := j.claim(context.Background(), executionRequest(p))
	if err != nil || !claim.Fresh {
		t.Fatalf("crashed owner left permanent lock: %#v %v", claim, err)
	}
}

func TestJournalRevisionCannotResetLocalGrantBudget(t *testing.T) {
	j, p, _ := journalFixture(t)
	request := executionRequest(p)
	if _, err := j.claim(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	for revision := 2; revision <= 3; revision++ {
		p.Revision = revision
		p.Digest = protocol.ScriptPolicyDigest(p)
		if err := j.applyPolicy(context.Background(), p); err != nil {
			t.Fatal(err)
		}
		request.Policy = p
		request.ExecutionID = fmt.Sprintf("%08d-3333-4333-8333-333333333333", revision)
		_, err := j.claim(context.Background(), request)
		if revision == 2 && err != nil {
			t.Fatal(err)
		}
		if revision == 3 && !errors.Is(err, errJournalBudget) {
			t.Fatalf("policy revision reset the local lifetime budget: %v", err)
		}
	}
}
