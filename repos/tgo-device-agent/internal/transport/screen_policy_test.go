package transport

import (
	"encoding/json"
	"testing"
)

func TestScreenPolicyWireDoesNotDefaultMissingOrNullPermissions(t *testing.T) {
	for _, raw := range []string{`null`, `{}`, `{"protocol_version":1,"revision":1,"grant_id":"a","max_actions":1,"digest":"d"}`, `{"protocol_version":1,"revision":1,"grant_id":"a","max_actions":1,"digest":"d","allow_click":null,"allow_text":false}`, `{"protocol_version":1,"revision":1,"grant_id":"a","max_actions":1,"digest":"d","allow_click":true,"allow_text":false,"click_regions":[{"width":1,"height":1}]}`} {
		if completeScreenPolicyParams(json.RawMessage(raw)) {
			t.Fatal("missing/null permission shape accepted")
		}
	}
	if !completeScreenPolicyParams(json.RawMessage(`{"protocol_version":1,"revision":1,"grant_id":"a","max_actions":1,"digest":"d","allow_click":false,"allow_text":false,"click_regions":[]}`)) {
		t.Fatal("explicit permission fields rejected")
	}
}
