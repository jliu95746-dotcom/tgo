//go:build windows

package approvedscript

import (
	"errors"
	"io"
	"os"
	"syscall"
	"unsafe"
)

func lockJournal(path string) (io.Closer, error) {
	name, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return nil, err
	}
	handle, err := syscall.CreateFile(name, syscall.GENERIC_READ|syscall.GENERIC_WRITE, 0, nil, syscall.OPEN_ALWAYS, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if errors.Is(err, syscall.Errno(32)) {
		return nil, errJournalBusy
	}
	if err != nil {
		return nil, err
	}
	return os.NewFile(uintptr(handle), path), nil
}

func replaceJournal(temporary, target string) error {
	oldPath, err := syscall.UTF16PtrFromString(temporary)
	if err != nil {
		return err
	}
	newPath, err := syscall.UTF16PtrFromString(target)
	if err != nil {
		return err
	}
	result, _, callErr := syscall.NewLazyDLL("kernel32.dll").NewProc("MoveFileExW").Call(uintptr(unsafe.Pointer(oldPath)), uintptr(unsafe.Pointer(newPath)), 0x1|0x8)
	if result == 0 {
		return callErr
	}
	return nil
}
