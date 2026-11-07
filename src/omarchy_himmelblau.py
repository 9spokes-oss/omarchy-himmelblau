#!/usr/bin/env python3
"""Explicit, experimental Omarchy/Himmelblau companion. No tenant operations."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile

VERSION = '0.1.0'
DEVELOPMENT_ASSETS = Path(__file__).resolve().parents[1] / 'assets'
ASSETS = DEVELOPMENT_ASSETS if DEVELOPMENT_ASSETS.is_dir() else Path('/usr/share/omarchy-himmelblau')
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
LOCAL_PASSWD = Path('/etc/passwd')
CONFIG_LAYERS = (Path('/usr/lib/himmelblau/himmelblau.conf'), Path('/run/himmelblau/himmelblau.conf'))
SERVER_CONFIG = Path('/var/cache/himmelblaud/himmelblau.conf')
CACHE_ALIAS = Path('/var/cache/himmelblaud')
CACHE_TARGET = Path('/var/cache/private/himmelblaud')


class Error(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


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


def atomic(path, data, mode):
    path = safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe(path)
    fd, name = tempfile.mkstemp(prefix='.himmelblau-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def patch_source(name, data):
    info = json.loads((ASSETS / 'manifest.json').read_text())[name]
    actual = digest(data)
    if actual == info['after']:
        return data  # Known patched source: adopt, never pretend it was pristine.
    if actual != info['before']:
        raise Error(f'Unsupported upstream source for {name}; refusing drift')
    with tempfile.TemporaryDirectory(prefix='omarchy-himmelblau-') as work:
        target = Path(work) / info['filename']
        target.write_bytes(data)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', work],
                       input=(ASSETS / (name + '.patch')).read_bytes(), check=True,
                       capture_output=True)
        result = target.read_bytes()
    if digest(result) != info['after']:
        raise Error(f'Unexpected patch result: {name}')
    return result


def identity(domain, user):
    domain = domain.lower()
    if len(domain) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
        raise Error('Invalid DNS domain')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}@' + re.escape(domain), user, re.I):
        raise Error('User must be a UPN in the selected domain')
    return domain, user.lower()


def validate_scope(data, domain, user):
    # Himmelblau unions pam_allow_groups from ALL sections, not only global.
    wanted = {'domain': domain, 'pam_allow_groups': user,
              'apply_policy': 'false', 'use_etc_skel': 'true'}
    for line in (data or b'').decode().splitlines():
        match = re.match(r'^\s*([a-zA-Z_]+)\s*=\s*(.*?)\s*$', line)
        if not match:
            continue
        key = match[1].lower()
        value = re.split(r'\s+[;#]', match[2], maxsplit=1)[0].strip().lower()
        if key in wanted and value != wanted[key].lower():
            raise Error(f'Conflicting scoped Himmelblau key in configuration hierarchy: {key}')
        if key == 'domains' and any(item.strip() != domain for item in value.split(',')):
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


def configuration_scope(domain, user):
    current = read(CONFIG)
    parent = CONFIG.parent
    if (not parent.is_dir() or parent.stat().st_uid != os.geteuid()
            or stat.S_IMODE(parent.stat().st_mode) != 0o755):
        raise Error('Main configuration directory must already be root-owned 0755; protected/missing directory requires manual review')
    if current is not None:
        metadata = CONFIG.stat()
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o644:
            raise Error('Existing main configuration must be root-owned 0644; protected/foreign configuration requires manual review')
    # The local main file supersedes vendor/runtime mains, but drop-ins merge.
    # Supporting arbitrary operator drop-in precedence is deliberately out of scope.
    for layer in (*CONFIG_LAYERS, CONFIG):
        directory = Path(str(layer) + '.d')
        safe(directory / '.guard')
        if directory.exists() and any(directory.glob('*.conf')):
            raise Error(f'Himmelblau configuration drop-ins require manual review: {directory}')
    server = read_generated_config()
    if server is not None:
        validate_scope(server, domain, user)
    validate_scope(read(CONFIG), domain, user)


def managed_config(data, domain, user):
    """Preserve existing text/settings; refuse conflicting explicit managed keys."""
    validate_scope(data, domain, user)
    text = (data or b'').decode()
    wanted = {'domain': domain, 'pam_allow_groups': user,
              'apply_policy': 'false', 'use_etc_skel': 'true'}
    lines = text.splitlines(keepends=True)
    section = None
    global_seen = False
    found = set()
    insert = len(lines)
    for index, line in enumerate(lines):
        if match := re.match(r'^\s*\[([^]]+)\]\s*(?:[#;].*)?$', line.strip()):
            if section == 'global':
                insert = index
            section = match[1].lower()
            if section == 'global':
                if global_seen:
                    raise Error('Duplicate global configuration section')
                global_seen = True
        elif section == 'global' and (match := re.match(r'^\s*([a-zA-Z_]+)\s*=\s*(.*?)\s*$', line.strip())):
            key = match[1].lower()
            if key in wanted:
                if key in found:
                    raise Error(f'Duplicate managed key: {key}')
                found.add(key)
                value = re.split(r'\s+[;#]', match[2], maxsplit=1)[0].strip()
                if value.lower() != wanted[key].lower():
                    raise Error(f'Conflicting existing Himmelblau key: {key}; review manually')
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
        raise Error('Admin opt-in requires an already resolvable NSS identity') from None
    if account.pw_uid < 1000:
        raise Error('Refusing system UID for admin opt-in')
    for line in LOCAL_PASSWD.read_text().splitlines():
        fields = line.split(':')
        if len(fields) > 2 and (fields[2] == str(account.pw_uid) or fields[0].lower() in {user.lower(), account.pw_name.lower()}):
            raise Error('Admin NSS UID collides with a local passwd account')
    return account.pw_uid


def root_required():
    if os.geteuid() != 0:
        raise Error('This command requires root; use sudo explicitly')


def load_state():
    data = read(STATE)
    if data is None:
        raise Error('No root setup state; run setup first')
    if STATE.stat().st_uid != os.geteuid() or stat.S_IMODE(STATE.stat().st_mode) != 0o600:
        raise Error('Unsafe state ownership/mode')
    check_state_ancestors(STATE.parent)
    if STATE.parent.stat().st_uid != os.geteuid() or stat.S_IMODE(STATE.parent.stat().st_mode) != 0o700:
        raise Error('Unsafe state directory ownership/mode')
    return json.loads(data)


def check_state_ancestors(path):
    for parent in path.parents:
        metadata = parent.stat()
        if metadata.st_uid not in (0, os.geteuid()) or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise Error('Unsafe state ancestor ownership/mode')


def save_state(state):
    # The containing directory holds original configuration/backups, root only.
    if STATE.parent.is_symlink():
        raise Error('Symlink state directory refused')
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_state_ancestors(STATE.parent)
    if STATE.parent.stat().st_uid != os.geteuid() or stat.S_IMODE(STATE.parent.stat().st_mode) != 0o700:
        raise Error('State directory must be owned by current root and mode 0700')
    atomic(STATE, (json.dumps(state, indent=2) + '\n').encode(), 0o600)


def planned(path, data, mode):
    before = read(path)
    if before == data:
        return None
    return {'path': str(path), 'before': base64.b64encode(before).decode() if before is not None else None,
            'before_mode': stat.S_IMODE(path.stat().st_mode) if before is not None else None,
            'before_uid': path.stat().st_uid if before is not None else None,
            'before_gid': path.stat().st_gid if before is not None else None,
            'after': digest(data), 'data': data, 'mode': mode}


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
    for directory in directories:
        safe(directory / '.guard')
        if not directory.is_dir() or directory.stat().st_uid != os.geteuid():
            raise Error(f'Unowned or missing companion theme directory: {directory}')
    return directories


def setup(domain, user, admin=False):
    domain, user = identity(domain, user)
    if read(STATE) is not None:
        state = load_state()
        if (state['domain'], state['user'], state['admin']) != (domain, user, admin):
            raise Error('Existing setup has different scope; rollback before changing scope')
        doctor()
        print('Already set up; no changes')
        return
    configuration_scope(domain, user)
    changes = []
    def add(path, data, mode=0o644):
        entry = planned(path, data, mode)
        if entry:
            changes.append(entry)
    original_pam = stock_pam()
    if read(PAM) is not None:
        raise Error('Dedicated PAM service exists without managed state; review manually')
    add(PAM, patch_source('lock-pam', original_pam))
    # Validate source, but never write Omarchy's packaged lock QML.
    lock = read(LOCK)
    if lock is None:
        raise Error('Missing Omarchy lock source')
    patch_source('lock-single-response', lock)
    add(CONFIG, managed_config(read(CONFIG), domain, user), 0o644)
    if COPY_THEME.exists() or COPY_THEME.is_symlink():
        raise Error('Companion SDDM theme already exists without managed state')
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
            content = patch_source('sddm-username', content)
        add(COPY_THEME / source.relative_to(THEME), content)
    if not any(p.name == 'Main.qml' for p in theme_files):
        raise Error('Original theme Main.qml missing')
    if read(OVERRIDE) is not None:
        raise Error('SDDM override already exists without managed state')
    add(OVERRIDE, b'[Theme]\nCurrent=omarchy-himmelblau\n')
    if admin:
        uid = administrator(user)
        if read(SUDOERS) is not None:
            raise Error('Companion sudoers file already exists without managed state')
        content = f'# Explicit single-identity admin opt-in; NSS UID, password/PIN required.\n#{uid} ALL=(ALL:ALL) ALL\n'.encode()
        with tempfile.NamedTemporaryFile() as candidate:
            candidate.write(content); candidate.flush()
            subprocess.run(['visudo', '-cf', candidate.name], check=True, capture_output=True)
        add(SUDOERS, content, 0o440)
    # All source/configuration preflight finished before touching guest files.
    state = {'version': VERSION, 'domain': domain, 'user': user, 'admin': admin,
             'files': [{k: v for k, v in entry.items() if k != 'data'} for entry in changes]}
    save_state(state)
    try:
        for directory in theme_directories(changes):
            safe(directory / '.guard')
            directory.mkdir(mode=0o755)
            directory.chmod(0o755)  # Public greeter assets, independent of root umask.
        for entry in changes:
            atomic(Path(entry['path']), entry['data'], entry['mode'])
    except Exception:
        try:
            rollback()
        except Exception:
            raise Error('Setup interrupted; automatic rollback blocked. Originals retained in root state; inspect manually.') from None
        raise Error('Setup failed; files changed by this attempt were restored.') from None
    print('Root setup complete. No daemon restart, authentication, join or session reload was performed.')
    print('In an unlocked graphical user session, run enable-user, then log out/in before lock testing.')


def stock_pam():
    data = read(STOCK_PAM)
    info = json.loads((ASSETS / 'manifest.json').read_text())['lock-pam']
    if data is None or digest(data) != info['before']:
        raise Error('Stock Omarchy PAM is modified/unsupported; restore a reviewed pristine source before setup or repair')
    return data


def require_complete_configuration(domain, user):
    current = read(CONFIG)
    if managed_config(current, domain, user) != current:
        raise Error('Managed Himmelblau configuration keys are missing; refusing effective-scope assumptions')


def root_issues(state):
    issues = []
    try:
        configuration_scope(state['domain'], state['user'])
        require_complete_configuration(state['domain'], state['user'])
        stock_pam()
    except Error as error:
        issues.append(str(error))
    for entry in state['files']:
        actual = read(Path(entry['path']))
        path = Path(entry['path'])
        if actual is None or digest(actual) != entry['after'] or stat.S_IMODE(path.stat().st_mode) != entry['mode'] or path.stat().st_uid != os.geteuid():
            issues.append(f'Managed file drift: {entry["path"]}')
    try:
        for directory in checked_theme_directories(state['files']):
            if stat.S_IMODE(directory.stat().st_mode) != 0o755:
                issues.append(f'Companion theme directory must be 0755: {directory}')
    except Error as error:
        issues.append(str(error))
    pam = read(PAM)
    expected_pam = json.loads((ASSETS / 'manifest.json').read_text())['lock-pam']['after']
    if pam is None or digest(pam) != expected_pam:
        issues.append(f'Lock PAM drift: {PAM}')
    for name, path in [('lock-single-response', LOCK)]:
        try:
            data = read(path)
            if data is None:
                raise Error('Missing source')
            patch_source(name, data)
        except Error:
            issues.append(f'Unsupported current source: {path}')
    return issues


def doctor():
    state = load_state()
    issues = root_issues(state)
    if issues:
        raise Error('\n'.join(issues))
    print('Managed root files and supported upstream source match. This is not an authentication or session test.')


def repair():
    state = load_state()
    configuration_scope(state['domain'], state['user'])
    require_complete_configuration(state['domain'], state['user'])
    desired_pam = patch_source('lock-pam', stock_pam())
    repairs = []
    # Preflight every managed file before reapplying any regenerated PAM file.
    for entry in state['files']:
        path = Path(entry['path'])
        data = read(path)
        if data is not None and digest(data) == entry['after']:
            if stat.S_IMODE(path.stat().st_mode) != entry['mode'] or path.stat().st_uid != os.geteuid():
                raise Error(f'Repair refuses permission drift: {path}')
            continue
        if path == PAM and (data is None or digest(data) == json.loads((ASSETS / 'manifest.json').read_text())['lock-pam']['before']):
            if digest(desired_pam) == entry['after']:
                repairs.append((path, desired_pam, entry['mode']))
                continue
        raise Error(f'Repair refuses unsupported managed drift: {path}')
    source = read(LOCK)
    if source is None:
        raise Error('Missing lock source')
    patch_source('lock-single-response', source)
    directories = checked_theme_directories(state['files'])
    for directory in directories:
        if stat.S_IMODE(directory.stat().st_mode) != 0o755:
            directory.chmod(0o755)
    for path, data, mode in repairs:
        atomic(path, data, mode)
    doctor()
    print('No session reload performed. Re-run enable-user as the graphical user to check its clone.')


def rollback():
    state = load_state()
    # Validate every target first: never partly restore over unrecognized edits.
    if COPY_THEME.is_dir():
        expected = {entry['path'] for entry in state['files']}
        for path in COPY_THEME.rglob('*'):
            if path.is_symlink() or (not path.is_dir() and str(path) not in expected):
                raise Error('Unexpected companion theme entry; refusing rollback')
    for entry in state['files']:
        data = read(Path(entry['path']))
        original = base64.b64decode(entry['before']) if entry['before'] is not None else None
        if data != original and (data is None or digest(data) != entry['after']):
            raise Error(f'Rollback refuses managed drift: {entry["path"]}')
    for entry in reversed(state['files']):
        path = Path(entry['path'])
        if entry['before'] is None:
            safe(path).unlink(missing_ok=True)
        else:
            atomic(path, base64.b64decode(entry['before']), entry['before_mode'])
            if os.geteuid() == 0:
                os.chown(path, entry['before_uid'], entry['before_gid'])
    # Only remove empty companion theme directories; never recursively delete others' data.
    if COPY_THEME.is_dir():
        for directory in sorted((p for p in COPY_THEME.rglob('*') if p.is_dir()), reverse=True):
            directory.rmdir()
        COPY_THEME.rmdir()
    STATE.unlink()
    print('Root managed files restored. Required beforehand: disable the clone and exit every session using it; keepLoaded code survives disabling. User clone/hooks remain separate.')


def canonical_user():
    account = pwd.getpwuid(os.getuid())
    alias = Path(account.pw_dir)
    if alias.is_symlink() and alias.lstat().st_uid not in (0, os.getuid()):
        raise Error('Unsafe NSS home alias owner')
    home = alias.resolve(strict=True)
    if not home.is_dir() or home.stat().st_uid != os.getuid():
        raise Error('Resolved NSS home must belong to the current user')
    return account, home


def user_paths():
    if os.geteuid() == 0:
        raise Error('enable-user must run as the graphical user, never through sudo')
    account, home = canonical_user()
    user = account.pw_name
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', user) or '..' in user:
        raise Error('Unsupported native clone user ID')
    if (user + '.lock').startswith('omarchy.'):
        raise Error('Native clone ID would use the reserved omarchy. namespace; enable-user is unsupported for this username')
    return (user, home / '.config/omarchy/plugins' / (user + '.lock'),
            home / '.local/state/omarchy-himmelblau/state.json',
            home / '.config/omarchy/hooks/post-update.d/omarchy-himmelblau-check')


def user_environment():
    account, home = canonical_user()
    return {**os.environ, **OMARCHY_ENV, 'HOME': str(home), 'USER': account.pw_name,
            'LOGNAME': account.pw_name, 'XDG_RUNTIME_DIR': f'/run/user/{os.getuid()}'}


def unlocked():
    result = subprocess.run(['omarchy-shell', 'lock', 'status'], check=True,
                            capture_output=True, text=True, env=user_environment())
    try:
        state = json.loads(result.stdout)
    except ValueError:
        raise Error('Unable to verify unlocked Quickshell state') from None
    keys = ('locked', 'requested', 'pending', 'sessionLocked', 'secure', 'authenticating')
    if any(state.get(key) is not False for key in keys):
        raise Error('Session locked/authenticating or state unknown; refusing clone changes')


def clone_hashes(clone):
    safe(clone / '.guard')
    result = {}
    for path in sorted(clone.rglob('*')):
        if path.is_symlink():
            raise Error('Symlink inside managed native clone')
        if path.is_file():
            result[str(path.relative_to(clone))] = digest(read(path))
    return result


def upstream_hashes():
    files = clone_hashes(LOCK.parent)
    source = read(LOCK)
    if source is None:
        raise Error('Missing upstream lock service')
    # Known before/after QML have identical intended behavior once patched.
    files['Service.qml'] = digest(patch_source('lock-single-response', source))
    return files


def user_status():
    user, clone, statefile, hook = user_paths()
    data = read(statefile)
    if data is None:
        raise Error('No managed user clone; run enable-user in an unlocked graphical session')
    state = json.loads(data)
    if state.get('phase') != 'ready':
        raise Error('Partial native clone setup; keep unlocked, inspect clone, disable it explicitly, and recover before retrying')
    if upstream_hashes() != state['upstream_files']:
        raise Error('Upstream lock plugin changed after cloning; explicit review and a new native clone are required')
    if clone_hashes(clone) != state['clone_files']:
        raise Error('Managed native clone drift; refusing automatic changes')
    for path, expected in [(clone / 'Service.qml', state['clone_hash']), (hook, state['hook_hash'])]:
        actual = read(path)
        if actual is None or digest(actual) != expected:
            raise Error(f'User managed drift: {path}')
    stock_pam()
    for name, path in [('lock-pam', PAM), ('lock-single-response', LOCK)]:
        actual = read(path)
        info = json.loads((ASSETS / 'manifest.json').read_text())[name]
        if name == 'lock-pam':
            if actual is None or digest(actual) != info['after']:
                raise Error('Dedicated lock PAM missing/drifted; review then run sudo omarchy-himmelblau repair explicitly')
        elif actual is None or digest(actual) not in (info['before'], info['after']):
            raise Error(f'Unsupported upstream source after update: {path}; explicit review needed')
    print(f'{user}.lock source and read-only hook match. Log out/in after changes; runtime activation is not inferred.')


def verify_native_clone(user):
    result = subprocess.run(['omarchy', 'plugin', 'list', '--json'], check=True,
                            capture_output=True, text=True, env=user_environment())
    try:
        plugins = json.loads(result.stdout)
    except ValueError:
        raise Error('Native plugin discovery returned invalid JSON') from None
    if not isinstance(plugins, list):
        raise Error('Native plugin discovery did not return the supported array schema')
    matches = [plugin for plugin in plugins if isinstance(plugin, dict) and plugin.get('id') == user + '.lock']
    if (len(matches) != 1 or matches[0].get('firstParty') is not False
            or matches[0].get('clonedFrom') != 'omarchy.lock'
            or matches[0].get('enabled') is not True):
        raise Error('Native discovery did not confirm the enabled third-party clone and its provenance')


def enable_user():
    user, clone, statefile, hook = user_paths()
    unlocked()
    if read(statefile) is not None:
        user_status()
        print('Existing managed clone unchanged; no reload performed')
        return
    safe(clone / 'Service.qml')
    if clone.exists() or clone.is_symlink():
        raise Error('Native clone already exists without managed state; refusing to overwrite')
    if read(hook) is not None:
        raise Error('Post-update hook already exists without managed state')
    info = json.loads((ASSETS / 'manifest.json').read_text())
    stock_pam()
    pam = read(PAM)
    if pam is None or digest(pam) != info['lock-pam']['after']:
        raise Error('Run root setup/repair before enable-user')
    source = read(LOCK)
    if source is None:
        raise Error('Missing original lock source')
    expected = patch_source('lock-single-response', source)
    original_tree = upstream_hashes()
    # Omarchy performs the complete native clone, including internal import rewriting.
    # It auto-enables the clone; keepLoaded code requires logout/login to adopt the patch.
    atomic(statefile, b'{"phase": "preparing"}\n', 0o600)
    try:
        subprocess.run(['omarchy', 'plugin', 'clone', 'omarchy.lock'], check=True, env=user_environment())
        verify_native_clone(user)
        actual = read(clone / 'Service.qml')
        if actual != source:
            raise Error('Native clone source differs; inspect manually before using the locker')
        atomic(clone / 'Service.qml', expected, 0o644)
        hook_data = ("#!/bin/sh\n# Read-only post-update check. Never repairs or reloads a locker.\n"
                     "if ! omarchy-himmelblau status; then\n"
                     "  printf '%s\\n' 'Review drift; root repair requires explicit sudo omarchy-himmelblau repair.' >&2\n"
                     "fi\n").encode()
        atomic(hook, hook_data, 0o755)
        atomic(statefile, (json.dumps({'phase': 'ready', 'version': VERSION, 'clone_hash': digest(expected),
                                     'hook_hash': digest(hook_data), 'clone_files': clone_hashes(clone), 'upstream_files': original_tree}, indent=2) + '\n').encode(), 0o600)
    except Exception as error:
        detail = f' {error}.' if isinstance(error, Error) else ''
        raise Error('Native clone preparation incomplete.' + detail + ' It may be enabled. Do not lock. Disable the native clone and inspect user state before retrying.') from None
    print('Native clone prepared and auto-enabled by Omarchy. No shell restart was performed.')
    print('Log out and back in while unlocked before lock testing; keepLoaded code may still be old until then.')
    print('Menu integration is an optional documented JSONC snippet; existing menus were not modified.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    sub = parser.add_subparsers(dest='command', required=True)
    setup_parser = sub.add_parser('setup', help='Explicit root single-account configuration; no authentication')
    setup_parser.add_argument('--domain', required=True)
    setup_parser.add_argument('--user', required=True)
    setup_parser.add_argument('--admin', action='store_true', help='Opt in the resolved NSS UID to authenticated sudo')
    for command in ('doctor', 'status', 'repair', 'rollback', 'enable-user'):
        sub.add_parser(command)
    args = parser.parse_args(argv)
    if args.command == 'enable-user':
        enable_user()
    elif args.command in ('doctor', 'status'):
        doctor() if os.geteuid() == 0 else user_status()
    else:
        root_required()
        if args.command == 'setup':
            setup(args.domain, args.user, args.admin)
        elif args.command == 'repair':
            repair()
        elif args.command == 'rollback':
            rollback()


if __name__ == '__main__':
    try:
        main()
    except (Error, OSError, ValueError, subprocess.CalledProcessError) as error:
        # Never print subprocess stdout/stderr (PAM/configuration can contain secrets).
        if isinstance(error, subprocess.CalledProcessError):
            sys.exit('Required command failed; no captured output was logged. Inspect its state directly.')
        sys.exit(str(error))
