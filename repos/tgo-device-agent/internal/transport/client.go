// Package transport implements the TCP JSON-RPC client that connects to
// tgo-device-control, handles authentication, heartbeat, reconnection,
// and dispatches incoming tool calls to the tool registry.
package transport

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os"
	"runtime"
	"strings"
	"sync/atomic"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/tools"
)

// Client manages the TCP connection to tgo-device-control.
type Client struct {
	cfg      *config.Config
	registry *tools.Registry

	deviceID  string
	projectID string

	requestID atomic.Int64
	running   atomic.Bool
}

// NewClient creates a new transport client.
func NewClient(cfg *config.Config, registry *tools.Registry) *Client {
	return &Client{
		cfg:      cfg,
		registry: registry,
	}
}

// Run connects to the server and enters the main loop.
// It automatically reconnects on disconnection until ctx is cancelled.
func (c *Client) Run(ctx context.Context) error {
	if !c.running.CompareAndSwap(false, true) {
		return errors.New("client is already running")
	}
	defer c.running.Store(false)
	delay := c.cfg.ReconnectInitialDelay
	attempts := 0

	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		authenticated, err := c.connectAndServe(ctx)
		if err == nil || ctx.Err() != nil {
			return nil
		}

		if authenticated {
			attempts = 0
			delay = c.cfg.ReconnectInitialDelay
		} else {
			attempts++
		}
		if c.cfg.MaxReconnectAttempts > 0 && attempts >= c.cfg.MaxReconnectAttempts {
			return fmt.Errorf("max reconnect attempts (%d) reached: %w", c.cfg.MaxReconnectAttempts, err)
		}

		slog.Warn("connection lost, reconnecting",
			"error", err,
			"delay", delay,
			"attempt", attempts,
		)

		select {
		case <-ctx.Done():
			return nil
		case <-time.After(delay):
		}

		// Exponential backoff
		delay = delay * 2
		if delay > c.cfg.ReconnectMaxDelay {
			delay = c.cfg.ReconnectMaxDelay
		}
	}
}

// connectAndServe performs a single connect-auth-serve cycle.
func (c *Client) connectAndServe(ctx context.Context) (bool, error) {
	addr := fmt.Sprintf("%s:%d", c.cfg.ServerHost, c.cfg.ServerPort)
	slog.Info("connecting to server", "addr", addr)

	dialer := net.Dialer{Timeout: 10 * time.Second}
	conn, err := dialer.DialContext(ctx, "tcp", addr)
	if err != nil {
		return false, fmt.Errorf("dial: %w", err)
	}

	session := newConnection(ctx, conn)
	defer session.close()

	slog.Info("connected to server", "addr", addr)

	// Authenticate
	if err := c.authenticate(session); err != nil {
		return false, fmt.Errorf("auth: %w", err)
	}

	slog.Info("authenticated",
		"device_id", c.deviceID,
		"project_id", c.projectID,
	)

	// Start heartbeat
	go c.heartbeatLoop(session)

	// Main read loop
	return true, c.readLoop(session)
}

// authenticate sends the auth request and reads the response directly using
// the shared scanner. This is done before the main readLoop starts.
func (c *Client) authenticate(session *connection) error {
	params := protocol.AuthParams{
		DeviceInfo: protocol.DeviceInfo{
			Name:      c.cfg.DeviceName,
			Version:   "1.0.0",
			OS:        runtime.GOOS,
			OSVersion: osVersion(),
		},
	}

	if c.cfg.DeviceToken != "" {
		params.DeviceToken = c.cfg.DeviceToken
		slog.Debug("authenticating with device token")
	} else if c.cfg.BindCode != "" {
		params.BindCode = c.cfg.BindCode
		slog.Debug("authenticating with bind code")
	} else {
		return errors.New("no bind code or device token available")
	}

	id := int(c.requestID.Add(1))
	req, err := protocol.NewRequest(id, "auth", params)
	if err != nil {
		return err
	}

	// Send auth request
	if err := session.writeMessage(req); err != nil {
		return fmt.Errorf("send auth: %w", err)
	}

	slog.Debug("auth request sent, waiting for response")

	// The connection's cancellation hook closes the socket to interrupt Scan.
	// No detached scanner goroutine can survive into a later connection.
	if err := session.conn.SetReadDeadline(time.Now().Add(30 * time.Second)); err != nil {
		return err
	}
	defer session.conn.SetReadDeadline(time.Time{})
	if !session.reader.Scan() {
		if err := session.reader.Err(); err != nil {
			return fmt.Errorf("read auth response: %w", err)
		}
		return errors.New("connection closed before auth response")
	}
	var resp protocol.Response
	if err := json.Unmarshal(session.reader.Bytes(), &resp); err != nil {
		return fmt.Errorf("parse auth response: %w", err)
	}
	var responseID int
	if resp.JSONRPC != "2.0" || resp.ID == nil || json.Unmarshal(*resp.ID, &responseID) != nil || responseID != id {
		return errors.New("auth response does not match the pending request")
	}
	return c.processAuthResult(&resp)
}

// processAuthResult handles the parsed auth response.
func (c *Client) processAuthResult(resp *protocol.Response) error {
	if resp.Error != nil {
		return fmt.Errorf("auth rejected: [%d] %s", resp.Error.Code, resp.Error.Message)
	}

	var result protocol.AuthResult
	if err := json.Unmarshal(resp.Result, &result); err != nil {
		return fmt.Errorf("parse auth result: %w", err)
	}
	if result.Status != "ok" || strings.TrimSpace(result.DeviceID) == "" || strings.TrimSpace(result.ProjectID) == "" {
		return errors.New("auth response lacks a confirmed device identity")
	}
	if c.cfg.DeviceToken == "" && result.DeviceToken == "" {
		return errors.New("registration response lacks a device token")
	}

	c.deviceID = result.DeviceID
	c.projectID = result.ProjectID

	// Save token on first registration
	if result.DeviceToken != "" {
		c.cfg.DeviceToken = result.DeviceToken
		if err := c.cfg.SaveTokenToFile(result.DeviceToken); err != nil {
			slog.Warn("failed to save device token", "error", err)
		} else {
			slog.Info("device token saved for reconnection")
		}
		// Clear bind code so reconnects use token
		c.cfg.BindCode = ""
	}

	return nil
}

// sendRequest sends a JSON-RPC request and waits for the matching response.
func (c *Client) sendRequest(ctx context.Context, session *connection, method string, params interface{}) (*protocol.Response, error) {
	id := int(c.requestID.Add(1))

	req, err := protocol.NewRequest(id, method, params)
	if err != nil {
		return nil, err
	}

	ch := make(chan *protocol.Response, 1)
	session.pending.Store(id, ch)
	defer session.pending.Delete(id)

	if err := session.writeMessage(req); err != nil {
		return nil, err
	}

	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-session.ctx.Done():
		return nil, errors.New("connection closed while waiting for response")
	case resp := <-ch:
		return resp, nil
	case <-time.After(30 * time.Second):
		return nil, fmt.Errorf("request %d (%s) timed out", id, method)
	}
}

// readLoop reads newline-delimited JSON messages using the shared scanner.
func (c *Client) readLoop(session *connection) error {
	for {
		select {
		case <-session.ctx.Done():
			return session.ctx.Err()
		default:
		}

		if !session.reader.Scan() {
			if err := session.reader.Err(); err != nil {
				return fmt.Errorf("read: %w", err)
			}
			return errors.New("connection closed by server")
		}

		// Copy bytes since scanner reuses the buffer
		raw := append([]byte(nil), session.reader.Bytes()...)

		if len(raw) == 0 {
			continue
		}

		slog.Debug("received message", "size", len(raw))

		// Determine if response or request
		if protocol.IsResponse(raw) {
			session.handleResponse(raw)
		} else {
			go c.handleRequest(session, raw)
		}
	}
}

// handleResponse resolves a pending request future.
func (session *connection) handleResponse(raw []byte) {
	var resp protocol.Response
	if err := json.Unmarshal(raw, &resp); err != nil {
		slog.Warn("failed to parse response", "error", err)
		return
	}

	if resp.ID == nil {
		return
	}

	// Extract numeric ID
	var id int
	if err := json.Unmarshal(*resp.ID, &id); err != nil {
		slog.Warn("failed to parse response id", "error", err)
		return
	}

	if ch, ok := session.pending.LoadAndDelete(id); ok {
		select {
		case ch.(chan *protocol.Response) <- &resp:
		default:
		}
	}
}

// handleRequest dispatches incoming server requests (tools/list, tools/call, ping).
func (c *Client) handleRequest(session *connection, raw []byte) {
	if session.ctx.Err() != nil {
		return
	}
	var req protocol.Request
	if err := json.Unmarshal(raw, &req); err != nil {
		slog.Warn("failed to parse request", "error", err)
		return
	}

	slog.Debug("handling request", "method", req.Method)

	switch req.Method {
	case "ping":
		c.handlePing(session, req.ID)
	case "tools/list":
		c.handleToolsList(session, req.ID)
	case "tools/call":
		c.handleToolsCall(session, req.ID, req.Params)
	default:
		if req.ID != nil {
			resp := protocol.NewErrorResponse(req.ID, protocol.ErrMethodNotFound,
				fmt.Sprintf("Method not found: %s", req.Method))
			session.writeMessage(resp)
		}
	}
}

// handlePing responds to server ping.
func (c *Client) handlePing(session *connection, id *json.RawMessage) {
	if id != nil {
		result := map[string]interface{}{
			"pong":      true,
			"timestamp": time.Now().Unix(),
		}
		resp, _ := protocol.NewResponse(id, result)
		session.writeMessage(resp)
	} else {
		// Notification ping -> respond with pong notification
		pong, _ := protocol.NewNotification("pong", nil)
		session.writeMessage(pong)
	}
}

// handleToolsList returns the tool definitions from the registry.
func (c *Client) handleToolsList(session *connection, id *json.RawMessage) {
	defs := c.registry.ListTools()
	result := protocol.ToolsListResult{Tools: defs}
	resp, err := protocol.NewResponse(id, result)
	if err != nil {
		slog.Error("failed to build tools/list response", "error", err)
		return
	}
	session.writeMessage(resp)
}

// handleToolsCall dispatches a tool call to the registry and returns the result.
func (c *Client) handleToolsCall(session *connection, id *json.RawMessage, paramsRaw json.RawMessage) {
	start := time.Now()

	var params protocol.ToolCallParams
	if err := json.Unmarshal(paramsRaw, &params); err != nil {
		resp := protocol.NewErrorResponse(id, protocol.ErrInvalidParams, "Invalid tools/call params")
		session.writeMessage(resp)
		return
	}

	slog.Info("tool call", "tool", params.Name, "args_keys", mapKeys(params.Arguments))

	if session.ctx.Err() != nil {
		return
	}
	result := c.registry.CallTool(session.ctx, params.Name, params.Arguments)

	elapsed := time.Since(start)
	slog.Info("tool call completed",
		"tool", params.Name,
		"is_error", result.IsError,
		"elapsed", elapsed,
	)

	resp, err := protocol.NewResponse(id, result)
	if err != nil {
		slog.Error("failed to build tools/call response", "error", err)
		return
	}
	session.writeMessage(resp)
}

// heartbeatLoop sends periodic heartbeat messages.
func (c *Client) heartbeatLoop(session *connection) {
	ticker := time.NewTicker(c.cfg.HeartbeatInterval)
	defer ticker.Stop()

	for {
		select {
		case <-session.ctx.Done():
			return
		case <-ticker.C:
			msg, _ := protocol.NewNotification("pong", nil)
			if err := session.writeMessage(msg); err != nil {
				slog.Warn("heartbeat send failed", "error", err)
				return
			}
			slog.Debug("heartbeat sent")
		}
	}
}

// mapKeys extracts the keys of a map for logging.
func mapKeys(m map[string]interface{}) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	return keys
}

// osVersion returns a best-effort OS version string.
func osVersion() string {
	switch runtime.GOOS {
	case "darwin":
		// Try sw_vers
		if data, err := os.ReadFile("/System/Library/CoreServices/SystemVersion.plist"); err == nil {
			s := string(data)
			if idx := strings.Index(s, "ProductVersion"); idx >= 0 {
				sub := s[idx:]
				start := strings.Index(sub, "<string>")
				end := strings.Index(sub, "</string>")
				if start >= 0 && end > start {
					return sub[start+8 : end]
				}
			}
		}
	}
	return runtime.GOARCH
}
