package protocol

import "testing"

// This vector is also produced by tgo-device-control's Python digest function.
func TestReadPolicyDigestMatchesService(t *testing.T) {
	const expected = "1695ab62a403ca4c1ff56c1b291b0ad6cccb5a3ae97b9f5c0e768748e32f52bf"
	if actual := ReadPolicyDigest(2, "fs_read", "/srv/orders", 100, 1000); actual != expected {
		t.Fatalf("read policy digest mismatch: %s", actual)
	}
}
