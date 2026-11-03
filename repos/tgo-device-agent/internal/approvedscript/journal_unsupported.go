//go:build !windows && !linux && !darwin && !freebsd && !openbsd && !netbsd && !dragonfly

package approvedscript

import (
	"errors"
	"io"
)

func lockJournal(string) (io.Closer, error) {
	return nil, errors.New("durable execution locks are unavailable on this platform")
}
func replaceJournal(string, string) error {
	return errors.New("durable execution replacement is unavailable on this platform")
}
