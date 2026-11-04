//go:build linux || darwin || freebsd || openbsd || netbsd || dragonfly

package approvedscript

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"syscall"
)

func lockJournal(path string) (io.Closer, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		file.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
			return nil, errJournalBusy
		}
		return nil, err
	}
	return file, nil
}

func replaceJournal(temporary, target string) error {
	if err := os.Rename(temporary, target); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(target))
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}
