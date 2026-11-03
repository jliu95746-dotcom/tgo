//go:build windows

package approvedscript

import (
	"fmt"
	"runtime"
	"syscall"
	"unsafe"
)

type keyBlob struct {
	Size uint32
	Data *byte
}

func localKeysSupported() bool { return true }
func protectLocalKey(plain, entropy []byte) ([]byte, error) {
	return cryptLocalKey("CryptProtectData", plain, entropy)
}
func unprotectLocalKey(wrapped, entropy []byte) ([]byte, error) {
	return cryptLocalKey("CryptUnprotectData", wrapped, entropy)
}

func cryptLocalKey(method string, data, entropy []byte) ([]byte, error) {
	if len(data) == 0 || len(data) > 32768 || len(entropy) == 0 || len(entropy) > 1024 {
		return nil, errJournalUnavailable
	}
	input := keyBlob{uint32(len(data)), &data[0]}
	purpose := keyBlob{uint32(len(entropy)), &entropy[0]}
	var output keyBlob
	// Current-user DPAPI; no machine-wide key and no interactive prompt.
	value, _, err := syscall.NewLazyDLL("crypt32.dll").NewProc(method).Call(uintptr(unsafe.Pointer(&input)), 0, uintptr(unsafe.Pointer(&purpose)), 0, 0, 1, uintptr(unsafe.Pointer(&output)))
	runtime.KeepAlive(data)
	runtime.KeepAlive(entropy)
	if output.Data != nil {
		defer syscall.LocalFree(syscall.Handle(unsafe.Pointer(output.Data)))
	}
	if value == 0 {
		return nil, fmt.Errorf("local key protection failed: %w", err)
	}
	if output.Data == nil || output.Size == 0 || output.Size > 32768 {
		return nil, errJournalUnavailable
	}
	buffer := unsafe.Slice(output.Data, int(output.Size))
	copied := append([]byte(nil), buffer...)
	clear(buffer)
	return copied, nil
}
