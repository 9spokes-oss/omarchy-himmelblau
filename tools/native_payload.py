"""Which native SDDM files the companion pins, and how they are checked.

Shared by finish-release.py (hashes a built package archive) and
pin-installed-native.py (hashes the installed package for local AUR builds), so
both pins cover the same files under the same rules.
"""
import hashlib
from pathlib import PurePosixPath

EXECUTABLES = frozenset({
    '/usr/bin/sddm', '/usr/bin/sddm-greeter', '/usr/bin/sddm-greeter-qt6',
    '/usr/lib/sddm/sddm-helper', '/usr/lib/sddm/sddm-helper-start-wayland',
    '/usr/lib/sddm/sddm-helper-start-x11user'})
COMPONENT_PREFIXES = ('/usr/lib/qt/qml/SddmComponents/', '/usr/lib/qt6/qml/SddmComponents/')


class PayloadError(Exception):
    pass


def selected(path):
    return path in EXECUTABLES or path.startswith(COMPONENT_PREFIXES)


def collect(entries):
    """Hash the pinned files among `entries`.

    Each entry is (path, is_dir, is_file, mode, uid, gid, read), where `path` is
    absolute and `read()` returns the file's bytes.
    """
    files = {}
    for path, is_dir, is_file, mode, uid, gid, read in entries:
        if '..' in PurePosixPath(path).parts:
            raise PayloadError('Unsafe archive path')
        if not selected(path) or is_dir:
            continue
        if not is_file or uid != 0 or gid != 0 or mode & 0o7022:
            raise PayloadError('Unsafe native payload entry: ' + path)
        if path in EXECUTABLES and mode != 0o755:
            raise PayloadError('Unexpected executable mode: ' + path)
        files[path] = hashlib.sha256(read()).hexdigest()
    if not EXECUTABLES.issubset(files):
        raise PayloadError('Native package is missing authentication executables')
    for prefix in COMPONENT_PREFIXES:
        if prefix + 'qmldir' not in files or not any(
                path.startswith(prefix) and path.endswith('.qml') for path in files):
            raise PayloadError('Native package is missing a complete QML component tree: ' + prefix)
    return dict(sorted(files.items()))
