package screen

import (
	"bytes"
	"context"
	"errors"
	"image"
	"image/color"
	"image/png"
	"path/filepath"
	"testing"
	"time"
)

type fakeBackend struct {
	window Window
	pixels []byte
	clicks int
	typed  int
	stop   bool
}

func (b *fakeBackend) Inspect(context.Context) (Window, error)         { return b.window, nil }
func (b *fakeBackend) Capture(context.Context, Window) ([]byte, error) { return b.pixels, nil }
func (b *fakeBackend) Click(context.Context, Window, int, int) error   { b.clicks++; return nil }
func (b *fakeBackend) TypeText(context.Context, Window, string) error  { b.typed++; return nil }
func (b *fakeBackend) StopRequested() bool                             { return b.stop }

func fixture(t *testing.T) (*Controller, *fakeBackend) {
	t.Helper()
	executable := filepath.Join(t.TempDir(), "owned-ui.exe")
	img := image.NewRGBA(image.Rect(0, 0, 64, 48))
	img.Set(0, 0, color.RGBA{R: 123, A: 255})
	var encoded bytes.Buffer
	if err := png.Encode(&encoded, img); err != nil {
		t.Fatal(err)
	}
	b := &fakeBackend{window: Window{Handle: 10, ProcessID: 20, ProcessStarted: 30, Executable: executable,
		Width: 64, Height: 48, X: 50, Y: 40, DPI: 96, Visible: true, Foreground: true, Interactive: true}, pixels: encoded.Bytes()}
	c, err := NewController(Grant{Handle: 10, Executable: executable, MaxActions: 2, AllowText: true,
		ClickRegions: []Region{{X: 4, Y: 4, Width: 20, Height: 20}}}, b)
	if err != nil {
		t.Fatal(err)
	}
	return c, b
}

func view(t *testing.T, c *Controller) Capture {
	t.Helper()
	result, err := c.Screenshot(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestWindowGrantIsBoundAndViewCanOnlyBeUsedOnce(t *testing.T) {
	c, b := fixture(t)
	capture := view(t, c)
	if capture.ViewID == "" || capture.Width != 64 || capture.Height != 48 || len(capture.PNG) == 0 {
		t.Fatal("missing image proof")
	}
	if err := c.Click(context.Background(), capture.ViewID, 8, 8); err != nil {
		t.Fatal(err)
	}
	if err := c.Click(context.Background(), capture.ViewID, 8, 8); err == nil {
		t.Fatal("replayed screenshot was accepted")
	}
	if b.clicks != 1 {
		t.Fatal("click was replayed")
	}
}

func TestScreenshotIsOnlyPNGAndBounded(t *testing.T) {
	for _, malformed := range [][]byte{[]byte("private-not-png"), make([]byte, MaxPNGBytes+1)} {
		c, b := fixture(t)
		b.pixels = malformed
		if _, err := c.Screenshot(context.Background()); err == nil {
			t.Fatal("malformed screenshot accepted")
		}
		if c.Active() {
			t.Fatal("bad capture did not revoke grant")
		}
	}
}

func TestWindowIdentityGeometryFocusAndDesktopChangesRevoke(t *testing.T) {
	changes := map[string]func(*Window){
		"handle": func(w *Window) { w.Handle++ }, "pid": func(w *Window) { w.ProcessID++ },
		"instance": func(w *Window) { w.ProcessStarted++ }, "program": func(w *Window) { w.Executable += ".other" },
		"width": func(w *Window) { w.Width++ }, "height": func(w *Window) { w.Height++ },
		"position": func(w *Window) { w.X++ }, "dpi": func(w *Window) { w.DPI++ },
		"focus": func(w *Window) { w.Foreground = false }, "hidden": func(w *Window) { w.Visible = false },
		"locked": func(w *Window) { w.Interactive = false },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			c, b := fixture(t)
			capture := view(t, c)
			change(&b.window)
			if err := c.Click(context.Background(), capture.ViewID, 8, 8); err == nil {
				t.Fatal("changed window accepted")
			}
			if c.Active() || b.clicks != 0 {
				t.Fatal("window stop was not permanent")
			}
		})
	}
}

func TestChangedImageStopsBeforeInput(t *testing.T) {
	c, b := fixture(t)
	capture := view(t, c)
	img := image.NewRGBA(image.Rect(0, 0, 64, 48))
	img.Set(1, 1, color.White)
	var data bytes.Buffer
	_ = png.Encode(&data, img)
	b.pixels = data.Bytes()
	if err := c.Type(context.Background(), capture.ViewID, "hello"); err == nil {
		t.Fatal("stale image accepted")
	}
	if b.typed != 0 || c.Active() {
		t.Fatal("image drift did not stop input")
	}
}

func TestClickIsLimitedToLocalApprovedRegion(t *testing.T) {
	for _, point := range [][2]int{{-1, 8}, {8, -1}, {64, 8}, {8, 48}, {30, 30}, {3, 5}} {
		c, b := fixture(t)
		capture := view(t, c)
		if err := c.Click(context.Background(), capture.ViewID, point[0], point[1]); err == nil {
			t.Fatal("unapproved point accepted")
		}
		if b.clicks != 0 {
			t.Fatal("unapproved click executed")
		}
	}
}

func TestTextRejectsControlCharactersAndRequiresLocalPermission(t *testing.T) {
	for _, text := range []string{"", "submit\n", "\t", "\r", "\x00", string([]byte{255})} {
		c, b := fixture(t)
		capture := view(t, c)
		if err := c.Type(context.Background(), capture.ViewID, text); err == nil {
			t.Fatal("unsafe text accepted")
		}
		if b.typed != 0 {
			t.Fatal("unsafe text entered")
		}
	}
	c, b := fixture(t)
	c.grant.AllowText = false
	capture := view(t, c)
	if err := c.Type(context.Background(), capture.ViewID, "hello"); err == nil || b.typed != 0 {
		t.Fatal("text permission bypass")
	}
}

func TestLocalStopAndCancelledContextDoNotPerformActions(t *testing.T) {
	for _, stop := range []func(*Controller, *fakeBackend){func(c *Controller, b *fakeBackend) { c.Stop() }, func(c *Controller, b *fakeBackend) { b.stop = true }} {
		c, b := fixture(t)
		capture := view(t, c)
		stop(c, b)
		if err := c.Click(context.Background(), capture.ViewID, 8, 8); err == nil || b.clicks != 0 {
			t.Fatal("stop was ignored")
		}
	}
	c, b := fixture(t)
	capture := view(t, c)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := c.Click(ctx, capture.ViewID, 8, 8); !errors.Is(err, context.Canceled) || b.clicks != 0 {
		t.Fatal("cancelled action was executed")
	}
}

func TestLocalActionBudgetCannotBeExceeded(t *testing.T) {
	c, b := fixture(t)
	for i := 0; i < 2; i++ {
		capture := view(t, c)
		if err := c.Click(context.Background(), capture.ViewID, 8, 8); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := c.Screenshot(context.Background()); err == nil || b.clicks != 2 {
		t.Fatal("action budget bypass")
	}
}

func TestLocalGrantRequiresWindowProgramBoundsAndLimitedBudget(t *testing.T) {
	c, b := fixture(t)
	changes := map[string]func(*Grant){
		"missing_window":    func(g *Grant) { g.Handle = 0 },
		"different_window":  func(g *Grant) { g.Handle++ },
		"relative_program":  func(g *Grant) { g.Executable = "owned.exe" },
		"different_program": func(g *Grant) { g.Executable += ".other" },
		"empty_budget":      func(g *Grant) { g.MaxActions = 0 },
		"excess_budget":     func(g *Grant) { g.MaxActions = 51 },
		"negative_region":   func(g *Grant) { g.ClickRegions = []Region{{X: -1, Width: 1, Height: 1}} },
		"empty_region":      func(g *Grant) { g.ClickRegions = []Region{{Width: 0, Height: 1}} },
		"overflow_region":   func(g *Grant) { g.ClickRegions = []Region{{X: 63, Width: 2, Height: 1}} },
	}
	for name, change := range changes {
		t.Run(name, func(t *testing.T) {
			grant := c.grant
			change(&grant)
			if _, err := NewController(grant, b); err == nil {
				t.Fatal("invalid local grant accepted")
			}
		})
	}
}

func TestStopWatcherLatchesContextCancellation(t *testing.T) {
	c, b := fixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { c.WatchStop(ctx); close(done) }()
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("local stop watcher did not finish")
	}
	if c.Active() || b.clicks != 0 {
		t.Fatal("cancelled local grant remained active")
	}
}
