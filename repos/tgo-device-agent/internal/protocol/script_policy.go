package protocol

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

var scriptName = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]{0,47}$`)
var scriptVersion = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$`)
var scriptSHA256 = regexp.MustCompile(`^[0-9a-f]{64}$`)
var scriptFlag = regexp.MustCompile(`^--?[A-Za-z][A-Za-z0-9_-]{0,47}$`)

// ScriptParameter defines one business value, not executable code or a command.
type ScriptParameter struct {
	Name      string   `json:"name"`
	Kind      string   `json:"kind"`
	Required  bool     `json:"required"`
	Minimum   *int64   `json:"minimum,omitempty"`
	Maximum   *int64   `json:"maximum,omitempty"`
	MaxLength int      `json:"max_length,omitempty"`
	Choices   []string `json:"choices,omitempty"`
}

func (p ScriptParameter) Validate() error {
	if !scriptName.MatchString(p.Name) {
		return fmt.Errorf("invalid business field name")
	}
	switch p.Kind {
	case "integer":
		if p.Minimum == nil || p.Maximum == nil || *p.Minimum > *p.Maximum || *p.Minimum < -9007199254740991 || *p.Maximum > 9007199254740991 || p.MaxLength != 0 || len(p.Choices) != 0 {
			return fmt.Errorf("invalid integer bounds")
		}
	case "string":
		if p.MaxLength < 1 || p.MaxLength > 4096 || p.Minimum != nil || p.Maximum != nil || len(p.Choices) > 32 {
			return fmt.Errorf("invalid text bounds")
		}
		seen := map[string]bool{}
		for _, value := range p.Choices {
			if !SafeScriptText(value) || utf8.RuneCountInString(value) > p.MaxLength || seen[value] {
				return fmt.Errorf("invalid text choices")
			}
			seen[value] = true
		}
	case "boolean":
		if p.Minimum != nil || p.Maximum != nil || p.MaxLength != 0 || len(p.Choices) != 0 {
			return fmt.Errorf("boolean fields cannot carry text or number constraints")
		}
	default:
		return fmt.Errorf("unsupported business field type")
	}
	return nil
}

func SafeScriptText(value string) bool {
	return utf8.ValidString(value) && !strings.ContainsFunc(value, func(r rune) bool {
		return unicode.IsControl(r) || r == utf8.RuneError || r == '\u2028' || r == '\u2029'
	})
}

// ScriptArgument is LOCAL configuration. A flag and its optional value are
// omitted together; business inputs can never choose a program or command line.
type ScriptArgument struct {
	Literal   string `json:"literal,omitempty"`
	Parameter string `json:"parameter,omitempty"`
	Flag      string `json:"flag,omitempty"`
}

// ScriptGrant stays on the device. Never serialize it as a server catalog.
type ScriptGrant struct {
	Approved          bool              `json:"approved"`
	ScriptID          string            `json:"script_id"`
	Title             string            `json:"title"`
	Version           string            `json:"version"`
	Runner            string            `json:"runner"`
	ScriptPath        string            `json:"script_path"`
	ContentSHA256     string            `json:"content_sha256"`
	Interpreter       string            `json:"interpreter"`
	InterpreterSHA256 string            `json:"interpreter_sha256"`
	WorkingDirectory  string            `json:"working_directory"`
	Effect            string            `json:"effect"`
	Parameters        []ScriptParameter `json:"parameters"`
	Arguments         []ScriptArgument  `json:"arguments"`
	ResultFields      []ScriptParameter `json:"result_fields"`
	TimeoutSeconds    int               `json:"timeout_seconds"`
	MaxOutputBytes    int               `json:"max_output_bytes"`
	MaxRuns           int               `json:"max_runs"`
}

func (g ScriptGrant) Validate() error {
	if !g.Approved || !scriptName.MatchString(g.ScriptID) || !scriptVersion.MatchString(g.Version) || g.Title == "" || !SafeScriptText(g.Title) || utf8.RuneCountInString(g.Title) > 100 {
		return fmt.Errorf("invalid local script approval")
	}
	if g.Runner != "python" && g.Runner != "node" && g.Runner != "powershell" {
		return fmt.Errorf("unsupported approved script runner")
	}
	if !scriptSHA256.MatchString(g.ContentSHA256) || !scriptSHA256.MatchString(g.InterpreterSHA256) {
		return fmt.Errorf("invalid reviewed file digest")
	}
	if g.Effect != "read_only" && g.Effect != "write" {
		return fmt.Errorf("script effect must be declared locally")
	}
	if !validScriptLimits(g.TimeoutSeconds, g.MaxOutputBytes, g.MaxRuns) || len(g.Parameters) > 16 || len(g.ResultFields) == 0 || len(g.ResultFields) > 32 || len(g.Arguments) > 64 {
		return fmt.Errorf("invalid script limits")
	}
	for _, fields := range [][]ScriptParameter{g.Parameters, g.ResultFields} {
		seen := map[string]bool{}
		for _, field := range fields {
			if err := field.Validate(); err != nil {
				return err
			}
			if seen[field.Name] {
				return fmt.Errorf("duplicate business field")
			}
			seen[field.Name] = true
		}
	}
	params := map[string]bool{}
	for _, p := range g.Parameters {
		params[p.Name] = true
	}
	for _, arg := range g.Arguments {
		if arg.Literal != "" {
			if arg.Parameter != "" || arg.Flag != "" || !SafeScriptText(arg.Literal) || len(arg.Literal) > 1024 {
				return fmt.Errorf("invalid fixed argument")
			}
		} else if !params[arg.Parameter] || (arg.Flag != "" && !scriptFlag.MatchString(arg.Flag)) {
			return fmt.Errorf("invalid business argument binding")
		}
	}
	return nil
}

type ScriptDescriptor struct {
	ScriptID       string            `json:"script_id"`
	Title          string            `json:"title"`
	Version        string            `json:"version"`
	GrantDigest    string            `json:"grant_digest"`
	Effect         string            `json:"effect"`
	Parameters     []ScriptParameter `json:"parameters"`
	ResultFields   []ScriptParameter `json:"result_fields"`
	TimeoutSeconds int               `json:"timeout_seconds"`
	MaxOutputBytes int               `json:"max_output_bytes"`
	MaxRuns        int               `json:"max_runs"`
}

type ScriptCatalog struct {
	ProtocolVersion int                `json:"protocol_version"`
	Scripts         []ScriptDescriptor `json:"scripts"`
}

type ScriptPolicy struct {
	ProtocolVersion int    `json:"protocol_version"`
	Revision        int    `json:"revision"`
	ScriptID        string `json:"script_id"`
	GrantDigest     string `json:"grant_digest"`
	TimeoutSeconds  int    `json:"timeout_seconds"`
	MaxOutputBytes  int    `json:"max_output_bytes"`
	MaxRuns         int    `json:"max_runs"`
	Digest          string `json:"digest"`
}

func validScriptLimits(seconds, output, runs int) bool {
	return seconds >= 1 && seconds <= 120 && output >= 256 && output <= 65536 && runs >= 1 && runs <= 100
}

func (p ScriptPolicy) Validate() error {
	if p.ProtocolVersion != 1 || p.Revision < 1 || p.Revision > 2147483647 || !scriptName.MatchString(p.ScriptID) || !scriptSHA256.MatchString(p.GrantDigest) || !validScriptLimits(p.TimeoutSeconds, p.MaxOutputBytes, p.MaxRuns) || p.Digest != ScriptPolicyDigest(p) {
		return fmt.Errorf("invalid approved script policy")
	}
	return nil
}

func ScriptPolicyDigest(p ScriptPolicy) string {
	data := fmt.Sprintf("tgo-approved-script-policy-v1\n%d\n%d\n%s\n%s\n%d\n%d\n%d\n", p.ProtocolVersion, p.Revision, p.ScriptID, p.GrantDigest, p.TimeoutSeconds, p.MaxOutputBytes, p.MaxRuns)
	digest := sha256.Sum256([]byte(data))
	return hex.EncodeToString(digest[:])
}

type ScriptPolicyAck struct {
	ProtocolVersion int    `json:"protocol_version"`
	SessionID       string `json:"session_id"`
	Revision        int    `json:"revision"`
	Digest          string `json:"digest"`
}

type ScriptPolicyApply struct {
	SessionID string       `json:"session_id"`
	Policy    ScriptPolicy `json:"policy"`
}

type ScriptSessionStatus struct {
	ProtocolVersion int    `json:"protocol_version"`
	State           string `json:"state"`
	SessionID       string `json:"session_id,omitempty"`
	Reason          string `json:"reason"`
}
