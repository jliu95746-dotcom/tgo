package tools

import (
	"context"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func rejectedScript(q protocol.ScriptReceiptQuery) protocol.ScriptExecutionReceipt {
	return protocol.ScriptExecutionReceipt{ProtocolVersion: 1, ExecutionID: q.ExecutionID, PolicyDigest: q.PolicyDigest, ParametersDigest: q.ParametersDigest, ContextDigest: q.ContextDigest, Status: "rejected", Failure: "policy_required"}
}

func (r *Registry) CancelScript(binding string, query protocol.ScriptReceiptQuery) protocol.ScriptCancelAck {
	ack := protocol.ScriptCancelAck{ScriptReceiptQuery: query}
	r.policyMu.RLock()
	session := r.scriptSession
	allowed := session != nil && binding == query.SessionID && session.Status().SessionID == binding
	r.policyMu.RUnlock()
	if allowed {
		ack.Signaled = session.Cancel(query)
	}
	return ack
}

func (r *Registry) ScriptReceipt(ctx context.Context, binding string, q protocol.ScriptReceiptQuery) protocol.ScriptExecutionReceipt {
	r.policyMu.RLock()
	session := r.scriptSession
	allowed := session != nil && binding == q.SessionID && session.Status().SessionID == binding
	r.policyMu.RUnlock()
	if !allowed {
		return rejectedScript(q)
	}
	return session.Receipt(ctx, q)
}

func (r *Registry) ExecuteScript(ctx context.Context, binding string, request protocol.ScriptExecuteRequest) protocol.ScriptExecutionReceipt {
	r.policyMu.RLock()
	session := r.scriptSession
	allowed := session != nil && binding == request.SessionID && session.Status().SessionID == binding
	r.policyMu.RUnlock()
	if !allowed {
		return rejectedScript(request.ScriptReceiptQuery)
	}
	return session.ExecuteReadOnly(ctx, request)
}
