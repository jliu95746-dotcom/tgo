package approvedscript

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync/atomic"
	"time"
)

const journalMagic = "TGOSJ1\n"

type executionJournal struct {
	root          string
	scope         journalScope
	guard         PathGuard
	cipher        cipher.AEAD
	writeOverride func([]byte) error // Local I/O seam, never configurable over the wire.
	stateRequired atomic.Bool
}

func newJournal(root string, scope journalScope, key []byte, guard PathGuard) (*executionJournal, error) {
	return openJournal(root, scope, key, guard, false)
}

func openJournal(root string, scope journalScope, key []byte, guard PathGuard, requireExisting bool) (*executionJournal, error) {
	if guard == nil || len(key) != 32 || !filepath.IsAbs(root) || !journalUUID.MatchString(scope.ProjectID) || !journalUUID.MatchString(scope.DeviceID) {
		return nil, errJournalUnavailable
	}
	resolved, err := guard.ResolvePath(root)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", errJournalUnavailable, err)
	}
	info, err := os.Stat(resolved)
	if err != nil || !info.IsDir() {
		return nil, errJournalUnavailable
	}
	block, err := aes.NewCipher(append([]byte(nil), key...))
	if err != nil {
		return nil, errJournalUnavailable
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, errJournalUnavailable
	}
	j := &executionJournal{root: resolved, scope: scope, guard: guard, cipher: aead}
	j.stateRequired.Store(requireExisting)
	if err := j.withState(context.Background(), func(*journalState) (bool, error) { return false, nil }); err != nil {
		return nil, err
	}
	return j, nil
}

func (j *executionJournal) path(name string) (string, error) {
	expected := filepath.Join(j.root, name)
	resolved, err := j.guard.ResolvePath(expected)
	if err != nil || resolved != expected {
		return "", errJournalUnavailable
	}
	return resolved, nil
}

func (j *executionJournal) aad() []byte {
	return []byte("tgo-script-journal-v1\n" + j.scope.ProjectID + "\n" + j.scope.DeviceID + "\n")
}

func (j *executionJournal) load() (*journalState, error) {
	path, err := j.path("executions.bin")
	if err != nil {
		return nil, err
	}
	file, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		if j.stateRequired.Load() {
			return nil, errJournalUnavailable
		}
		return &journalState{Version: 1, Scope: j.scope, Policies: map[string]journalPolicy{}, Entries: map[string]journalEntry{}}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("%w: %v", errJournalUnavailable, err)
	}
	j.stateRequired.Store(true)
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > maxJournalPlaintext+1024 {
		return nil, errJournalUnavailable
	}
	data, err := io.ReadAll(io.LimitReader(file, maxJournalPlaintext+1025))
	if err != nil || len(data) > maxJournalPlaintext+1024 || !bytes.HasPrefix(data, []byte(journalMagic)) {
		return nil, errJournalUnavailable
	}
	data = data[len(journalMagic):]
	nonceSize := j.cipher.NonceSize()
	if len(data) < nonceSize+j.cipher.Overhead() {
		return nil, errJournalUnavailable
	}
	plain, err := j.cipher.Open(nil, data[:nonceSize], data[nonceSize:], j.aad())
	if err != nil {
		return nil, errJournalUnavailable
	}
	var state journalState
	decoder := json.NewDecoder(bytes.NewReader(plain))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&state) != nil || !state.valid(j.scope) {
		return nil, errJournalUnavailable
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, errJournalUnavailable
	}
	return &state, nil
}

func (j *executionJournal) save(state *journalState) error {
	if !state.valid(j.scope) {
		return errJournalUnavailable
	}
	plain, err := json.Marshal(state)
	if err != nil || len(plain) > maxJournalPlaintext {
		return errJournalUnavailable
	}
	nonce := make([]byte, j.cipher.NonceSize())
	if _, err := io.ReadFull(rand.Reader, nonce); err != nil {
		return errJournalUnavailable
	}
	data := append([]byte(journalMagic), nonce...)
	data = j.cipher.Seal(data, nonce, plain, j.aad())
	// A failed/uncertain first save must never turn a later missing file into
	// permission to start again with a blank history.
	j.stateRequired.Store(true)
	if j.writeOverride != nil {
		return j.writeOverride(data)
	}
	return j.atomicSave(data)
}

func (j *executionJournal) atomicSave(data []byte) error {
	path, err := j.path("executions.bin")
	if err != nil {
		return err
	}
	file, err := os.CreateTemp(j.root, ".execution-*.tmp")
	if err != nil {
		return fmt.Errorf("%w: %v", errJournalUnavailable, err)
	}
	temporary := file.Name()
	defer os.Remove(temporary)
	defer file.Close()
	if _, err := j.guard.ResolvePath(temporary); err != nil {
		return errJournalUnavailable
	}
	if _, err := file.Write(data); err != nil {
		return fmt.Errorf("%w: %v", errJournalUnavailable, err)
	}
	if err := file.Sync(); err != nil {
		return fmt.Errorf("%w: %v", errJournalUnavailable, err)
	}
	if err := file.Close(); err != nil {
		return fmt.Errorf("%w: %v", errJournalUnavailable, err)
	}
	if err := replaceJournal(temporary, path); err != nil {
		return fmt.Errorf("%w: %v", errJournalUnavailable, err)
	}
	return nil
}

func (j *executionJournal) withState(parent context.Context, action func(*journalState) (bool, error)) (returned error) {
	if parent == nil {
		return errJournalUnavailable
	}
	ctx, cancel := context.WithTimeout(parent, 5*time.Second)
	defer cancel()
	path, err := j.path("executions.lock")
	if err != nil {
		return err
	}
	var lock io.Closer
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		lock, err = lockJournal(path)
		if err == nil {
			break
		}
		if !errors.Is(err, errJournalBusy) {
			return fmt.Errorf("%w: %v", errJournalUnavailable, err)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(20 * time.Millisecond):
		}
	}
	defer func() {
		if err := lock.Close(); err != nil {
			returned = errors.Join(returned, fmt.Errorf("%w: %v", errJournalUnavailable, err))
		}
	}()
	state, err := j.load()
	if err != nil {
		return err
	}
	changed, err := action(state)
	if err != nil {
		return err
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if changed {
		if err := j.save(state); err != nil {
			return err
		}
		return ctx.Err()
	}
	return nil
}
