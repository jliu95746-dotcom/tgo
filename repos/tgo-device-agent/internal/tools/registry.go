// Package tools provides the tool registry and built-in tool implementations
// (fs_read, fs_write, fs_edit, shell_exec).
package tools

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/tgoai/tgo-device-agent/internal/approvedscript"
	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/sandbox"
	"github.com/tgoai/tgo-device-agent/internal/screen"
)

// Tool is the interface that all tools must implement.
type Tool interface {
	// Name returns the unique tool name (e.g. "fs_read").
	Name() string
	// Definition returns the MCP-compatible tool definition with JSON Schema.
	Definition() protocol.ToolDefinition
	// Execute runs the tool with the given arguments and returns a result.
	Execute(ctx context.Context, args map[string]interface{}) *protocol.ToolCallResult
}

// Registry holds registered tools and dispatches calls.
type Registry struct {
	scriptEnabled          bool
	scriptRoot             string
	scriptSession          *approvedscript.Session
	scriptGeneration       uint64
	scriptCatalog          *approvedscript.Catalog
	scriptCatalogErr       error
	tools                  map[string]Tool
	order                  []string // preserve registration order for listing
	sb                     *sandbox.Sandbox
	policyMu               sync.RWMutex
	readPolicy             *readPolicyState
	policyRequired         bool
	screenController       *screen.Controller
	screenConnections      int
	screenPolicyRequired   bool
	screenPolicy           *protocol.ScreenPolicy
	screenPolicyRevision   int
	screenPolicyLastDigest string
}

type readPolicyState struct {
	revision  int
	digest    string
	directory string
	maxLines  int
	maxOffset int
}

// NewRegistry creates a Registry with all built-in tools registered.
func NewRegistry(cfg *config.Config) *Registry {
	businessConfig := *cfg
	scriptRoot, rootErr := cfg.ScriptStateRoot()
	if cfg.ScriptExecutionEnabled {
		businessConfig.DeniedPaths = append(append([]string(nil), cfg.DeniedPaths...), cfg.TokenFile)
		if rootErr == nil {
			businessConfig.DeniedPaths = append(businessConfig.DeniedPaths, scriptRoot)
		} else {
			slog.Error("script private state configuration rejected", "error", rootErr)
		}
	}
	sb := sandbox.New(&businessConfig)
	r := &Registry{
		tools: make(map[string]Tool),
		sb:    sb,
	}
	r.scriptEnabled = cfg.ScriptExecutionEnabled
	r.scriptRoot = scriptRoot
	if cfg.ScriptExecutionEnabled {
		r.policyRequired = true
		if rootErr != nil {
			r.scriptRoot = ""
		}
	}
	r.scriptCatalog, r.scriptCatalogErr = approvedscript.NewCatalog(cfg.ApprovedScripts, sb)

	// Register built-in tools
	r.Register(NewFSRead(sb, cfg))
	r.Register(NewFSWrite(sb, cfg))
	r.Register(NewFSEdit(sb, cfg))
	r.Register(NewShellExec(sb, cfg))
	r.registerScreen(cfg.Screen)

	slog.Info("tool registry initialized", "tool_count", len(r.tools))
	return r
}

// ApplyReadPolicy validates the server's scope against the local sandbox.
// A failed update clears the prior applied policy, so an old scope cannot be reused.
func (r *Registry) ApplyReadPolicy(policy protocol.ReadPolicy) (protocol.ReadPolicyAck, error) {
	r.policyMu.Lock()
	defer r.policyMu.Unlock()
	r.policyRequired = true
	if r.scriptSession != nil {
		r.scriptSession.Suspend()
	}
	if r.screenController != nil {
		r.screenController.Stop()
	}
	r.readPolicy = nil
	if policy.Revision < 1 || policy.ToolName != "fs_read" ||
		policy.AllowedDirectory == "" || !filepath.IsAbs(policy.AllowedDirectory) ||
		policy.MaxLines < 1 || policy.MaxLines > 500 || policy.MaxOffset < 0 || policy.MaxOffset > 100000 ||
		policy.Digest != protocol.ReadPolicyDigest(policy.Revision, policy.ToolName, policy.AllowedDirectory, policy.MaxLines, policy.MaxOffset) {
		return protocol.ReadPolicyAck{}, fmt.Errorf("invalid read policy")
	}
	resolved, err := r.sb.ResolvePath(policy.AllowedDirectory)
	if err != nil {
		return protocol.ReadPolicyAck{}, fmt.Errorf("read policy outside local sandbox")
	}
	info, err := os.Stat(resolved)
	if err != nil || !info.IsDir() {
		return protocol.ReadPolicyAck{}, fmt.Errorf("read policy directory unavailable")
	}
	realDirectory, err := filepath.EvalSymlinks(resolved)
	if err != nil {
		return protocol.ReadPolicyAck{}, fmt.Errorf("read policy directory unavailable")
	}
	r.readPolicy = &readPolicyState{
		revision: policy.Revision, digest: policy.Digest, directory: realDirectory,
		maxLines: policy.MaxLines, maxOffset: policy.MaxOffset,
	}
	return protocol.ReadPolicyAck{Revision: policy.Revision, Digest: policy.Digest}, nil
}

// ResetPolicyForConnection prevents a stale acknowledgment surviving reconnect.
func (r *Registry) ResetPolicyForConnection() {
	r.policyMu.Lock()
	defer r.policyMu.Unlock()
	if r.scriptSession != nil {
		r.scriptSession.Suspend()
	}
	if r.readPolicy != nil {
		r.policyRequired = true
	}
	r.readPolicy = nil
	r.screenConnections++
	r.screenPolicy = nil
	if r.screenController != nil && r.screenConnections > 1 {
		r.screenController.Stop()
	}
}

func (r *Registry) checkReadPolicy(name string, args map[string]interface{}) (string, error) {
	if !r.policyRequired {
		return "", nil // Existing devices without a published policy retain their local sandbox.
	}
	if r.readPolicy == nil || name != "fs_read" {
		return "", fmt.Errorf("device policy is not active for this tool")
	}
	path, ok := args["path"].(string)
	if !ok || !filepath.IsAbs(path) {
		return "", fmt.Errorf("read policy requires an absolute file path")
	}
	for key := range args {
		if key != "path" && key != "limit" && key != "offset" && key != "encoding" {
			return "", fmt.Errorf("unsupported read argument")
		}
	}
	limit, ok := policyIntArg(args["limit"])
	if !ok || limit < 1 || limit > r.readPolicy.maxLines {
		return "", fmt.Errorf("read line limit exceeds policy")
	}
	if offsetRaw, exists := args["offset"]; exists {
		offset, valid := policyIntArg(offsetRaw)
		if !valid || offset < 0 || offset > r.readPolicy.maxOffset {
			return "", fmt.Errorf("read offset exceeds policy")
		}
	}
	if encoding, exists := args["encoding"]; exists && encoding != "utf-8" {
		return "", fmt.Errorf("read encoding is not allowed")
	}
	resolved, err := r.sb.ResolvePath(path)
	if err != nil {
		return "", fmt.Errorf("file is outside local sandbox")
	}
	realPath, err := filepath.EvalSymlinks(resolved)
	if err != nil {
		return "", fmt.Errorf("file is unavailable")
	}
	relative, err := filepath.Rel(r.readPolicy.directory, realPath)
	if err != nil || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) || filepath.IsAbs(relative) {
		return "", fmt.Errorf("file is outside active read policy")
	}
	return realPath, nil
}

func policyIntArg(value interface{}) (int, bool) {
	switch number := value.(type) {
	case int:
		return number, true
	case float64:
		if number != number || number < -100000 || number > 100000 {
			return 0, false
		}
		converted := int(number)
		return converted, float64(converted) == number
	default:
		return 0, false
	}
}

// Register adds a tool to the registry.
func (r *Registry) Register(t Tool) {
	name := t.Name()
	r.tools[name] = t
	r.order = append(r.order, name)
	slog.Debug("tool registered", "name", name)
}

// ListTools returns all tool definitions in registration order.
func (r *Registry) ListTools() []protocol.ToolDefinition {
	r.policyMu.RLock()
	defer r.policyMu.RUnlock()
	defs := make([]protocol.ToolDefinition, 0, len(r.order))
	for _, name := range r.order {
		if isScreenTool(name) && r.screenController != nil && !r.screenController.Active() {
			continue
		}
		if r.policyRequired && (r.readPolicy == nil || name != "fs_read") {
			continue
		}
		if !r.policyRequired && r.screenPolicyRequired && !r.screenToolAllowed(name) {
			continue
		}
		definition := r.tools[name].Definition()
		if r.policyRequired {
			definition.Description = "Read an approved business file within the active device policy."
			definition.InputSchema = map[string]interface{}{
				"type": "object",
				"properties": map[string]interface{}{
					"path": map[string]interface{}{
						"type": "string", "description": "Absolute file path within the approved directory",
					},
					"limit": map[string]interface{}{
						"type": "integer", "minimum": 1, "maximum": r.readPolicy.maxLines,
					},
					"offset": map[string]interface{}{
						"type": "integer", "minimum": 0, "maximum": r.readPolicy.maxOffset,
					},
					"encoding": map[string]interface{}{
						"type": "string", "enum": []string{"utf-8"}, "default": "utf-8",
					},
				},
				"required":             []string{"path", "limit"},
				"additionalProperties": false,
			}
		}
		defs = append(defs, definition)
	}
	return defs
}

// CallTool dispatches a tool call by name.
func (r *Registry) CallTool(ctx context.Context, name string, args map[string]interface{}) *protocol.ToolCallResult {
	r.policyMu.RLock()
	defer r.policyMu.RUnlock()
	if !r.policyRequired && r.screenPolicyRequired {
		if err := r.checkScreenPolicy(name, args); err != nil {
			return protocol.TextResult("Error: "+err.Error(), true)
		}
	}
	canonicalPath, err := r.checkReadPolicy(name, args)
	if err != nil {
		return protocol.TextResult("Error: "+err.Error(), true)
	}
	t, ok := r.tools[name]
	if !ok {
		return protocol.TextResult(
			fmt.Sprintf("Error: tool '%s' not found. Available tools: %v", name, r.order),
			true,
		)
	}
	if canonicalPath != "" {
		checkedArgs := make(map[string]interface{}, len(args))
		for key, value := range args {
			checkedArgs[key] = value
		}
		checkedArgs["path"] = canonicalPath
		return t.Execute(ctx, checkedArgs)
	}
	return t.Execute(ctx, args)
}
