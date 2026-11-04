// Package main is the entry point for tgo-device-agent.
//
// Usage:
//
//	tgo-device-agent --server localhost:9876 --bind-code ABC123
//	tgo-device-agent --server localhost:9876              # uses saved token
//	tgo-device-agent --config /path/to/config.json
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"runtime"
	"strings"
	"syscall"
	"time"

	"github.com/tgoai/tgo-device-agent/internal/config"
	"github.com/tgoai/tgo-device-agent/internal/screen"
	"github.com/tgoai/tgo-device-agent/internal/tools"
	"github.com/tgoai/tgo-device-agent/internal/transport"
)

const version = "1.0.0"

func main() {
	if handled, code := screen.RunCaptureWorkerIfRequested(); handled {
		os.Exit(code)
	}
	cfg := config.DefaultConfig()

	// CLI flags
	var (
		serverAddr   string
		configFile   string
		showVer      bool
		selectScreen bool
	)
	flag.StringVar(&serverAddr, "server", "", "Server address host:port (overrides config)")
	flag.StringVar(&cfg.BindCode, "bind-code", "", "Bind code for first-time registration")
	flag.StringVar(&cfg.DeviceName, "name", cfg.DeviceName, "Device display name")
	flag.StringVar(&cfg.WorkRoot, "work-root", cfg.WorkRoot, "Root directory for file operations")
	flag.StringVar(&cfg.LogLevel, "log-level", cfg.LogLevel, "Log level: debug, info, warn, error")
	flag.StringVar(&configFile, "config", "", "Path to JSON config file")
	flag.BoolVar(&showVer, "version", false, "Print version and exit")
	flag.BoolVar(&selectScreen, "screen-select", false, "在本机选择并核对要授权的 Windows 窗口")
	flag.Uint64Var(&cfg.Screen.Handle, "screen-window", 0, "本机授权：指定前台 Windows 窗口句柄（十进制或十六进制）")
	flag.StringVar(&cfg.Screen.Executable, "screen-executable", "", "本机授权：目标窗口程序的完整路径")
	flag.IntVar(&cfg.Screen.MaxActions, "screen-max-actions", cfg.Screen.MaxActions, "本次屏幕输入操作上限，1 至 50 次")
	flag.Parse()

	if showVer {
		fmt.Printf("tgo-device-agent v%s (%s/%s)\n", version, runtime.GOOS, runtime.GOARCH)
		os.Exit(0)
	}

	// Load config file if provided
	if configFile != "" {
		if err := cfg.LoadFromFile(configFile); err != nil {
			fmt.Fprintf(os.Stderr, "Error loading config: %v\n", err)
			os.Exit(1)
		}
	}

	// Override server from flag
	if serverAddr != "" {
		parts := strings.SplitN(serverAddr, ":", 2)
		cfg.ServerHost = parts[0]
		if len(parts) == 2 {
			fmt.Sscanf(parts[1], "%d", &cfg.ServerPort)
		}
	}

	// Setup structured logger
	setupLogger(cfg.LogLevel)

	slog.Info("tgo-device-agent starting",
		"version", version,
		"server", fmt.Sprintf("%s:%d", cfg.ServerHost, cfg.ServerPort),
		"work_root", cfg.WorkRoot,
		"os", runtime.GOOS,
		"arch", runtime.GOARCH,
	)

	// Load saved token for reconnection
	cfg.LoadTokenFromFile()

	// Validate: need either bind code or token
	if cfg.BindCode == "" && cfg.DeviceToken == "" {
		fmt.Fprintln(os.Stderr, "Error: either --bind-code or a saved device token is required")
		fmt.Fprintln(os.Stderr, "  First time:  tgo-device-agent --server HOST:PORT --bind-code CODE")
		fmt.Fprintln(os.Stderr, "  Reconnect:   tgo-device-agent --server HOST:PORT  (uses saved token)")
		os.Exit(1)
	}

	// Setup graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	defer signal.Stop(sigCh)

	go func() {
		select {
		case sig := <-sigCh:
			slog.Info("received signal, shutting down", "signal", sig)
			cancel()
		case <-ctx.Done():
		}
	}()
	if selectScreen {
		grant, err := screen.SelectLocalWindow(ctx)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			if !errors.Is(err, screen.ErrSelectionCancelled) && ctx.Err() == nil {
				os.Exit(1)
			}
			return
		}
		cfg.Screen = grant // Explicit local selection overrides file/flag grants.
	}
	registry := tools.NewRegistry(cfg)
	registry.WatchLocalScreenStop(ctx)
	client := transport.NewClient(cfg, registry)

	// Run the client (blocks until ctx cancelled or fatal error)
	if err := client.Run(ctx); err != nil {
		slog.Error("agent exited with error", "error", err)
		os.Exit(1)
	}

	slog.Info("agent stopped gracefully")
}

func setupLogger(level string) {
	var lvl slog.Level
	switch strings.ToLower(level) {
	case "debug":
		lvl = slog.LevelDebug
	case "warn", "warning":
		lvl = slog.LevelWarn
	case "error":
		lvl = slog.LevelError
	default:
		lvl = slog.LevelInfo
	}

	handler := slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{
		Level:     lvl,
		AddSource: false,
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			if a.Key == slog.TimeKey {
				a.Value = slog.StringValue(time.Now().Format("15:04:05.000"))
			}
			return a
		},
	})
	slog.SetDefault(slog.New(handler))
}
