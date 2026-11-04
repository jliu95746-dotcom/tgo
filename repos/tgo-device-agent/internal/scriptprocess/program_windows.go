//go:build windows

package scriptprocess

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

type programHold struct{ files []*os.File }

func (h *programHold) Close() error {
	var result error
	for i := len(h.files) - 1; i >= 0; i-- {
		result = errors.Join(result, h.files[i].Close())
	}
	h.files = nil
	return result
}

// Keep the reviewed file non-writable/non-renamable and its path components
// non-rebindable until CreateProcess has mapped the executable image. These
// handles are not inherited. Imported DLLs/dependencies are a separate scope.
func holdReviewedProgram(request Request) (io.Closer, error) {
	hold := &programHold{}
	if request.ProgramSHA256 == "" {
		return hold, nil
	}
	expected, err := hex.DecodeString(request.ProgramSHA256)
	if err != nil || len(expected) != sha256.Size {
		return nil, fmt.Errorf("invalid reviewed executable digest")
	}
	open := func(path string, directory bool) (*os.File, error) {
		name, err := syscall.UTF16PtrFromString(path)
		if err != nil {
			return nil, err
		}
		access, share, flags := uint32(syscall.GENERIC_READ), uint32(syscall.FILE_SHARE_READ), uint32(syscall.FILE_FLAG_OPEN_REPARSE_POINT)
		if directory {
			// Metadata-only access does not participate in Windows share-access
			// accounting, so it cannot prevent a directory rename.
			access = syscall.GENERIC_READ
			share = syscall.FILE_SHARE_READ | syscall.FILE_SHARE_WRITE
			flags |= syscall.FILE_FLAG_BACKUP_SEMANTICS
		}
		handle, err := syscall.CreateFile(name, access, share, nil, syscall.OPEN_EXISTING, flags, 0)
		if err != nil {
			return nil, err
		}
		file := os.NewFile(uintptr(handle), path)
		hold.files = append(hold.files, file)
		var info syscall.ByHandleFileInformation
		if err := syscall.GetFileInformationByHandle(handle, &info); err != nil {
			return nil, err
		}
		if info.FileAttributes&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0 || (info.FileAttributes&syscall.FILE_ATTRIBUTE_DIRECTORY != 0) != directory {
			return nil, fmt.Errorf("reviewed executable path contains a reparse point or changed object")
		}
		return file, nil
	}
	// Top-down locking prevents an ancestor being replaced between locking a
	// child directory and later resolving the executable by its absolute path.
	heldDirectories := map[string]bool{}
	lockDirectories := func(path string) error {
		if !filepath.IsAbs(path) {
			return fmt.Errorf("reviewed working directory is not absolute")
		}
		parents := []string{}
		for directory := path; directory != filepath.Dir(directory); directory = filepath.Dir(directory) {
			parents = append(parents, directory)
		}
		for index := len(parents) - 1; index >= 0; index-- {
			key := strings.ToLower(parents[index])
			if heldDirectories[key] {
				continue
			}
			if _, err := open(parents[index], true); err != nil {
				return err
			}
			heldDirectories[key] = true
		}
		return nil
	}
	if err := lockDirectories(filepath.Dir(request.Program)); err != nil {
		hold.Close()
		return nil, err
	}
	if err := lockDirectories(request.Directory); err != nil {
		hold.Close()
		return nil, err
	}
	file, err := open(request.Program, false)
	if err != nil {
		hold.Close()
		return nil, err
	}
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 256*1024*1024 {
		hold.Close()
		return nil, fmt.Errorf("reviewed executable is not a bounded regular file")
	}
	hash := sha256.New()
	size, err := io.Copy(hash, io.LimitReader(file, 256*1024*1024+1))
	if err != nil || size > 256*1024*1024 || hex.EncodeToString(hash.Sum(nil)) != request.ProgramSHA256 {
		hold.Close()
		return nil, fmt.Errorf("reviewed executable contents changed at startup")
	}
	return hold, nil
}
