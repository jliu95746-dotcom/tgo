package approvedscript

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/scriptprocess"
)

func (j *executionJournal) applyPolicy(ctx context.Context, policy protocol.ScriptPolicy) error {
	if policy.Validate() != nil {
		return errJournalConflict
	}
	return j.withState(ctx, func(state *journalState) (bool, error) {
		if current, ok := state.Policies[policy.ScriptID]; ok {
			if current.Policy.Revision > policy.Revision || (current.Policy.Revision == policy.Revision && current.Policy != policy) {
				return false, errJournalConflict
			}
			if current.Policy == policy {
				return false, nil
			}
		} else if len(state.Policies) >= 64 {
			return false, errJournalUnavailable
		}
		state.Policies[policy.ScriptID] = journalPolicy{Policy: policy}
		return true, nil
	})
}

func (j *executionJournal) claim(ctx context.Context, request scriptExecutionRequest) (journalClaim, error) {
	var result journalClaim
	if !request.valid() {
		return result, errJournalConflict
	}
	err := j.withState(ctx, func(state *journalState) (bool, error) {
		current, ok := state.Policies[request.Policy.ScriptID]
		if !ok || current.Policy != request.Policy {
			return false, errJournalConflict
		}
		if existing, ok := state.Entries[request.ExecutionID]; ok {
			if existing.Receipt.Request != request {
				return false, errJournalConflict
			}
			result.Receipt = existing.Receipt
			if result.Receipt.Status == "running" {
				result.Receipt.Status = "unknown"
				result.Receipt.Failure = "execution_unconfirmed"
				result.Receipt.Fields = nil
			}
			return false, nil
		}
		if current.Count >= current.Policy.MaxRuns {
			return false, errJournalBudget
		}
		reserved := 0
		grantCount := 0
		for _, entry := range state.Entries {
			reserved += entry.Receipt.Request.Policy.MaxOutputBytes + 2048
			if entry.Receipt.Request.Policy.GrantDigest == request.Policy.GrantDigest {
				if entry.Receipt.Request.GrantMaxRuns != request.GrantMaxRuns {
					return false, errJournalConflict
				}
				grantCount++
			}
		}
		if grantCount >= request.GrantMaxRuns {
			return false, errJournalBudget
		}
		if len(state.Entries) >= 1024 || reserved+request.Policy.MaxOutputBytes+2048 > 8*1024*1024 {
			return false, errJournalUnavailable
		}
		token := make([]byte, 32)
		if _, err := rand.Read(token); err != nil {
			return false, errJournalUnavailable
		}
		receipt := executionReceipt{Request: request, Status: "running", StartedAt: time.Now().UTC()}
		value := hex.EncodeToString(token)
		state.Entries[request.ExecutionID] = journalEntry{Receipt: receipt, ClaimToken: value}
		current.Count++
		state.Policies[request.Policy.ScriptID] = current
		result = journalClaim{Fresh: true, Token: value, Receipt: receipt}
		return true, nil
	})
	if err != nil {
		return journalClaim{}, err
	}
	return result, nil
}

func (j *executionJournal) finish(ctx context.Context, request scriptExecutionRequest, token string, result evaluation) (executionReceipt, error) {
	var receipt executionReceipt
	err := j.withState(ctx, func(state *journalState) (bool, error) {
		entry, ok := state.Entries[request.ExecutionID]
		if !ok || !request.valid() || entry.Receipt.Request != request || entry.Receipt.Status != "running" || subtle.ConstantTimeCompare([]byte(token), []byte(entry.ClaimToken)) != 1 {
			return false, errJournalConflict
		}
		now := time.Now().UTC()
		receipt = entry.Receipt
		receipt.FinishedAt = &now
		receipt.Status = string(result.Outcome)
		receipt.Failure = result.Failure
		if result.Outcome == scriptprocess.CleanupFailed {
			receipt.Status = "unknown"
		}
		current, ok := state.Policies[request.Policy.ScriptID]
		if !ok || current.Policy != request.Policy {
			receipt.Status = "canceled"
			receipt.Failure = "policy_changed"
		}
		if receipt.Status == "succeeded" {
			receipt.Fields = result.Fields
			receipt.Failure = ""
		} else {
			receipt.Fields = nil
		}
		fields, err := json.Marshal(receipt.Fields)
		if err != nil || len(fields) > request.Policy.MaxOutputBytes {
			return false, errJournalUnavailable
		}
		state.Entries[request.ExecutionID] = journalEntry{Receipt: receipt}
		return true, nil
	})
	if err != nil {
		return executionReceipt{}, err
	}
	return receipt, nil
}
