package transport

import (
	"bytes"
	"encoding/json"
	"io"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func decodeScriptExecution[T protocol.ScriptReceiptQuery | protocol.ScriptExecuteRequest | protocol.ScriptBudgetQuery](raw json.RawMessage, target *T, required []string) bool {
	if len(raw) == 0 || len(raw) > 131072 {
		return false
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || fields == nil {
		return false
	}
	for _, name := range required {
		value, ok := fields[name]
		if !ok || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return false
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(target) != nil {
		return false
	}
	_, err := decoder.Token()
	return err == io.EOF
}

var scriptExecutionFields = []string{"protocol_version", "session_id", "execution_id", "policy_digest", "parameters_digest", "context_digest"}

func (c *Client) handleScriptCancellation(session *connection, id *json.RawMessage, raw json.RawMessage) {
	if id == nil || session.ctx.Err() != nil {
		return
	}
	var request protocol.ScriptReceiptQuery
	if !decodeScriptExecution(raw, &request, scriptExecutionFields) || request.Validate() != nil {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Invalid bound script cancellation"))
		return
	}
	result := c.registry.CancelScript(session.scriptBinding, request)
	response, err := protocol.NewResponse(id, result)
	if err == nil {
		session.writeMessage(response)
	}
}

func (c *Client) handleScriptReceipt(session *connection, id *json.RawMessage, raw json.RawMessage) {
	if id == nil || session.ctx.Err() != nil {
		return
	}
	var request protocol.ScriptReceiptQuery
	if !decodeScriptExecution(raw, &request, scriptExecutionFields) || request.Validate() != nil {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Invalid bound script receipt query"))
		return
	}
	result := c.registry.ScriptReceipt(session.ctx, session.scriptBinding, request)
	response, err := protocol.NewResponse(id, result)
	if err == nil {
		session.writeMessage(response)
	}
}

func (c *Client) handleScriptExecution(session *connection, id *json.RawMessage, raw json.RawMessage) {
	if id == nil || session.ctx.Err() != nil {
		return
	}
	var request protocol.ScriptExecuteRequest
	required := append(append([]string(nil), scriptExecutionFields...), "parameters", "timeout_seconds")
	if !decodeScriptExecution(raw, &request, required) || request.Validate() != nil {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Invalid bound script execution"))
		return
	}
	result := c.registry.ExecuteScript(session.ctx, session.scriptBinding, request)
	response, err := protocol.NewResponse(id, result)
	if err == nil {
		session.writeMessage(response)
	}
}
