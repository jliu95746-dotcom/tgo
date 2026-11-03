package protocol

import (
	"strings"
	"testing"
)

func TestScriptPolicyBindsExactGrantAndExecutionLimits(t *testing.T) {
	p := ScriptPolicy{ProtocolVersion: 1, Revision: 3, ScriptID: "daily_report", GrantDigest: strings.Repeat("a", 64), TimeoutSeconds: 20, MaxOutputBytes: 4096, MaxRuns: 2}
	p.Digest = ScriptPolicyDigest(p)
	if err := p.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*ScriptPolicy){
		func(v *ScriptPolicy) { v.Revision++ },
		func(v *ScriptPolicy) { v.ScriptID = "other_report" },
		func(v *ScriptPolicy) { v.GrantDigest = strings.Repeat("b", 64) },
		func(v *ScriptPolicy) { v.TimeoutSeconds++ },
		func(v *ScriptPolicy) { v.MaxOutputBytes++ },
		func(v *ScriptPolicy) { v.MaxRuns++ },
	} {
		copy := p
		change(&copy)
		if copy.Validate() == nil {
			t.Fatal("changed scope retained the old policy digest")
		}
	}
	if p.Digest != "c67ba11a5189a797b80e7e00fc943219c91bd270aba8929db2227b4d388fe057" {
		t.Fatalf("unexpected contract vector: %s", p.Digest)
	}
}

func TestScriptParameterDefinitionsAreTypedAndBounded(t *testing.T) {
	lo, hi := int64(1), int64(31)
	valid := []ScriptParameter{
		{Name: "month", Kind: "integer", Required: true, Minimum: &lo, Maximum: &hi},
		{Name: "category", Kind: "string", MaxLength: 20, Choices: []string{"女包", "配件"}},
		{Name: "include_details", Kind: "boolean"},
	}
	for _, p := range valid {
		if err := p.Validate(); err != nil {
			t.Fatal(err)
		}
	}
	for _, p := range []ScriptParameter{
		{Name: "command", Kind: "object"}, {Name: "bad\nname", Kind: "boolean"},
		{Name: "count", Kind: "integer"}, {Name: "text", Kind: "string"},
		{Name: "text", Kind: "string", MaxLength: 1, Choices: []string{"过长"}},
		{Name: "text", Kind: "string", MaxLength: 10, Choices: []string{"重复", "重复"}},
		{Name: "flag", Kind: "boolean", Maximum: &hi},
	} {
		if p.Validate() == nil {
			t.Fatalf("invalid parameter admitted: %#v", p)
		}
	}
}
