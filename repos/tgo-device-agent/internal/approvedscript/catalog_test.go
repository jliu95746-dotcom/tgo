package approvedscript

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/sandbox"
)

func hashBytes(value []byte) string { s := sha256.Sum256(value); return hex.EncodeToString(s[:]) }

func fixture(t *testing.T) (protocol.ScriptGrant, PathGuard) {
	t.Helper()
	root := t.TempDir()
	script := filepath.Join(root, "daily.py")
	interpreter := filepath.Join(root, "python.exe")
	for path, body := range map[string]string{script: "print('只用于目录验证，不执行')", interpreter: "fixture-runtime"} {
		if err := os.WriteFile(path, []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	lo, hi := int64(1), int64(31)
	g := protocol.ScriptGrant{Approved: true, ScriptID: "daily_report", Title: "每日业务报表", Version: "v1", Runner: "python", ScriptPath: script, ContentSHA256: hashBytes([]byte("print('只用于目录验证，不执行')")), Interpreter: interpreter, InterpreterSHA256: hashBytes([]byte("fixture-runtime")), WorkingDirectory: root, Effect: "read_only", TimeoutSeconds: 20, MaxOutputBytes: 4096, MaxRuns: 3,
		Parameters:   []protocol.ScriptParameter{{Name: "day", Kind: "integer", Required: true, Minimum: &lo, Maximum: &hi}, {Name: "category", Kind: "string", MaxLength: 20}},
		Arguments:    []protocol.ScriptArgument{{Parameter: "day", Flag: "--day"}, {Parameter: "category", Flag: "--category"}},
		ResultFields: []protocol.ScriptParameter{{Name: "count", Kind: "integer", Required: true, Minimum: &lo, Maximum: &hi}},
	}
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	return g, sandbox.New(cfg)
}

func TestOnlyLocallyApprovedImmutableScriptsEnterCatalog(t *testing.T) {
	g, guard := fixture(t)
	disabled := protocol.ScriptGrant{ScriptID: "disabled", Approved: false}
	c, err := NewCatalog([]protocol.ScriptGrant{g, disabled}, guard)
	if err != nil {
		t.Fatal(err)
	}
	list, err := c.List()
	if err != nil || len(list.Scripts) != 1 {
		t.Fatalf("catalog: %#v %v", list, err)
	}
	body, _ := json.Marshal(list)
	if strings.Contains(string(body), g.ScriptPath) || strings.Contains(string(body), g.Interpreter) {
		t.Fatal("catalog exposed private paths")
	}
	g.Title = "caller mutation"
	if list.Scripts[0].Title != "每日业务报表" {
		t.Fatal("caller mutated the stored grant")
	}
	if err := os.WriteFile(g.ScriptPath, []byte("changed after approval"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := c.List(); err == nil {
		t.Fatal("changed script remained approved")
	}
}

func TestPreparationBindsVersionArgumentsAndResultSchema(t *testing.T) {
	g, guard := fixture(t)
	c, err := NewCatalog([]protocol.ScriptGrant{g}, guard)
	if err != nil {
		t.Fatal(err)
	}
	list, _ := c.List()
	digest := list.Scripts[0].GrantDigest
	p, err := c.Prepare(g.ScriptID, digest, map[string]json.RawMessage{"day": json.RawMessage(`12`), "category": json.RawMessage(`"女包 & 配件"`)})
	if err != nil {
		t.Fatal(err)
	}
	if len(p.Arguments) != 4 || p.Arguments[3] != "女包 & 配件" || len(p.Script) == 0 {
		t.Fatal("business input was not preserved as individual argv values")
	}
	minimal, err := c.Prepare(g.ScriptID, digest, map[string]json.RawMessage{"day": json.RawMessage(`12`)})
	if err != nil || len(minimal.Arguments) != 2 || minimal.Arguments[0] != "--day" {
		t.Fatal("omitted optional value left an unbound argument flag")
	}
	if minimal.ParametersDigest == p.ParametersDigest {
		t.Fatal("different input retained the same preparation digest")
	}
	if _, err := c.Prepare(g.ScriptID, strings.Repeat("0", 64), map[string]json.RawMessage{"day": json.RawMessage(`12`)}); err == nil {
		t.Fatal("wrong grant digest accepted")
	}
	for _, values := range []map[string]json.RawMessage{
		{}, {"day": json.RawMessage(`true`)}, {"day": json.RawMessage(`1.5`)}, {"day": json.RawMessage(`32`)},
		{"day": json.RawMessage(`12`), "command": json.RawMessage(`"anything"`)},
		{"day": json.RawMessage(`12`), "category": json.RawMessage(`"line\nbreak"`)},
	} {
		if _, err := c.Prepare(g.ScriptID, digest, values); err == nil {
			t.Fatalf("invalid input accepted: %#v", values)
		}
	}
	if _, err := c.ValidateResult(g.ScriptID, digest, []byte(`{"count":12}`)); err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{`{"count":true}`, `{"count":12,"count":1}`, `{"count":12,"secret":"hidden"}`, `{"count":12} trailing`, `[]`} {
		if _, err := c.ValidateResult(g.ScriptID, digest, []byte(raw)); err == nil {
			t.Fatalf("invalid result accepted: %s", raw)
		}
	}
}

func TestApprovalCannotEscapeSandboxOrReuseChangedRuntime(t *testing.T) {
	g, guard := fixture(t)
	outside := filepath.Join(t.TempDir(), "elsewhere.py")
	os.WriteFile(outside, []byte("outside"), 0600)
	bad := g
	bad.ScriptPath = outside
	bad.ContentSHA256 = hashBytes([]byte("outside"))
	c, err := NewCatalog([]protocol.ScriptGrant{bad}, guard)
	if err == nil {
		_, err = c.List()
	}
	if err == nil {
		t.Fatal("script outside sandbox accepted")
	}
	c, _ = NewCatalog([]protocol.ScriptGrant{g}, guard)
	listed, _ := c.List()
	os.WriteFile(g.Interpreter, []byte("runtime replaced"), 0600)
	if _, err := c.Prepare(g.ScriptID, listed.Scripts[0].GrantDigest, map[string]json.RawMessage{"day": json.RawMessage(`1`)}); err == nil {
		t.Fatal("changed runtime accepted")
	}
}

func TestCatalogOwnsDefinitionsAndPreparedScriptBytes(t *testing.T) {
	g, guard := fixture(t)
	c, err := NewCatalog([]protocol.ScriptGrant{g}, guard)
	if err != nil {
		t.Fatal(err)
	}
	first, err := c.List()
	if err != nil {
		t.Fatal(err)
	}
	digest := first.Scripts[0].GrantDigest
	*g.Parameters[0].Maximum = 999
	g.Parameters[1].Name = "modified_by_caller"
	*first.Scripts[0].Parameters[0].Minimum = -999
	first.Scripts[0].ResultFields[0].Name = "modified_response"
	second, err := c.List()
	if err != nil || second.Scripts[0].GrantDigest != digest || *second.Scripts[0].Parameters[0].Minimum != 1 || *second.Scripts[0].Parameters[0].Maximum != 31 || second.Scripts[0].ResultFields[0].Name != "count" {
		t.Fatal("mutable caller or response changed the reviewed definition")
	}
	prepared, err := c.Prepare(g.ScriptID, digest, map[string]json.RawMessage{"day": json.RawMessage(`12`)})
	if err != nil {
		t.Fatal(err)
	}
	approvedBytes := string(prepared.Script)
	if err := os.WriteFile(g.ScriptPath, []byte("replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	if string(prepared.Script) != approvedBytes {
		t.Fatal("prepared plan retained a mutable file reference")
	}
	if _, err := c.Prepare(g.ScriptID, digest, map[string]json.RawMessage{"day": json.RawMessage(`12`)}); err == nil {
		t.Fatal("changed source entered a new preparation")
	}
}
