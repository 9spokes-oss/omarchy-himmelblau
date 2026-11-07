#!/usr/bin/python3 -I
"""Explicit, experimental Omarchy/Himmelblau companion. No tenant operations."""
import argparse
import base64
import errno
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

VERSION = '0.5.3'
# The one sddm-himmelblau release this companion supports (tests/test_versions.py).
NATIVE_VERSION = '0.21.0-4'
ASSETS = Path('/usr/share/omarchy-himmelblau')
COMMAND_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
OMARCHY_ENV = {'OMARCHY_PATH': '/usr/share/omarchy'}
STATE = Path('/var/lib/omarchy-himmelblau/state.json')
STOCK_PAM = Path('/etc/pam.d/omarchy-lock-password')
PAM = Path('/etc/pam.d/omarchy-himmelblau-lock-password')
LOCK = Path('/usr/share/omarchy/shell/plugins/lock/Service.qml')
CONFIG = Path('/etc/himmelblau/himmelblau.conf')
THEME = Path('/usr/share/sddm/themes/omarchy')
COPY_THEME = Path('/usr/share/sddm/themes/omarchy-himmelblau')
OVERRIDE = Path('/etc/sddm.conf.d/99-z-himmelblau.conf')
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


def atomic(path, data, mode):
    path = safe(path)
    ensure_directory(path.parent)
    safe(path)
    fd, name = tempfile.mkstemp(prefix='.himmelblau-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
        fsync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


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
    elif name == 'previous-release':
        # The one release that repair and locker-preflight upgrade from.
        required = {'version': str, 'sddm-conversation': str, 'plugin-files': dict}
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
        canonical = pwd.getpwnam(account.pw_name)
        reverse = pwd.getpwuid(account.pw_uid)
    except KeyError:
        raise Error(f'{user} does not resolve through NSS') from None
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


def load_state():
    data = read(STATE)
    if data is None:
        raise Error('No root setup state; run setup first')
    if STATE.stat().st_uid != os.geteuid() or stat.S_IMODE(STATE.stat().st_mode) != 0o600:
        raise Error('Unsafe state ownership/mode')
    check_state_ancestors(STATE.parent)
    if STATE.parent.stat().st_uid != os.geteuid() or stat.S_IMODE(STATE.parent.stat().st_mode) != 0o700:
        raise Error('Unsafe state directory ownership/mode')
    return check_state(parse_json(data, STATE))


def parse_json(data, path):
    try:
        return json.loads(data)
    except ValueError:
        raise Error(f'Invalid JSON: {path}') from None


def check_state(state):
    """Raise Error unless root state has every field the root commands index."""
    def valid_entry(entry):
        if not isinstance(entry, dict) or 'before' not in entry:
            return False
        fields = {'path': str, 'after': str, 'mode': int}
        if entry['before'] is not None:
            fields.update(before=str, before_mode=int, before_uid=int, before_gid=int)
        return all(type(entry.get(key)) is kind for key, kind in fields.items())
    fields = {'domain': str, 'user': str, 'admin': bool, 'apply_policy': bool,
              'identity': dict, 'files': list}
    if not (isinstance(state, dict)
            and all(type(state.get(key)) is kind for key, kind in fields.items())
            and all(valid_entry(entry) for entry in state['files'])
            and ('transaction' not in state
                 or isinstance(state['transaction'], dict)
                 and isinstance(state['transaction'].get('progress'), dict))):
        raise Error(f'Invalid state file: {STATE}')
    return state


def check_state_ancestors(path):
    for parent in path.parents:
        metadata = parent.stat()
        if metadata.st_uid not in (0, os.geteuid()) or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise Error('Unsafe state ancestor ownership/mode')


def save_state(state):
    # The containing directory holds original configuration/backups, root only.
    if STATE.parent.is_symlink():
        raise Error('Symlink state directory refused')
    ensure_directory(STATE.parent)
    check_state_ancestors(STATE.parent)
    if STATE.parent.stat().st_uid != os.geteuid() or stat.S_IMODE(STATE.parent.stat().st_mode) != 0o700:
        raise Error('State directory must be owned by current root and mode 0700')
    atomic(STATE, (json.dumps(state, indent=2) + '\n').encode(), 0o600)


def planned(path, data, mode):
    before = read(path)
    if before == data:
        return None
    entry = {'path': str(path), 'before': None, 'before_mode': None, 'before_uid': None,
             'before_gid': None, 'after': digest(data), 'data': data, 'mode': mode}
    if before is not None:
        metadata = path.stat()
        entry.update(before=base64.b64encode(before).decode(),
                     before_mode=stat.S_IMODE(metadata.st_mode),
                     before_uid=metadata.st_uid, before_gid=metadata.st_gid)
    return entry


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
        if not directory.is_dir() or directory.stat().st_uid != os.geteuid():
            raise Error(f'Unowned or missing companion theme directory: {directory}')
    return directories


def setup(domain, user, admin=False):
    domain, user = identity(domain, user)
    no_pending_recovery()
    if read(STATE) is not None:
        state = load_state()
        if (state['domain'], state['user'], state['admin']) != (domain, user, admin):
            raise Error('Existing setup uses a different domain, user or admin setting')
        doctor()
        print('Already set up; no changes')
        return
    native_sddm_contract()
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
    add(OVERRIDE, b'[Theme]\nCurrent=omarchy-himmelblau\n')
    if admin:
        if read(SUDOERS) is not None:
            raise Error(f'{SUDOERS} exists but is not managed')
        content = admin_rule(principal)
        validate_sudo_rule(content)
        add(SUDOERS, content, 0o440)
    # All source/configuration preflight finished before touching guest files.
    state = {'version': VERSION, 'domain': domain, 'user': user, 'admin': admin,
             'identity': principal, 'apply_policy': False,
             'files': [{k: v for k, v in entry.items() if k != 'data'} for entry in changes]}
    state['transaction'] = {'kind': 'setup', 'progress': {entry['path']: 'pending' for entry in changes}}
    save_state(state)
    try:
        for directory in theme_directories(changes):
            safe(directory / '.guard')
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)  # Public greeter assets, independent of root umask.
            fsync_directory(directory.parent)
        for entry in changes:
            state['transaction']['progress'][entry['path']] = 'applying'
            save_state(state)
            atomic(Path(entry['path']), entry['data'], entry['mode'])
            state['transaction']['progress'][entry['path']] = 'applied'
            save_state(state)
        del state['transaction']
        save_state(state)
    except Exception:
        try:
            undo_setup()
        except Exception:
            raise Error(f'Setup failed and could not be undone; originals are in {STATE}') from None
        raise Error('Setup failed and was undone') from None
    print('Root setup complete.')


def policy(enable):
    """Explicit local configuration only; no service or tenant operations."""
    state = load_state()
    old_state = read(STATE)
    backup = STATE.parent / 'policy-backup.json'
    no_pending_recovery()
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
        entry = planned(CONFIG, after, 0o644)
        state['files'].append({key: value for key, value in entry.items() if key != 'data'})
    else:
        entry['after'] = digest(after)  # Preserve the original setup rollback bytes.
    state['apply_policy'] = enable
    state['version'] = VERSION
    journal = {'state': base64.b64encode(old_state).decode(),
               'config': base64.b64encode(before).decode(), 'mode': 0o644,
               'files': [{'path': str(CONFIG), 'data': base64.b64encode(before).decode(),
                          'mode': 0o644, 'after': digest(after)}],
               'after_state': digest((json.dumps(state, indent=2) + '\n').encode()), 'progress': {}}
    atomic(backup, (json.dumps(journal, indent=2) + '\n').encode(), 0o600)
    try:
        journal_step(backup, journal, str(CONFIG), 'applying')
        atomic(CONFIG, after, 0o644)
        journal_step(backup, journal, str(CONFIG), 'applied')
        journal_step(backup, journal, '@state', 'applying')
        save_state(state)
        journal_step(backup, journal, '@state', 'applied')
        issues = root_issues(state, transaction=backup)
        if issues:
            raise Error('\n'.join(issues))
    except Exception:
        try:
            atomic(CONFIG, before, 0o644)
            atomic(STATE, old_state, 0o600)
        except Exception:
            raise Error(f'Policy change failed and could not be undone; originals are in {backup}') \
                from None
        unlink_durable(backup)
        raise Error('Policy change failed and was undone') from None
    unlink_durable(backup)
    print(f'Set apply_policy = {str(enable).lower()}.')


def stock_pam():
    data = read(STOCK_PAM)
    if data is None or digest(data) != manifest('lock-pam')['before']:
        raise Error(f'{STOCK_PAM} is not the supported pristine version')
    return data


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
    if (payload.get('package') != 'sddm-himmelblau' or payload.get('version') != NATIVE_VERSION
            or not isinstance(payload.get('artifact_sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', payload['artifact_sha256'])
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


def no_pending_recovery(transaction=None):
    for operation in ('repair', 'policy'):
        journal = STATE.parent / f'{operation}-backup.json'
        if read(journal) is not None and journal != transaction:
            raise Error(f'Pending {operation} recovery journal: {journal}')


def root_issues(state, *, transaction=None):
    issues = []
    try:
        no_pending_recovery(transaction)
        if state.get('transaction') is not None:
            raise Error('Interrupted root transaction; run recover first')
        check_identity(state)
        check_admin_identity(state)
        configuration_scope(state['domain'], state['user'], state['apply_policy'])
        require_complete_configuration(state['domain'], state['user'], state['apply_policy'])
        stock_pam()
        native_sddm_contract()
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
                or path.stat().st_uid != os.geteuid()):
            issues.append(f'Managed file drift: {entry["path"]}')
    try:
        for directory in checked_theme_directories(state['files']):
            if stat.S_IMODE(directory.stat().st_mode) != 0o755:
                issues.append(f'Companion theme directory must be 0755: {directory}')
    except Error as error:
        issues.append(str(error))
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


def repair():
    state = load_state()
    old_state = read(STATE)
    backup = STATE.parent / 'repair-backup.json'
    no_pending_recovery()
    if state.get('transaction') is not None:
        raise Error('Interrupted root transaction; run recover first')
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
    previous = manifest('previous-release')['sddm-conversation']
    upgrade = theme_entry is not None and theme_entry['after'] == previous
    if theme_entry is None or (not upgrade and theme_entry['after'] != digest(theme)):
        raise Error('Unsupported managed SDDM theme state')
    repairs = []
    # Full file/source/configuration preflight precedes journal or managed writes.
    for entry in state['files']:
        path = Path(entry['path'])
        data = read(path)
        if data is not None:
            metadata = path.stat()
            if stat.S_IMODE(metadata.st_mode) != entry['mode'] or metadata.st_uid != os.geteuid():
                raise Error(f'Repair refuses permission drift: {path}')
        if (path == RECOVERY_ACCOUNT and data is None
                and entry['after'] == digest(identity_file(principal))):
            repairs.append((path, identity_file(principal), entry['mode']))
            continue
        if data is not None and digest(data) == entry['after']:
            if upgrade and path == theme_path:
                repairs.append((path, theme, entry['mode']))
            continue
        if path == PIN_PAM and data is None and entry['after'] == digest(PIN_PAM_CONTENT):
            repairs.append((path, PIN_PAM_CONTENT, entry['mode']))
            continue
        if path == PAM and (data is None or digest(data) == manifest('lock-pam')['before']):
            if digest(desired_pam) == entry['after']:
                repairs.append((path, desired_pam, entry['mode']))
                continue
        raise Error(f'Repair refuses unsupported managed drift: {path}')
    patch_source('lock-conversation', read(LOCK) or b'')
    patch_source('lock-view', read(LOCK.parent / 'LockView.qml') or b'')
    directories = checked_theme_directories(state['files'])
    if upgrade:
        # Rollback metadata stays; only the managed result changes.
        theme_entry['after'] = digest(theme)
    state['version'] = VERSION
    originals = []
    for path, data, mode in repairs:
        current = read(path)
        originals.append({
            'path': str(path),
            'data': base64.b64encode(current).decode() if current is not None else None,
            'mode': stat.S_IMODE(path.stat().st_mode) if path.exists() else mode,
            'after': digest(data)})
    directory_modes = [(directory, stat.S_IMODE(directory.stat().st_mode)) for directory in directories]
    journal = {'state': base64.b64encode(old_state).decode(), 'files': originals,
               'directories': {str(path): mode for path, mode in directory_modes},
               'after_state': digest((json.dumps(state, indent=2) + '\n').encode()), 'progress': {}}
    atomic(backup, (json.dumps(journal, indent=2) + '\n').encode(), 0o600)
    try:
        for directory in directories:
            journal_step(backup, journal, str(directory), 'applying')
            directory.chmod(0o755)
            fsync_directory(directory)
            journal_step(backup, journal, str(directory), 'applied')
        for path, data, mode in repairs:
            journal_step(backup, journal, str(path), 'applying')
            atomic(path, data, mode)
            journal_step(backup, journal, str(path), 'applied')
        journal_step(backup, journal, '@state', 'applying')
        save_state(state)
        journal_step(backup, journal, '@state', 'applied')
        issues = root_issues(state, transaction=backup)
        if issues:
            raise Error('\n'.join(issues))
    except Exception:
        try:
            for entry in reversed(originals):
                path = Path(entry['path'])
                if entry['data'] is None:
                    unlink_durable(path, missing_ok=True)
                else:
                    atomic(path, base64.b64decode(entry['data']), entry['mode'])
            for directory, mode in directory_modes:
                directory.chmod(mode)
            atomic(STATE, old_state, 0o600)
        except Exception:
            raise Error(f'Repair failed and could not be undone; originals are in {backup}') from None
        unlink_durable(backup)
        raise Error('Repair failed and was undone') from None
    unlink_durable(backup)
    print('Root repair complete.')


def journal_step(path, journal, target, phase):
    journal['progress'][target] = phase
    atomic(path, (json.dumps(journal, indent=2) + '\n').encode(), 0o600)


def recover(recovery_only=False):
    state = load_state()
    require_no_live_locker(recovery_only)
    pending = [STATE.parent / f'{kind}-backup.json' for kind in ('policy', 'repair')
               if read(STATE.parent / f'{kind}-backup.json') is not None]
    if not pending:
        if state.get('transaction', {}).get('kind') in ('setup', 'rollback'):
            rollback(recovery_only=recovery_only)
            return
        raise Error('No interrupted managed transaction to recover')
    if len(pending) != 1 or state.get('transaction'):
        raise Error('More than one recovery record exists')
    backup = pending[0]
    metadata = backup.stat()
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise Error('Unsafe recovery journal ownership/mode')
    journal = parse_json(read(backup), backup)
    if not valid_journal(journal):
        raise Error(f'Unsupported recovery journal: {backup}')
    before_state = base64.b64decode(journal['state'])
    current_state = read(STATE)
    if current_state != before_state and digest(current_state) != journal.get('after_state'):
        raise Error('Recovery refuses unknown state drift')
    # Validate every target before restoring any file, including already restored files.
    for entry in journal['files']:
        path = Path(entry['path'])
        current = read(path)
        original = base64.b64decode(entry['data']) if entry['data'] is not None else None
        if current != original and (current is None or digest(current) != entry['after']):
            raise Error(f'Recovery refuses unknown target drift: {path}')
        if current is not None and (path.stat().st_uid != os.geteuid()
                                    or stat.S_IMODE(path.stat().st_mode) != entry['mode']):
            raise Error(f'Recovery refuses target permission drift: {path}')
    for name, mode in journal.get('directories', {}).items():
        path = Path(name)
        safe(path / '.guard')
        if (not path.is_relative_to(COPY_THEME) or not path.is_dir()
                or path.stat().st_uid != os.geteuid()
                or stat.S_IMODE(path.stat().st_mode) not in (mode, 0o755)):
            raise Error('Recovery refuses directory drift')
    for entry in reversed(journal['files']):
        path = Path(entry['path'])
        journal_step(backup, journal, str(path), 'restoring')
        if entry['data'] is None:
            unlink_durable(path, missing_ok=True)
        else:
            atomic(path, base64.b64decode(entry['data']), entry['mode'])
        journal_step(backup, journal, str(path), 'restored')
    for name, mode in journal.get('directories', {}).items():
        path = Path(name)
        journal_step(backup, journal, name, 'restoring')
        path.chmod(mode)
        fsync_directory(path)
        journal_step(backup, journal, name, 'restored')
    journal_step(backup, journal, '@state', 'restoring')
    atomic(STATE, before_state, 0o600)
    journal_step(backup, journal, '@state', 'restored')
    unlink_durable(backup)
    print('Interrupted transaction restored.')


def valid_journal(journal):
    def valid_entry(entry):
        return (isinstance(entry, dict) and type(entry.get('path')) is str
                and type(entry.get('mode')) is int and type(entry.get('after')) is str
                and (entry.get('data') is None or type(entry['data']) is str))
    return (isinstance(journal, dict) and type(journal.get('state')) is str
            and type(journal.get('after_state')) is str
            and isinstance(journal.get('progress'), dict)
            and isinstance(journal.get('files'), list)
            and all(valid_entry(entry) for entry in journal['files'])
            and isinstance(journal.get('directories', {}), dict)
            and all(type(mode) is int for mode in journal.get('directories', {}).values()))


def require_no_live_locker(recovery_only=False):
    if recovery_only:
        print('Skipping the live session check (--recovery-only).')
        return
    try:
        result = run(['/usr/bin/loginctl', 'list-sessions', '--json=short'], text=True, timeout=5)
        sessions = json.loads(result.stdout)
        if not isinstance(sessions, list):
            raise Error('Unknown session enumeration')
        for session in sessions:
            name = session.get('session') if isinstance(session, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
                raise Error('Unknown session identifier')
            detail = run(['/usr/bin/loginctl', 'show-session', name,
                          '--property=Type', '--property=Class'], text=True, timeout=5)
            properties = dict(line.split('=', 1) for line in detail.stdout.splitlines() if '=' in line)
            if (properties.get('Type') not in ('tty', 'unspecified', 'x11', 'wayland')
                    or not properties.get('Class')):
                raise Error('Unknown session state')
            if properties['Type'] in ('x11', 'wayland') and properties['Class'] != 'greeter':
                raise Error('A graphical session remains active')
        processes = run(['/usr/bin/pgrep', '-x', 'quickshell|qs|Hyprland'], check=False, timeout=5)
        if processes.returncode != 1:
            raise Error('Locker or compositor processes are running')
    except (OSError, ValueError, subprocess.SubprocessError, Error):
        raise Error('A graphical session or locker is running, or session state is unknown') from None


def rollback(recovery_only=False):
    state = load_state()
    no_pending_recovery()
    require_no_live_locker(recovery_only)
    restore_managed_files(state)
    print('Root managed files restored.')


def undo_setup():
    # A failed setup ran from a live session, so the locker check does not apply.
    state = load_state()
    no_pending_recovery()
    if state.get('transaction', {}).get('kind') != 'setup':
        raise Error('No interrupted setup to undo')
    restore_managed_files(state)


def restore_managed_files(state):
    # Validate every target first: never partly restore over unrecognized edits.
    if COPY_THEME.is_dir():
        expected = {entry['path'] for entry in state['files']}
        expected_directories = set(theme_directories(state['files']))
        for path in COPY_THEME.rglob('*'):
            if (path.is_symlink() or (path.is_dir() and path not in expected_directories)
                    or (not path.is_dir() and str(path) not in expected)):
                raise Error('Unexpected companion theme entry; refusing rollback')
    for entry in state['files']:
        data = read(Path(entry['path']))
        original = base64.b64decode(entry['before']) if entry['before'] is not None else None
        if data != original and (data is None or digest(data) != entry['after']):
            raise Error(f'Rollback refuses managed drift: {entry["path"]}')
    progress = {entry['path']: 'pending' for entry in state['files']}
    state['transaction'] = {'kind': 'rollback', 'progress': progress}
    save_state(state)
    for entry in reversed(state['files']):
        state['transaction']['progress'][entry['path']] = 'restoring'
        save_state(state)
        path = Path(entry['path'])
        if entry['before'] is None:
            unlink_durable(path, missing_ok=True)
        else:
            atomic(path, base64.b64decode(entry['before']), entry['before_mode'])
            os.chown(path, entry['before_uid'], entry['before_gid'])
        state['transaction']['progress'][entry['path']] = 'restored'
        save_state(state)
    # Only remove empty companion theme directories; never recursively delete others' data.
    if COPY_THEME.is_dir():
        for directory in sorted((p for p in COPY_THEME.rglob('*') if p.is_dir()), reverse=True):
            rmdir_durable(directory)
        rmdir_durable(COPY_THEME)
    unlink_durable(STATE)


def no_extended_acl(path):
    for attribute in ('system.posix_acl_access', 'system.posix_acl_default'):
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


def unlocked():
    result = run(['/usr/bin/omarchy-shell', 'lock', 'status'], text=True,
                 env=user_environment(), timeout=5)
    try:
        state = json.loads(result.stdout)
    except ValueError:
        raise Error('Unable to verify unlocked Quickshell state') from None
    keys = ('locked', 'requested', 'pending', 'sessionLocked', 'secure', 'authenticating')
    if any(state.get(key) is not False for key in keys):
        raise Error('Session is locked or its lock state is unknown')


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


def activate_plugin():
    user_required()
    unlocked()
    lock_prerequisites()
    info = fixed_plugin_files()
    plugins = native_plugins()
    fixed = [plugin for plugin in plugins if plugin['id'] == info['id']]
    if (len(fixed) != 1 or fixed[0].get('firstParty') is not False
            or fixed[0].get('clonedFrom') != 'omarchy.lock'):
        raise Error('Fixed lock plugin is not installed as a clone of omarchy.lock')
    enabled = lock_implementations(plugins)
    if len(enabled) > 1 or any(plugin['id'] not in ('omarchy.lock', info['id']) for plugin in enabled):
        raise Error('Another lock implementation is enabled; disable it first')
    try:
        unlocked()
        run(['/usr/bin/omarchy', 'plugin', 'enable', info['id']], env=user_environment(), timeout=5)
        plugin_status(quiet=True)
    except Exception:
        # Even a failed native command may have changed persisted selection.
        raise Error('Plugin activation incomplete; the enabled lock plugin may have changed') from None
    print('Fixed lock plugin enabled; restart the shell or log in again before locking.')


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
    for command in ('rollback', 'recover'):
        recovery_parser = sub.add_parser(command)
        recovery_parser.add_argument('--recovery-only', action='store_true',
                                     help='skip the live session check')
    policy_parser = sub.add_parser('policy', help='set apply_policy in himmelblau.conf (root)')
    policy_choice = policy_parser.add_mutually_exclusive_group(required=True)
    policy_choice.add_argument('--enable', dest='policy_enabled', action='store_true')
    policy_choice.add_argument('--disable', dest='policy_enabled', action='store_false')
    sub.add_parser('activate-plugin')
    args = parser.parse_args(argv)
    if args.development:
        if os.geteuid() == 0:
            raise Error('--development refuses to run as root')
        if Path(__file__).resolve().parent.name != 'src':
            raise Error('--development works only as python3 src/omarchy_himmelblau.py in a checkout')
        global ASSETS
        ASSETS = Path(__file__).resolve().parents[1] / 'assets'
    if args.command == 'activate-plugin':
        activate_plugin()
    elif args.command in ('status', 'plugin-status'):
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
