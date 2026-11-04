package protocol

import (
	"crypto/sha256"
	"encoding/hex"
	"strconv"
	"strings"
)

type ScreenRegion struct {
	X      int `json:"x"`
	Y      int `json:"y"`
	Width  int `json:"width"`
	Height int `json:"height"`
}
type ScreenGrantStatus struct {
	ProtocolVersion int            `json:"protocol_version"`
	Active          bool           `json:"active"`
	GrantID         string         `json:"grant_id,omitempty"`
	Width           int            `json:"width,omitempty"`
	Height          int            `json:"height,omitempty"`
	DPI             int            `json:"dpi,omitempty"`
	AllowClick      bool           `json:"allow_click"`
	AllowText       bool           `json:"allow_text"`
	ClickRegions    []ScreenRegion `json:"click_regions,omitempty"`
	MaxActions      int            `json:"max_actions,omitempty"`
	UsedActions     int            `json:"used_actions"`
}
type ScreenPolicy struct {
	ProtocolVersion int            `json:"protocol_version"`
	Revision        int            `json:"revision"`
	GrantID         string         `json:"grant_id"`
	MaxActions      int            `json:"max_actions"`
	AllowClick      bool           `json:"allow_click"`
	AllowText       bool           `json:"allow_text"`
	ClickRegions    []ScreenRegion `json:"click_regions"`
	Digest          string         `json:"digest"`
}
type ScreenPolicyAck struct {
	ProtocolVersion int    `json:"protocol_version"`
	Revision        int    `json:"revision"`
	GrantID         string `json:"grant_id"`
	Digest          string `json:"digest"`
}

func ScreenPolicyDigest(policy ScreenPolicy) string {
	lines := []string{"tgo-screen-policy-v1", strconv.Itoa(policy.Revision), policy.GrantID,
		strconv.Itoa(policy.MaxActions), strconv.FormatBool(policy.AllowClick), strconv.FormatBool(policy.AllowText), strconv.Itoa(len(policy.ClickRegions))}
	for _, region := range policy.ClickRegions {
		lines = append(lines, strconv.Itoa(region.X)+","+strconv.Itoa(region.Y)+","+strconv.Itoa(region.Width)+","+strconv.Itoa(region.Height))
	}
	digest := sha256.Sum256([]byte(strings.Join(lines, "\n") + "\n"))
	return hex.EncodeToString(digest[:])
}
