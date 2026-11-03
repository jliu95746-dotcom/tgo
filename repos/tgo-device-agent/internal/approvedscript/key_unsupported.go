//go:build !windows

package approvedscript

func localKeysSupported() bool                         { return false }
func protectLocalKey([]byte, []byte) ([]byte, error)   { return nil, errJournalUnavailable }
func unprotectLocalKey([]byte, []byte) ([]byte, error) { return nil, errJournalUnavailable }
