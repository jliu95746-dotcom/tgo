// Package approvedscript validates local reviewed definitions and prepares
// bounded execution. Discovery and preparation never establish authorization.
package approvedscript

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/tgoai/tgo-device-agent/internal/protocol"
)

type PathGuard interface {
	ResolvePath(string) (string, error)
	ValidateCommand(string) error
}

type Catalog struct {
	grants map[string]protocol.ScriptGrant
	guard  PathGuard
}

// Prepared contains device-local execution details, never a network response.
type Prepared struct {
	Descriptor       protocol.ScriptDescriptor
	Interpreter      string
	WorkingDirectory string
	Script           []byte
	Arguments        []string
	ParametersDigest string
}

func NewCatalog(grants []protocol.ScriptGrant, guard PathGuard) (*Catalog, error) {
	if len(grants) > 32 || guard == nil {
		return nil, fmt.Errorf("invalid local script catalog")
	}
	c := &Catalog{grants: map[string]protocol.ScriptGrant{}, guard: guard}
	for _, grant := range grants {
		if !grant.Approved {
			continue
		}
		if err := grant.Validate(); err != nil {
			return nil, err
		}
		if _, duplicate := c.grants[grant.ScriptID]; duplicate {
			return nil, fmt.Errorf("duplicate approved script id")
		}
		data, _ := json.Marshal(grant)
		var owned protocol.ScriptGrant
		if json.Unmarshal(data, &owned) != nil {
			return nil, fmt.Errorf("invalid local script approval")
		}
		c.grants[grant.ScriptID] = owned
	}
	return c, nil
}

func digest(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }

func descriptor(g protocol.ScriptGrant) protocol.ScriptDescriptor {
	if g.Parameters == nil {
		g.Parameters = []protocol.ScriptParameter{}
	}
	data, _ := json.Marshal(g)
	d := protocol.ScriptDescriptor{ScriptID: g.ScriptID, Title: g.Title, Version: g.Version, GrantDigest: digest(data), Effect: g.Effect, Parameters: g.Parameters, ResultFields: g.ResultFields, TimeoutSeconds: g.TimeoutSeconds, MaxOutputBytes: g.MaxOutputBytes, MaxRuns: g.MaxRuns}
	// Do not expose mutable slices or numeric-bound pointers retained in the grant.
	encoded, _ := json.Marshal(d)
	var detached protocol.ScriptDescriptor
	_ = json.Unmarshal(encoded, &detached)
	return detached
}

func readReviewed(path, expected string, maximum int64) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("reviewed file is unavailable")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > maximum {
		return nil, fmt.Errorf("reviewed file is not a bounded regular file")
	}
	data, err := io.ReadAll(io.LimitReader(file, maximum+1))
	if err != nil || int64(len(data)) > maximum || digest(data) != expected {
		return nil, fmt.Errorf("reviewed file contents changed")
	}
	return data, nil
}

type materialized struct {
	script           []byte
	interpreter      string
	workingDirectory string
}

func (c *Catalog) materialize(g protocol.ScriptGrant) (materialized, error) {
	for _, path := range []string{g.ScriptPath, g.WorkingDirectory, g.Interpreter} {
		if len(path) > 1024 || !filepath.IsAbs(path) || !protocol.SafeScriptText(path) {
			return materialized{}, fmt.Errorf("local script paths must be explicit absolute paths")
		}
	}
	extension := map[string]string{"python": ".py", "node": ".js", "powershell": ".ps1"}[g.Runner]
	if strings.ToLower(filepath.Ext(g.ScriptPath)) != extension {
		return materialized{}, fmt.Errorf("script does not match its approved runner")
	}
	script, err := c.guard.ResolvePath(g.ScriptPath)
	if err != nil {
		return materialized{}, fmt.Errorf("script is outside the local sandbox")
	}
	cwd, err := c.guard.ResolvePath(g.WorkingDirectory)
	if err != nil {
		return materialized{}, fmt.Errorf("script working directory is outside the local sandbox")
	}
	info, err := os.Stat(cwd)
	if err != nil || !info.IsDir() {
		return materialized{}, fmt.Errorf("script working directory is unavailable")
	}
	interpreter, err := filepath.EvalSymlinks(g.Interpreter)
	if err != nil {
		return materialized{}, fmt.Errorf("reviewed runtime is unavailable")
	}
	if err := verifyRuntime(interpreter, g.InterpreterSHA256); err != nil {
		return materialized{}, err
	}
	code, err := readReviewed(script, g.ContentSHA256, 1024*1024)
	if err != nil {
		return materialized{}, err
	}
	return materialized{script: code, interpreter: interpreter, workingDirectory: cwd}, nil
}

func (c *Catalog) List() (protocol.ScriptCatalog, error) {
	result := protocol.ScriptCatalog{ProtocolVersion: 1, Scripts: []protocol.ScriptDescriptor{}}
	ids := make([]string, 0, len(c.grants))
	for id := range c.grants {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		g := c.grants[id]
		if _, err := c.materialize(g); err != nil {
			return protocol.ScriptCatalog{}, err
		}
		result.Scripts = append(result.Scripts, descriptor(g))
	}
	return result, nil
}

func (c *Catalog) Prepare(id, grantDigest string, parameters map[string]json.RawMessage) (Prepared, error) {
	g, ok := c.grants[id]
	if !ok {
		return Prepared{}, fmt.Errorf("script has no current local approval")
	}
	d := descriptor(g)
	if grantDigest != d.GrantDigest {
		return Prepared{}, fmt.Errorf("approved script definition changed")
	}
	code, err := c.materialize(g)
	if err != nil {
		return Prepared{}, err
	}
	values, text, err := validateValues(g.Parameters, parameters, true)
	if err != nil {
		return Prepared{}, err
	}
	arguments := []string{}
	for _, arg := range g.Arguments {
		if arg.Literal != "" {
			arguments = append(arguments, arg.Literal)
			continue
		}
		value, exists := text[arg.Parameter]
		if !exists {
			continue
		}
		if arg.Flag != "" {
			arguments = append(arguments, arg.Flag)
		}
		arguments = append(arguments, value)
	}
	if err := c.guard.ValidateCommand(strings.Join(append([]string{g.Interpreter}, arguments...), " ")); err != nil {
		return Prepared{}, fmt.Errorf("prepared script arguments violate the local command policy")
	}
	encoded, _ := json.Marshal(values)
	if len(encoded) > 16384 {
		return Prepared{}, fmt.Errorf("business inputs exceed the approved budget")
	}
	return Prepared{Descriptor: d, Interpreter: code.interpreter, WorkingDirectory: code.workingDirectory, Script: code.script, Arguments: arguments, ParametersDigest: digest([]byte("tgo-approved-script-input-v1\n" + d.GrantDigest + "\n" + string(encoded)))}, nil
}

func (c *Catalog) ValidateResult(id, grantDigest string, raw []byte) (map[string]json.RawMessage, error) {
	g, ok := c.grants[id]
	if !ok || descriptor(g).GrantDigest != grantDigest {
		return nil, fmt.Errorf("script result has no matching local approval")
	}
	if len(raw) > g.MaxOutputBytes || !utf8.Valid(raw) {
		return nil, fmt.Errorf("script result exceeds the approved output scope")
	}
	if _, err := c.materialize(g); err != nil {
		return nil, err
	}
	values, err := decodeResult(raw)
	if err != nil {
		return nil, err
	}
	normalized, _, err := validateValues(g.ResultFields, values, false)
	if err == nil {
		encoded, encodeErr := json.Marshal(normalized)
		if encodeErr != nil || len(encoded) > g.MaxOutputBytes {
			return nil, fmt.Errorf("normalized script result exceeds the approved output scope")
		}
	}
	return normalized, err
}

func verifyRuntime(path, expected string) error {
	file, err := os.Open(path)
	if err != nil {
		return fmt.Errorf("reviewed runtime is unavailable")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 256*1024*1024 {
		return fmt.Errorf("reviewed runtime is not a bounded regular file")
	}
	hash := sha256.New()
	size, err := io.Copy(hash, io.LimitReader(file, 256*1024*1024+1))
	if err != nil || size > 256*1024*1024 || hex.EncodeToString(hash.Sum(nil)) != expected {
		return fmt.Errorf("reviewed runtime contents changed")
	}
	return nil
}
