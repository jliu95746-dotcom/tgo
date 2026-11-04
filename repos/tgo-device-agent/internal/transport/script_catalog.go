package transport

import (
	"encoding/json"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func (c *Client) handleScriptCatalog(session *connection, id *json.RawMessage, params json.RawMessage) {
	if id == nil || session.ctx.Err() != nil {
		return
	}
	if len(params) != 0 {
		var fields map[string]json.RawMessage
		if json.Unmarshal(params, &fields) != nil || fields == nil || len(fields) != 0 {
			session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Script catalog takes no parameters"))
			return
		}
	}
	catalog, err := c.registry.ScriptCatalog()
	if session.ctx.Err() != nil {
		return
	}
	if err != nil {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrToolExecFailed, "Local approved script catalog is unavailable; check the local reviewed configuration and files"))
		return
	}
	response, err := protocol.NewResponse(id, catalog)
	if err == nil {
		session.writeMessage(response)
	}
}
