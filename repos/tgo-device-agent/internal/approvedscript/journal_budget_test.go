package approvedscript

import (
	"context"
	"errors"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/scriptprocess"
)

func TestJournalBudgetCountsReservationsAcrossReopenAndPolicyRevision(t *testing.T) {
	j, policy, key := journalFixture(t)
	ctx := context.Background()
	assertUsage := func(journal *executionJournal, policy protocol.ScriptPolicy, used, grantUsed int) {
		t.Helper()
		p, g, err := journal.usage(ctx, policy, 2)
		if err != nil || p != used || g != grantUsed {
			t.Fatalf("usage: policy=%d grant=%d error=%v", p, g, err)
		}
	}
	assertUsage(j, policy, 0, 0)
	request := executionRequest(policy)
	claim, err := j.claim(ctx, request)
	if err != nil || !claim.Fresh {
		t.Fatal(err)
	}
	assertUsage(j, policy, 1, 1) // Unknown/running work has already consumed a run.
	assertUsage(reopenJournal(t, j, key), policy, 1, 1)
	if replay, err := j.claim(ctx, request); err != nil || replay.Fresh {
		t.Fatal("query/replay consumed a new run")
	}
	assertUsage(j, policy, 1, 1)
	if _, err := j.finish(ctx, request, claim.Token, evaluation{Outcome: scriptprocess.Failed, Failure: "fixture_failed"}); err != nil {
		t.Fatal(err)
	}
	assertUsage(j, policy, 1, 1) // Failure never refunds the reservation.
	policy.Revision++
	policy.Digest = protocol.ScriptPolicyDigest(policy)
	if err := j.applyPolicy(ctx, policy); err != nil {
		t.Fatal(err)
	}
	assertUsage(j, policy, 0, 1)
	request = executionRequest(policy)
	request.ExecutionID = "44444444-4444-4444-8444-444444444444"
	if _, err := j.claim(ctx, request); err != nil {
		t.Fatal(err)
	}
	assertUsage(j, policy, 1, 2)
	policy.Revision++
	policy.Digest = protocol.ScriptPolicyDigest(policy)
	if err := j.applyPolicy(ctx, policy); err != nil {
		t.Fatal(err)
	}
	assertUsage(j, policy, 0, 2) // Republishing does not reset the local grant.
}

func TestJournalBudgetRejectsChangedScopeAndCanceledCaller(t *testing.T) {
	j, policy, _ := journalFixture(t)
	old := policy
	policy.Revision++
	policy.Digest = protocol.ScriptPolicyDigest(policy)
	if _, _, err := j.usage(context.Background(), policy, 2); !errors.Is(err, errJournalConflict) {
		t.Fatal("unpublished scope accepted")
	}
	if _, _, err := j.usage(context.Background(), old, 0); err == nil {
		t.Fatal("invalid grant limit accepted")
	}
	if _, err := j.claim(context.Background(), executionRequest(old)); err != nil {
		t.Fatal(err)
	}
	if _, _, err := j.usage(context.Background(), old, 3); err == nil {
		t.Fatal("changed grant limit accepted")
	}
	canceled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, _, err := j.usage(canceled, old, 2); err == nil {
		t.Fatal("canceled query returned usable counts")
	}
}
