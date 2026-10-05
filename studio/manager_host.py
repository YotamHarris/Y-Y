"""Local configuration, Windows credential storage, singleton, and sign-in task."""
import contextlib
import ctypes
from ctypes import wintypes
import getpass
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.sax.saxutils as xml

import fe_board
import fe_sync
import studio_config

TASK = f'{studio_config.name()} Project Manager'      # the sign-in task's name
CREDENTIAL = f'{studio_config.name()}/DiscordBot'      # the bot token's Credential Manager entry


def config_path():
    return fe_board.board_dir() / 'manager' / 'config.json'


def load_config():
    cfg = json.loads(config_path().read_text(encoding='utf-8'))
    for name in ('codex', 'claude'):
        if cfg.get(name):
            cfg[name] = moved(cfg[name])
    return cfg


def moved(path):
    """A CLI that updates itself into a new versioned folder (Codex: bin/<hash>/codex.exe)
    is found in the newest sibling folder, so an update does not take the service down."""
    p = Path(path)
    if p.is_file():
        return path
    found = sorted((f for f in p.parent.parent.glob('*/' + p.name) if f.is_file()),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    return str(found[0]) if found else path


_CLI_VERSIONS = {}  # (path, mtime) -> version, so `--version` runs once per binary


def _cli_version(f):
    """A Claude Code binary's version: from its versioned folder or file name, else `--version`."""
    named = [part for part in f.parts if re.fullmatch(r'\d+\.\d+\.\d+', part)]
    if named:
        return tuple(int(x) for x in named[-1].split('.'))
    key = (str(f), f.stat().st_mtime)
    if key not in _CLI_VERSIONS:
        try:
            out = subprocess.run([str(f), '--version'], capture_output=True, text=True, timeout=30,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
            m = re.search(r'(\d+)\.(\d+)\.(\d+)', out)
            _CLI_VERSIONS[key] = tuple(int(x) for x in m.groups()) if m else ()
        except (OSError, subprocess.SubprocessError):
            _CLI_VERSIONS[key] = ()
    return _CLI_VERSIONS[key]


def claude_cli(path):
    """The newest Claude Code CLI on this machine (D300): the configured one, the native
    install's versions, or the desktop app's bundled copy, which it keeps current. The model
    classes are the CLI's aliases (`opus`, `sonnet`), and only a current CLI maps them to the
    latest model; the native install updates itself only when run interactively."""
    if Path(path).stem.lower() != 'claude':
        return path  # a stand-in (the tests' fake CLI) is run as configured
    home, appdata = Path.home(), Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming')
    local = Path(os.environ.get('LOCALAPPDATA') or home / 'AppData' / 'Local')
    found = [Path(path)] + list((home / '.local' / 'share' / 'claude' / 'versions').glob('*')) + \
        list((appdata / 'Claude' / 'claude-code').glob('*/*/claude.exe')) + \
        list(local.glob('Packages/Claude_*/LocalCache/Roaming/Claude/claude-code/*/*/claude.exe'))
    # D326: the desktop app is a Store (MSIX) package, so its %APPDATA% is redirected into
    # its own LocalCache. Inside the app (a session's shell) both paths show the copy; the
    # service starts outside it and sees only the LocalCache path.
    found = [f for f in found if f.is_file()]
    if not found:
        return path
    # the configured binary wins a tie, so nothing moves while the versions agree
    return str(max(found, key=lambda f: (_cli_version(f), str(f) == str(path))))


def configure(guild, channel, owner, token=False):
    path = config_path()
    cfg = load_config() if path.exists() else {}
    cfg.setdefault('created', time.time())
    cfg['repo'] = str(studio_config.repo_root())
    for provider in ('codex', 'claude'):
        found = shutil.which(provider)
        saved = cfg.get(provider, '')
        cfg[provider] = found or (saved if saved and Path(saved).is_file() else '')
    for key, value in (('guild_id', guild), ('channel_id', channel), ('owner_id', owner)):
        if value:
            if not str(value).isdigit():
                raise ValueError(f'{key} must be a Discord numeric ID')
            cfg[key] = str(value)
    if token:
        secret = getpass.getpass('Discord bot token (stored in Windows Credential Manager): ').strip()
        if not secret:
            raise ValueError('empty token')
        credential_write(secret)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    tmp.replace(path)
    return path


class Credential(ctypes.Structure):
    _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
                ('Comment', wintypes.LPWSTR), ('LastWritten', wintypes.FILETIME),
                ('CredentialBlobSize', wintypes.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD),
                ('Attributes', ctypes.c_void_p), ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]


def credential_api():
    if os.name != 'nt':
        raise RuntimeError('Windows Credential Manager is required')
    dll = ctypes.WinDLL('advapi32', use_last_error=True)
    dll.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    dll.CredWriteW.restype = wintypes.BOOL
    dll.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.POINTER(ctypes.POINTER(Credential))]
    dll.CredReadW.restype = wintypes.BOOL
    dll.CredFree.argtypes = [ctypes.c_void_p]
    return dll


def credential_write(token):
    dll = credential_api()
    data = token.encode('utf-8')
    blob = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    value = Credential(Type=1, TargetName=CREDENTIAL, CredentialBlobSize=len(data),
                       CredentialBlob=blob, Persist=2, UserName='Discord bot')
    if not dll.CredWriteW(ctypes.byref(value), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def credential_read():
    dll = credential_api()
    ptr = ctypes.POINTER(Credential)()
    if not dll.CredReadW(CREDENTIAL, 1, 0, ctypes.byref(ptr)):
        if ctypes.get_last_error() == 1168:
            return None
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(ptr.contents.CredentialBlob, ptr.contents.CredentialBlobSize).decode('utf-8')
    finally:
        dll.CredFree(ptr)


def _lock(f):
    f.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _lock_file():
    path = config_path().with_name('supervisor.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open('a+b')
    # Never read the byte: on Windows a locked region refuses other processes' reads,
    # and a candidate waiting for the lock must get as far as waiting.
    if f.seek(0, os.SEEK_END) == 0:
        f.write(b'0')
        f.flush()
    return f


@contextlib.contextmanager
def singleton(wait=0.0, still=lambda: True):
    """The service lock: one process serves. OS releases the lock on crash; no stale
    PID heuristics. A handoff's candidate waits (D189) while still() holds."""
    with _lock_file() as f:
        deadline = time.monotonic() + wait
        while True:
            try:
                _lock(f)
                break
            except OSError:
                if time.monotonic() >= deadline or not still():
                    raise
                time.sleep(0.25)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_UN)


def serving():
    """Does a service hold the lock? (A boot starts one only when none does.)"""
    try:
        with singleton():
            return False
    except OSError:
        return True


def doctor(config, auth=True):
    problems = []
    for key in ('guild_id', 'channel_id', 'owner_id'):
        if not str(config.get(key, '')).isdigit():
            problems.append(f'Missing {key}: run setup')
    if os.name != 'nt' or not credential_read():
        problems.append('Missing Discord token: run setup --token')
    for module in ('discord', 'psutil'):
        try:
            __import__(module)
        except ImportError:
            problems.append(f'Missing {module}: install manager-requirements.txt into the manager venv')
    for name in ('codex', 'claude'):
        exe = config.get(name, '')
        if not exe or not Path(exe).is_file():
            problems.append(f'Missing {name} executable: rerun setup')
            continue
        if auth:
            args = [exe, 'login', 'status'] if name == 'codex' else [exe, 'auth', 'status']
            p = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=30, creationflags=fe_sync.NO_WINDOW)
            status = (p.stdout + p.stderr).lower()
            expected = ('chatgpt' in status) if name == 'codex' else ('claude.ai' in status)
            if p.returncode or not expected:
                problems.append(f'{name}: a subscription login is required; API/unknown authentication is not enabled')
    # A checkout whose tools predate the board schema is no longer a problem:
    # fe_board.connect holds the version bump back until it catches up, and a
    # leased checkout may not pull, so refusing here kept the service down.
    return problems


def install_task(repo):
    """At sign-in and every minute after, a boot from the manager's checkout makes sure
    the service runs (D189); a boot while one serves exits at once. The time trigger
    repeats from installation on, the logon trigger's repetition only from a sign-in."""
    if os.name != 'nt':
        raise RuntimeError('Sign-in installation requires Windows')
    # XML avoids shell quoting for paths and contains no credentials.
    who = subprocess.run(['whoami'], capture_output=True, text=True, check=True,
                         creationflags=fe_sync.NO_WINDOW).stdout.strip()
    exe = Path(sys.executable).with_name('pythonw.exe')
    script = studio_config.tool('fe_manager.py', root=repo)
    every = '<Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>'
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    content = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
 <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{xml.escape(who)}</UserId>{every}</LogonTrigger><TimeTrigger><Enabled>true</Enabled><StartBoundary>{now}</StartBoundary>{every}</TimeTrigger></Triggers>
 <Principals><Principal id="Author"><UserId>{xml.escape(who)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
 <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure></Settings>
 <Actions Context="Author"><Exec><Command>{xml.escape(str(exe))}</Command><Arguments>{xml.escape(subprocess.list2cmdline([str(script), 'serve']))}</Arguments><WorkingDirectory>{xml.escape(str(Path(repo)))}</WorkingDirectory></Exec></Actions>
</Task>'''
    path = config_path().with_name('scheduled-task.xml')
    path.write_text(content, encoding='utf-16')
    subprocess.run(['schtasks', '/Create', '/TN', TASK, '/XML', str(path), '/F'], check=True,
                   creationflags=fe_sync.NO_WINDOW)


def worker_job():
    """On Windows a dying wrapper must not leave a CLI editing its checkout.

    The handle intentionally lives until process exit. Assign the wrapper itself
    before it spawns children; all descendants inherit membership in this job.
    """
    if os.name != 'nt':
        return
    class Basic(ctypes.Structure):
        _fields_ = [('ProcessTime', ctypes.c_int64), ('JobTime', ctypes.c_int64),
                    ('Flags', wintypes.DWORD), ('MinWorkingSet', ctypes.c_size_t),
                    ('MaxWorkingSet', ctypes.c_size_t), ('ActiveProcesses', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('Priority', wintypes.DWORD), ('Scheduling', wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('Basic', Basic), ('IO', IO), ('ProcessMemory', ctypes.c_size_t),
                    ('JobMemory', ctypes.c_size_t), ('PeakProcess', ctypes.c_size_t), ('PeakJob', ctypes.c_size_t)]
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.GetCurrentProcess.restype = wintypes.HANDLE
    job = k.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) \
            or not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return job
