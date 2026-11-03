package approvedscript

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

type SessionOwner struct{ ProjectID, DeviceID string }

// Session is initialized from authenticated transport identity, not tool input.
type Session struct {
	mu                 sync.Mutex
	ctx                context.Context
	cancel             context.CancelFunc
	stop               func() bool
	id                 string
	catalog            *Catalog
	journal            *executionJournal
	policy             *protocol.ScriptPolicy
	closed             bool
	sequence           uint64
	active             map[uint64]context.CancelFunc
	activeScopes       map[uint64]protocol.ScriptReceiptQuery
	canceledScopes     map[protocol.ScriptReceiptQuery]struct{}
	cancelLimitReached bool
}

type SessionResult struct {
	Status           string
	Failure          string
	ExecutionID      string
	PolicyDigest     string
	ParametersDigest string
	Replayed         bool
	Fields           map[string]json.RawMessage
}

func OpenSession(ctx context.Context, catalog *Catalog, root string, owner SessionOwner, guard PathGuard) (*Session, error) {
	if ctx == nil || catalog == nil {
		return nil, errJournalUnavailable
	}
	journal, err := openPrivateJournal(ctx, root, journalScope{ProjectID: owner.ProjectID, DeviceID: owner.DeviceID}, guard)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return nil, errJournalUnavailable
	}
	child, cancel := context.WithCancel(ctx)
	s := &Session{ctx: child, cancel: cancel, id: hex.EncodeToString(nonce), catalog: catalog, journal: journal, active: map[uint64]context.CancelFunc{}, activeScopes: map[uint64]protocol.ScriptReceiptQuery{}, canceledScopes: map[protocol.ScriptReceiptQuery]struct{}{}}
	s.mu.Lock()
	s.stop = context.AfterFunc(ctx, s.Close)
	s.mu.Unlock()
	return s, nil
}

func (s *Session) Status() protocol.ScriptSessionStatus {
	s.mu.Lock()
	defer s.mu.Unlock()
	value := protocol.ScriptSessionStatus{ProtocolVersion: 1, State: "closed", Reason: "session_closed"}
	if s.closed || s.ctx.Err() != nil {
		return value
	}
	value.SessionID = s.id
	value.State = "awaiting_policy"
	value.Reason = "policy_required"
	if s.policy != nil {
		value.State = "active"
		value.Reason = "policy_confirmed"
	}
	return value
}

func (s *Session) suspendLocked() {
	s.policy = nil
	for _, cancel := range s.active {
		cancel()
	}
}
func (s *Session) Suspend() { s.mu.Lock(); defer s.mu.Unlock(); s.suspendLocked() }
func (s *Session) Close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	s.suspendLocked()
	s.cancel()
	stop := s.stop
	s.mu.Unlock()
	if stop != nil {
		stop()
	}
}

func (s *Session) Apply(id string, policy protocol.ScriptPolicy) (protocol.ScriptPolicyAck, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed || s.ctx.Err() != nil || id != s.id {
		return protocol.ScriptPolicyAck{}, errors.New("script policy session mismatch")
	}
	duplicate := s.policy != nil && *s.policy == policy
	if !duplicate {
		s.suspendLocked()
	}
	g, ok := s.catalog.grants[policy.ScriptID]
	if !ok || policy.Validate() != nil || g.Effect != "read_only" || descriptor(g).GrantDigest != policy.GrantDigest || policy.MaxRuns > g.MaxRuns || policy.TimeoutSeconds > g.TimeoutSeconds || policy.MaxOutputBytes > g.MaxOutputBytes {
		s.suspendLocked()
		return protocol.ScriptPolicyAck{}, errors.New("script policy exceeds current local approval")
	}
	if _, err := s.catalog.materialize(g); err != nil {
		s.suspendLocked()
		return protocol.ScriptPolicyAck{}, errors.New("local script approval is unavailable")
	}
	if err := s.journal.applyPolicy(s.ctx, policy); err != nil {
		s.suspendLocked()
		return protocol.ScriptPolicyAck{}, errors.New("script policy could not be persisted")
	}
	if s.ctx.Err() != nil {
		return protocol.ScriptPolicyAck{}, errors.New("script policy session closed")
	}
	copy := policy
	s.policy = &copy
	return protocol.ScriptPolicyAck{ProtocolVersion: 1, SessionID: s.id, Revision: policy.Revision, Digest: policy.Digest}, nil
}

// RunReadOnly is a package-level compatibility entry. The remote execution path
// uses ExecuteReadOnly to validate the complete immutable request identity.
func (s *Session) RunReadOnly(caller context.Context, id, executionID, contextDigest string, parameters map[string]json.RawMessage) SessionResult {
	return s.runReadOnly(caller, id, executionID, contextDigest, parameters, "", "", 0)
}

func (s *Session) runReadOnly(caller context.Context, id, executionID, contextDigest string, parameters map[string]json.RawMessage, expectedPolicy, expectedParameters string, seconds int) SessionResult {
	result := SessionResult{Status: "rejected", Failure: "policy_required", ExecutionID: executionID}
	if caller != nil && caller.Err() != nil {
		result.Status, result.Failure = "canceled", "caller_canceled"
		return result
	}
	s.mu.Lock()
	if caller == nil || s.closed || s.ctx.Err() != nil || id != s.id || s.policy == nil || (expectedPolicy != "" && s.policy.Digest != expectedPolicy) {
		s.mu.Unlock()
		return result
	}
	policy := *s.policy
	scope := protocol.ScriptReceiptQuery{ProtocolVersion: 1, SessionID: s.id, ExecutionID: executionID, PolicyDigest: policy.Digest, ParametersDigest: expectedParameters, ContextDigest: contextDigest}
	if _, canceled := s.canceledScopes[scope]; canceled || s.cancelLimitReached {
		s.mu.Unlock()
		result.Status, result.Failure = "canceled", "execution_canceled"
		return result
	}
	if seconds > policy.TimeoutSeconds || seconds < 0 {
		s.mu.Unlock()
		result.Failure = "invalid_execution_timeout"
		return result
	}
	if expectedParameters != "" {
		prepared, err := s.catalog.Prepare(policy.ScriptID, policy.GrantDigest, parameters)
		if err != nil || prepared.ParametersDigest != expectedParameters {
			s.mu.Unlock()
			result.Failure = "execution_binding_conflict"
			return result
		}
	}
	ctx, cancel := context.WithCancel(s.ctx)
	if seconds > 0 {
		cancel()
		ctx, cancel = context.WithTimeout(s.ctx, time.Duration(seconds)*time.Second)
	}
	s.sequence++
	sequence := s.sequence
	s.active[sequence] = cancel
	s.activeScopes[sequence] = scope
	s.mu.Unlock()
	stop := context.AfterFunc(caller, cancel)
	defer stop()
	defer cancel()
	run := s.catalog.runDurableReadOnly(ctx, s.journal, executionID, policy, contextDigest, parameters)
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.active, sequence)
	delete(s.activeScopes, sequence)
	result.PolicyDigest = policy.Digest
	result.ParametersDigest = run.Receipt.Request.ParametersDigest
	result.Replayed = run.Replayed
	if s.closed || s.ctx.Err() != nil || s.policy == nil || s.policy.Digest != policy.Digest {
		result.Status = "canceled"
		result.Failure = "policy_inactive"
		return result
	}
	result.Status = run.Receipt.Status
	result.Failure = run.Receipt.Failure
	if result.Status == "succeeded" && caller.Err() == nil {
		result.Fields = run.Receipt.Fields
	} else if caller.Err() != nil {
		result.Status = "canceled"
		result.Failure = "caller_canceled"
	}
	return result
}
