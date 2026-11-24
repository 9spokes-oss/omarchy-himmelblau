#!/usr/bin/python3
"""Export a clean committed tree and checksum-pinned makepkg inputs.

Build native/ first, then run finish-release.py with its exact package archive.
When the native inputs are unchanged since the previous release, reuse that
release's native package instead (see tools/native_inputs.py).
Nothing installs packages, changes services, or reads private lab files.
"""
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'tools'))
from native_inputs import NativeInputsError, decide  # noqa: E402

def git(*args):
    return subprocess.check_output(['/usr/bin/git', '-C', str(root), *args])
if git('status', '--porcelain', '--untracked-files=all'):
    raise SystemExit('Release export requires a clean committed worktree, including untracked files')
commit = git('rev-parse', 'HEAD').decode().strip()
tree = git('rev-parse', commit + '^{tree}').decode().strip()
def committed(path):
    return git('show', commit + ':' + path)
try:
    native = decide(root, commit)
except NativeInputsError as error:
    raise SystemExit(str(error)) from None
output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=False)
archive = gzip.compress(git('archive', '--format=tar', '--prefix=omarchy-himmelblau/', commit), mtime=0)
digest = hashlib.sha256(archive).hexdigest()
(output / 'source.tar.gz').write_bytes(archive)
version = re.search(r'^pkgver=(\S+)$', committed('packaging/PKGBUILD').decode(), re.M)[1]
record = {'commit': commit, 'tree': tree, 'source_sha256': digest, 'version': version, **native}
(output / 'release.json').write_text(json.dumps(record, indent=2) + '\n')
for kind, path in [('native', 'packaging/sddm-himmelblau'), ('companion', 'packaging')]:
    directory = output / kind
    directory.mkdir()
    (directory / 'source.tar.gz').write_bytes(archive)
    recipe = committed(path + '/PKGBUILD').decode().replace('@SOURCE_SHA256@', digest)
    (directory / 'PKGBUILD').write_text(recipe)
    if kind == 'native':
        for name in ['0000-arch-cmake4.patch', '0001-native-conversation.patch', '0002-greeter-keep-running.patch',
                     'sddm-himmelblau-pin-change.pam', 'LGPL-2.1.txt']:
            (directory / name).write_bytes(committed(path + '/' + name))
print('Clean source export:', commit)
if 'native_reused_from' in native:
    print(f"Native inputs are unchanged since {native['native_reused_from']} "
          f"({native['native_source_commit']}): do not build native/. Run tools/finish-release.py "
          f"against {native['native_reused_from']}'s published sddm-himmelblau {native['native_version']} file.")
else:
    print('Build native/ with makepkg, then run tools/finish-release.py against that package.')
