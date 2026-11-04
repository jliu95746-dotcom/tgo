package approvedscript

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"strings"
	"time"
	"unicode/utf16"
	"unicode/utf8"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/scriptprocess"
)

// This executor is deliberately package-private until durable policy,
// idempotency and execution authorization are connected. It cannot be called
// through scripts/list, tools/call or any management endpoint.
type executionLimits struct{ TimeoutSeconds, MaxOutputBytes int }
type evaluation struct {
	Outcome          scriptprocess.Outcome
	Failure          string
	ParametersDigest string
	Fields           map[string]json.RawMessage
	Err              error // Local diagnostics only; never serialize this as an API response.
}

func (c *Catalog) evaluateReadOnly(ctx context.Context, id, grantDigest string, parameters map[string]json.RawMessage, limits executionLimits) evaluation {
	result := evaluation{Outcome: scriptprocess.Rejected, Failure: "invalid_execution_scope"}
	g, exists := c.grants[id]
	if !exists || ctx == nil {
		return result
	}
	if g.Effect != "read_only" {
		result.Failure = "approval_required"
		return result
	}
	if limits.TimeoutSeconds < 1 || limits.TimeoutSeconds > g.TimeoutSeconds || limits.MaxOutputBytes < 256 || limits.MaxOutputBytes > g.MaxOutputBytes {
		return result
	}
	prepared, err := c.Prepare(id, grantDigest, parameters)
	if err != nil {
		result.Failure = "invalid_reviewed_input"
		result.Err = err
		return result
	}
	result.ParametersDigest = prepared.ParametersDigest
	request, err := runtimeRequest(g, prepared, parameters, limits.MaxOutputBytes)
	if err != nil {
		result.Failure = "invalid_runtime_arguments"
		result.Err = err
		return result
	}
	deadline, cancel := context.WithTimeout(ctx, time.Duration(limits.TimeoutSeconds)*time.Second)
	defer cancel()
	run := scriptprocess.Run(deadline, request)
	result.Outcome = run.Outcome
	result.Err = run.Err
	if run.Outcome != scriptprocess.Succeeded {
		result.Failure = "process_" + string(run.Outcome)
		return result
	}
	fields, err := c.ValidateResult(id, grantDigest, run.Stdout)
	if err != nil {
		result.Outcome = scriptprocess.Failed
		result.Failure = "invalid_reviewed_result"
		result.Err = err
		return result
	}
	encoded, err := json.Marshal(fields)
	if err != nil || len(encoded) > limits.MaxOutputBytes {
		result.Outcome = scriptprocess.OutputExceeded
		result.Failure = "result_budget_exceeded"
		return result
	}
	if deadline.Err() != nil {
		result.Outcome = scriptprocess.Canceled
		if deadline.Err() == context.DeadlineExceeded {
			result.Outcome = scriptprocess.TimedOut
		}
		result.Failure = "process_" + string(result.Outcome)
		return result
	}
	result.Failure = ""
	result.Fields = fields
	return result
}

func runtimeRequest(g protocol.ScriptGrant, p Prepared, parameters map[string]json.RawMessage, output int) (scriptprocess.Request, error) {
	request := scriptprocess.Request{Program: p.Interpreter, ProgramSHA256: g.InterpreterSHA256, Directory: p.WorkingDirectory, MaxOutputBytes: output, Input: p.Script}
	switch g.Runner {
	case "python":
		request.Arguments = append([]string{"-I", "-B", "-"}, p.Arguments...)
	case "node":
		request.Arguments = append([]string{"-"}, p.Arguments...)
	case "powershell":
		payload, err := powershellInput(g, p.Script, parameters)
		if err != nil {
			return request, err
		}
		request.Arguments = []string{"-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encodedPowerShell(powerShellBootstrap)}
		request.Input = payload
	default:
		return request, fmt.Errorf("unsupported reviewed script runtime")
	}
	return request, nil
}

type powerShellPayload struct {
	Code       string                     `json:"code"`
	Positional []json.RawMessage          `json:"positional"`
	Named      map[string]json.RawMessage `json:"named"`
}

func powershellInput(g protocol.ScriptGrant, code []byte, parameters map[string]json.RawMessage) ([]byte, error) {
	if !utf8.Valid(code) {
		return nil, fmt.Errorf("PowerShell script must be reviewed as UTF-8")
	}
	values, _, err := validateValues(g.Parameters, parameters, true)
	if err != nil {
		return nil, err
	}
	payload := powerShellPayload{Code: strings.TrimPrefix(string(code), "\ufeff"), Positional: []json.RawMessage{}, Named: map[string]json.RawMessage{}}
	seen := map[string]bool{}
	for _, binding := range g.Arguments {
		if binding.Literal != "" {
			value, _ := json.Marshal(binding.Literal)
			payload.Positional = append(payload.Positional, value)
			continue
		}
		value, exists := values[binding.Parameter]
		if !exists {
			continue
		}
		if binding.Flag == "" {
			payload.Positional = append(payload.Positional, value)
			continue
		}
		name := strings.TrimLeft(binding.Flag, "-")
		key := strings.ToLower(name)
		if seen[key] {
			return nil, fmt.Errorf("PowerShell parameter names must be unique")
		}
		seen[key] = true
		payload.Named[name] = value
	}
	return json.Marshal(payload)
}

// The bootstrap is fixed local code. Business values are deserialized into
// typed arguments, never concatenated into PowerShell source or command text.
const powerShellBootstrap = `$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$p=[Console]::In.ReadToEnd()|ConvertFrom-Json
$named=@{}
foreach($item in $p.named.PSObject.Properties){$named[$item.Name]=$item.Value}
$positionals=@($p.positional)
$reviewed=[ScriptBlock]::Create($p.code)
& $reviewed @positionals @named
if(-not $?){exit 1}
`

func encodedPowerShell(value string) string {
	units := utf16.Encode([]rune(value))
	data := make([]byte, len(units)*2)
	for index, unit := range units {
		binary.LittleEndian.PutUint16(data[index*2:], unit)
	}
	return base64.StdEncoding.EncodeToString(data)
}
