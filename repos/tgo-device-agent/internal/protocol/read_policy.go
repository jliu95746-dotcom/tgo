package protocol

import (
	"crypto/sha256"
	"encoding/hex"
	"strconv"
)

// ReadPolicy is a narrow, read-only business scope sent after device auth.
// The device must intersect it with its own sandbox before acknowledging it.
type ReadPolicy struct {
	Revision         int    `json:"revision"`
	ToolName         string `json:"tool_name"`
	AllowedDirectory string `json:"allowed_directory"`
	MaxLines         int    `json:"max_lines"`
	MaxOffset        int    `json:"max_offset"`
	Digest           string `json:"digest"`
}

type ReadPolicyAck struct {
	Revision int    `json:"revision"`
	Digest   string `json:"digest"`
}

// ReadPolicyDigest is shared with the Python service's canonical digest rule.
func ReadPolicyDigest(revision int, toolName, directory string, maxLines, maxOffset int) string {
	payload := "tgo-read-policy-v1\n" + strconv.Itoa(revision) + "\n" + toolName + "\n" + directory + "\n" + strconv.Itoa(maxLines) + "\n" + strconv.Itoa(maxOffset) + "\n"
	sum := sha256.Sum256([]byte(payload))
	return hex.EncodeToString(sum[:])
}
