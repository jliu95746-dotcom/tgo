//go:build linux || darwin || freebsd || openbsd || netbsd || dragonfly

package scriptprocess

import (
	"errors"
	"os"
	"os/exec"
	"syscall"
)

type unixProcess struct{ command *exec.Cmd }

func startProcess(request Request, environment []string, stdin, stdout, stderr *os.File) (ownedProcess, error) {
	if request.ProgramSHA256 != "" {
		return nil, errors.New("reviewed executable pinning is not implemented on this platform")
	}
	command := exec.Command(request.Program, request.Arguments...)
	command.Dir = request.Directory
	command.Env = environment
	command.Stdin = stdin
	command.Stdout = stdout
	command.Stderr = stderr
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := command.Start(); err != nil {
		return nil, err
	}
	return &unixProcess{command}, nil
}

func (p *unixProcess) Wait() (int, error) {
	err := p.command.Wait()
	if p.command.ProcessState == nil {
		return -1, err
	}
	return p.command.ProcessState.ExitCode(), err
}
func (p *unixProcess) Stop() error {
	err := syscall.Kill(-p.command.Process.Pid, syscall.SIGKILL)
	if errors.Is(err, syscall.ESRCH) {
		return nil
	}
	return err
}
func (p *unixProcess) Close() {}
