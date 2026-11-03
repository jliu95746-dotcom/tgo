package approvedscript

import (
	"context"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func emptyReceipt(q protocol.ScriptReceiptQuery, status, failure string) protocol.ScriptExecutionReceipt {
	return protocol.ScriptExecutionReceipt{ProtocolVersion: 1, ExecutionID: q.ExecutionID, PolicyDigest: q.PolicyDigest, ParametersDigest: q.ParametersDigest, ContextDigest: q.ContextDigest, Status: status, Failure: failure}
}

func (s *Session) Receipt(caller context.Context, q protocol.ScriptReceiptQuery) protocol.ScriptExecutionReceipt {
	result := emptyReceipt(q, "rejected", "policy_required")
	if caller == nil || caller.Err() != nil || q.Validate() != nil {
		return result
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed || s.ctx.Err() != nil || s.id != q.SessionID || s.policy == nil || s.policy.Digest != q.PolicyDigest {
		return result
	}
	ctx, cancel := context.WithCancel(s.ctx)
	defer cancel()
	stop := context.AfterFunc(caller, cancel)
	defer stop()
	receipt, found, err := s.journal.lookup(ctx, q.ExecutionID)
	if err != nil || ctx.Err() != nil {
		return emptyReceipt(q, "unknown", "receipt_unavailable")
	}
	if !found {
		return emptyReceipt(q, "not_found", "")
	}
	if receipt.Request.Policy.Digest != q.PolicyDigest || receipt.Request.ParametersDigest != q.ParametersDigest || receipt.Request.ContextDigest != q.ContextDigest {
		return emptyReceipt(q, "rejected", "execution_binding_conflict")
	}
	result.Status, result.Failure, result.Replayed = receipt.Status, receipt.Failure, true
	if receipt.Status == "succeeded" {
		result.Fields = receipt.Fields
	}
	return result
}
