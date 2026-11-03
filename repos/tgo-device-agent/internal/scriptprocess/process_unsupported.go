//go:build (!windows && !linux && !darwin && !freebsd && !openbsd && !netbsd && !dragonfly) || (windows && !amd64 && !arm64)

package scriptprocess

import (
	"errors"
	"os"
)

func startProcess(Request, []string, *os.File, *os.File, *os.File) (ownedProcess, error) {
	return nil, errors.New("bounded script processes are unavailable on this platform")
}
