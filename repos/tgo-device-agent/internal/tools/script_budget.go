package tools

import (
	"context"
	"errors"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func (r *Registry) ScriptBudget(ctx context.Context, binding string, query protocol.ScriptBudgetQuery) (protocol.ScriptBudget, error) {
	r.policyMu.RLock()
	session := r.scriptSession
	allowed := session != nil && binding == query.SessionID && session.Status().SessionID == binding
	r.policyMu.RUnlock()
	if !allowed {
		return protocol.ScriptBudget{}, errors.New("script connection is not active")
	}
	return session.Budget(ctx, query)
}
