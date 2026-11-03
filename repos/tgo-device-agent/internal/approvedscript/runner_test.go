package approvedscript

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/protocol"
	"github.com/tgoai/tgo-device-agent/internal/sandbox"
	"github.com/tgoai/tgo-device-agent/internal/scriptprocess"
)

func runnerFixture(t *testing.T, runner, body string) (*Catalog, protocol.ScriptGrant, string) {
	t.Helper()
	variable := map[string]string{"python": "TGO_SCRIPT_TEST_PYTHON", "node": "TGO_SCRIPT_TEST_NODE", "powershell": "TGO_SCRIPT_TEST_POWERSHELL"}[runner]
	program := os.Getenv(variable)
	if program == "" {
		t.Skip("explicit local runtime required: " + variable)
	}
	program, err := filepath.Abs(program)
	if err != nil {
		t.Fatal(err)
	}
	runtime, err := os.ReadFile(program)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	extension := map[string]string{"python": ".py", "node": ".js", "powershell": ".ps1"}[runner]
	script := filepath.Join(root, "report"+extension)
	if err := os.WriteFile(script, []byte(body), 0600); err != nil {
		t.Fatal(err)
	}
	lo, hi := int64(1), int64(31)
	g := protocol.ScriptGrant{Approved: true, ScriptID: "daily_report", Title: "每日报表", Version: "v1", Runner: runner, ScriptPath: script, ContentSHA256: hashBytes([]byte(body)), Interpreter: program, InterpreterSHA256: hashBytes(runtime), WorkingDirectory: root, Effect: "read_only", TimeoutSeconds: 10, MaxOutputBytes: 4096, MaxRuns: 2,
		Parameters:   []protocol.ScriptParameter{{Name: "day", Kind: "integer", Required: true, Minimum: &lo, Maximum: &hi}, {Name: "include", Kind: "boolean", Required: true}, {Name: "label", Kind: "string", MaxLength: 256}},
		Arguments:    []protocol.ScriptArgument{{Parameter: "day", Flag: "--day"}, {Parameter: "include", Flag: "--include"}, {Parameter: "label", Flag: "--label"}},
		ResultFields: []protocol.ScriptParameter{{Name: "day", Kind: "integer", Required: true, Minimum: &lo, Maximum: &hi}, {Name: "include", Kind: "boolean", Required: true}, {Name: "label", Kind: "string", MaxLength: 256}},
	}
	cfg := config.DefaultConfig()
	cfg.WorkRoot = root
	c, err := NewCatalog([]protocol.ScriptGrant{g}, sandbox.New(cfg))
	if err != nil {
		t.Fatal(err)
	}
	list, err := c.List()
	if err != nil {
		t.Fatal(err)
	}
	return c, g, list.Scripts[0].GrantDigest
}

func TestReviewedScriptRunsWithEachRealRuntime(t *testing.T) {
	bodies := map[string]string{
		"python":     "import sys,json\na=sys.argv[1:]\nprint(json.dumps({'day':int(a[1]),'include':a[3]=='true','label':a[5]}))\n",
		"node":       "const a=process.argv.slice(2);console.log(JSON.stringify({day:Number(a[1]),include:a[3]==='true',label:a[5]}));",
		"powershell": "param([int]$day,[bool]$include,[string]$label)\n@{day=$day;include=$include;label=$label}|ConvertTo-Json -Compress\n",
	}
	for runner, body := range bodies {
		t.Run(runner, func(t *testing.T) {
			c, g, digest := runnerFixture(t, runner, body)
			label := "女包 & 'quoted'; $(no-command) \\\""
			encoded, _ := json.Marshal(label)
			result := c.evaluateReadOnly(context.Background(), g.ScriptID, digest, map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`), "label": encoded}, executionLimits{TimeoutSeconds: 10, MaxOutputBytes: 4096})
			if result.Outcome != scriptprocess.Succeeded || string(result.Fields["day"]) != "12" || string(result.Fields["include"]) != "false" {
				t.Fatalf("run: %#v", result)
			}
			if len(result.ParametersDigest) != 64 {
				t.Fatal("result missing the exact input binding")
			}
			var returned string
			if json.Unmarshal(result.Fields["label"], &returned) != nil || returned != label {
				t.Fatalf("business text was evaluated or corrupted: %q", returned)
			}
		})
	}
}

func TestScriptFailureCannotReturnUnvalidatedOrPartialBusinessResult(t *testing.T) {
	for name, body := range map[string]string{"exit": "import sys\nprint('{\"day\":12,\"include\":false}')\nsys.exit(7)", "extra": "print('{\"day\":12,\"include\":false,\"private\":\"hidden\"}')", "budget": "print('x'*10000)", "timeout": "import time\ntime.sleep(30)"} {
		t.Run(name, func(t *testing.T) {
			c, g, digest := runnerFixture(t, "python", body)
			seconds := 10
			if name == "timeout" {
				seconds = 1
			}
			result := c.evaluateReadOnly(context.Background(), g.ScriptID, digest, map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}, executionLimits{TimeoutSeconds: seconds, MaxOutputBytes: 256})
			want := map[string]scriptprocess.Outcome{"exit": scriptprocess.Failed, "extra": scriptprocess.Failed, "budget": scriptprocess.OutputExceeded, "timeout": scriptprocess.TimedOut}[name]
			if result.Outcome != want || len(result.Fields) != 0 || strings.Contains(result.Failure, "hidden") {
				t.Fatalf("unverified result leaked: %#v", result)
			}
		})
	}
}

func TestScriptWriteEffectsAndChangedVersionsDoNotStart(t *testing.T) {
	c, g, digest := runnerFixture(t, "python", "raise Exception('should never start')")
	params := map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}
	for _, limits := range []executionLimits{{TimeoutSeconds: 11, MaxOutputBytes: 4096}, {TimeoutSeconds: 1, MaxOutputBytes: 4097}} {
		if result := c.evaluateReadOnly(context.Background(), g.ScriptID, digest, params, limits); result.Outcome != scriptprocess.Rejected {
			t.Fatalf("expanded limits: %#v", result)
		}
	}
	if result := c.evaluateReadOnly(context.Background(), g.ScriptID, strings.Repeat("0", 64), params, executionLimits{1, 4096}); result.Outcome != scriptprocess.Rejected {
		t.Fatal("changed definition started")
	}
	g.Effect = "write"
	c.grants[g.ScriptID] = g
	if result := c.evaluateReadOnly(context.Background(), g.ScriptID, descriptor(g).GrantDigest, params, executionLimits{1, 4096}); result.Failure != "approval_required" {
		t.Fatalf("write was not gated: %#v", result)
	}
}

func TestNormalizedResultCannotExceedTheExecutionBudget(t *testing.T) {
	c, g, digest := runnerFixture(t, "python", "import json\nprint(json.dumps({'day':12,'include':False,'label':'&'*100}))\n")
	result := c.evaluateReadOnly(context.Background(), g.ScriptID, digest, map[string]json.RawMessage{"day": json.RawMessage(`12`), "include": json.RawMessage(`false`)}, executionLimits{TimeoutSeconds: 10, MaxOutputBytes: 256})
	if result.Outcome != scriptprocess.OutputExceeded || len(result.Fields) != 0 {
		t.Fatalf("normalized output evaded budget: %#v", result)
	}
}
