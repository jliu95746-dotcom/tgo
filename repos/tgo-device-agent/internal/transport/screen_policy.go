package transport

import (
	"bytes"
	"encoding/json"
	"io"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

func (c *Client) handleScreenStatus(session *connection, id *json.RawMessage, raw json.RawMessage) {
	var args map[string]json.RawMessage
	if len(raw) > 0 && json.Unmarshal(raw, &args) != nil || len(args) != 0 {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Screen status accepts no parameters"))
		return
	}
	response, err := protocol.NewResponse(id, c.registry.ScreenGrantStatus(session.ctx))
	if err == nil {
		session.writeMessage(response)
	}
}

func (c *Client) handleScreenPolicy(session *connection, id *json.RawMessage, raw json.RawMessage) {
	if !completeScreenPolicyParams(raw) {
		c.registry.RejectScreenPolicyUpdate()
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Screen policy fields must be explicit and non-null"))
		return
	}
	var policy protocol.ScreenPolicy
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&policy) != nil {
		c.registry.RejectScreenPolicyUpdate()
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Invalid screen policy"))
		return
	}
	var trailing json.RawMessage
	if decoder.Decode(&trailing) != io.EOF {
		c.registry.RejectScreenPolicyUpdate()
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Invalid screen policy"))
		return
	}
	ack, err := c.registry.ApplyScreenPolicy(session.ctx, policy)
	if err != nil {
		session.writeMessage(protocol.NewErrorResponse(id, protocol.ErrInvalidParams, err.Error()))
		return
	}
	response, err := protocol.NewResponse(id, ack)
	if err == nil {
		session.writeMessage(response)
	}
}

func completeScreenPolicyParams(raw json.RawMessage) bool {
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || fields == nil {
		return false
	}
	for _, name := range []string{"protocol_version", "revision", "grant_id", "max_actions", "allow_click", "allow_text", "digest"} {
		value, present := fields[name]
		if !present || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return false
		}
	}
	if value, present := fields["click_regions"]; present {
		var regions []map[string]json.RawMessage
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) || json.Unmarshal(value, &regions) != nil {
			return false
		}
		for _, region := range regions {
			for _, name := range []string{"x", "y", "width", "height"} {
				value, present := region[name]
				if !present || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
					return false
				}
			}
		}
	}
	return true
}
