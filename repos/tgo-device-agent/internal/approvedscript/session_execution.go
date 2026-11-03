package approvedscript

import (
	"context"
	"encoding/json"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

// Cancel only signals an exact active execution, including its original policy.
// A delayed cancellation must not affect new work on this same connection.
func (s *Session) Cancel(query protocol.ScriptReceiptQuery) bool {
	if query.Validate() != nil {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed || s.ctx.Err() != nil || s.id != query.SessionID {
		return false
	}
	// TCP handlers may be scheduled out of order. Remember a cancellation before
	// a queued run registers itself, without claiming any durable execution.
	if s.policy != nil && s.policy.Digest == query.PolicyDigest {
		if s.canceledScopes == nil {
			s.canceledScopes = map[protocol.ScriptReceiptQuery]struct{}{}
		}
		if len(s.canceledScopes) < 256 {
			s.canceledScopes[query] = struct{}{}
		} else if _, exists := s.canceledScopes[query]; !exists {
			// Never evict a prior cancellation and permit a delayed run to start.
			s.cancelLimitReached = true
		}
	}
	signaled := false
	for sequence, scope := range s.activeScopes {
		if scope == query {
			if cancel := s.active[sequence]; cancel != nil {
				cancel()
				signaled = true
			}
		}
	}
	return signaled
}

func (s *Session) ExecuteReadOnly(caller context.Context, request protocol.ScriptExecuteRequest) protocol.ScriptExecutionReceipt {
	result := emptyReceipt(request.ScriptReceiptQuery, "rejected", "invalid_execution_scope")
	if request.Validate() != nil {
		return result
	}
	owned := make(map[string]json.RawMessage, len(request.Parameters))
	for name, value := range request.Parameters {
		owned[name] = append(json.RawMessage(nil), value...)
	}
	request.Parameters = owned
	run := s.runReadOnly(caller, request.SessionID, request.ExecutionID, request.ContextDigest, request.Parameters, request.PolicyDigest, request.ParametersDigest, request.TimeoutSeconds)
	result.Status, result.Failure, result.Replayed = run.Status, run.Failure, run.Replayed
	result.Fields = run.Fields
	return result
}
