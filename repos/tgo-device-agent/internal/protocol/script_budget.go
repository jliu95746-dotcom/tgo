package protocol

import "errors"

// Budget is an observation of durable reservations, never an execution permit.
type ScriptBudgetQuery struct {
	ProtocolVersion int    `json:"protocol_version"`
	SessionID       string `json:"session_id"`
	PolicyDigest    string `json:"policy_digest"`
}

func (q ScriptBudgetQuery) Validate() error {
	if q.ProtocolVersion != 1 || !scriptSessionID.MatchString(q.SessionID) || !scriptSHA256.MatchString(q.PolicyDigest) {
		return errors.New("invalid bound script budget query")
	}
	return nil
}

type ScriptBudget struct {
	ScriptBudgetQuery
	GrantDigest   string `json:"grant_digest"`
	PolicyMaxRuns int    `json:"policy_max_runs"`
	PolicyUsed    int    `json:"policy_used"`
	GrantMaxRuns  int    `json:"grant_max_runs"`
	GrantUsed     int    `json:"grant_used"`
}
