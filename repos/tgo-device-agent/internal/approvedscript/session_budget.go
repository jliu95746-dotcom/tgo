package approvedscript

import (
	"context"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func (j *executionJournal) usage(ctx context.Context, policy protocol.ScriptPolicy, grantMaxRuns int) (int, int, error) {
	if policy.Validate() != nil || grantMaxRuns < policy.MaxRuns || grantMaxRuns > 100 {
		return 0, 0, errJournalConflict
	}
	policyUsed, grantUsed := 0, 0
	err := j.withState(ctx, func(state *journalState) (bool, error) {
		current, ok := state.Policies[policy.ScriptID]
		if !ok || current.Policy != policy {
			return false, errJournalConflict
		}
		policyUsed = current.Count
		for _, entry := range state.Entries {
			if entry.Receipt.Request.Policy.GrantDigest == policy.GrantDigest {
				if entry.Receipt.Request.GrantMaxRuns != grantMaxRuns {
					return false, errJournalConflict
				}
				grantUsed++
			}
		}
		if grantUsed > grantMaxRuns {
			return false, errJournalConflict
		}
		return false, nil
	})
	if err != nil {
		return 0, 0, err
	}
	return policyUsed, grantUsed, nil
}

func (s *Session) Budget(caller context.Context, query protocol.ScriptBudgetQuery) (protocol.ScriptBudget, error) {
	if caller == nil || caller.Err() != nil || query.Validate() != nil {
		return protocol.ScriptBudget{}, errJournalConflict
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed || s.ctx.Err() != nil || s.id != query.SessionID || s.policy == nil || s.policy.Digest != query.PolicyDigest {
		return protocol.ScriptBudget{}, errJournalConflict
	}
	policy := *s.policy
	g, ok := s.catalog.grants[policy.ScriptID]
	if !ok || g.Effect != "read_only" || descriptor(g).GrantDigest != policy.GrantDigest {
		return protocol.ScriptBudget{}, errJournalConflict
	}
	if _, err := s.catalog.materialize(g); err != nil {
		return protocol.ScriptBudget{}, errJournalUnavailable
	}
	ctx, cancel := context.WithCancel(s.ctx)
	defer cancel()
	stop := context.AfterFunc(caller, cancel)
	defer stop()
	p, gUsed, err := s.journal.usage(ctx, policy, g.MaxRuns)
	if err != nil {
		return protocol.ScriptBudget{}, err
	}
	return protocol.ScriptBudget{ScriptBudgetQuery: query, GrantDigest: policy.GrantDigest, PolicyMaxRuns: policy.MaxRuns, PolicyUsed: p, GrantMaxRuns: g.MaxRuns, GrantUsed: gUsed}, nil
}
