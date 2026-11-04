package protocol

import (
	"encoding/json"
	"errors"
	"regexp"
)

var scriptExecutionUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var scriptSessionID = regexp.MustCompile(`^[0-9a-f]{32}$`)

type ScriptReceiptQuery struct {
	ProtocolVersion  int    `json:"protocol_version"`
	SessionID        string `json:"session_id"`
	ExecutionID      string `json:"execution_id"`
	PolicyDigest     string `json:"policy_digest"`
	ParametersDigest string `json:"parameters_digest"`
	ContextDigest    string `json:"context_digest"`
}

func (q ScriptReceiptQuery) Validate() error {
	if q.ProtocolVersion != 1 || !scriptSessionID.MatchString(q.SessionID) || !scriptExecutionUUID.MatchString(q.ExecutionID) || !scriptSHA256.MatchString(q.PolicyDigest) || !scriptSHA256.MatchString(q.ParametersDigest) || !scriptSHA256.MatchString(q.ContextDigest) {
		return errors.New("invalid bound script execution identity")
	}
	return nil
}

type ScriptExecuteRequest struct {
	ScriptReceiptQuery
	TimeoutSeconds int                        `json:"timeout_seconds"`
	Parameters     map[string]json.RawMessage `json:"parameters"`
}

// Signaled acknowledges cancellation dispatch, not process completion.
type ScriptCancelAck struct {
	ScriptReceiptQuery
	Signaled bool `json:"signaled"`
}

func (q ScriptExecuteRequest) Validate() error {
	if q.ScriptReceiptQuery.Validate() != nil || q.TimeoutSeconds < 1 || q.TimeoutSeconds > 120 || q.Parameters == nil || len(q.Parameters) > 16 {
		return errors.New("invalid reviewed script execution")
	}
	data, err := json.Marshal(q.Parameters)
	if err != nil || len(data) > 65536 {
		return errors.New("script input exceeds the wire budget")
	}
	return nil
}

type ScriptExecutionReceipt struct {
	ProtocolVersion  int                        `json:"protocol_version"`
	ExecutionID      string                     `json:"execution_id"`
	PolicyDigest     string                     `json:"policy_digest"`
	ParametersDigest string                     `json:"parameters_digest"`
	ContextDigest    string                     `json:"context_digest"`
	Status           string                     `json:"status"`
	Failure          string                     `json:"failure,omitempty"`
	Replayed         bool                       `json:"replayed"`
	Fields           map[string]json.RawMessage `json:"fields,omitempty"`
}
