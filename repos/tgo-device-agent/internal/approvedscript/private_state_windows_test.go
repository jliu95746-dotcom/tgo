//go:build windows && (amd64 || arm64)

package approvedscript

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/sandbox"
)

func TestPrivateStateReusesProtectedKeyAndRefusesOwnerChangeOrMissingKey(t *testing.T) {
	root := filepath.Join(t.TempDir(), "private")
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	guard := sandbox.New(cfg)
	owner := journalScope{ProjectID: "11111111-1111-4111-8111-111111111111", DeviceID: "22222222-2222-4222-8222-222222222222"}
	j, err := openPrivateJournal(context.Background(), root, owner, guard)
	if err != nil {
		t.Fatal(err)
	}
	_, p, _ := journalFixture(t)
	if err := j.applyPolicy(context.Background(), p); err != nil {
		t.Fatal(err)
	}
	if _, err := j.claim(context.Background(), executionRequest(p)); err != nil {
		t.Fatal(err)
	}
	second, err := openPrivateJournal(context.Background(), root, owner, guard)
	if err != nil {
		t.Fatal(err)
	}
	claim, err := second.claim(context.Background(), executionRequest(p))
	if err != nil || claim.Fresh || claim.Receipt.Status != "unknown" {
		t.Fatal("protected state did not survive reopen")
	}
	other := owner
	other.DeviceID = "44444444-4444-4444-8444-444444444444"
	if _, err := openPrivateJournal(context.Background(), root, other, guard); err == nil {
		t.Fatal("changed authenticated owner reused private state")
	}
	identity := filepath.Join(root, "identity.json")
	before, err := os.ReadFile(identity)
	if err != nil {
		t.Fatal(err)
	}
	if os.Remove(identity) != nil {
		t.Fatal("owned identity fixture could not be removed")
	}
	if _, err := openPrivateJournal(context.Background(), root, owner, guard); err == nil {
		t.Fatal("lost key was silently regenerated over an existing ledger")
	}
	if _, err := os.Stat(identity); !os.IsNotExist(err) {
		t.Fatal("failed recovery wrote a replacement identity")
	}
	if bytes.Contains(before, []byte("raw_key")) {
		t.Fatal("identity has a plaintext key field")
	}
}

func TestWindowsKeyProtectionChecksContextAndIntegrity(t *testing.T) {
	plain := bytes.Repeat([]byte("owned-private-key-payload"), 2)
	scope := []byte("owned-purpose-one")
	wrapped, err := protectLocalKey(plain, scope)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(wrapped, plain) {
		t.Fatal("key protection left plaintext")
	}
	unwrapped, err := unprotectLocalKey(wrapped, scope)
	if err != nil || !bytes.Equal(unwrapped, plain) {
		t.Fatal("protected key could not reopen")
	}
	if _, err := unprotectLocalKey(wrapped, []byte("other-purpose")); err == nil {
		t.Fatal("wrong context unwrapped key")
	}
	wrapped[len(wrapped)/2] ^= 1
	if value, err := unprotectLocalKey(wrapped, scope); err == nil && bytes.Equal(value, plain) {
		t.Fatal("tampered blob recovered the original key")
	}
}

func TestPrivateStateRefusesMissingLedgerWithoutResettingItsIdentity(t *testing.T) {
	root := filepath.Join(t.TempDir(), "private")
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	guard := sandbox.New(cfg)
	owner := journalScope{ProjectID: "11111111-1111-4111-8111-111111111111", DeviceID: "22222222-2222-4222-8222-222222222222"}
	if _, err := openPrivateJournal(context.Background(), root, owner, guard); err != nil {
		t.Fatal(err)
	}
	identity := filepath.Join(root, "identity.json")
	before, err := os.ReadFile(identity)
	if err != nil {
		t.Fatal(err)
	}
	ledger := filepath.Join(root, "executions.bin")
	if err := os.Remove(ledger); err != nil {
		t.Fatal(err)
	}
	if _, err := openPrivateJournal(context.Background(), root, owner, guard); err == nil {
		t.Fatal("lost execution history was silently reset")
	}
	after, err := os.ReadFile(identity)
	if err != nil || !bytes.Equal(before, after) {
		t.Fatal("failed recovery replaced the protected identity")
	}
	if _, err := os.Stat(ledger); !os.IsNotExist(err) {
		t.Fatal("failed recovery created a new empty ledger")
	}
}
