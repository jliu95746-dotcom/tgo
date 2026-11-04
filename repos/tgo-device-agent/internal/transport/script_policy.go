package transport

import (
	"bytes"
	"encoding/json"
	"io"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func (c *Client) handleScriptStatus(session *connection, id *json.RawMessage, raw json.RawMessage) {
	if id == nil || session.ctx.Err() != nil {
		return
	}
	if len(raw) > 0 {
		var fields map[string]json.RawMessage
		if json.Unmarshal(raw, &fields) != nil || fields == nil || len(fields) != 0 {
			session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Script status accepts no parameters"))
			return
		}
	}
	response, err := protocol.NewResponse(id, c.registry.ScriptStatus(session.scriptBinding))
	if err == nil {
		session.writeMessage(response)
	}
}

func (c *Client) handleScriptPolicy(session *connection, id *json.RawMessage, raw json.RawMessage) {
	if id == nil || session.ctx.Err() != nil {
		return
	}
	reject := func() {
		c.registry.RejectScriptPolicy(session.scriptBinding)
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Invalid connection-bound script policy"))
	}
	if len(raw) == 0 || len(raw) > 4096 {
		reject()
		return
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || fields == nil {
		reject()
		return
	}
	for _, name := range []string{"session_id", "policy"} {
		value, ok := fields[name]
		if !ok || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			reject()
			return
		}
	}
	var request protocol.ScriptPolicyApply
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil {
		reject()
		return
	}
	if _, err := decoder.Token(); err != io.EOF {
		reject()
		return
	}
	ack, err := c.registry.ApplyScriptPolicy(session.scriptBinding, request)
	if err != nil {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, err.Error()))
		return
	}
	response, err := protocol.NewResponse(id, ack)
	if err == nil {
		session.writeMessage(response)
	}
}
