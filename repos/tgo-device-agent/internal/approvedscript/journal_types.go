package approvedscript

import (
	"encoding/json"
	"errors"
	"regexp"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

var (
	errJournalConflict    = errors.New("execution journal scope conflict")
	errJournalBudget      = errors.New("execution policy budget exhausted")
	errJournalUnavailable = errors.New("execution journal unavailable")
	errJournalBusy        = errors.New("execution journal locked")
	journalUUID           = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	journalHash           = regexp.MustCompile(`^[0-9a-f]{64}$`)
	journalFailure        = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)
)

const maxJournalPlaintext = 12 * 1024 * 1024

type journalScope struct {
	ProjectID string `json:"project_id"`
	DeviceID  string `json:"device_id"`
}

type scriptExecutionRequest struct {
	ExecutionID      string                `json:"execution_id"`
	Policy           protocol.ScriptPolicy `json:"policy"`
	ParametersDigest string                `json:"parameters_digest"`
	ContextDigest    string                `json:"context_digest"`
	GrantMaxRuns     int                   `json:"grant_max_runs"`
}

func (r scriptExecutionRequest) valid() bool {
	return journalUUID.MatchString(r.ExecutionID) && journalHash.MatchString(r.ParametersDigest) && journalHash.MatchString(r.ContextDigest) && r.Policy.Validate() == nil && r.GrantMaxRuns >= r.Policy.MaxRuns && r.GrantMaxRuns <= 100
}

type executionReceipt struct {
	Request    scriptExecutionRequest     `json:"request"`
	Status     string                     `json:"status"`
	Failure    string                     `json:"failure,omitempty"`
	Fields     map[string]json.RawMessage `json:"fields,omitempty"`
	StartedAt  time.Time                  `json:"started_at"`
	FinishedAt *time.Time                 `json:"finished_at,omitempty"`
}

type journalEntry struct {
	Receipt    executionReceipt `json:"receipt"`
	ClaimToken string           `json:"claim_token,omitempty"`
}
type journalPolicy struct {
	Policy protocol.ScriptPolicy `json:"policy"`
	Count  int                   `json:"count"`
}
type journalState struct {
	Version  int                      `json:"version"`
	Scope    journalScope             `json:"scope"`
	Policies map[string]journalPolicy `json:"policies"`
	Entries  map[string]journalEntry  `json:"entries"`
}

type journalClaim struct {
	Fresh   bool
	Token   string
	Receipt executionReceipt
}

func (s *journalState) valid(owner journalScope) bool {
	if s.Version != 1 || s.Scope != owner || s.Policies == nil || s.Entries == nil || len(s.Policies) > 64 || len(s.Entries) > 1024 {
		return false
	}
	counts := map[string]int{}
	grants := map[string]int{}
	grantLimits := map[string]int{}
	for id, entry := range s.Entries {
		r := entry.Receipt
		if id != r.Request.ExecutionID || !r.Request.valid() || r.StartedAt.IsZero() || len(r.Fields) > 32 || (r.Failure != "" && !journalFailure.MatchString(r.Failure)) {
			return false
		}
		fields, err := json.Marshal(r.Fields)
		if err != nil || len(fields) > r.Request.Policy.MaxOutputBytes {
			return false
		}
		switch r.Status {
		case "running":
			if !journalHash.MatchString(entry.ClaimToken) || len(r.Fields) != 0 || r.Failure != "" || r.FinishedAt != nil {
				return false
			}
		case "succeeded", "failed", "canceled", "timed_out", "output_exceeded", "rejected", "start_failed", "unknown":
			if entry.ClaimToken != "" || r.FinishedAt == nil || r.FinishedAt.IsZero() || (r.Status != "succeeded" && len(r.Fields) != 0) {
				return false
			}
		default:
			return false
		}
		counts[r.Request.Policy.Digest]++
		hash := r.Request.Policy.GrantDigest
		if maximum, ok := grantLimits[hash]; ok && maximum != r.Request.GrantMaxRuns {
			return false
		}
		grantLimits[hash] = r.Request.GrantMaxRuns
		grants[hash]++
		if grants[hash] > r.Request.GrantMaxRuns {
			return false
		}
	}
	for id, value := range s.Policies {
		if id != value.Policy.ScriptID || value.Policy.Validate() != nil || value.Count < 0 || value.Count > value.Policy.MaxRuns || counts[value.Policy.Digest] != value.Count {
			return false
		}
	}
	return true
}
