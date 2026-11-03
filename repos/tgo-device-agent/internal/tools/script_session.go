package tools

import (
	"context"
	"fmt"
	"log/slog"

	"github.com/tgoai/tgo-device-agent/internal/approvedscript"
	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/sandbox"
)

func (r *Registry) BeginScriptConnection(ctx context.Context, owner approvedscript.SessionOwner) (string, func()) {
	r.policyMu.Lock()
	r.scriptGeneration++
	generation := r.scriptGeneration
	if r.scriptSession != nil {
		r.scriptSession.Close()
	}
	r.scriptSession = nil
	enabled, root := r.scriptEnabled, r.scriptRoot
	catalog, invalid := r.scriptCatalog, r.scriptCatalogErr
	r.policyMu.Unlock()
	if !enabled || root == "" || invalid != nil {
		if enabled && invalid != nil {
			slog.Error("script catalog rejected during connection initialization", "error", invalid)
		}
		return "", func() {}
	}
	privateConfig := config.DefaultConfig()
	privateConfig.WorkRoot = root
	session, err := approvedscript.OpenSession(ctx, catalog, root, owner, sandbox.New(privateConfig))
	if err != nil {
		slog.Error("script private state initialization failed", "error", err)
		return "", func() {}
	}
	r.policyMu.Lock()
	if r.scriptGeneration != generation || ctx.Err() != nil {
		r.policyMu.Unlock()
		session.Close()
		return "", func() {}
	}
	r.scriptSession = session
	id := session.Status().SessionID
	r.policyMu.Unlock()
	return id, func() {
		session.Close()
		r.policyMu.Lock()
		if r.scriptSession == session {
			r.scriptSession = nil
		}
		r.policyMu.Unlock()
	}
}

func (r *Registry) ScriptStatus(binding string) protocol.ScriptSessionStatus {
	r.policyMu.RLock()
	defer r.policyMu.RUnlock()
	if !r.scriptEnabled {
		return protocol.ScriptSessionStatus{ProtocolVersion: 1, State: "disabled", Reason: "local_feature_disabled"}
	}
	if r.scriptSession == nil {
		return protocol.ScriptSessionStatus{ProtocolVersion: 1, State: "unavailable", Reason: "private_state_unavailable"}
	}
	status := r.scriptSession.Status()
	if status.SessionID != binding {
		return protocol.ScriptSessionStatus{ProtocolVersion: 1, State: "closed", Reason: "connection_changed"}
	}
	return status
}

func (r *Registry) ApplyScriptPolicy(binding string, request protocol.ScriptPolicyApply) (protocol.ScriptPolicyAck, error) {
	r.policyMu.Lock()
	defer r.policyMu.Unlock()
	if r.scriptSession == nil || r.scriptSession.Status().SessionID != binding {
		return protocol.ScriptPolicyAck{}, fmt.Errorf("script connection is not active")
	}
	if binding != request.SessionID {
		return protocol.ScriptPolicyAck{}, fmt.Errorf("script policy session mismatch")
	}
	r.policyRequired = true
	r.readPolicy = nil
	if r.screenController != nil {
		r.screenController.Stop()
	}
	return r.scriptSession.Apply(request.SessionID, request.Policy)
}

func (r *Registry) RejectScriptPolicy(binding string) {
	r.policyMu.Lock()
	defer r.policyMu.Unlock()
	if r.scriptSession != nil && r.scriptSession.Status().SessionID == binding {
		r.scriptSession.Suspend()
	}
}
