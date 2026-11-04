package config

import (
	"fmt"
	"path/filepath"
	"strings"
)

func (c *Config) ScriptStateRoot() (string, error) {
	root := c.ScriptStateDirectory
	if root == "" {
		token, err := filepath.Abs(c.TokenFile)
		if err != nil {
			return "", err
		}
		root = filepath.Join(filepath.Dir(token), "script-state")
	}
	if !filepath.IsAbs(root) || strings.ContainsRune(root, 0) {
		return "", fmt.Errorf("script state directory must be a dedicated absolute local path")
	}
	root = filepath.Clean(root)
	work, err := filepath.Abs(c.WorkRoot)
	if err != nil {
		return "", err
	}
	relative, err := filepath.Rel(root, work)
	if filepath.Dir(root) == root || err == nil && (relative == "." || relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))) {
		return "", fmt.Errorf("script state directory must not contain the business workspace")
	}
	return root, nil
}
