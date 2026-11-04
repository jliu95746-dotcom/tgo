//go:build windows && (amd64 || arm64)

package scriptprocess

import (
	"fmt"
	"os"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"unsafe"
)

var kernel = syscall.NewLazyDLL("kernel32.dll")

type basicJobLimits struct {
	ProcessTime, JobTime                 int64
	Flags                                uint32
	MinimumWorkingSet, MaximumWorkingSet uintptr
	ActiveProcessLimit                   uint32
	Affinity                             uintptr
	PriorityClass, SchedulingClass       uint32
}

type jobLimits struct {
	Basic                                                      basicJobLimits
	IO                                                         [6]uint64
	ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory uintptr
}

type startupInfoEx struct {
	syscall.StartupInfo
	Attributes unsafe.Pointer
}

type windowsProcess struct {
	mu           sync.Mutex
	process, job syscall.Handle
	closed       bool
}

//go:uintptrescapes
func winCall(name string, args ...uintptr) error {
	value, _, err := kernel.NewProc(name).Call(args...)
	if value == 0 {
		return fmt.Errorf("%s: %w", name, err)
	}
	return nil
}

func createJob() (syscall.Handle, error) {
	value, _, err := kernel.NewProc("CreateJobObjectW").Call(0, 0)
	if value == 0 {
		return 0, fmt.Errorf("CreateJobObjectW: %w", err)
	}
	job := syscall.Handle(value)
	limits := jobLimits{Basic: basicJobLimits{Flags: 0x00002000}} // KILL_ON_JOB_CLOSE; no breakaway flags.
	if err := winCall("SetInformationJobObject", value, 9, uintptr(unsafe.Pointer(&limits)), unsafe.Sizeof(limits)); err != nil {
		syscall.CloseHandle(job)
		return 0, err
	}
	return job, nil
}

func processAttributes(handles, jobs []syscall.Handle) ([]byte, error) {
	var size uintptr
	// The size query deliberately returns FALSE/ERROR_INSUFFICIENT_BUFFER.
	kernel.NewProc("InitializeProcThreadAttributeList").Call(0, 2, 0, uintptr(unsafe.Pointer(&size)))
	if size == 0 || size > 1024*1024 {
		return nil, fmt.Errorf("invalid process attribute allocation")
	}
	buffer := make([]byte, size)
	pointer := uintptr(unsafe.Pointer(&buffer[0]))
	if err := winCall("InitializeProcThreadAttributeList", pointer, 2, 0, uintptr(unsafe.Pointer(&size))); err != nil {
		return nil, err
	}
	update := func(attribute uintptr, value unsafe.Pointer, length uintptr) error {
		return winCall("UpdateProcThreadAttribute", pointer, 0, attribute, uintptr(value), length, 0, 0)
	}
	if err := update(0x00020002, unsafe.Pointer(&handles[0]), uintptr(len(handles))*unsafe.Sizeof(handles[0])); err != nil {
		kernel.NewProc("DeleteProcThreadAttributeList").Call(pointer)
		return nil, err
	}
	// JOB_LIST assigns the child during CreateProcess, before any script code can
	// spawn an unowned child. Requires Windows 10 / Server 2016 or newer.
	if err := update(0x0002000d, unsafe.Pointer(&jobs[0]), unsafe.Sizeof(jobs[0])); err != nil {
		kernel.NewProc("DeleteProcThreadAttributeList").Call(pointer)
		return nil, err
	}
	return buffer, nil
}

func startProcess(request Request, environment []string, stdin, stdout, stderr *os.File) (ownedProcess, error) {
	hold, err := holdReviewedProgram(request)
	if err != nil {
		return nil, err
	}
	defer hold.Close()
	job, err := createJob()
	if err != nil {
		return nil, err
	}
	ok := false
	defer func() {
		if !ok {
			syscall.CloseHandle(job)
		}
	}()
	current, err := syscall.GetCurrentProcess()
	if err != nil {
		return nil, err
	}
	handles := make([]syscall.Handle, 3)
	for i, file := range []*os.File{stdin, stdout, stderr} {
		if err := syscall.DuplicateHandle(current, syscall.Handle(file.Fd()), current, &handles[i], 0, true, syscall.DUPLICATE_SAME_ACCESS); err != nil {
			return nil, err
		}
		defer syscall.CloseHandle(handles[i])
	}
	jobs := []syscall.Handle{job}
	attributes, err := processAttributes(handles, jobs)
	if err != nil {
		return nil, err
	}
	defer func() {
		kernel.NewProc("DeleteProcThreadAttributeList").Call(uintptr(unsafe.Pointer(&attributes[0])))
		runtime.KeepAlive(handles)
		runtime.KeepAlive(jobs)
	}()
	info := startupInfoEx{Attributes: unsafe.Pointer(&attributes[0])}
	info.Cb = uint32(unsafe.Sizeof(info))
	info.Flags = syscall.STARTF_USESTDHANDLES | syscall.STARTF_USESHOWWINDOW
	info.StdInput = handles[0]
	info.StdOutput = handles[1]
	info.StdErr = handles[2]
	arguments := []string{syscall.EscapeArg(request.Program)}
	for _, arg := range request.Arguments {
		arguments = append(arguments, syscall.EscapeArg(arg))
	}
	command, err := syscall.UTF16FromString(strings.Join(arguments, " "))
	if err != nil || len(command) > 32767 {
		return nil, fmt.Errorf("invalid Windows argument vector")
	}
	program, err := syscall.UTF16PtrFromString(request.Program)
	if err != nil {
		return nil, err
	}
	directory, err := syscall.UTF16PtrFromString(request.Directory)
	if err != nil {
		return nil, err
	}
	var env []uint16
	for _, entry := range environment {
		part, err := syscall.UTF16FromString(entry)
		if err != nil {
			return nil, err
		}
		env = append(env, part...)
	}
	env = append(env, 0)
	if len(env) == 1 {
		env = append(env, 0)
	}
	var process syscall.ProcessInformation
	const flags = 0x00000400 | 0x00080000 | 0x08000000 // Unicode environment, STARTUPINFOEX, no console window.
	err = syscall.CreateProcess(program, &command[0], nil, nil, true, flags, &env[0], directory, &info.StartupInfo, &process)
	runtime.KeepAlive(attributes)
	runtime.KeepAlive(handles)
	runtime.KeepAlive(jobs)
	runtime.KeepAlive(env)
	if err != nil {
		return nil, err
	}
	syscall.CloseHandle(process.Thread)
	ok = true
	return &windowsProcess{process: process.Process, job: job}, nil
}

func (p *windowsProcess) Wait() (int, error) {
	for {
		p.mu.Lock()
		if p.closed {
			p.mu.Unlock()
			return -1, fmt.Errorf("owned process handle closed")
		}
		event, err := syscall.WaitForSingleObject(p.process, 50)
		if err != nil {
			p.mu.Unlock()
			return -1, err
		}
		if event == syscall.WAIT_OBJECT_0 {
			var code uint32
			err = syscall.GetExitCodeProcess(p.process, &code)
			p.mu.Unlock()
			return int(code), err
		}
		p.mu.Unlock()
		if event != syscall.WAIT_TIMEOUT {
			return -1, fmt.Errorf("unexpected owned process wait state")
		}
	}
}

func (p *windowsProcess) Stop() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return nil
	}
	return winCall("TerminateJobObject", uintptr(p.job), 1)
}
func (p *windowsProcess) Close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.closed {
		syscall.CloseHandle(p.job)
		syscall.CloseHandle(p.process)
		p.closed = true
	}
}
