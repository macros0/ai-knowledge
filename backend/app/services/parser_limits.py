"""Native Windows job owned only by the supervising parent process."""
from __future__ import annotations


class WindowsParserJob:
    """Enforce committed-memory limits and kill the complete job on close.

    No BREAKAWAY flags or inheritable handle: descendants cannot outlive the
    parent's last handle. The parser must await admission before opening input.
    """

    def __init__(self, memory_bytes: int):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._ctypes = ctypes
        self._kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.CreateIoCompletionPort.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.c_size_t, wintypes.DWORD]
        kernel.CreateIoCompletionPort.restype = wintypes.HANDLE
        kernel.GetQueuedCompletionStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                                     ctypes.POINTER(ctypes.c_size_t),
                                                     ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD]
        kernel.GetQueuedCompletionStatus.restype = wintypes.BOOL
        self._port = None
        self._memory_exceeded = False
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        # JOB_MEMORY | PROCESS_MEMORY | KILL_ON_JOB_CLOSE (no breakaway).
        limits.BasicLimitInformation.LimitFlags = 0x200 | 0x100 | 0x2000
        limits.ProcessMemoryLimit = memory_bytes
        limits.JobMemoryLimit = memory_bytes
        if not kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        self._port = kernel.CreateIoCompletionPort(wintypes.HANDLE(-1), None, 0, 1)
        if not self._port:
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

        class CompletionPort(ctypes.Structure):
            _fields_ = [("CompletionKey", ctypes.c_void_p), ("CompletionPort", wintypes.HANDLE)]

        association = CompletionPort(1, self._port)
        if not kernel.SetInformationJobObject(self._handle, 7, ctypes.byref(association), ctypes.sizeof(association)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid: int) -> None:
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE, required by AssignProcessToJobObject.
        process = self._kernel.OpenProcess(0x100 | 0x1, False, pid)
        if not process:
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, process):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
        finally:
            self._kernel.CloseHandle(process)

    def close(self) -> None:
        if self._handle:
            if not self._kernel.CloseHandle(self._handle):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            self._handle = None
        if self._port:
            if not self._kernel.CloseHandle(self._port):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            self._port = None

    def memory_exceeded(self) -> bool:
        """Read OS limit events, including failures during interpreter startup."""
        from ctypes import wintypes

        ctypes = self._ctypes
        code, key, overlapped = wintypes.DWORD(), ctypes.c_size_t(), ctypes.c_void_p()
        while self._kernel.GetQueuedCompletionStatus(self._port, ctypes.byref(code), ctypes.byref(key),
                                                     ctypes.byref(overlapped), 0):
            # JOB_OBJECT_MSG_PROCESS_MEMORY_LIMIT / JOB_OBJECT_MSG_JOB_MEMORY_LIMIT
            if code.value in {9, 10}:
                self._memory_exceeded = True
        if ctypes.get_last_error() != 258:  # WAIT_TIMEOUT means queue drained
            raise ctypes.WinError(ctypes.get_last_error())
        return self._memory_exceeded
