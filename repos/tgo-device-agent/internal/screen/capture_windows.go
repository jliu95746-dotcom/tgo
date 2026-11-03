//go:build windows && amd64

package screen

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"image"
	"image/png"
	"syscall"
	"unsafe"
)

type bitmapHeader struct {
	Size                        uint32
	Width, Height               int32
	Planes, BitCount            uint16
	Compression, SizeImage      uint32
	XPixel, YTile               int32
	ColorsUsed, ColorsImportant uint32
}
type bitmapInfo struct {
	Header bitmapHeader
	Colors [1]uint32
}
type pngBuffer struct{ bytes.Buffer }

func (b *pngBuffer) Write(data []byte) (int, error) {
	if len(data) > MaxPNGBytes-b.Len() {
		return 0, errors.New("窗口 PNG 超过大小限制")
	}
	return b.Buffer.Write(data)
}
func (b *nativeBackend) Capture(ctx context.Context, window Window) ([]byte, error) {
	return captureInWorker(ctx, window)
}

// PrintWindow may block inside another application. It is only used by the
// separately bounded worker process, never on the agent's own UI thread.
func (b *nativeBackend) renderWindow(ctx context.Context, window Window) ([]byte, error) {
	if err := b.recheck(ctx, window); err != nil {
		return nil, err
	}
	var encoded pngBuffer
	err := withDPI(func() error {
		var frame winRect
		if call(user32, "GetWindowRect", b.handle, uintptr(unsafe.Pointer(&frame))) == 0 {
			return errWindow
		}
		width, height := int(frame.Right-frame.Left), int(frame.Bottom-frame.Top)
		left, top := window.X-int(frame.Left), window.Y-int(frame.Top)
		if width < 1 || height < 1 || width > 8192 || height > 8192 || width > MaxPixels/height || left < 0 || top < 0 || left+window.Width > width || top+window.Height > height {
			return errWindow
		}
		dc := call(user32, "GetDC", b.handle)
		if dc == 0 {
			return errWindow
		}
		defer call(user32, "ReleaseDC", b.handle, dc)
		memory := call(gdi32, "CreateCompatibleDC", dc)
		if memory == 0 {
			return errWindow
		}
		defer call(gdi32, "DeleteDC", memory)
		info := bitmapInfo{Header: bitmapHeader{Size: 40, Width: int32(width), Height: -int32(height), Planes: 1, BitCount: 32}}
		var pixels unsafe.Pointer
		bitmap := call(gdi32, "CreateDIBSection", dc, uintptr(unsafe.Pointer(&info)), 0, uintptr(unsafe.Pointer(&pixels)), 0, 0)
		if bitmap == 0 || pixels == nil {
			return errWindow
		}
		defer call(gdi32, "DeleteObject", bitmap)
		prior := call(gdi32, "SelectObject", memory, bitmap)
		if prior == 0 || prior == ^uintptr(0) {
			return errWindow
		}
		defer call(gdi32, "SelectObject", memory, prior)
		// The worker owns PrintWindow and all GDI resources. Only the cropped
		// client is returned; the parent enforces a process deadline.
		if call(user32, "PrintWindow", b.handle, memory, 0) == 0 {
			return fmt.Errorf("PrintWindow failed: %v", syscall.GetLastError())
		}
		// Finish this thread's GDI operations before reading the DIB memory.
		if call(gdi32, "GdiFlush") == 0 {
			return errWindow
		}
		data := unsafe.Slice((*byte)(pixels), width*height*4)
		img := image.NewRGBA(image.Rect(0, 0, window.Width, window.Height))
		nonBlank := false
		for y := 0; y < window.Height; y++ {
			for x := 0; x < window.Width; x++ {
				source := ((y+top)*width + x + left) * 4
				target := (y*window.Width + x) * 4
				img.Pix[target] = data[source+2]
				img.Pix[target+1] = data[source+1]
				img.Pix[target+2] = data[source]
				img.Pix[target+3] = 255
				if data[source]|data[source+1]|data[source+2] != 0 {
					nonBlank = true
				}
			}
		}
		if !nonBlank {
			return errors.New("此窗口未提供可核验画面，不能使用屏幕工具")
		}
		if err := ctx.Err(); err != nil {
			return err
		}
		return png.Encode(&encoded, img)
	})
	if err != nil {
		return nil, err
	}
	if err = b.recheck(ctx, window); err != nil {
		return nil, err
	}
	return encoded.Bytes(), nil
}
