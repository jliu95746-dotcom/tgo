package screen

import (
	"context"
	"testing"
)

func TestWindowSelectionDefaultsToScreenshotWithoutInput(t *testing.T) {
	c, b := fixture(t)
	candidate := Candidate{Window: b.window, Title: "合成测试窗口"}
	grant, err := GrantForSelection(&candidate, LocalSettings{MaxActions: 5, ClickRegion: c.grant.ClickRegions[0]})
	if err != nil {
		t.Fatal(err)
	}
	if grant.Handle != b.window.Handle || grant.Executable != b.window.Executable || grant.AllowText || len(grant.ClickRegions) != 0 {
		t.Fatal("selection silently expanded local input permissions")
	}
	if _, err := NewController(grant, b); err != nil {
		t.Fatal(err)
	}
}

func TestWindowSelectionMustBeExplicitAndWithinSelectedClient(t *testing.T) {
	_, b := fixture(t)
	candidate := Candidate{Window: b.window, Title: "合成测试窗口"}
	if _, err := GrantForSelection(nil, LocalSettings{MaxActions: 5}); err == nil {
		t.Fatal("no selected window was accepted")
	}
	for _, settings := range []LocalSettings{
		{MaxActions: 0}, {MaxActions: 51},
		{MaxActions: 5, AllowClick: true, ClickRegion: Region{Width: 65, Height: 1}},
		{MaxActions: 5, AllowClick: true, ClickRegion: Region{X: -1, Width: 1, Height: 1}},
		{MaxActions: 5, AllowClick: true},
	} {
		if _, err := GrantForSelection(&candidate, settings); err == nil {
			t.Fatal("invalid local selection accepted")
		}
	}
}

func TestSelectionCannotBindAReplacedOrMovedWindow(t *testing.T) {
	for name, change := range map[string]func(*Window){
		"process":  func(w *Window) { w.ProcessStarted++ },
		"window":   func(w *Window) { w.Handle++ },
		"layout":   func(w *Window) { w.Width++ },
		"position": func(w *Window) { w.X++ },
	} {
		t.Run(name, func(t *testing.T) {
			_, b := fixture(t)
			candidate := Candidate{Window: b.window, Title: "合成测试窗口"}
			grant, err := GrantForSelection(&candidate, LocalSettings{MaxActions: 5, AllowText: true})
			if err != nil {
				t.Fatal(err)
			}
			change(&b.window)
			if _, err := NewController(grant, b); err == nil {
				t.Fatal("window changed between selection and binding")
			}
		})
	}
}

func TestExplicitTextAndRegionSelectionKeepExistingInputLimits(t *testing.T) {
	_, b := fixture(t)
	candidate := Candidate{Window: b.window, Title: "合成测试窗口"}
	grant, err := GrantForSelection(&candidate, LocalSettings{MaxActions: 1, AllowText: true, AllowClick: true, ClickRegion: Region{X: 4, Y: 4, Width: 10, Height: 10}})
	if err != nil {
		t.Fatal(err)
	}
	controller, err := NewController(grant, b)
	if err != nil {
		t.Fatal(err)
	}
	image := view(t, controller)
	if err := controller.Click(context.Background(), image.ViewID, 5, 5); err != nil {
		t.Fatal(err)
	}
	if _, err := controller.Screenshot(context.Background()); err == nil || b.clicks != 1 {
		t.Fatal("selection action budget was not enforced")
	}
}

func TestBackgroundSelectionDoesNotAllowBackgroundOperation(t *testing.T) {
	_, backend := fixture(t)
	backend.window.Foreground = false
	candidate := Candidate{Window: backend.window, Title: "合成测试窗口"}
	grant, err := GrantForSelection(&candidate, LocalSettings{MaxActions: 5})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := NewController(grant, backend); err == nil {
		t.Fatal("selection weakened foreground requirement")
	}
	backend.window.Foreground = true
	if _, err := NewController(grant, backend); err != nil {
		t.Fatal(err)
	}
}
