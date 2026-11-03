package approvedscript

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"time"
)

const privateKeyMagic = "tgo-private-script-key-v1\n"

type privateIdentity struct {
	Version      int          `json:"version"`
	Owner        journalScope `json:"owner"`
	ProtectedKey []byte       `json:"protected_key"`
}

func openPrivateJournal(parent context.Context, root string, owner journalScope, guard PathGuard) (*executionJournal, error) {
	if parent == nil || guard == nil || !localKeysSupported() || !filepath.IsAbs(root) || !journalUUID.MatchString(owner.ProjectID) || !journalUUID.MatchString(owner.DeviceID) {
		return nil, errJournalUnavailable
	}
	resolved, err := guard.ResolvePath(root)
	if err != nil || resolved != root {
		return nil, errJournalUnavailable
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, errJournalUnavailable
	}
	ctx, cancel := context.WithTimeout(parent, 5*time.Second)
	defer cancel()
	lockPath, err := guard.ResolvePath(filepath.Join(root, "bootstrap.lock"))
	if err != nil || lockPath != filepath.Join(root, "bootstrap.lock") {
		return nil, errJournalUnavailable
	}
	var lock io.Closer
	for {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		lock, err = lockJournal(lockPath)
		if err == nil {
			break
		}
		if !errors.Is(err, errJournalBusy) {
			return nil, errJournalUnavailable
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(20 * time.Millisecond):
		}
	}
	defer lock.Close()
	entropy := []byte("tgo-device-script-identity-v1\n" + owner.ProjectID + "\n" + owner.DeviceID + "\n")
	identityPath, err := guard.ResolvePath(filepath.Join(root, "identity.json"))
	if err != nil || identityPath != filepath.Join(root, "identity.json") {
		return nil, errJournalUnavailable
	}
	var identity privateIdentity
	created := false
	readIdentity := func() ([]byte, error) {
		file, err := os.Open(identityPath)
		if err != nil {
			return nil, err
		}
		defer file.Close()
		info, err := file.Stat()
		if err != nil || !info.Mode().IsRegular() || info.Size() > 49152 {
			return nil, errJournalUnavailable
		}
		return io.ReadAll(io.LimitReader(file, 49153))
	}
	data, err := readIdentity()
	if errors.Is(err, os.ErrNotExist) {
		entries, err := os.ReadDir(root)
		if err != nil {
			return nil, errJournalUnavailable
		}
		for _, entry := range entries {
			if entry.Name() != "bootstrap.lock" {
				return nil, errJournalUnavailable
			}
		}
		key := make([]byte, 32)
		if _, err := rand.Read(key); err != nil {
			return nil, errJournalUnavailable
		}
		defer clear(key)
		payload := append([]byte(privateKeyMagic), key...)
		checksum := sha256.Sum256(append(append([]byte(nil), entropy...), key...))
		payload = append(payload, checksum[:]...)
		wrapped, err := protectLocalKey(payload, entropy)
		clear(payload)
		if err != nil {
			return nil, err
		}
		identity = privateIdentity{Version: 1, Owner: owner, ProtectedKey: wrapped}
		data, err = json.Marshal(identity)
		if err != nil {
			return nil, errJournalUnavailable
		}
		file, err := os.CreateTemp(root, ".identity-*.tmp")
		if err != nil {
			return nil, errJournalUnavailable
		}
		temporary := file.Name()
		defer os.Remove(temporary)
		defer file.Close()
		if _, err := guard.ResolvePath(temporary); err != nil {
			return nil, errJournalUnavailable
		}
		if _, err := file.Write(data); err != nil {
			return nil, errJournalUnavailable
		}
		if file.Sync() != nil || file.Close() != nil {
			return nil, errJournalUnavailable
		}
		if replaceJournal(temporary, identityPath) != nil {
			return nil, errJournalUnavailable
		}
		created = true
	} else if err != nil {
		return nil, errJournalUnavailable
	}
	if len(data) > 49152 {
		return nil, errJournalUnavailable
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&identity) != nil || identity.Version != 1 || identity.Owner != owner || len(identity.ProtectedKey) == 0 || len(identity.ProtectedKey) > 32768 {
		return nil, errJournalUnavailable
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, errJournalUnavailable
	}
	payload, err := unprotectLocalKey(identity.ProtectedKey, entropy)
	if err != nil {
		return nil, err
	}
	defer clear(payload)
	if len(payload) != len(privateKeyMagic)+64 || !bytes.HasPrefix(payload, []byte(privateKeyMagic)) {
		return nil, errJournalUnavailable
	}
	key := payload[len(privateKeyMagic) : len(privateKeyMagic)+32]
	checksum := sha256.Sum256(append(append([]byte(nil), entropy...), key...))
	if subtle.ConstantTimeCompare(checksum[:], payload[len(privateKeyMagic)+32:]) != 1 {
		return nil, errJournalUnavailable
	}
	if ctx.Err() != nil {
		return nil, ctx.Err()
	}
	if !created {
		path, err := guard.ResolvePath(filepath.Join(root, "executions.bin"))
		if err != nil || path != filepath.Join(root, "executions.bin") {
			return nil, errJournalUnavailable
		}
		if _, err := os.Stat(path); err != nil {
			return nil, errJournalUnavailable
		}
	}
	journal, err := openJournal(root, owner, key, guard, !created)
	if err != nil {
		return nil, err
	}
	if created {
		if err := journal.withState(ctx, func(*journalState) (bool, error) { return true, nil }); err != nil {
			return nil, err
		}
	}
	return journal, nil
}
