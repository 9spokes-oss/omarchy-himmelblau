#!/usr/bin/python3
"""Pin the installed sddm-himmelblau files for a locally built companion (AUR).

Usage: pin-installed-native.py NATIVE_VERSION SOURCE_COMMIT OUTPUT

A local build has no published native artifact to hash, so the manifest says
"build": "local" and carries no artifact_sha256. The pin then means: these are
the files sddm-himmelblau NATIVE_VERSION installed when the companion was
built. The companion's doctor still detects later changes to them. That they
came from the reviewed source rests on the PKGBUILDs' pinned source checksums.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_payload import COMPONENT_PREFIXES, PayloadError, collect, package_paths  # noqa: E402

if len(sys.argv) != 4:
    raise SystemExit('Usage: pin-installed-native.py NATIVE_VERSION SOURCE_COMMIT OUTPUT')
version, commit, output = sys.argv[1], sys.argv[2], Path(sys.argv[3])
if not re.fullmatch(r'[0-9a-f]{40}', commit):
    raise SystemExit('SOURCE_COMMIT must be a full commit hash')


def pacman(*args):
    try:
        return subprocess.run(['/usr/bin/pacman', *args], capture_output=True, text=True,
                              timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise SystemExit('pacman query failed: ' + ' '.join(args)) from None


if pacman('-Q', 'sddm-himmelblau').strip() != f'sddm-himmelblau {version}':
    raise SystemExit(f'sddm-himmelblau {version} is not installed')

found = set()
for prefix in COMPONENT_PREFIXES:
    directory = Path(prefix)
    if directory.is_symlink() or not directory.is_dir():
        raise SystemExit('Missing native QML component directory: ' + prefix)
    found.update(str(path) for path in directory.rglob('*') if not path.is_dir() or path.is_symlink())
try:
    paths = package_paths(pacman('-Qlq', 'sddm-himmelblau').splitlines(), found)
except PayloadError as error:
    raise SystemExit(str(error)) from None


def entry(path):
    metadata = os.lstat(path)
    return (path, stat.S_ISDIR(metadata.st_mode), stat.S_ISREG(metadata.st_mode),
            stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid,
            lambda: Path(path).read_bytes())


try:
    files = collect(entry(path) for path in sorted(paths))
except PayloadError as error:
    raise SystemExit(str(error)) from None
manifest = {'package': 'sddm-himmelblau', 'version': version, 'build': 'local',
            'artifact_sha256': None, 'source_commit': commit, 'files': files}
output.write_text(json.dumps(manifest, indent=2) + '\n')
print('Pinned', len(files), 'installed native files.')
