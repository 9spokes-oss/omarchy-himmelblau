#!/usr/bin/python3
"""Pin the independently built native payload into the companion package input.

Usage: finish-release.py RELEASE_DIRECTORY NATIVE_PACKAGE
Run only on the artifact you built from the reviewed release export. A package's
own metadata is not a signature; published SHA256SUMS and source are the trust
boundary. This creates inputs for a separately trusted companion artifact.
"""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

if len(sys.argv) != 3:
    raise SystemExit('Usage: finish-release.py RELEASE_DIRECTORY NATIVE_PACKAGE')
directory, package = (Path(value).resolve() for value in sys.argv[1:3])
record = json.loads((directory / 'release.json').read_text())
if hashlib.sha256((directory / 'source.tar.gz').read_bytes()).hexdigest() != record['source_sha256']:
    raise SystemExit('Release source changed')
recipe = directory / 'companion/PKGBUILD'
if '@NATIVE_PAYLOAD_SHA256@' not in recipe.read_text():
    raise SystemExit('companion/PKGBUILD is already pinned; export a fresh release to pin another package')
native_recipe = (directory / 'native/PKGBUILD').read_text()
fields = [re.search(rf'^{key}=(\S+)$', native_recipe, re.M) for key in ('pkgver', 'pkgrel')]
if not all(fields):
    raise SystemExit('native/PKGBUILD has no pkgver or pkgrel')
version = '-'.join(field[1] for field in fields)
data = subprocess.check_output(['/usr/bin/zstd', '-d', '-q', '-c', str(package)], timeout=120)
required = {'/usr/bin/sddm', '/usr/bin/sddm-greeter', '/usr/bin/sddm-greeter-qt6',
            '/usr/lib/sddm/sddm-helper', '/usr/lib/sddm/sddm-helper-start-wayland',
            '/usr/lib/sddm/sddm-helper-start-x11user'}
files = {}
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    members = archive.getmembers()
    canonical = [str(PurePosixPath(m.name.removeprefix('./'))) for m in members]
    if any(m.name.startswith('/') or '..' in PurePosixPath(m.name).parts for m in members):
        raise SystemExit('Unsafe archive path')
    if len(set(canonical)) != len(members):
        raise SystemExit('Duplicate archive entries')
    pkginfo = dict(zip(canonical, members)).get('.PKGINFO')
    if pkginfo is None or not pkginfo.isfile():
        raise SystemExit('Native package has no .PKGINFO')
    info = archive.extractfile(pkginfo).read().decode()
    if not re.search(r'^pkgname = sddm-himmelblau$', info, re.M) or not re.search(rf'^pkgver = {re.escape(version)}$', info, re.M):
        raise SystemExit('Unexpected native package identity')
    for member in members:
        path = '/' + member.name.removeprefix('./')
        if '..' in PurePosixPath(path).parts:
            raise SystemExit('Unsafe archive path')
        selected = path in required or any(path.startswith(prefix) for prefix in (
            '/usr/lib/qt/qml/SddmComponents/', '/usr/lib/qt6/qml/SddmComponents/'))
        if not selected or member.isdir():
            continue
        if not member.isfile() or member.uid != 0 or member.gid != 0 or member.mode & 0o7022:
            raise SystemExit('Unsafe native payload entry: ' + path)
        if path in required and member.mode != 0o755:
            raise SystemExit('Unexpected executable mode: ' + path)
        files[path] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
if not required.issubset(files):
    raise SystemExit('Native package is missing authentication executables')
for prefix in ('/usr/lib/qt/qml/SddmComponents/', '/usr/lib/qt6/qml/SddmComponents/'):
    if prefix + 'qmldir' not in files or not any(path.startswith(prefix) and path.endswith('.qml') for path in files):
        raise SystemExit('Native package is missing a complete QML component tree: ' + prefix)
manifest = {'package': 'sddm-himmelblau', 'version': version,
            'artifact_sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
            'source_commit': record['commit'], 'files': dict(sorted(files.items()))}
payload = (json.dumps(manifest, indent=2) + '\n').encode()
(directory / 'companion/native-payload.json').write_bytes(payload)
recipe.write_text(recipe.read_text().replace('@NATIVE_PAYLOAD_SHA256@', hashlib.sha256(payload).hexdigest()))
record['native_artifact_sha256'] = manifest['artifact_sha256']
(directory / 'release.json').write_text(json.dumps(record, indent=2) + '\n')
print('Pinned', len(files), 'native payload files; companion/ is ready for makepkg.')
