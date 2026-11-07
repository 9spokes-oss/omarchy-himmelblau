import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

NATIVE_RECIPE = (ROOT / 'packaging/sddm-himmelblau/PKGBUILD').read_text()
NATIVE_VERSION = '-'.join(re.search(rf'^{key}=(\S+)$', NATIVE_RECIPE, re.M)[1] for key in ('pkgver', 'pkgrel'))
NATIVE_PATHS = ['/usr/bin/sddm', '/usr/bin/sddm-greeter', '/usr/bin/sddm-greeter-qt6',
                '/usr/lib/sddm/sddm-helper', '/usr/lib/sddm/sddm-helper-start-wayland',
                '/usr/lib/sddm/sddm-helper-start-x11user']

def release_directory(root):
    (root / 'companion').mkdir()
    (root / 'native').mkdir()
    (root / 'companion/PKGBUILD').write_text('@NATIVE_PAYLOAD_SHA256@')
    (root / 'native/PKGBUILD').write_text(NATIVE_RECIPE)
    (root / 'source.tar.gz').write_bytes(b'test-source')
    (root / 'release.json').write_text(json.dumps({'commit': 'a' * 40,
        'source_sha256': hashlib.sha256(b'test-source').hexdigest()}))

def native_package(path, pkginfo=b'pkgname = sddm-himmelblau\npkgver = ' + NATIVE_VERSION.encode() + b'\n', complete=True):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w') as archive:
        def add(name, data, mode):
            entry = tarfile.TarInfo(name.lstrip('/'))
            entry.uid = entry.gid = 0
            entry.mode = mode
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        if pkginfo is not None:
            add('.PKGINFO', pkginfo, 0o644)
        for name in NATIVE_PATHS:
            add(name, b'fixture-executable', 0o755)
        if complete:
            for prefix in ('usr/lib/qt/qml/SddmComponents/', 'usr/lib/qt6/qml/SddmComponents/'):
                add(prefix + 'qmldir', b'fixture-component', 0o644)
                add(prefix + 'Component.qml', b'Item {}', 0o644)
    path.write_bytes(subprocess.check_output(['zstd', '-q', '-c'], input=payload.getvalue()))
    return path

def finish(*arguments):
    return subprocess.run(['python3', str(ROOT / 'tools/finish-release.py'), *map(str, arguments)],
                          capture_output=True, timeout=10)

class ReleaseTest(unittest.TestCase):
    def test_export_refuses_dirty_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tools').mkdir()
            shutil.copyfile(ROOT / 'tools/prepare-release.py', root / 'tools/prepare-release.py')
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            result = subprocess.run(['python3', str(root / 'tools/prepare-release.py'), str(root / 'out')], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'clean committed worktree', result.stderr)
            self.assertFalse((root / 'out').exists())

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required for package archive verification')
    def test_native_payload_requires_both_complete_component_trees(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release_directory(root)
            for complete in (False, True):
                package = native_package(root / 'native.pkg.tar.zst', complete=complete)
                result = finish(root, package)
                self.assertEqual(result.returncode, 0 if complete else 1, result.stderr)
                if complete:
                    manifest = json.loads((root / 'companion/native-payload.json').read_text())
                    self.assertEqual(len(manifest['files']), 10)
                    self.assertEqual(manifest['version'], NATIVE_VERSION)
                    self.assertEqual(manifest['artifact_sha256'], hashlib.sha256(package.read_bytes()).hexdigest())
                else:
                    self.assertFalse((root / 'companion/native-payload.json').exists())

    def test_finish_requires_both_arguments(self):
        result = finish()
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'Usage: finish-release.py', result.stderr)
        self.assertNotIn(b'Traceback', result.stderr)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required for package archive verification')
    def test_finish_reports_missing_pkginfo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release_directory(root)
            package = native_package(root / 'native.pkg.tar.zst', pkginfo=None)
            result = finish(root, package)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'no .PKGINFO', result.stderr)
            self.assertNotIn(b'Traceback', result.stderr)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required for package archive verification')
    def test_finish_matches_package_version_literally(self):
        # An unescaped '.' in the version would accept this look-alike.
        lookalike = NATIVE_VERSION.replace('.', 'x', 1)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release_directory(root)
            package = native_package(root / 'native.pkg.tar.zst',
                                     pkginfo=f'pkgname = sddm-himmelblau\npkgver = {lookalike}\n'.encode())
            result = finish(root, package)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'Unexpected native package identity', result.stderr)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd is required for package archive verification')
    def test_finish_refuses_already_pinned_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release_directory(root)
            package = native_package(root / 'native.pkg.tar.zst')
            self.assertEqual(finish(root, package).returncode, 0)
            pinned = {name: (root / name).read_bytes() for name in ('companion/PKGBUILD', 'release.json')}
            result = finish(root, package)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'already pinned', result.stderr)
            self.assertNotIn(b'Traceback', result.stderr)
            self.assertEqual(pinned, {name: (root / name).read_bytes() for name in pinned})

    def test_corresponding_source_archive_ignores_host_umask(self):
        # prepare-native.py is replaced by a stub that writes files under the host umask;
        # the real one needs the network.
        stub = (
            "import sys\n"
            "from pathlib import Path\n"
            "tree = Path(sys.argv[1]) / 'sddm-1.2.3'\n"
            "(tree / 'sub').mkdir(parents=True)\n"
            "(tree / 'sub/plain.txt').write_text('plain')\n"
            "(tree / 'run.sh').write_text('run')\n"
            "(tree / 'run.sh').chmod(0o750)\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tools').mkdir()
            shutil.copyfile(ROOT / 'tools/corresponding-source.py', root / 'tools/corresponding-source.py')
            (root / 'tools/prepare-native.py').write_text(stub)
            (root / 'packaging/sddm-himmelblau').mkdir(parents=True)
            (root / 'packaging/sddm-himmelblau/provenance.json').write_text(json.dumps({'baseline_tag': 'v1.2.3'}))
            git = ['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']
            subprocess.run([*git, 'init', '-q'], check=True)
            subprocess.run([*git, 'add', '.'], check=True)
            subprocess.run([*git, 'commit', '-q', '-m', 'fixture'], check=True)
            commit = subprocess.check_output([*git, 'rev-parse', 'HEAD']).decode().strip()
            source = subprocess.check_output([*git, 'archive', '--format=tar.gz', '--prefix=omarchy-himmelblau/', commit])
            digests = []
            for umask in ('022', '077'):
                release = root / ('release-' + umask)
                release.mkdir()
                (release / 'source.tar.gz').write_bytes(source)
                (release / 'release.json').write_text(json.dumps({'commit': commit,
                    'source_sha256': hashlib.sha256(source).hexdigest()}))
                subprocess.run(['sh', '-c', f'umask {umask}; exec python3 "$0" "$1"',
                                str(root / 'tools/corresponding-source.py'), str(release)],
                               check=True, capture_output=True, timeout=30)
                output = (release / 'sddm-corresponding-source.tar.gz').read_bytes()
                digests.append(hashlib.sha256(output).hexdigest())
            self.assertEqual(digests[0], digests[1])
            with tarfile.open(release / 'sddm-corresponding-source.tar.gz') as archive:
                modes = {m.name: (m.mode, m.uid, m.gid, m.mtime) for m in archive.getmembers()}
            self.assertEqual(modes, {'sddm-1.2.3': (0o755, 0, 0, 0), 'sddm-1.2.3/run.sh': (0o755, 0, 0, 0),
                                     'sddm-1.2.3/sub': (0o755, 0, 0, 0), 'sddm-1.2.3/sub/plain.txt': (0o644, 0, 0, 0)})

    def test_corresponding_source_refuses_other_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'tools').mkdir()
            shutil.copyfile(ROOT / 'tools/corresponding-source.py', root / 'tools/corresponding-source.py')
            git = ['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']
            subprocess.run([*git, 'init', '-q'], check=True)
            subprocess.run([*git, 'add', '.'], check=True)
            subprocess.run([*git, 'commit', '-q', '-m', 'fixture'], check=True)
            release = root / 'release'
            release.mkdir()
            (release / 'release.json').write_text(json.dumps({'commit': 'b' * 40}))
            result = subprocess.run(['python3', str(root / 'tools/corresponding-source.py'), str(release)],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'exact release source checkout', result.stderr)
