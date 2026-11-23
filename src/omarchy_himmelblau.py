#!/usr/bin/python3 -I
"""Explicit, experimental Omarchy/Himmelblau companion. No tenant operations."""
import argparse
import base64
import errno
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile

VERSION = '0.6.1'
# The one sddm-himmelblau release this companion supports (tests/test_versions.py).
NATIVE_VERSION = '0.21.0-5'
ASSETS = Path('/usr/share/omarchy-himmelblau')
COMMAND_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
OMARCHY_ENV = {'OMARCHY_PATH': '/usr/share/omarchy'}
STATE = Path('/var/lib/omarchy-himmelblau/state.json')
SCHEMA = 1  # Persistent state format.
JOURNAL_FORMAT = 1
STOCK_PAM = Path('/etc/pam.d/omarchy-lock-password')
SYSTEM_AUTH = Path('/etc/pam.d/system-auth')  # Owned: patched from its baseline (below).
SYSTEM_LOGIN = Path('/etc/pam.d/system-login')  # Watched: must stay pristine pambase.
# Omarchy's installer edits SDDM's PAM files. Replacing sddm with the native package saves
# those edits as .pacsave and installs the defaults; setup puts Omarchy's edits back.
SDDM_PAMS = (Path('/etc/pam.d/sddm'), Path('/etc/pam.d/sddm-autologin'))
# sddm-himmelblau NATIVE_VERSION's own copies of SDDM_PAMS, by name.
NATIVE_SDDM_PAMS = {
    'sddm': 'd7394292a65ae463926c2c3d2cb4e67bbfeb20995450c8e4c92fe5a28e7c4254',
    'sddm-autologin': '1a84cf752782b03b53f66188013bf7e4af4f5e6feb7266bfe58c3faaa20777b4',
}
# system-auth baselines: pristine pambase, or Omarchy's install-time lockout tuning of it
# (install/config/increase-lockout-limit.sh). The state's restore_kind names the one setup
# found; rollback restores it.
BASELINES = ('pambase', 'omarchy')
# Omarchy's two sed substitutions on system-auth, line by line.
OMARCHY_LOCKOUT = (
    (re.compile(rb'(auth\s+required\s+pam_faillock.so)\s+preauth.*'),
     rb'\1 preauth silent deny=10 unlock_time=120'),
    (re.compile(rb'(auth\s+\[default=die\]\s+pam_faillock.so)\s+authfail.*'),
     rb'\1 authfail deny=10 unlock_time=120'),
)
# Exactly the lines `aad-tool configure-pam` 4.0.8 adds to each pambase file.
AAD_TOOL_LINES = (
    b'auth\tsufficient\tpam_himmelblau.so ignore_unknown_user set_authtok\n',
    b'account\t[success=ok auth_err=die default=ignore]\tpam_himmelblau.so ignore_unknown_user\n',
    b'password\tsufficient\tpam_himmelblau.so ignore_unknown_user set_authtok\n',
    b'session\toptional\tpam_himmelblau.so\n',
)
PAM = Path('/etc/pam.d/omarchy-himmelblau-lock-password')
LOCK = Path('/usr/share/omarchy/shell/plugins/lock/Service.qml')
CONFIG = Path('/etc/himmelblau/himmelblau.conf')
THEME = Path('/usr/share/sddm/themes/omarchy')
COPY_THEME = Path('/usr/share/sddm/themes/omarchy-himmelblau')
OVERRIDE = Path('/etc/sddm.conf.d/99-z-himmelblau.conf')
SDDM_CONF = Path('/etc/sddm.conf')
SDDM_CONF_DIRS = (Path('/usr/lib/sddm/sddm.conf.d'), Path('/etc/sddm.conf.d'))
SUDOERS = Path('/etc/sudoers.d/90-himmelblau-user')
RECOVERY_ACCOUNT = Path('/etc/omarchy-himmelblau/recovery-account')
NATIVE_PAYLOAD = Path('/usr/share/omarchy-himmelblau/native-payload.json')
NATIVE_EXECUTABLES = tuple(Path(path) for path in (
    '/usr/bin/sddm', '/usr/bin/sddm-greeter', '/usr/bin/sddm-greeter-qt6',
    '/usr/lib/sddm/sddm-helper', '/usr/lib/sddm/sddm-helper-start-wayland',
    '/usr/lib/sddm/sddm-helper-start-x11user'))
NATIVE_COMPONENTS = (Path('/usr/lib/qt/qml/SddmComponents'), Path('/usr/lib/qt6/qml/SddmComponents'))
PIN_PAM = Path('/etc/pam.d/omarchy-himmelblau-pin-change')
PIN_PAM_CONTENT = (b'# Dedicated current-user PIN replacement; no session unlock.\n'
                   b'account [success=ok default=die] pam_himmelblau.so\n'
                   b'password required pam_himmelblau.so\n')
OVERRIDE_CONTENT = b'[Theme]\nCurrent=omarchy-himmelblau\n'
SDDM_PIN_PAM = Path('/etc/pam.d/sddm-himmelblau-pin-change')
SDDM_PIN_PAM_CONTENT = (b'# Native SDDM PIN replacement; a successful change never starts a session.\n'
                        b'account [success=ok default=die] pam_himmelblau.so\n'
                        b'password required pam_himmelblau.so\n')
LOCAL_PASSWD = Path('/etc/passwd')
CONFIG_LAYERS = (Path('/usr/lib/himmelblau/himmelblau.conf'), Path('/run/himmelblau/himmelblau.conf'))
SERVER_CONFIG = Path('/var/cache/himmelblaud/himmelblau.conf')
CACHE_ALIAS = Path('/var/cache/himmelblaud')
CACHE_TARGET = Path('/var/cache/private/himmelblaud')


class Error(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, *, env=COMMAND_ENV, timeout=30, check=True, **kwargs):
    """Run a fixed-path command with a clean environment; output is always captured."""
    return subprocess.run(argv, check=check, capture_output=True, env=env, cwd='/',
                          timeout=timeout, **kwargs)


def safe(path):
    """Reject symlinks anywhere in a path, including existing parent directories."""
    path = Path(path)
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise Error(f'Symlink refused: {part}')
    if path.exists() and not path.is_file():
        raise Error(f'Expected regular file: {path}')
    return path


def read(path):
    path = safe(path)
    return path.read_bytes() if path.exists() else None


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def ensure_directory(path, mode=0o700):
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        safe(directory / '.guard')
        directory.mkdir(mode=mode)
        fsync_directory(directory.parent)


def unlink_durable(path, missing_ok=False):
    safe(path).unlink(missing_ok=missing_ok)
    if path.parent.exists():
        fsync_directory(path.parent)


def rmdir_durable(path):
    path.rmdir()
    fsync_directory(path.parent)


def atomic(path, data, mode, uid=None, gid=None):
    """Replace path with data. Owner and mode are set on the temporary inode before it
    is synced and renamed, so the target is never visible with partial metadata."""
    path = safe(path)
    ensure_directory(path.parent)
    safe(path)
    fd, name = tempfile.mkstemp(prefix='.himmelblau-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            if uid is not None:
                os.fchown(output.fileno(), uid, gid)
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        no_extended_acl(Path(name))  # An inherited default ACL would outlive the journal.
        os.replace(name, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def create_exclusive(path, data, mode):
    """Create path with data, failing if it already exists (never replaces)."""
    path = safe(path)
    fd, name = tempfile.mkstemp(prefix='.himmelblau-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            raise Error(f'Another root transaction journal exists ({path}); run recover') from None
    finally:
        os.unlink(name)
    fsync_directory(path.parent)


_MANIFESTS = {}


def manifest(name):
    """Return one validated entry of patches.json, parsed once per assets directory."""
    path = ASSETS / 'patches.json'
    if path not in _MANIFESTS:
        try:
            _MANIFESTS[path] = json.loads(path.read_text())
        except ValueError:
            raise Error(f'Invalid {path}') from None
    entries = _MANIFESTS[path]
    entry = entries.get(name) if isinstance(entries, dict) else None
    if name == 'fixed-plugin':
        required = {'id': str, 'files': dict, 'upstream': dict}
    elif name == 'pambase':
        required = {}  # Supported releases by version; pambase_release() checks each.
    elif name == 'previous-release':
        # The one release upgrade moves from and whose plugin locker-preflight still
        # accepts, with what it wrote for each output upgrade may rewrite.
        required = {'version': str, 'sddm-conversation': str, 'lock-pam': str,
                    'plugin-files': dict}
    else:
        required = {'filename': str, 'before': str, 'after': str}
    if not isinstance(entry, dict) or any(not isinstance(entry.get(key), kind)
                                          for key, kind in required.items()):
        raise Error(f'Invalid {path} entry: {name}')
    return entry


def supported_source_hash(name, data):
    info = manifest(name)
    if digest(data) not in (info['before'], info['after']):
        raise Error(f'Unsupported upstream source for {name}; refusing drift')
    return info['after']


def patch_source(name, data):
    info = manifest(name)
    actual = digest(data)
    if actual == info['after']:
        return data  # Known patched source: adopt, never pretend it was pristine.
    if actual != info['before']:
        raise Error(f'Unsupported upstream source for {name}; refusing drift')
    with tempfile.TemporaryDirectory(prefix='omarchy-himmelblau-') as work:
        target = Path(work) / info['filename']
        target.write_bytes(data)
        run(['/usr/bin/patch', '--batch', '--fuzz=0', '-p1', '-d', work],
            input=(ASSETS / (name + '.patch')).read_bytes())
        result = target.read_bytes()
    if digest(result) != info['after']:
        raise Error(f'Unexpected patch result: {name}')
    return result


def identity(domain, user):
    domain = domain.lower()
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    if len(domain) > 253 or not re.fullmatch(rf'(?:{label}\.)+[a-z]{{2,63}}', domain):
        raise Error('Invalid DNS domain')
    local = r'[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}'
    if len(user) > 256 or not re.fullmatch(local + '@' + re.escape(domain), user, re.I):
        raise Error('User must be a UPN in the selected domain')
    return domain, user.lower()


INI_SECTION = re.compile(r'^\s*\[([^]]+)\]\s*(?:[#;].*)?$')
INI_KEY = re.compile(r'^\s*([a-zA-Z_]+)\s*=\s*(.*?)\s*$')


def ini_lines(text):
    """Yield (line index, section, key, value); key and value are None on a section header."""
    section = None
    for index, line in enumerate(text.splitlines()):
        line = line.strip()
        if match := INI_SECTION.match(line):
            section = match[1].lower()
            yield index, section, None, None
        elif match := INI_KEY.match(line):
            value = re.split(r'\s+[;#]', match[2], maxsplit=1)[0].strip()
            yield index, section, match[1].lower(), value


def managed_keys(domain, user, apply_policy):
    return {'domain': domain, 'pam_allow_groups': user,
            'apply_policy': str(apply_policy).lower(), 'use_etc_skel': 'true'}


def validate_scope(data, domain, user, apply_policy=False):
    # Himmelblau unions pam_allow_groups from ALL sections, not only global.
    wanted = managed_keys(domain, user, apply_policy)
    for _, _, key, value in ini_lines((data or b'').decode()):
        if key in wanted and value.lower() != wanted[key].lower():
            raise Error(f'Himmelblau configuration sets a conflicting {key}')
        if key == 'domains' and any(item.strip() != domain for item in value.lower().split(',')):
            raise Error('Additional configured domains require manual scope review')


def read_generated_config():
    # systemd DynamicUser CacheDirectory uses a root-owned public alias pointing
    # into root:root 0700 private storage. This exception is read-only and exact.
    if not CACHE_ALIAS.is_symlink():
        return read(SERVER_CONFIG)
    if SERVER_CONFIG.parent != CACHE_ALIAS:
        raise Error('Unexpected generated configuration location')
    alias = CACHE_ALIAS.lstat()
    if (alias.st_uid, alias.st_gid) != (0, 0) or os.readlink(CACHE_ALIAS) != 'private/himmelblaud':
        raise Error('Untrusted systemd cache alias')
    if CACHE_ALIAS.resolve(strict=True) != CACHE_TARGET:
        raise Error('Unexpected systemd cache alias target')
    safe(CACHE_TARGET / '.guard')  # No alias or symlink in the canonical path.
    private = CACHE_TARGET.parent.lstat()
    target = CACHE_TARGET.lstat()
    if ((private.st_uid, private.st_gid) != (0, 0)
            or stat.S_IMODE(private.st_mode) != 0o700
            or not stat.S_ISDIR(private.st_mode)):
        raise Error('Unsafe systemd private cache directory')
    # Outside the service namespace, idmapped DynamicUser storage is nobody.
    if ((target.st_uid, target.st_gid) not in ((0, 0), (65534, 65534))
            or stat.S_IMODE(target.st_mode) & 0o022
            or not stat.S_ISDIR(target.st_mode)):
        raise Error('Unsafe systemd cache target ownership/mode')
    return read(CACHE_TARGET / SERVER_CONFIG.name)


def configuration_scope(domain, user, apply_policy=False):
    current = read(CONFIG)
    parent = CONFIG.parent
    if (not parent.is_dir() or parent.stat().st_uid != os.geteuid()
            or stat.S_IMODE(parent.stat().st_mode) != 0o755):
        raise Error(f'{parent} must be an existing root-owned 0755 directory')
    if current is not None:
        metadata = CONFIG.stat()
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o644:
            raise Error(f'{CONFIG} must be root-owned 0644')
    # The local main file supersedes vendor/runtime mains, but drop-ins merge.
    # Supporting arbitrary operator drop-in precedence is deliberately out of scope.
    for layer in (*CONFIG_LAYERS, CONFIG):
        directory = Path(str(layer) + '.d')
        safe(directory / '.guard')
        if directory.exists() and any(directory.glob('*.conf')):
            raise Error(f'Unsupported Himmelblau drop-in configuration: {directory}')
    server = read_generated_config()
    if server is not None:
        validate_scope(server, domain, user, apply_policy)
    validate_scope(read(CONFIG), domain, user, apply_policy)


def managed_config(data, domain, user, apply_policy=False):
    """Preserve existing text/settings; refuse conflicting explicit managed keys."""
    validate_scope(data, domain, user, apply_policy)
    text = (data or b'').decode()
    wanted = managed_keys(domain, user, apply_policy)
    lines = text.splitlines(keepends=True)
    previous = None
    global_seen = False
    found = set()
    insert = len(lines)
    for index, section, key, value in ini_lines(text):
        if key is None:
            if previous == 'global':
                insert = index
            previous = section
            if section == 'global':
                if global_seen:
                    raise Error('Duplicate global configuration section')
                global_seen = True
        elif section == 'global' and key in wanted:
            if key in found:
                raise Error(f'Duplicate managed key: {key}')
            found.add(key)
            if value.lower() != wanted[key].lower():
                raise Error(f'Conflicting existing Himmelblau key: {key}')
    extra = ''.join(f'{key} = {value}\n' for key, value in wanted.items() if key not in found)
    if not global_seen:
        return (text.rstrip() + '\n\n[global]\n' + extra).lstrip('\n').encode()
    if not extra:
        return data
    # Keep a missing final newline from joining two settings.
    if insert and not lines[insert - 1].endswith('\n'):
        lines[insert - 1] += '\n'
    lines.insert(insert, '# omarchy-himmelblau: explicit single-account scope\n' + extra)
    return ''.join(lines).encode()


def administrator(user):
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        raise Error(f'{user} does not resolve through NSS; check that himmelblaud is running '
                    f'and {CONFIG} sets its domain') from None
    try:
        canonical = pwd.getpwnam(account.pw_name)
        reverse = pwd.getpwuid(account.pw_uid)
    except KeyError:
        # Before its first sign-in, Himmelblau answers a forward lookup with a computed
        # entry but not the reverse one.
        raise Error(f'{user} has not signed in on this machine yet: run '
                    f'"aad-tool auth-test --name {user}" once, then retry') from None
    name, uid = account.pw_name, account.pw_uid
    reserved = ('ALL', 'DEFAULTS', 'USER_ALIAS', 'RUNAS_ALIAS', 'HOST_ALIAS', 'CMND_ALIAS', 'ROOT')
    if (uid < 1000 or name.upper() in reserved
            or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.@-]{0,255}', name)):
        raise Error('Refusing system UID or unsafe canonical sudo principal')
    if ((canonical.pw_name, canonical.pw_uid) != (name, uid)
            or (reverse.pw_name, reverse.pw_uid) != (name, uid)):
        raise Error('NSS principal round-trip mismatch; admin authorization refused')
    for line in LOCAL_PASSWD.read_text().splitlines():
        fields = line.split(':')
        if len(fields) > 2 and (fields[2] == str(uid)
                                or fields[0].lower() in {user.lower(), name.lower()}):
            raise Error('Admin NSS identity collides with a local passwd account')
    # Lookup consistency detects current drift; it cannot prove global NSS uniqueness.
    return {'requested_upn': user, 'canonical_name': name, 'uid': uid}


def admin_rule(principal):
    return ('# Explicit canonical NSS principal; password/PIN required.\n'
            f'"{principal["canonical_name"]}" ALL=(ALL:ALL) ALL\n').encode()


def validate_sudo_rule(content):
    with tempfile.NamedTemporaryFile() as candidate:
        candidate.write(content)
        candidate.flush()
        run(['/usr/bin/visudo', '-cf', candidate.name])


def identity_file(principal):
    fields = (principal['requested_upn'], principal['canonical_name'], principal['uid'])
    return ''.join(f'{field}\n' for field in fields).encode('ascii')


def recovery_parent():
    safe(RECOVERY_ACCOUNT)
    parent = RECOVERY_ACCOUNT.parent
    if (not parent.is_dir() or parent.stat().st_uid != os.geteuid()
            or stat.S_IMODE(parent.stat().st_mode) != 0o755):
        raise Error('Recovery policy directory must already be root-owned 0755')


def saved_identity(state):
    principal = administrator(state['user'])
    if state['identity'] != principal:
        raise Error('Saved identity no longer matches NSS')
    return principal


def check_identity(state):
    principal = saved_identity(state)
    recovery_parent()
    if read(RECOVERY_ACCOUNT) != identity_file(principal):
        raise Error('Recovery principal policy missing or drifted')
    metadata = RECOVERY_ACCOUNT.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o644:
        raise Error('Recovery principal policy permission drift')
    return principal


def check_admin_identity(state):
    if state['admin'] and read(SUDOERS) != admin_rule(saved_identity(state)):
        raise Error('Canonical admin rule drift')


def root_required():
    if os.geteuid() != 0:
        raise Error('Run this command as root')


def user_required():
    if os.geteuid() == 0:
        raise Error('Run this command as the desktop user, not root')


def journal_path():
    return STATE.parent / 'transaction.json'


_lock_depth = 0


def exclusive(function):
    """Hold an exclusive lock on the state directory for a whole root command, so two
    commands can never plan or apply changes concurrently. Nested calls reuse it."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        global _lock_depth
        if _lock_depth:
            _lock_depth += 1
            try:
                return function(*args, **kwargs)
            finally:
                _lock_depth -= 1
        prepare_state_directory()
        fd = os.open(STATE.parent / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or (metadata.st_uid, metadata.st_gid) != owner():
                raise Error('Unsafe state lock file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Error('Another omarchy-himmelblau root command is running') from None
            _lock_depth = 1
            try:
                return function(*args, **kwargs)
            finally:
                _lock_depth = 0
        finally:
            os.close(fd)
    return wrapper


def owner():
    """Owner of every file and directory the companion writes (root in production)."""
    return os.geteuid(), os.getegid()


def check_state_directory(*, allow_journal=False):
    """Every root command starts here. The state directory is private and holds only
    state.json, transaction.json and atomic() temporary files; a pending journal
    blocks everything except recover."""
    directory = STATE.parent
    if directory.is_symlink():
        raise Error('Symlink state directory refused')
    if not directory.exists():
        return
    check_state_ancestors(directory)
    metadata = directory.stat()
    if (metadata.st_uid, metadata.st_gid) != owner() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise Error('Unsafe state directory ownership/mode')
    for path in directory.iterdir():
        if path.suffix == '.json' and path.name not in (STATE.name, journal_path().name):
            raise Error(f'Unexpected file in state directory: {path}')
    if not allow_journal and (journal_path().exists() or journal_path().is_symlink()):
        raise Error(f'Interrupted root transaction ({journal_path()}); run recover first')


def check_state_file():
    metadata = STATE.stat()
    if (metadata.st_uid, metadata.st_gid) != owner() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise Error('Unsafe state ownership/mode')


def load_state(any_version=False):
    """Load schema-1 state. Only rollback and upgrade accept a state from another version."""
    check_state_directory()
    data = read(STATE)
    if data is None:
        raise Error('No root setup state; run setup first')
    check_state_file()
    state = check_state(parse_json(data, STATE))
    if not any_version and state['version'] != VERSION:
        if state['version'] == manifest('previous-release')['version']:
            raise Error(f'State was written by companion {state["version"]}, not {VERSION}; '
                        'run upgrade')
        raise Error(f'State was written by companion {state["version"]}, not {VERSION}; '
                    'run rollback, then setup')
    return state


def parse_json(data, path):
    try:
        return json.loads(data)
    except ValueError:
        raise Error(f'Invalid JSON: {path}') from None


def check_state(state):
    """Raise Error unless root state is schema 1 with every field the root commands index."""
    def canonical(path):
        # Absolute, with no '.', '..', repeated or leading '//' components: one spelling per file.
        return type(path) is str and path.startswith('/') and not path.startswith('//') \
            and os.path.normpath(path) == path
    def valid_entry(entry):
        if not isinstance(entry, dict) or 'restore' not in entry or not canonical(entry.get('path')):
            return False
        fields = {'path': str, 'after': str, 'mode': int, 'restore_kind': str}
        if entry['restore'] is not None:
            fields.update(restore=str, restore_mode=int, restore_uid=int, restore_gid=int)
        if not all(type(entry.get(key)) is kind for key, kind in fields.items()):
            return False
        # system-auth alone rolls back to its baseline, never to its pre-setup bytes, and
        # never to metadata a non-root user could write.
        if Path(entry['path']) == SYSTEM_AUTH:
            return (entry['restore_kind'] in BASELINES and entry['restore'] is not None
                    and entry['mode'] == 0o644
                    and safe_pam_metadata(entry['restore_mode'], entry['restore_uid'],
                                          entry['restore_gid']))
        return entry['restore_kind'] == 'original'
    def valid_watched(entry):
        return (isinstance(entry, dict) and canonical(entry.get('path'))
                and all(type(entry.get(key)) is str for key in ('path', 'sha256', 'pambase')))
    fields = {'version': str, 'domain': str, 'user': str, 'admin': bool, 'apply_policy': bool,
              'identity': dict, 'files': list, 'pambase': str, 'watched': list}
    if not isinstance(state, dict):
        raise Error(f'Invalid state file: {STATE}')
    if 'schema' not in state:
        raise Error(f'Invalid state file: {STATE}; it predates this companion. '
                    'Roll back with the companion that wrote it, or restore a snapshot')
    if type(state['schema']) is not int:
        raise Error(f'Invalid state file: {STATE}')
    if state['schema'] != SCHEMA:
        raise Error(f'Unsupported state schema {state["schema"]}: {STATE}')
    if not (all(type(state.get(key)) is kind for key, kind in fields.items())
            and all(valid_entry(entry) for entry in state['files'])
            and sum(Path(entry['path']) == SYSTEM_AUTH for entry in state['files']) == 1
            # Exactly system-login, for the recorded release: an empty list would watch nothing.
            and len(state['watched']) == 1 and valid_watched(state['watched'][0])
            and Path(state['watched'][0]['path']) == SYSTEM_LOGIN
            and state['watched'][0]['pambase'] == state['pambase']):
        raise Error(f'Invalid state file: {STATE}')
    # One entry per managed path, and none watched: rollback writes every managed path.
    managed = [Path(entry['path']) for entry in state['files']]
    if len(set(managed)) != len(managed) or set(managed) & {Path(entry['path'])
                                                            for entry in state['watched']}:
        raise Error(f'Invalid state file: {STATE}')
    return state


def check_state_ancestors(path):
    for parent in path.parents:
        metadata = parent.stat()
        if metadata.st_uid not in (0, os.geteuid()) or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise Error('Unsafe state ancestor ownership/mode')


def serialize_state(state):
    return (json.dumps(state, indent=2) + '\n').encode()


def prepare_state_directory():
    # The state directory holds rollback bytes of root configuration: root only.
    if STATE.parent.is_symlink():
        raise Error('Symlink state directory refused')
    ensure_directory(STATE.parent)
    check_state_ancestors(STATE.parent)
    metadata = STATE.parent.stat()
    if (metadata.st_uid, metadata.st_gid) != owner() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise Error('State directory must be owned by current root and mode 0700')


def save_state(state):
    prepare_state_directory()
    atomic(STATE, serialize_state(state), 0o600)


def snapshot(paths):
    """Each path's file record (bytes, owner and mode) or None, taken before a command's
    checks: transact(expected=...) refuses to start if any has changed since."""
    return {Path(path): file_record(Path(path)) for path in paths}


def root_targets(state):
    """Every file repair or rollback may write or delete."""
    return [*(Path(entry['path']) for entry in state['files']), SYSTEM_LOGIN,
            *map(pacnew, (SYSTEM_AUTH, SYSTEM_LOGIN))]


def planned(path, data, mode):
    """A managed-file change: what to write, and the file's current bytes as its rollback target."""
    before = read(path)
    if before == data:
        return None
    entry = {'path': str(path), 'after': digest(data), 'mode': mode, 'restore': None,
             'restore_mode': None, 'restore_uid': None, 'restore_gid': None,
             'restore_kind': 'original', 'data': data}
    if before is not None:
        metadata = path.stat()
        entry.update(restore=base64.b64encode(before).decode(),
                     restore_mode=stat.S_IMODE(metadata.st_mode),
                     restore_uid=metadata.st_uid, restore_gid=metadata.st_gid)
    return entry


def state_entry(entry):
    return {key: value for key, value in entry.items() if key != 'data'}


def theme_directories(entries):
    directories = set()
    for entry in entries:
        path = Path(entry['path'])
        if path.is_relative_to(COPY_THEME):
            parent = path.parent
            while parent.is_relative_to(COPY_THEME):
                directories.add(parent)
                if parent == COPY_THEME:
                    break
                parent = parent.parent
    return sorted(directories, key=lambda path: (len(path.parts), str(path)))


def checked_theme_directories(entries):
    directories = theme_directories(entries)
    paths = (Path(entry['path']) for entry in entries)
    expected = {path for path in paths if path.is_relative_to(COPY_THEME)}
    if COPY_THEME.is_dir():
        for path in COPY_THEME.rglob('*'):
            if (path.is_symlink() or (path.is_dir() and path not in directories)
                    or (not path.is_dir() and path not in expected)):
                raise Error(f'Unexpected companion theme entry: {path}')
    for directory in directories:
        safe(directory / '.guard')
        if (not directory.is_dir()
                or (directory.stat().st_uid, directory.stat().st_gid) != owner()):
            raise Error(f'Unowned or missing companion theme directory: {directory}')
    return directories


@exclusive
def setup(domain, user, admin=False):
    domain, user = identity(domain, user)
    check_state_directory()
    if read(STATE) is not None:
        state = load_state()
        if (state['domain'], state['user'], state['admin']) != (domain, user, admin):
            raise Error('Existing setup uses a different domain, user or admin setting')
        doctor()
        print('Already set up; no changes')
        return
    # Before any check: every file setup may write or delete, as it is now.
    seen = snapshot([RECOVERY_ACCOUNT, PAM, PIN_PAM, SYSTEM_AUTH, SYSTEM_LOGIN, CONFIG, OVERRIDE,
                     SUDOERS, *map(pacnew, (SYSTEM_AUTH, SYSTEM_LOGIN)), *SDDM_PAMS,
                     *map(pacsave, SDDM_PAMS)])
    native_sddm_contract()
    require_no_autologin()
    version = installed_pambase()
    release = pambase_release(version)
    for path in (SYSTEM_AUTH, SYSTEM_LOGIN):
        if read(pacnew(path)) is not None or pacnew(path).is_symlink():
            raise Error(f'Pending pambase update: {pacnew(path)}; merge it first')
    kind, baseline_auth, pristine_login = pambase_baseline(release)
    sddm_pams = omarchy_sddm_restores()
    principal = administrator(user)
    recovery_parent()
    if read(RECOVERY_ACCOUNT) is not None:
        raise Error(f'{RECOVERY_ACCOUNT} exists but is not managed')
    configuration_scope(domain, user)
    changes = []
    def add(path, data, mode=0o644):
        entry = planned(path, data, mode)
        if entry:
            changes.append(entry)
    add(RECOVERY_ACCOUNT, identity_file(principal))
    original_pam = stock_pam()
    if read(PAM) is not None:
        raise Error(f'{PAM} exists but is not managed')
    add(PAM, patch_source('lock-pam', original_pam))
    if read(PIN_PAM) is not None:
        raise Error(f'{PIN_PAM} exists but is not managed')
    add(PIN_PAM, PIN_PAM_CONTENT)
    # system-auth rolls back to its baseline, even when setup started from aad-tool's
    # layout, so rollback can never bring that layout back.
    auth = planned(SYSTEM_AUTH, patched_system_auth(release, kind, baseline_auth), 0o644)
    auth.update(restore=base64.b64encode(baseline_auth).decode(), restore_kind=kind)
    changes.append(auth)
    # Validate source, but never write Omarchy's packaged lock QML.
    lock = read(LOCK)
    if lock is None:
        raise Error('Missing Omarchy lock source')
    patch_source('lock-conversation', lock)
    patch_source('lock-view', read(LOCK.parent / 'LockView.qml') or b'')
    add(CONFIG, managed_config(read(CONFIG), domain, user), 0o644)
    if COPY_THEME.exists() or COPY_THEME.is_symlink():
        raise Error(f'{COPY_THEME} exists but is not managed')
    if THEME.is_symlink() or not THEME.is_dir():
        raise Error('Expected original SDDM theme directory')
    theme_files = sorted(THEME.rglob('*'))
    if not theme_files:
        raise Error('Empty original SDDM theme')
    for source in theme_files:
        if source.is_symlink():
            raise Error('Symlink in original SDDM theme refused')
        if source.is_dir():
            continue
        content = read(source)
        if source.name == 'Main.qml':
            content = patch_source('sddm-conversation', content)
        add(COPY_THEME / source.relative_to(THEME), content)
    if not any(p.name == 'Main.qml' for p in theme_files):
        raise Error('Original theme Main.qml missing')
    if read(OVERRIDE) is not None:
        raise Error(f'{OVERRIDE} exists but is not managed')
    add(OVERRIDE, OVERRIDE_CONTENT)
    if admin:
        if read(SUDOERS) is not None:
            raise Error(f'{SUDOERS} exists but is not managed')
        content = admin_rule(principal)
        validate_sudo_rule(content)
        add(SUDOERS, content, 0o440)
    # All source/configuration preflight finished before touching guest files.
    state = {'schema': SCHEMA, 'version': VERSION, 'domain': domain, 'user': user,
             'admin': admin, 'identity': principal, 'apply_policy': False,
             'pambase': version,
             'files': [state_entry(entry) for entry in changes],
             'watched': [{'path': str(SYSTEM_LOGIN), 'sha256': digest(pristine_login),
                          'pambase': version}]}
    files = [(Path(entry['path']), entry['data'], entry['mode'], *owner()) for entry in changes]
    if read(SYSTEM_LOGIN) != pristine_login:
        # Undo aad-tool's system-login lines. Watched, not owned: rollback never writes it.
        metadata = SYSTEM_LOGIN.stat()
        files.append((SYSTEM_LOGIN, pristine_login, stat.S_IMODE(metadata.st_mode),
                      metadata.st_uid, metadata.st_gid))
    # Restored, not owned: rollback leaves Omarchy's SDDM PAM edits in place.
    files += sddm_pams
    # The theme copy didn't exist when setup checked: none of its files may appear since.
    seen.update({Path(entry['path']): None for entry in changes
                 if Path(entry['path']).is_relative_to(COPY_THEME)})
    # A failed setup runs from a live session, so its undo skips the locker check.
    transact('setup', state, files,
             [(directory, 0o755) for directory in theme_directories(changes)],  # Public greeter assets.
             expected=seen)
    print('Root setup complete.')


@exclusive
def policy(enable):
    """Explicit local configuration only; no service or tenant operations."""
    state = load_state()
    seen = snapshot([CONFIG])
    issues = root_issues(state)
    if issues:
        raise Error('\n'.join(issues))
    if state['apply_policy'] == enable:
        print(f'apply_policy is already {str(enable).lower()}; no changes')
        return
    # Generated configuration loads last. Never rewrite it or ignore conflicts.
    validate_scope(read_generated_config(), state['domain'], state['user'], enable)
    before = read(CONFIG)
    lines = before.decode().splitlines(keepends=True)
    replaced = 0
    for index, section, key, _ in ini_lines(before.decode()):
        line = lines[index]
        if (section == 'global' and key == 'apply_policy'
                and (match := re.match(r'^(\s*apply_policy\s*=\s*)(true|false)(.*)$', line, re.I))):
            lines[index] = match[1] + str(enable).lower() + match[3] + line[match.end():]
            replaced += 1
    if replaced != 1:
        raise Error('Expected exactly one managed global apply_policy key')
    after = ''.join(lines).encode()
    # Also reject scoped overrides in other sections before changing anything.
    managed_config(after, state['domain'], state['user'], enable)
    entry = next((item for item in state['files'] if Path(item['path']) == CONFIG), None)
    if entry is None:
        state['files'].append(state_entry(planned(CONFIG, after, 0o644)))
    else:
        entry['after'] = digest(after)  # Preserve the original setup rollback bytes.
    state['apply_policy'] = enable
    transact('policy', state, [(CONFIG, after, 0o644, *owner())], verify=verify_root,
             expected=seen)
    print(f'Set apply_policy = {str(enable).lower()}.')


def stock_pam():
    data = read(STOCK_PAM)
    if data is None or digest(data) != manifest('lock-pam')['before']:
        raise Error(f'{STOCK_PAM} is not the supported pristine version')
    return data


def pacnew(path):
    return path.with_name(path.name + '.pacnew')


def installed_pambase():
    """The installed pambase version, from pacman's local database."""
    try:
        result = run(['/usr/bin/pacman', '-Q', 'pambase'], text=True)
    except (OSError, subprocess.SubprocessError):
        raise Error('pambase is not installed') from None
    match = re.fullmatch(r'pambase ([0-9A-Za-z._+:-]+)', result.stdout.strip())
    if not match:
        raise Error('Unrecognised pambase package version')
    return match[1]


def pambase_release(version):
    """The running package's hashes and patches for one pambase release: system-login,
    aad-tool's system-login, and each system-auth baseline with its aad-tool output and patch."""
    entry = manifest('pambase').get(version)
    baselines = entry.get('baselines') if isinstance(entry, dict) else None
    if (not isinstance(baselines, dict) or set(baselines) != set(BASELINES)
            or any(type(entry.get(key)) is not str for key in ('system-login', 'aad-tool-system-login'))
            or any(not isinstance(baseline, dict)
                   or any(type(baseline.get(key)) is not str for key in ('system-auth', 'aad-tool', 'patch'))
                   for baseline in baselines.values())):
        raise Error(f'Unsupported pambase {version}: review and merge '
                    f'{pacnew(SYSTEM_AUTH)} by hand')
    return entry


def pacsave(path):
    return path.with_name(path.name + '.pacsave')


def omarchy_sddm_pam(path, data):
    """data with the edits Omarchy's installer makes to SDDM's PAM files, as its sed
    would: install/login/sddm.sh drops gnome-keyring's auth and password lines, and
    install/config/increase-lockout-limit.sh moves sddm-autologin's faillock reset to
    after pam_permit."""
    lines = data.splitlines(keepends=True)
    if path.name == 'sddm':
        return b''.join(line for line in lines
                        if not re.search(rb'-(auth|password).*pam_gnome_keyring\.so', line))
    result = []
    for line in lines:
        if re.search(rb'pam_faillock\.so (preauth|authsucc)', line):
            continue
        result.append(line)
        if re.search(rb'auth.*pam_permit\.so', line):
            result.append(b'auth        required    pam_faillock.so authsucc\n')
    return b''.join(result)


def omarchy_sddm_restores():
    """Writes that put back Omarchy's SDDM PAM edits after the native package replaced
    sddm: each .pacsave must be exactly Omarchy's edit of the package's file, and the
    installed file must still be the package's (an administrator's later edit to a line
    Omarchy's edit drops would otherwise compare equal and be lost)."""
    writes = []
    for path in SDDM_PAMS:
        saved = read(pacsave(path))
        if saved is None:
            continue
        current = read(path)
        if current is None or digest(current) != NATIVE_SDDM_PAMS[path.name]:
            raise Error(f'{path} is not sddm-himmelblau {NATIVE_VERSION} as installed, so '
                        f'{pacsave(path)} cannot be checked against it; review and merge it '
                        'by hand, then remove it')
        if omarchy_sddm_pam(path, current) != saved:
            raise Error(f"{pacsave(path)} is not Omarchy's edit of {path}; review and merge "
                        'it by hand, then remove it')
        require_safe_pam_file(path)
        metadata = path.stat()
        writes += [(path, saved, stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid),
                   (pacsave(path), None, 0, 0, 0)]
    return writes


def omarchy_tuned(data):
    """data with Omarchy's lockout substitutions applied, as its sed would."""
    lines = []
    for line in data.splitlines(keepends=True):
        body = line.rstrip(b'\n')
        end = line[len(body):]
        for pattern, replacement in OMARCHY_LOCKOUT:
            match = pattern.fullmatch(body)
            if match:
                body = match.expand(replacement)
        lines.append(body + end)
    return b''.join(lines)


def without_aad_tool(data):
    """data with each of aad-tool's four lines removed once; None unless each occurs once."""
    lines = data.splitlines(keepends=True)
    if any(lines.count(line) != 1 for line in AAD_TOOL_LINES):
        return None
    return b''.join(line for line in lines if line not in AAD_TOOL_LINES)


def safe_pam_metadata(mode, uid, gid):
    """Owner and mode a system PAM file (or its rollback target) must have: the companion's
    owner (root) for user and group, no group or other write, no special bits."""
    return (uid, gid) == owner() and not mode & 0o7022


def require_safe_pam_file(path):
    """Refuse a system PAM file, or a .pacnew about to become one, that a non-root user could
    change: its bytes alone prove nothing about who can rewrite it."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise Error(f'Missing {path}') from None
    if (not stat.S_ISREG(metadata.st_mode)
            or not safe_pam_metadata(stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid)):
        raise Error(f'Unsafe ownership/mode on {path}: it must be a regular file owned by root, '
                    'writable only by root')
    no_extended_acl(path)
    # Whoever can write a parent directory can replace the file, and a default ACL on its
    # directory would be inherited by the replacement setup writes there.
    try:
        check_state_ancestors(path)
    except Error:
        raise Error(f'Unsafe ownership/mode on a parent directory of {path}: each must be '
                    'owned by root and writable only by root') from None
    no_extended_acl(path.parent)
    for parent in path.parents[1:]:
        no_extended_acl(parent, ('system.posix_acl_access',))


def pambase_baseline(release):
    """(baseline kind, system-auth baseline bytes, pristine system-login bytes), from the
    files as they are now. Accepts a baseline system-auth with pristine system-login, or
    aad-tool's output on them: in both files (4.0.8), or in system-auth alone (aad-tool
    once it skips a file that includes another configured one). aad-tool output is proved
    by hash, and by stripping its lines back to the baseline and pristine hashes."""
    auth, login = read(SYSTEM_AUTH), read(SYSTEM_LOGIN)
    if auth is None or login is None:
        raise Error('Missing pambase PAM files')
    # setup keeps system-auth's metadata as its rollback target and system-login's as is.
    require_safe_pam_file(SYSTEM_AUTH)
    require_safe_pam_file(SYSTEM_LOGIN)
    baselines = release['baselines']
    if digest(login) == release['system-login']:
        for kind in BASELINES:
            if digest(auth) == baselines[kind]['system-auth']:
                return kind, auth, login
            if digest(auth) == baselines[kind]['aad-tool']:
                auth = without_aad_tool(auth)
                if auth is not None and digest(auth) == baselines[kind]['system-auth']:
                    return kind, auth, login
                break
    elif digest(login) == release['aad-tool-system-login']:
        for kind in BASELINES:
            if digest(auth) == baselines[kind]['aad-tool']:
                auth, login = without_aad_tool(auth), without_aad_tool(login)
                if (auth is not None and login is not None
                        and digest(auth) == baselines[kind]['system-auth']
                        and digest(login) == release['system-login']):
                    return kind, auth, login
                break
    raise Error(f'{SYSTEM_AUTH} and {SYSTEM_LOGIN} must both be pambase as installed (with or '
                "without Omarchy's lockout tuning), or exactly aad-tool configure-pam "
                'output on it; refusing')


def patched_system_auth(release, kind, baseline):
    if digest(baseline) != release['baselines'][kind]['system-auth']:
        raise Error(f'Unsupported {SYSTEM_AUTH} baseline')
    return patch_source(release['baselines'][kind]['patch'], baseline)


def system_auth_entry(state):
    return next(entry for entry in state['files'] if Path(entry['path']) == SYSTEM_AUTH)


def pambase_upgrade(state, installed):
    """After a pambase upgrade, the installed release and the new system-auth baseline of
    the state's kind (bytes and file record). pacman leaves the new copy as a .pacnew; it
    keeps our file without one only if the package's copy didn't change. An Omarchy
    baseline is the .pacnew with Omarchy's lockout tuning. Hashes decide; the .pacnew
    name alone proves nothing."""
    release = pambase_release(installed)
    kind = system_auth_entry(state)['restore_kind']
    wanted = release['baselines'][kind]['system-auth']
    hint = f'pambase {installed}: review and merge {pacnew(SYSTEM_AUTH)} by hand'
    if digest(read(SYSTEM_LOGIN) or b'') != release['system-login']:
        raise Error(f'{SYSTEM_LOGIN} is not pristine {hint}')
    require_safe_pam_file(SYSTEM_LOGIN)
    record = file_record(pacnew(SYSTEM_AUTH))
    if record is not None:
        # Its owner and mode become system-auth's on rollback.
        require_safe_pam_file(pacnew(SYSTEM_AUTH))
        data = base64.b64decode(record['data'])
        if digest(data) != release['baselines']['pambase']['system-auth']:
            raise Error(f'{pacnew(SYSTEM_AUTH)} is not pristine {hint}')
        if kind == 'omarchy':
            data = omarchy_tuned(data)
            record = dict(record, data=b64(data))
            if digest(data) != wanted:
                raise Error(f"Omarchy's lockout tuning of {pacnew(SYSTEM_AUTH)} is not the "
                            f'supported {hint}')
    else:
        entry = system_auth_entry(state)
        record = {'data': entry['restore'], 'mode': entry['restore_mode'],
                  'uid': entry['restore_uid'], 'gid': entry['restore_gid']}
        data = base64.b64decode(record['data'])
        if digest(data) != wanted:
            raise Error(f'No {pacnew(SYSTEM_AUTH)}, and the stored {SYSTEM_AUTH} rollback '
                        f'target is not {hint}')
    return release, data, record


def pambase_issues(state):
    issues = []
    try:
        installed = installed_pambase()
        if installed != state['pambase']:
            issues.append(f'pambase {installed} is installed, but setup verified '
                          f'{state["pambase"]}; run repair')
        release = pambase_release(state['pambase'])
        entry = system_auth_entry(state)
        if (digest(base64.b64decode(entry['restore']))
                != release['baselines'][entry['restore_kind']]['system-auth']):
            issues.append(f'Stored {SYSTEM_AUTH} rollback target is not its {entry["restore_kind"]} '
                          'baseline')
        if state['watched'][0]['sha256'] != release['system-login']:
            issues.append(f'Stored {SYSTEM_LOGIN} hash is not pristine pambase {state["pambase"]}')
    except Error as error:
        issues.append(str(error))
    for path in (SYSTEM_AUTH, SYSTEM_LOGIN):
        try:
            require_safe_pam_file(path)
        except Error as error:
            issues.append(str(error))
    for entry in state['watched']:
        if digest(read(Path(entry['path'])) or b'') != entry['sha256']:
            issues.append(f'Watched file changed: {entry["path"]} (was aad-tool configure-pam run?)')
    if read(pacnew(SYSTEM_AUTH)) is not None:
        issues.append(f'Pending pambase update: {pacnew(SYSTEM_AUTH)}; run repair')
    if read(pacnew(SYSTEM_LOGIN)) is not None:
        issues.append(f'Pending pambase update: {pacnew(SYSTEM_LOGIN)}; {SYSTEM_LOGIN} must be '
                      'pristine pambase: merge it by hand, then run doctor')
    for path in SDDM_PAMS:
        if read(pacsave(path)) is not None:
            issues.append(f"{pacsave(path)} exists: replacing sddm set aside Omarchy's edits "
                          f'to {path}; restore them by hand (docs/sddm.md), then run doctor')
    return issues


def require_complete_configuration(domain, user, apply_policy=False):
    current = read(CONFIG)
    if managed_config(current, domain, user, apply_policy) != current:
        raise Error(f'{CONFIG} is missing managed keys')


def native_payload_contract():
    safe(NATIVE_PAYLOAD)
    check_state_ancestors(NATIVE_PAYLOAD)
    metadata = NATIVE_PAYLOAD.stat()
    if (metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o644
            or metadata.st_size > 1024 * 1024):
        raise Error('Unsafe independent native payload manifest')
    no_extended_acl(NATIVE_PAYLOAD)
    payload = json.loads(read(NATIVE_PAYLOAD))
    if not isinstance(payload, dict):
        raise Error('Invalid native payload payload')
    # A release pin names its published artifact. A local (AUR) build has none:
    # tools/pin-installed-native.py marks it "build": "local" with a null hash.
    artifact = payload.get('artifact_sha256')
    if 'build' in payload:
        artifact_ok = payload['build'] == 'local' and artifact is None
    else:
        artifact_ok = isinstance(artifact, str) and re.fullmatch(r'[0-9a-f]{64}', artifact)
    if (payload.get('package') != 'sddm-himmelblau' or payload.get('version') != NATIVE_VERSION
            or not artifact_ok
            or not isinstance(payload.get('source_commit'), str)
            or not re.fullmatch(r'[0-9a-f]{40}', payload['source_commit'])):
        raise Error('Unsupported independent native payload provenance')
    files = payload.get('files')
    if not isinstance(files, dict) or not {str(path) for path in NATIVE_EXECUTABLES}.issubset(files):
        raise Error('Incomplete native authentication executable manifest')
    component_files = set()
    for directory in NATIVE_COMPONENTS:
        safe(directory / '.guard')
        if (not directory.is_dir() or directory.stat().st_uid != os.geteuid()
                or stat.S_IMODE(directory.stat().st_mode) != 0o755):
            raise Error('Unsafe or missing native QML component directory')
        names = set()
        for path in directory.rglob('*'):
            safe(path / '.guard' if path.is_dir() else path)
            if path.is_dir():
                if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022:
                    raise Error('Unsafe native component directory')
            else:
                names.add(str(path.relative_to(directory)))
                component_files.add(str(path))
        if 'qmldir' not in names or not any(name.endswith('.qml') for name in names):
            raise Error('Native component tree lacks qmldir or QML payload')
    if set(files) != {str(path) for path in NATIVE_EXECUTABLES} | component_files:
        raise Error('Native component manifest differs from complete installed file set')
    for name, expected in files.items():
        path = Path(name)
        in_components = any(path.is_relative_to(parent) for parent in NATIVE_COMPONENTS)
        if (not path.is_absolute() or '..' in path.parts
                or (path not in NATIVE_EXECUTABLES and not in_components)):
            raise Error('Unexpected native payload path')
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise Error('Invalid native payload hash')
        safe(path)
        check_state_ancestors(path)
        no_extended_acl(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as source:
            metadata = os.fstat(source.fileno())
            mode = stat.S_IMODE(metadata.st_mode)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or mode & 0o022 or (path in NATIVE_EXECUTABLES and mode != 0o755)):
                raise Error('Unsafe native authentication payload metadata')
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if actual != expected:
            raise Error('Native authentication payload differs from companion release pin')


def autologin_sources():
    """SDDM configuration files that set a non-empty [Autologin] User=.

    Mirrors SDDM 0.21's ConfigReader: every regular file in each configuration
    directory, then /etc/sddm.conf; each line is cut at its first '#' and trimmed; a
    line containing '=' is an assignment, otherwise '[name]' starts a section. Any file
    setting a user counts, whatever later files say, so the check never depends on
    SDDM's file ordering."""
    files = []
    for directory in SDDM_CONF_DIRS:
        if directory.is_dir():
            files += sorted(path for path in directory.iterdir()
                            if path.is_file() or path.is_symlink())
    found = []
    for path in files + [SDDM_CONF]:
        data = read(path)  # A symlinked configuration file is refused, not skipped.
        if data is None:
            continue
        section = 'General'
        for line in data.decode('utf-8', errors='replace').splitlines():
            line = line.strip().split('#', 1)[0].strip()
            if '=' in line:
                name, value = (part.strip() for part in line.split('=', 1))
                if section == 'Autologin' and name == 'User' and value:
                    found.append(path)
                    break
            elif line.startswith('[') and line.endswith(']'):
                section = line[1:-1]
    return found


def require_no_autologin():
    # Autologin skips the sign-in screen (bypassing the Entra user) and leaves the
    # session's lock screen without a faillock tally, so it is unsupported.
    sources = autologin_sources()
    if sources:
        raise Error('SDDM autologin is configured in ' + ', '.join(map(str, sources))
                    + '; it is unsupported. Remove its [Autologin] User= line, then retry')


def native_sddm_contract():
    # Companion-owned release pin is independent of the native package metadata.
    native_payload_contract()
    # This verifies installed artifacts, never the running daemon.
    try:
        result = run(['/usr/bin/pacman', '-Q', 'sddm-himmelblau'], text=True)
    except (OSError, subprocess.SubprocessError):
        raise Error(f'sddm-himmelblau {NATIVE_VERSION} is not installed') from None
    if result.stdout.strip() != f'sddm-himmelblau {NATIVE_VERSION}':
        raise Error('Unsupported native SDDM package version')
    if read(SDDM_PIN_PAM) != SDDM_PIN_PAM_CONTENT:
        raise Error(f'Native SDDM PIN service is missing or changed: {SDDM_PIN_PAM}')
    metadata = SDDM_PIN_PAM.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o644:
        raise Error('Unsafe native SDDM PIN service ownership/mode')


def verify_root(state):
    """Post-commit check for policy and repair; any issue undoes the operation."""
    issues = root_issues(state, in_transaction=True)
    if issues:
        raise Error('\n'.join(issues))


def root_issues(state, *, in_transaction=False):
    issues = []
    try:
        check_state_directory(allow_journal=in_transaction)
        check_identity(state)
        check_admin_identity(state)
        configuration_scope(state['domain'], state['user'], state['apply_policy'])
        require_complete_configuration(state['domain'], state['user'], state['apply_policy'])
        stock_pam()
        native_sddm_contract()
        require_no_autologin()
        supported_source_hash('sddm-conversation', read(THEME / 'Main.qml') or b'')
        if digest(read(COPY_THEME / 'Main.qml') or b'') != manifest('sddm-conversation')['after']:
            raise Error('SDDM conversation theme needs explicit root repair')
    except Error as error:
        issues.append(str(error))
    for entry in state['files']:
        actual = read(Path(entry['path']))
        path = Path(entry['path'])
        if (actual is None or digest(actual) != entry['after']
                or stat.S_IMODE(path.stat().st_mode) != entry['mode']
                or (path.stat().st_uid, path.stat().st_gid) != owner()):
            issues.append(f'Managed file drift: {entry["path"]}')
    try:
        for directory in checked_theme_directories(state['files']):
            if stat.S_IMODE(directory.stat().st_mode) != 0o755:
                issues.append(f'Companion theme directory must be 0755: {directory}')
    except Error as error:
        issues.append(str(error))
    issues.extend(pambase_issues(state))
    if read(PIN_PAM) != PIN_PAM_CONTENT:
        issues.append('PIN replacement PAM service missing/drifted; explicit repair required')
    pam = read(PAM)
    if pam is None or digest(pam) != manifest('lock-pam')['after']:
        issues.append(f'Lock PAM drift: {PAM}')
    for name, path in [('lock-conversation', LOCK), ('lock-view', LOCK.parent / 'LockView.qml')]:
        try:
            data = read(path)
            if data is None:
                raise Error('Missing source')
            supported_source_hash(name, data)
        except Error:
            issues.append(f'Unsupported current source: {path}')
    return issues


def doctor():
    state = load_state()
    issues = root_issues(state)
    if issues:
        raise Error('\n'.join(issues))
    print('Managed root files and upstream sources match.')


@exclusive
def repair():
    """Rewrite managed files the companion can regenerate exactly, within one companion
    version; also move system-auth to a supported pambase upgrade."""
    state = load_state()
    seen = snapshot(root_targets(state))
    native_sddm_contract()
    principal = saved_identity(state)
    recovery_parent()
    check_admin_identity(state)
    configuration_scope(state['domain'], state['user'], state['apply_policy'])
    require_complete_configuration(state['domain'], state['user'], state['apply_policy'])
    desired_pam = patch_source('lock-pam', stock_pam())
    theme = patch_source('sddm-conversation', read(THEME / 'Main.qml') or b'')
    theme_path = COPY_THEME / 'Main.qml'
    theme_entry = next((entry for entry in state['files'] if Path(entry['path']) == theme_path), None)
    if theme_entry is None or theme_entry['after'] != digest(theme):
        raise Error('Unsupported managed SDDM theme state')
    installed = installed_pambase()
    if installed != state['pambase']:
        # Accept a pambase upgrade only as the installed release's exact pristine files.
        release, baseline_auth, record = pambase_upgrade(state, installed)
        upgrade = True
    else:
        release = pambase_release(installed)
        baseline_auth = base64.b64decode(system_auth_entry(state)['restore'])
        record, upgrade = None, False
        if read(pacnew(SYSTEM_AUTH)) is not None:
            raise Error(f'{pacnew(SYSTEM_AUTH)} exists but pambase is unchanged; review it by hand')
    if digest(read(SYSTEM_LOGIN) or b'') != release['system-login']:
        raise Error(f'{SYSTEM_LOGIN} is not pristine pambase {installed}; '
                    'repair never rewrites it (run rollback, then setup)')
    require_safe_pam_file(SYSTEM_LOGIN)
    if read(SYSTEM_AUTH) is not None:
        require_safe_pam_file(SYSTEM_AUTH)
    desired_auth = patched_system_auth(release, system_auth_entry(state)['restore_kind'],
                                       baseline_auth)
    repairs = []
    # Full file/source/configuration preflight precedes journal or managed writes.
    for entry in state['files']:
        path = Path(entry['path'])
        data = read(path)
        if data is not None:
            metadata = path.stat()
            allowed = [(entry['mode'], *owner())]
            if path == SYSTEM_AUTH and not upgrade and data == baseline_auth:
                # Put back as setup found it: the baseline's bytes with their own metadata.
                allowed.append((entry['restore_mode'], entry['restore_uid'], entry['restore_gid']))
            if (stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid) not in allowed:
                raise Error(f'Repair refuses permission drift: {path}')
        if (path == RECOVERY_ACCOUNT and data is None
                and entry['after'] == digest(identity_file(principal))):
            repairs.append((path, identity_file(principal), entry['mode'], *owner()))
            continue
        if path == SYSTEM_AUTH:
            # Same release: our output, or the baseline to re-patch. Upgrade: our
            # output for the old release, replaced by the new release's patch.
            current = None if data is None else digest(data)
            allowed = (entry['after'],) if upgrade else (entry['after'], digest(baseline_auth))
            if current != digest(desired_auth):
                if current not in allowed:
                    raise Error(f'Repair refuses unsupported managed drift: {path}')
                repairs.append((path, desired_auth, entry['mode'], *owner()))
            entry['after'] = digest(desired_auth)
            continue
        if data is not None and digest(data) == entry['after']:
            continue
        if path == PIN_PAM and data is None and entry['after'] == digest(PIN_PAM_CONTENT):
            repairs.append((path, PIN_PAM_CONTENT, entry['mode'], *owner()))
            continue
        if path == PAM and (data is None or digest(data) == manifest('lock-pam')['before']):
            if digest(desired_pam) == entry['after']:
                repairs.append((path, desired_pam, entry['mode'], *owner()))
                continue
        raise Error(f'Repair refuses unsupported managed drift: {path}')
    patch_source('lock-conversation', read(LOCK) or b'')
    patch_source('lock-view', read(LOCK.parent / 'LockView.qml') or b'')
    directories = checked_theme_directories(state['files'])
    if upgrade:
        system_auth_entry(state).update(restore=record['data'], restore_mode=record['mode'],
                                        restore_uid=record['uid'], restore_gid=record['gid'])
        state['pambase'] = installed
        state['watched'] = [{'path': str(SYSTEM_LOGIN), 'sha256': release['system-login'],
                             'pambase': installed}]
        if read(pacnew(SYSTEM_AUTH)) is not None:
            repairs.append((pacnew(SYSTEM_AUTH), None, 0, 0, 0))
    transact('repair', state, repairs, [(directory, 0o755) for directory in directories],
             verify=verify_root, expected=seen)
    print('Root repair complete.')


@exclusive
def upgrade():
    """Move a setup written by the previous companion release to this one, in place.

    Accepts state from exactly previous-release, and only while every managed file is
    still what that release wrote (bytes, owner and mode). Rewrites only the outputs that
    may change between releases, each from the hash previous-release records for it; every
    other managed file must already be what this release would write. Restore targets are
    kept, so rollback still restores what setup found."""
    previous = manifest('previous-release')
    state = load_state(any_version=True)
    if state['version'] == VERSION:
        doctor()
        print(f'Already at companion {VERSION}; no changes')
        return
    if state['version'] != previous['version']:
        raise Error(f'State was written by companion {state["version"]}; upgrade accepts only '
                    f'{previous["version"]}. Run rollback, then setup')
    seen = snapshot(root_targets(state))
    require_no_live_locker()
    native_sddm_contract()
    require_no_autologin()
    principal = check_identity(state)
    check_admin_identity(state)
    configuration_scope(state['domain'], state['user'], state['apply_policy'])
    require_complete_configuration(state['domain'], state['user'], state['apply_policy'])
    issues = pambase_issues(state)
    if issues:
        # repair cannot run on another version's state: nothing here may be fixed in place.
        raise Error('\n'.join([*issues, f'Upgrade needs the {previous["version"]} setup '
                                          'unchanged; run rollback, then setup']))
    patch_source('lock-conversation', read(LOCK) or b'')
    patch_source('lock-view', read(LOCK.parent / 'LockView.qml') or b'')
    auth = system_auth_entry(state)
    # Outputs that may change between releases, and what this release writes for each.
    pinned = {COPY_THEME / 'Main.qml': ('sddm-conversation',
                                        patch_source('sddm-conversation', read(THEME / 'Main.qml') or b'')),
              PAM: ('lock-pam', patch_source('lock-pam', stock_pam()))}
    # Outputs that must not: the same inputs give the same bytes in both releases.
    fixed = {RECOVERY_ACCOUNT: identity_file(principal), PIN_PAM: PIN_PAM_CONTENT,
             OVERRIDE: OVERRIDE_CONTENT,
             SYSTEM_AUTH: patched_system_auth(pambase_release(state['pambase']), auth['restore_kind'],
                                              base64.b64decode(auth['restore']))}
    if state['admin']:
        fixed[SUDOERS] = admin_rule(principal)
    recorded = [Path(entry['path']) for entry in state['files']]
    if (set(pinned) | set(fixed)) - set(recorded) or (not state['admin'] and SUDOERS in recorded):
        raise Error('Unsupported managed file set; run rollback, then setup')
    writes = []
    for entry in state['files']:
        path = Path(entry['path'])
        current = file_record(path)
        if (current is None or digest(base64.b64decode(current['data'])) != entry['after']
                or (current['mode'], current['uid'], current['gid']) != (entry['mode'], *owner())):
            raise Error(f'Upgrade refuses managed drift: {path}; run rollback, then setup')
        if path in pinned:
            name, data = pinned[path]
            if entry['after'] != previous[name]:
                raise Error(f'Upgrade refuses: {path} is not what companion '
                            f'{previous["version"]} wrote; run rollback, then setup')
            if digest(data) != entry['after']:
                writes.append((path, data, entry['mode'], *owner()))
                entry['after'] = digest(data)  # Keep the restore target: absence, for both.
        elif path in fixed:
            if digest(fixed[path]) != entry['after']:
                raise Error(f'Upgrade cannot change {path}; run rollback, then setup')
        elif path != CONFIG and not path.is_relative_to(COPY_THEME):
            raise Error(f'Upgrade refuses unknown managed file: {path}')
    for directory in checked_theme_directories(state['files']):
        if stat.S_IMODE(directory.stat().st_mode) != 0o755:
            raise Error(f'Companion theme directory must be 0755: {directory}; '
                        'run rollback, then setup')
    state['version'] = VERSION
    transact('upgrade', state, writes, verify=verify_root, expected=seen)
    print(f'Root setup upgraded from companion {previous["version"]} to {VERSION}. '
          'Reboot, then update the lock plugin.')



# Transactions. Every root change (setup, policy, repair, rollback) runs through
# transact(): one journal, written once before the first change, records each
# target's exact before (bytes or absence, owner, mode) and after (hash or
# absence, owner, mode), and the state file's before bytes and after hash.
# recover() and a failed operation's inline undo share restore_journal(), which
# checks every target against before or after, then puts every one back.

OPERATION_LABELS = {'setup': 'Setup', 'policy': 'Policy change', 'repair': 'Repair',
                    'upgrade': 'Upgrade', 'rollback': 'Rollback'}


def b64(data):
    return base64.b64encode(data).decode() if data is not None else None


def file_record(path):
    path = safe(path)
    data = read(path)
    if data is None:
        return None
    metadata = path.stat()
    return {'data': b64(data), 'mode': stat.S_IMODE(metadata.st_mode),
            'uid': metadata.st_uid, 'gid': metadata.st_gid}


def directory_record(path):
    safe(path / '.guard')
    if not path.exists():
        return None
    if not path.is_dir():
        raise Error(f'Expected directory: {path}')
    metadata = path.stat()
    return {'mode': stat.S_IMODE(metadata.st_mode), 'uid': metadata.st_uid, 'gid': metadata.st_gid}


def make_directory(path, mode):
    # Clear the umask so the directory is created with its exact mode: no crash window
    # leaves one that matches neither its journalled before nor after value.
    safe(path / '.guard')
    previous = os.umask(0)
    try:
        path.mkdir(mode=mode)
    finally:
        os.umask(previous)
    fsync_directory(path.parent)
    no_extended_acl(path)


def depth(entry):
    return len(Path(entry['path']).parts)


def transact(operation, state, files, directories=(), verify=None, expected=None):
    """Apply one journalled root change.

    files: (path, data or None to delete, mode, uid, gid), applied in order.
    directories: (path, mode or None to remove); created and chmodded before the
    files, removed (deepest first, must be empty) after them.
    state: the new state, or None to delete the state file.
    verify: called after the commit; an exception undoes the whole operation.
    expected: snapshot() taken before the operation's checks; any change since is refused.
    """
    check_state_directory()
    prepare_state_directory()
    old_state = read(STATE)
    if old_state is not None:
        check_state_file()
    new_state = serialize_state(state) if state is not None else None
    uid, gid = owner()
    # Everything this change was checked against must still be as it was.
    for path, record in (expected or {}).items():
        if file_record(path) != record:
            raise Error(f'{path} changed while {operation} was checking it; nothing was '
                        f'written: review it, then run {operation} again')
    refuse_acl_loss([STATE, *(path for path, *_ in files)])
    refuse_directory_acl([path for path, _ in directories])
    # Whatever this change or its undo may create (each file is rewritten or recreated on
    # undo, each directory recreated) must not inherit a default ACL.
    refuse_inherited_acl([STATE, journal_path(), *(path for path, *_ in files),
                          *(path for path, _ in directories)])
    journal = {
        'journal': JOURNAL_FORMAT, 'operation': operation,
        'state_before': b64(old_state),
        'state_after': digest(new_state) if new_state is not None else None,
        'files': [{'path': str(path), 'before': file_record(path),
                   'after': None if data is None else
                   {'sha256': digest(data), 'mode': mode, 'uid': file_uid, 'gid': file_gid}}
                  for path, data, mode, file_uid, file_gid in files],
        'directories': [{'path': str(path), 'before': directory_record(path),
                         'after': None if mode is None else {'mode': mode, 'uid': uid, 'gid': gid}}
                        for path, mode in directories],
    }
    create_exclusive(journal_path(), (json.dumps(journal, indent=2) + '\n').encode(), 0o600)
    try:
        for entry in sorted(journal['directories'], key=depth):
            if entry['after'] is None:
                continue
            path = Path(entry['path'])
            if entry['before'] is None:
                make_directory(path, entry['after']['mode'])
            os.chown(path, entry['after']['uid'], entry['after']['gid'])
            path.chmod(entry['after']['mode'])
            fsync_directory(path)
        for path, data, mode, file_uid, file_gid in files:
            if data is None:
                unlink_durable(path, missing_ok=True)
            else:
                atomic(path, data, mode, file_uid, file_gid)
        for entry in sorted(journal['directories'], key=depth, reverse=True):
            if entry['after'] is None and Path(entry['path']).exists():
                rmdir_durable(Path(entry['path']))
        if new_state is not None:
            save_state(state)
        else:
            unlink_durable(STATE, missing_ok=True)
        if verify is not None:
            verify(state)
    except Exception:
        label = OPERATION_LABELS[operation]
        try:
            restore_journal(journal)
        except Exception:
            raise Error(f'{label} failed and could not be undone; run recover '
                        f'(journal: {journal_path()})') from None
        unlink_durable(journal_path())
        raise Error(f'{label} failed and was undone') from None
    unlink_durable(journal_path())


def matches_file(current, before, after):
    if current == before:
        return True
    if after is None or current is None:
        return current is None and after is None
    return (digest(base64.b64decode(current['data'])) == after['sha256']
            and (current['mode'], current['uid'], current['gid'])
            == (after['mode'], after['uid'], after['gid']))


def matches_directory(current, before, after):
    # Owner and mode are two system calls, so each field may independently be at its
    # before or after value; any other value, or an unexpected absence, is drift.
    if current is None or before is None or after is None:
        return current == before or current == after
    return all(current[key] in (before[key], after[key]) for key in ('mode', 'uid', 'gid'))


def restore_journal(journal):
    """Put every journalled target back to its before value, after checking that each
    one is exactly at its before or after value. Never rolls forward."""
    current_state = read(STATE)
    if current_state is not None:
        check_state_file()
    before_state = base64.b64decode(journal['state_before']) if journal['state_before'] else None
    if not (current_state == before_state
            or (current_state is None and journal['state_after'] is None)
            or (current_state is not None and digest(current_state) == journal['state_after'])):
        raise Error('Recovery refuses unknown state drift')
    for entry in journal['directories']:
        if not matches_directory(directory_record(Path(entry['path'])), entry['before'], entry['after']):
            raise Error(f'Recovery refuses directory drift: {entry["path"]}')
    for entry in journal['files']:
        if not matches_file(file_record(Path(entry['path'])), entry['before'], entry['after']):
            raise Error(f'Recovery refuses unknown target drift: {entry["path"]}')
    refuse_acl_loss([STATE, *(Path(entry['path']) for entry in journal['files'])])
    refuse_directory_acl([Path(entry['path']) for entry in journal['directories']])
    refuse_inherited_acl([STATE, *(Path(entry['path']) for entry in journal['files']
                                   if entry['before'] is not None),
                          *(Path(entry['path']) for entry in journal['directories']
                            if entry['before'] is not None)])
    for entry in sorted(journal['directories'], key=depth):
        before, path = entry['before'], Path(entry['path'])
        if before is None:
            continue
        if not path.exists():
            make_directory(path, before['mode'])
        os.chown(path, before['uid'], before['gid'])
        path.chmod(before['mode'])
        fsync_directory(path)
    for entry in reversed(journal['files']):
        before, path = entry['before'], Path(entry['path'])
        if before is None:
            unlink_durable(path, missing_ok=True)
        else:
            atomic(path, base64.b64decode(before['data']), before['mode'], before['uid'], before['gid'])
    for entry in sorted(journal['directories'], key=depth, reverse=True):
        path = Path(entry['path'])
        if entry['before'] is None and path.exists():
            rmdir_durable(path)
    if before_state is None:
        unlink_durable(STATE, missing_ok=True)
    else:
        atomic(STATE, before_state, 0o600)


def valid_journal(journal):
    def metadata(value, *keys):
        return isinstance(value, dict) and all(type(value.get(key)) is int for key in keys)
    def valid_file(entry):
        before, after = entry.get('before'), entry.get('after')
        return (isinstance(entry, dict) and type(entry.get('path')) is str
                and (before is None or metadata(before, 'mode', 'uid', 'gid')
                     and type(before.get('data')) is str)
                and (after is None or metadata(after, 'mode', 'uid', 'gid')
                     and type(after.get('sha256')) is str))
    def valid_directory(entry):
        before, after = entry.get('before'), entry.get('after')
        return (isinstance(entry, dict) and type(entry.get('path')) is str
                and Path(entry['path']).is_relative_to(COPY_THEME)
                and (before is None or metadata(before, 'mode', 'uid', 'gid'))
                and (after is None or metadata(after, 'mode', 'uid', 'gid')))
    return (isinstance(journal, dict) and journal.get('journal') == JOURNAL_FORMAT
            and journal.get('operation') in OPERATION_LABELS
            and (journal.get('state_before') is None or type(journal['state_before']) is str)
            and (journal.get('state_after') is None or type(journal['state_after']) is str)
            and isinstance(journal.get('files'), list)
            and all(valid_file(entry) for entry in journal['files'])
            and isinstance(journal.get('directories'), list)
            and all(valid_directory(entry) for entry in journal['directories']))


@exclusive
def recover(recovery_only=False):
    """Undo an interrupted root transaction. Never loads state first: the operation may
    have created or deleted it."""
    check_state_directory(allow_journal=True)
    path = journal_path()
    data = read(path)
    if data is None:
        raise Error('No interrupted root transaction to recover')
    metadata = path.stat()
    if (metadata.st_uid, metadata.st_gid) != owner() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise Error('Unsafe recovery journal ownership/mode')
    journal = parse_json(data, path)
    if not valid_journal(journal):
        raise Error(f'Unsupported recovery journal: {path}')
    require_no_live_locker(recovery_only)
    restore_journal(journal)
    unlink_durable(path)
    print('Interrupted transaction restored.')


def require_no_live_locker(recovery_only=False):
    if recovery_only:
        print('Skipping the live session check (--recovery-only).')
        return
    try:
        result = run(['/usr/bin/loginctl', 'list-sessions', '--json=short'], text=True, timeout=5)
        sessions = json.loads(result.stdout)
        if not isinstance(sessions, list):
            raise Error('Unknown session enumeration')
        greeters = set()
        for session in sessions:
            name = session.get('session') if isinstance(session, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
                raise Error('Unknown session identifier')
            detail = run(['/usr/bin/loginctl', 'show-session', name, '--property=Type',
                          '--property=Class', '--property=User'], text=True, timeout=5)
            properties = dict(line.split('=', 1) for line in detail.stdout.splitlines() if '=' in line)
            if (properties.get('Type') not in ('tty', 'unspecified', 'x11', 'wayland')
                    or not properties.get('Class')):
                raise Error('Unknown session state')
            if properties['Class'] == 'greeter':
                uid = properties.get('User', '')
                if not re.fullmatch(r'[1-9][0-9]{0,9}', uid):
                    raise Error('Unknown greeter owner')
                greeters.add(int(uid))
            elif properties['Type'] in ('x11', 'wayland'):
                raise Error('A graphical session remains active')
        # Omarchy's SDDM runs its greeter on Hyprland as the greeter user: that compositor
        # is the login screen, not a session or locker. Any other owner refuses.
        processes = run(['/usr/bin/pgrep', '-x', 'quickshell|qs|Hyprland'], check=False,
                        text=True, timeout=5)
        if processes.returncode == 0:
            for pid in processes.stdout.split():
                if not re.fullmatch(r'[1-9][0-9]{0,9}', pid):
                    raise Error('Unknown process identifier')
                try:
                    owner = Path('/proc', pid).stat().st_uid
                except FileNotFoundError:
                    continue
                if owner not in greeters:
                    raise Error('Locker or compositor processes are running')
        elif processes.returncode != 1:
            raise Error('Locker or compositor processes are running')
    except (OSError, ValueError, subprocess.SubprocessError, Error):
        raise Error('A graphical session or locker is running, or session state is unknown') from None


@exclusive
def rollback(recovery_only=False):
    """Restore every managed file's rollback target. Accepts a schema-1 state from any
    companion version: everything it needs is in the state."""
    state = load_state(any_version=True)
    seen = snapshot(root_targets(state))
    require_no_live_locker(recovery_only)
    # Validate every target first: never partly restore over unrecognized edits.
    directories = [path for path in theme_directories(state['files']) if path.exists()]
    if COPY_THEME.is_dir():
        expected = {entry['path'] for entry in state['files']}
        for path in COPY_THEME.rglob('*'):
            if (path.is_symlink() or (path.is_dir() and path not in directories)
                    or (not path.is_dir() and str(path) not in expected)):
                raise Error('Unexpected companion theme entry; refusing rollback')
    # While pambase is unchanged, everything rollback needs is in the root-owned state;
    # the running package's supported list is consulted only after an upgrade.
    installed = installed_pambase()
    extra = []
    auth = system_auth_entry(state)
    auth_target = (base64.b64decode(auth['restore']), auth['restore_mode'],
                   auth['restore_uid'], auth['restore_gid'])
    if installed == state['pambase']:
        for entry in state['watched']:
            if digest(read(Path(entry['path'])) or b'') != entry['sha256']:
                raise Error(f'Rollback refuses: {entry["path"]} changed since setup '
                            '(was aad-tool configure-pam run?); review it by hand')
        if read(pacnew(SYSTEM_AUTH)) is not None:
            raise Error(f'Rollback refuses: {pacnew(SYSTEM_AUTH)} exists but pambase is '
                        'unchanged; review it by hand')
        try:
            require_safe_pam_file(SYSTEM_LOGIN)
        except Error as error:
            raise Error(f'Rollback refuses: {error}; fix it by hand') from None
    else:
        try:
            _, baseline, record = pambase_upgrade(state, installed)
        except Error as error:
            raise Error(f'Rollback refuses: {error}') from None
        auth_target = (baseline, record['mode'], record['uid'], record['gid'])
        if read(pacnew(SYSTEM_AUTH)) is not None:
            extra.append((pacnew(SYSTEM_AUTH), None, 0, 0, 0))
    uid, gid = owner()
    for entry in state['files']:
        current = file_record(Path(entry['path']))
        restore = None if entry['restore'] is None else {
            'data': entry['restore'], 'mode': entry['restore_mode'],
            'uid': entry['restore_uid'], 'gid': entry['restore_gid']}
        after = {'sha256': entry['after'], 'mode': entry['mode'], 'uid': uid, 'gid': gid}
        if not matches_file(current, restore, after):
            raise Error(f'Rollback refuses managed drift: {entry["path"]}')
    files = [(Path(entry['path']), *auth_target) if entry is auth else
             (Path(entry['path']),
              None if entry['restore'] is None else base64.b64decode(entry['restore']),
              entry['restore_mode'], entry['restore_uid'], entry['restore_gid'])
             for entry in reversed(state['files'])]
    transact('rollback', None, files + extra, [(path, None) for path in directories],
             expected=seen)
    print('Root managed files restored; Entra sign-in no longer works on this machine.')


def refuse_acl_loss(paths):
    """The journal records bytes, owner and mode: replacing or deleting a file that carries
    an ACL would drop it silently, and recover could not put it back."""
    for path in paths:
        if read(path) is not None:
            no_extended_acl(path)


def refuse_directory_acl(paths):
    """A directory record holds owner and mode only: never chmod or remove a directory
    that carries an ACL."""
    for path in paths:
        if path.is_dir():
            no_extended_acl(path)


def refuse_inherited_acl(paths):
    """A file or directory created at each path would inherit its nearest existing
    ancestor's default ACL, which the journal can't record and rollback would refuse."""
    for path in paths:
        parent = path.parent
        while not parent.exists():
            parent = parent.parent
        no_extended_acl(parent, ('system.posix_acl_default',))


def no_extended_acl(path, attributes=('system.posix_acl_access', 'system.posix_acl_default')):
    for attribute in attributes:
        try:
            os.getxattr(path, attribute, follow_symlinks=False)
        except OSError as error:
            if error.errno in (errno.ENODATA, errno.ENOTSUP):
                continue
            raise Error('Unable to verify managed-path ACLs') from None
        raise Error(f'Extended ACL refused for authentication code path: {path}')


def user_path_guard(home, target):
    if not target.is_relative_to(home):
        raise Error('Managed user target is outside canonical home')
    paths = [*reversed(home.parents), home]
    current = home
    for component in target.relative_to(home).parts:
        current = current / component
        paths.append(current)
    for path in paths:
        if path.is_symlink():
            raise Error('Symlink in managed user ancestry')
        if not path.exists():
            continue
        metadata = path.stat()
        if metadata.st_uid not in (0, os.getuid()):
            raise Error('Foreign owner in managed user ancestry')
        shared_parent = (path != home and not path.is_relative_to(home)
                         and metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX)
        if metadata.st_mode & 0o022 and not shared_parent:
            raise Error('Writable managed user ancestry/file refused')
        no_extended_acl(path)


def canonical_user():
    account = pwd.getpwuid(os.getuid())
    alias = Path(account.pw_dir)
    if alias.is_symlink() and alias.lstat().st_uid not in (0, os.getuid()):
        raise Error('Unsafe NSS home alias owner')
    home = alias.resolve(strict=True)
    if not home.is_dir() or home.stat().st_uid != os.getuid():
        raise Error('Resolved NSS home must belong to the current user')
    user_path_guard(home, home)
    return account, home


def user_environment():
    account, home = canonical_user()
    keys = ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS')
    session = {key: os.environ[key] for key in keys if key in os.environ}
    return {**COMMAND_ENV, **session, **OMARCHY_ENV, 'HOME': str(home), 'USER': account.pw_name,
            'LOGNAME': account.pw_name, 'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}'}


def native_plugins():
    result = run(['/usr/bin/omarchy', 'plugin', 'list', '--json'], text=True,
                 env=user_environment(), timeout=5)
    try:
        plugins = json.loads(result.stdout)
    except ValueError:
        raise Error('Native plugin discovery returned invalid JSON') from None
    if not isinstance(plugins, list):
        raise Error('Native plugin discovery did not return a list')
    if any(not isinstance(plugin, dict) or not isinstance(plugin.get('id'), str)
           or type(plugin.get('enabled')) is not bool for plugin in plugins):
        raise Error('Unsupported native plugin discovery entry')
    if len({plugin['id'] for plugin in plugins}) != len(plugins):
        raise Error('Duplicate native plugin discovery ID')
    return plugins


def lock_implementations(plugins):
    return [plugin for plugin in plugins if plugin.get('enabled') is True and
            (plugin['id'] == 'omarchy.lock' or plugin.get('clonedFrom') == 'omarchy.lock')]


def fixed_plugin_files(allow_previous=False):
    """Check the installed fixed plugin. allow_previous also accepts the previous release."""
    account, home = canonical_user()
    info = manifest('fixed-plugin')
    directory = home / '.config/omarchy/plugins' / info['id']
    user_path_guard(home, directory)
    runtime_files = set()
    for path in (directory / 'plugin').rglob('*'):
        user_path_guard(home, path)
        if path.is_symlink() or path.is_dir():
            raise Error('Unexpected directory/symlink in fixed plugin runtime tree')
        runtime_files.add(str(path.relative_to(directory)))

    def release(files):
        if runtime_files != {name for name in files if name.startswith('plugin/')}:
            return 'tree'
        for name, wanted in files.items():
            target = directory / name
            user_path_guard(home, target)
            data = read(target)
            if data is None or digest(data) != wanted:
                return 'source'
        return None

    mismatch = release(info['files'])
    if mismatch:
        previous = manifest('previous-release')
        if release(previous['plugin-files']):
            raise Error(f'Fixed plugin {"runtime tree" if mismatch == "tree" else "source"}'
                        ' differs from companion release')
        if not allow_previous:
            raise Error(f'Fixed lock plugin {previous["version"]} needs updating to {info["version"]}')
    for name, wanted in info['upstream'].items():
        target = LOCK.parent / name
        data = read(target)
        if data is None or digest(data) != wanted:
            raise Error('Unsupported installed Omarchy lock source for fixed plugin')
    return info


def lock_prerequisites():
    # The lock uses only the dedicated service; stock PAM matters to setup/repair.
    data = read(PAM)
    if data is None or digest(data) != manifest('lock-pam')['after']:
        raise Error('Dedicated lock PAM is missing or changed')
    metadata = PAM.stat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o644:
        raise Error('Unsafe dedicated lock PAM metadata')


def plugin_status(quiet=False, allow_previous=False):
    user_required()
    lock_prerequisites()
    info = fixed_plugin_files(allow_previous)
    plugins = native_plugins()
    candidates = [plugin for plugin in plugins if plugin['id'] == info['id']]
    if (len(candidates) != 1 or candidates[0].get('firstParty') is not False
            or candidates[0].get('clonedFrom') != 'omarchy.lock'):
        raise Error('Native discovery did not confirm the fixed third-party lock plugin')
    enabled = lock_implementations(plugins)
    if len(enabled) != 1 or enabled[0]['id'] != info['id']:
        raise Error('Fixed lock plugin must be the only enabled lock implementation')
    if not quiet:
        print('Fixed lock plugin source and prerequisites match.')


def locker_preflight():
    """Run by the lock plugin: exit 0 when the fixed plugin can authenticate.

    The previous plugin release passes too: it refuses to lock without this check,
    so an upgrade that updates the companion first must not stop it locking.
    """
    plugin_status(quiet=True, allow_previous=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development', action='store_true',
                        help='use checkout assets (unprivileged checkout runs only)')
    parser.add_argument('--version', action='version', version=VERSION)
    sub = parser.add_subparsers(dest='command', required=True)
    setup_parser = sub.add_parser('setup', help='configure one Entra account (root)')
    setup_parser.add_argument('--domain', required=True)
    setup_parser.add_argument('--user', required=True)
    setup_parser.add_argument('--admin', action='store_true',
                              help='allow the account to use sudo with its password or PIN')
    # status is the same check as plugin-status; existing post-update hooks call it.
    for command in ('doctor', 'status', 'repair', 'plugin-status', 'locker-preflight'):
        sub.add_parser(command)
    sub.add_parser('upgrade', help='move the previous release\'s setup to this release (root)')
    for command in ('rollback', 'recover'):
        recovery_parser = sub.add_parser(command)
        recovery_parser.add_argument('--recovery-only', action='store_true',
                                     help='skip the live session check')
    policy_parser = sub.add_parser('policy', help='set apply_policy in himmelblau.conf (root)')
    policy_choice = policy_parser.add_mutually_exclusive_group(required=True)
    policy_choice.add_argument('--enable', dest='policy_enabled', action='store_true')
    policy_choice.add_argument('--disable', dest='policy_enabled', action='store_false')
    args = parser.parse_args(argv)
    if args.development:
        if os.geteuid() == 0:
            raise Error('--development refuses to run as root')
        if Path(__file__).resolve().parent.name != 'src':
            raise Error('--development works only as python3 src/omarchy_himmelblau.py in a checkout')
        global ASSETS
        ASSETS = Path(__file__).resolve().parents[1] / 'assets'
    if args.command in ('status', 'plugin-status'):
        plugin_status()
    elif args.command == 'locker-preflight':
        locker_preflight()
    else:
        root_required()
        if args.command == 'doctor':
            doctor()
        elif args.command == 'setup':
            setup(args.domain, args.user, args.admin)
        elif args.command == 'policy':
            policy(args.policy_enabled)
        elif args.command == 'repair':
            repair()
        elif args.command == 'upgrade':
            upgrade()
        elif args.command == 'rollback':
            rollback(args.recovery_only)
        elif args.command == 'recover':
            recover(args.recovery_only)


def cli(argv=None):
    try:
        main(argv)
    except subprocess.TimeoutExpired as error:
        sys.exit(f'Command timed out: {error.cmd[0]}')
    except subprocess.CalledProcessError as error:
        # Never print subprocess output: PAM and configuration text can contain secrets.
        sys.exit(f'Command failed: {error.cmd[0]}')
    except (Error, OSError, ValueError) as error:
        sys.exit(str(error))


if __name__ == '__main__':
    cli()
