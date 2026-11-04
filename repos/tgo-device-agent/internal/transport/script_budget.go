package transport

import (
	"encoding/json"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func (c *Client) handleScriptBudget(session *connection, id *json.RawMessage, raw json.RawMessage) {
	if id == nil || session.ctx.Err() != nil {
		return
	}
	var query protocol.ScriptBudgetQuery
	if !decodeScriptExecution(raw, &query, []string{"protocol_version", "session_id", "policy_digest"}) || query.Validate() != nil {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Invalid bound script budget query"))
		return
	}
	result, err := c.registry.ScriptBudget(session.ctx, session.scriptBinding, query)
	if err != nil {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Bound script budget is unavailable"))
		return
	}
	response, err := protocol.NewResponse(id, result)
	if err == nil {
		session.writeMessage(response)
	}
}
