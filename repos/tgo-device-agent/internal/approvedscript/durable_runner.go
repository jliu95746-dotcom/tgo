package approvedscript

import (
	"context"
	"encoding/json"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

type durableEvaluation struct {
	Receipt  executionReceipt
	Replayed bool
	Err      error
}

// Package-private: transport identity, policy publication/revocation, key
// provisioning and authorization must be connected before remote invocation.
func (c *Catalog) runDurableReadOnly(ctx context.Context, journal *executionJournal, id string, policy protocol.ScriptPolicy, contextDigest string, parameters map[string]json.RawMessage) durableEvaluation {
	request := scriptExecutionRequest{ExecutionID: id, Policy: policy, ContextDigest: contextDigest}
	result := durableEvaluation{Receipt: executionReceipt{Request: request, Status: "rejected", Failure: "invalid_execution_scope"}, Err: errJournalConflict}
	g, ok := c.grants[policy.ScriptID]
	if !ok || journal == nil || ctx == nil || policy.Validate() != nil || g.Effect != "read_only" || policy.GrantDigest != descriptor(g).GrantDigest || policy.MaxRuns > g.MaxRuns || policy.TimeoutSeconds > g.TimeoutSeconds || policy.MaxOutputBytes > g.MaxOutputBytes {
		return result
	}
	// Own the typed values before claiming. A caller's later map mutation must
	// not change what executes under the persisted input digest.
	owned, _, err := validateValues(g.Parameters, parameters, true)
	if err != nil {
		result.Err = err
		return result
	}
	prepared, err := c.Prepare(g.ScriptID, policy.GrantDigest, owned)
	if err != nil {
		result.Err = err
		return result
	}
	request.ParametersDigest = prepared.ParametersDigest
	request.GrantMaxRuns = g.MaxRuns
	result.Receipt.Request = request
	claim, err := journal.claim(ctx, request)
	if err != nil {
		result.Err = err
		result.Receipt.Failure = "reservation_unavailable"
		return result
	}
	if !claim.Fresh {
		return durableEvaluation{Receipt: claim.Receipt, Replayed: true}
	}
	run := c.evaluateReadOnly(ctx, g.ScriptID, policy.GrantDigest, owned, executionLimits{TimeoutSeconds: policy.TimeoutSeconds, MaxOutputBytes: policy.MaxOutputBytes})
	// Even canceled/timed-out operations must save their terminal receipt. This
	// storage step has its own bounded lock context and cannot restart the work.
	receipt, err := journal.finish(context.Background(), request, claim.Token, run)
	if err != nil {
		return durableEvaluation{Receipt: executionReceipt{Request: request, Status: "unknown", Failure: "completion_unconfirmed", StartedAt: claim.Receipt.StartedAt}, Err: err}
	}
	return durableEvaluation{Receipt: receipt}
}
