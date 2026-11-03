package protocol

import "testing"

func TestScreenPolicyCanonicalVectorMatchesPython(t *testing.T) {
	policy := ScreenPolicy{ProtocolVersion: 1, Revision: 1, GrantID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", MaxActions: 5}
	if ScreenPolicyDigest(policy) != "3e1772db3e2809fb5dddd5b25fa4f4bf3051622c3059bdc707d506b40a1826d2" {
		t.Fatal("screen digest changed from the cross-language contract")
	}
}
