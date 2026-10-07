//! OS process-tree lifetime only: one-file PyInstaller has an internal worker.
use std::{
    io,
    mem::size_of,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
};
use tokio::process::{Child, Command};
use windows_sys::Win32::{
    Foundation::{HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
        },
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
        Threading::{
            OpenThread, ResumeThread, CREATE_NO_WINDOW, CREATE_SUSPENDED, THREAD_SUSPEND_RESUME,
        },
    },
};

pub struct ProcessJob(OwnedHandle);

impl ProcessJob {
    fn create() -> io::Result<Self> {
        // SAFETY: null security/name are permitted; returned handles are uniquely owned.
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }
            let job = Self(OwnedHandle::from_raw_handle(handle));
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            Ok(job)
        }
    }

    fn attach(&self, child: &Child) -> io::Result<()> {
        let handle = child
            .raw_handle()
            .ok_or_else(|| io::Error::other("missing process handle"))?;
        // SAFETY: both live handles belong to this host; the child is still suspended.
        if unsafe { AssignProcessToJobObject(self.0.as_raw_handle() as HANDLE, handle as HANDLE) }
            == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}

fn resume_primary_thread(pid: u32) -> io::Result<()> {
    // Tokio owns the process handle but not the primary thread handle. A freshly
    // CREATE_SUSPENDED child has one primary thread and has executed no user code.
    // SAFETY: snapshot entries are initialized to the required size; live thread
    // handles are owned and closed once; only our suspended child's thread is resumed.
    unsafe {
        let raw = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        if raw == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let snapshot = OwnedHandle::from_raw_handle(raw);
        let mut entry: THREADENTRY32 = std::mem::zeroed();
        entry.dwSize = size_of::<THREADENTRY32>() as u32;
        let mut valid = Thread32First(snapshot.as_raw_handle(), &mut entry);
        while valid != 0 {
            if entry.th32OwnerProcessID == pid {
                let raw = OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID);
                if raw.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let thread = OwnedHandle::from_raw_handle(raw);
                if ResumeThread(thread.as_raw_handle()) == u32::MAX {
                    return Err(io::Error::last_os_error());
                }
                return Ok(());
            }
            valid = Thread32Next(snapshot.as_raw_handle(), &mut entry);
        }
        Err(io::Error::other("suspended child thread not found"))
    }
}

pub fn spawn(command: &mut Command) -> io::Result<(Child, ProcessJob)> {
    let job = ProcessJob::create()?;
    command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
    let child = command.spawn()?;
    // Attach before resuming, so even the first worker inherits the job. Any error
    // drops job + kill_on_drop child. Last job handle close kills the whole tree.
    job.attach(&child)?;
    resume_primary_thread(
        child
            .id()
            .ok_or_else(|| io::Error::other("missing child PID"))?,
    )?;
    Ok((child, job))
}
