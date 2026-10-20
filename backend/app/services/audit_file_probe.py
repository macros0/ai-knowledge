"""Isolated configuration/file worker. No application imports, connections or SQL."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

MAX_IPC = 32768
LIMITS = {'passfile': 1048576, 'sslrootcert': 4194304, 'sslcrl': 4194304}
NAMES = ('passfile', 'sslrootcert', 'sslcrl', 'sslcert', 'sslkey')


class CleanupIncomplete(RuntimeError):
    pass


def planned_directory(request):
    directory = Path(request['directory'])
    owner = request['owner']
    if (not directory.is_absolute() or len(owner) != 32 or
            any(c not in '0123456789abcdef' for c in owner) or
            not directory.name.startswith('okf-audit-files-' + owner + '-') or
            directory.is_symlink() or getattr(directory, 'is_junction', lambda: False)()):
        raise ValueError('Invalid owner')
    return directory


def owned_directory(request):
    directory = planned_directory(request)
    owner = request['owner']
    marker_path = directory / 'owner'
    if marker_path.is_symlink() or getattr(marker_path, 'is_junction', lambda: False)():
        raise ValueError('Invalid owner marker')
    fd = os.open(marker_path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(fd, 'rb') as marker:
        info = os.fstat(marker.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != 32:
            raise ValueError('Invalid owner marker')
        actual = marker.read(33)
    if actual != owner.encode('ascii'):
        raise ValueError('Invalid owner')
    return directory


def create(request):
    directory = planned_directory(request)
    # Exclusive creation is the proof used only by this live helper to unwind
    # a failed marker write. A different helper must require the exact marker.
    directory.mkdir(mode=0o700)
    marker_created = False
    try:
        protect_windows_directory(directory)
        fd = os.open(directory / 'owner', os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        marker_created = True
        try:
            data = request['owner'].encode('ascii')
            while data:
                written = os.write(fd, data)
                if not 0 < written <= len(data):
                    raise OSError('Owner marker write failed')
                data = data[written:]
        finally:
            os.close(fd)
    except BaseException:
        try:
            if marker_created:
                (directory / 'owner').unlink()
            directory.rmdir()
        except BaseException as exc:
            raise CleanupIncomplete('Owner creation cleanup incomplete') from exc
        raise
    return {'status': 'ok'}


def cleanup(request):
    directory = planned_directory(request)
    try:
        directory.stat()
    except FileNotFoundError:
        return {'status': 'ok'}
    directory = owned_directory(request)
    for name in NAMES:
        (directory / name).unlink(missing_ok=True)
    (directory / 'owner').unlink()
    directory.rmdir()
    return {'status': 'ok'}


def home_directory(request):
    if os.name == 'nt':
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.WinDLL('shell32').SHGetFolderPathW(None, 26, None, 0, buffer) != 0:
            return None
        return Path(buffer.value) / 'postgresql'
    if request.get('home'):
        return Path(request['home'])
    import pwd
    try:
        return Path(pwd.getpwuid(os.geteuid()).pw_dir)
    except KeyError:
        return None


def source_files(request):
    fields = request['files']
    if not isinstance(fields, dict) or set(fields) - set(NAMES):
        raise ValueError('Invalid fields')
    home = home_directory(request)
    ssl_home = home if os.name == 'nt' or home is None else home / '.postgresql'
    defaults = {
        'passfile': home / ('pgpass.conf' if os.name == 'nt' else '.pgpass') if home else None,
        'sslrootcert': ssl_home / 'root.crt' if ssl_home else None,
        'sslcrl': ssl_home / 'root.crl' if ssl_home else None,
        'sslcert': ssl_home / 'postgresql.crt' if ssl_home else None,
    }
    result = {}
    for name in defaults:
        value = fields.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > 4096 or '\0' in value):
            raise ValueError('Invalid path')
        result[name] = Path(value) if value else defaults[name]
    return result


def identity(info):
    # Windows CRT fstat ctime may differ from path stat creation time.
    # File id, size and modification time are comparable across both APIs.
    core = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    return core if os.name == 'nt' else core + (info.st_ctime_ns,)


def copy_file(source, target, name):
    if source is None:
        return 'missing', None
    try:
        fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    except (FileNotFoundError, NotADirectoryError):
        return 'missing', None
    except PermissionError:
        if name == 'passfile':
            return 'ignored', None
        raise
    with os.fdopen(fd, 'rb') as reader:
        before = os.fstat(reader.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Nonregular source')
        if name == 'passfile' and os.name != 'nt' and before.st_mode & 0o077:
            return 'ignored', None
        if before.st_size > LIMITS[name]:
            raise ValueError('Oversized source')
        content = reader.read(LIMITS[name] + 1)
        after = os.fstat(reader.fileno())
    if len(content) > LIMITS[name] or identity(before) != identity(after):
        raise ValueError('Changed source')
    output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(output, 'wb') as writer:
        writer.write(content)
    return 'ready', identity(after)


def protect_windows_directory(directory):
    """Set an inheritable private DACL before any secret snapshot is written."""
    if os.name != 'nt':
        return
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    dword = ctypes.c_ulong
    boolean = ctypes.c_int
    kernel.GetCurrentProcess.restype = pointer
    kernel.CloseHandle.argtypes = [pointer]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    adv.OpenProcessToken.argtypes = [pointer, dword, ctypes.POINTER(pointer)]
    adv.OpenProcessToken.restype = boolean
    adv.GetTokenInformation.argtypes = [pointer, ctypes.c_int, pointer, dword, ctypes.POINTER(dword)]
    adv.GetTokenInformation.restype = boolean
    adv.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(pointer)]
    adv.ConvertSidToStringSidW.restype = boolean
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [ctypes.c_wchar_p, dword, ctypes.POINTER(pointer), pointer]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = boolean
    adv.GetSecurityDescriptorDacl.argtypes = [pointer, ctypes.POINTER(boolean), ctypes.POINTER(pointer), ctypes.POINTER(boolean)]
    adv.GetSecurityDescriptorDacl.restype = boolean
    adv.SetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, dword, pointer, pointer, pointer, pointer]
    adv.SetNamedSecurityInfoW.restype = dword
    token, user_text, descriptor = pointer(), pointer(), pointer()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise ValueError('Private file token unavailable')
        user = ctypes.create_string_buffer(4096)
        needed = dword()
        if not adv.GetTokenInformation(token, 1, user, len(user), ctypes.byref(needed)):
            raise ValueError('Private file user unavailable')
        sid = ctypes.cast(user, ctypes.POINTER(pointer))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(user_text)):
            raise ValueError('Private file user unavailable')
        principal = ctypes.wstring_at(user_text)
        policy = 'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;' + principal + ')'
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(policy, 1, ctypes.byref(descriptor), None):
            raise ValueError('Private file policy unavailable')
        present, defaulted, acl = boolean(), boolean(), pointer()
        if not adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)) or not present.value or not acl:
            raise ValueError('Private file policy unavailable')
        if adv.SetNamedSecurityInfoW(str(directory), 1, 4 | 0x80000000, None, None, acl, None):
            raise ValueError('Private file policy unavailable')
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if user_text:
            kernel.LocalFree(user_text)
        if token:
            kernel.CloseHandle(token)


def source_observation(source, name):
    if source is None:
        return ('none',)
    try:
        resolved = str(source.resolve(strict=False))
        try:
            return ('present', resolved, identity(source.stat()))
        except (FileNotFoundError, NotADirectoryError):
            return ('missing', resolved)
    except PermissionError:
        if name == 'passfile':
            return ('unreadable',)
        raise


def prepare(request):
    directory = owned_directory(request)
    protect_windows_directory(directory)
    sources = source_files(request)
    result = {name: 'missing' for name in NAMES}
    used = [name for name in LIMITS if
            (name == 'passfile' and request['uses_passfile']) or
            (name != 'passfile' and request['tls'])]
    if request['tls'] and not request['client_disabled']:
        used.append('sslcert')
    baseline = {name: source_observation(sources[name], name) for name in used}
    seen = {}
    if request['tls'] and not request['client_disabled']:
        certificate = sources['sslcert']
        if certificate is not None:
            try:
                certificate.stat()
            except (FileNotFoundError, NotADirectoryError):
                pass
            else:
                raise ValueError('Client certificate unsupported')
    for name in LIMITS:
        if name == 'passfile' and not request['uses_passfile']:
            continue
        if name != 'passfile' and not request['tls']:
            continue
        result[name], observed = copy_file(sources[name], directory / name, name)
        if observed is not None:
            seen[name] = observed
    for name, observed in seen.items():
        if identity(sources[name].stat()) != observed:
            raise ValueError('Rotated source')
    for name, observed in baseline.items():
        if source_observation(sources[name], name) != observed:
            raise ValueError('Rotated source')
    return {'status': 'ok', 'files': result}


def defaults(request):
    environment = request['environment']
    if not isinstance(environment, dict) or any(not (key.startswith('PG') or key == 'HOME') for key in environment):
        raise ValueError('Invalid defaults environment')
    if 'PGSERVICE' in environment:
        raise ValueError('Service unsupported')
    for key, value in environment.items():
        if not isinstance(value, str):
            raise ValueError('Invalid default')
        os.environ[key] = value
    # Discover the pinned binary distribution without importing the driver.
    # Importing the whole driver in four children exceeds the CPU-limited phase
    # budget. All discovery/loading remains inside this killable worker.
    spec = importlib.util.find_spec('psycopg_binary')
    if spec is None or spec.origin is None:
        raise ValueError('Missing binary driver')
    libs = Path(spec.origin).parent.parent / 'psycopg_binary.libs'
    candidates = list(libs.glob('libpq-*.dll' if os.name == 'nt' else 'libpq-*.so.*'))
    if len(candidates) != 1:
        raise ValueError('Ambiguous libpq')
    dll_directory = os.add_dll_directory(str(libs)) if os.name == 'nt' else None
    try:
        lib = ctypes.CDLL(str(candidates[0]))
        lib.PQlibVersion.restype = ctypes.c_int
        if lib.PQlibVersion() != (180004 if os.name == 'nt' else 180006):
            raise ValueError('Unproven libpq')
        class Option(ctypes.Structure):
            _fields_ = [(key, ctypes.c_char_p) for key in
                        ('keyword', 'envvar', 'compiled', 'val', 'label', 'dispchar')] + [('dispsize', ctypes.c_int)]
        lib.PQconndefaults.argtypes = []
        lib.PQconndefaults.restype = ctypes.POINTER(Option)
        lib.PQconninfoFree.argtypes = [ctypes.POINTER(Option)]
        lib.PQconninfoFree.restype = None
        options = lib.PQconndefaults()
        if not options:
            raise ValueError('Defaults unavailable')
        parameters = {}
        try:
            for index in range(128):
                option = options[index]
                if not option.keyword:
                    break
                if option.val is not None:
                    parameters[option.keyword.decode()] = option.val.decode()
            else:
                raise ValueError('Oversized defaults')
        finally:
            lib.PQconninfoFree(options)
        return {'status': 'ok', 'parameters': parameters}
    finally:
        if dll_directory is not None:
            dll_directory.close()


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_IPC + 1)
        if len(raw) > MAX_IPC:
            raise ValueError('Oversized input')
        request = json.loads(raw)
        operation = request['operation']
        if operation == 'defaults':
            result = defaults(request)
        elif operation == 'create':
            result = create(request)
        elif operation == 'prepare':
            result = prepare(request)
        elif operation == 'cleanup':
            result = cleanup(request)
        else:
            raise ValueError('Invalid operation')
        encoded = json.dumps(result, ensure_ascii=True).encode()
        if len(encoded) > MAX_IPC:
            raise ValueError('Oversized output')
    except CleanupIncomplete:
        encoded = b'{"status":"cleanup_incomplete"}'
    except BaseException:
        encoded = b'{"status":"unsupported"}'
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
