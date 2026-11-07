import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

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
        paths = ['/usr/bin/sddm', '/usr/bin/sddm-greeter', '/usr/bin/sddm-greeter-qt6',
                 '/usr/lib/sddm/sddm-helper', '/usr/lib/sddm/sddm-helper-start-wayland',
                 '/usr/lib/sddm/sddm-helper-start-x11user']
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'companion').mkdir()
            (root / 'companion/PKGBUILD').write_text('@NATIVE_PAYLOAD_SHA256@')
            (root / 'source.tar.gz').write_bytes(b'test-source')
            (root / 'release.json').write_text(json.dumps({'commit': 'a' * 40,
                'source_sha256': hashlib.sha256(b'test-source').hexdigest()}))
            for complete in (False, True):
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode='w') as archive:
                    def add(name, data, mode):
                        entry = tarfile.TarInfo(name.lstrip('/'))
                        entry.uid = entry.gid = 0
                        entry.mode = mode
                        entry.size = len(data)
                        archive.addfile(entry, io.BytesIO(data))
                    add('.PKGINFO', b'pkgname = sddm-himmelblau\npkgver = 0.21.0-2\n', 0o644)
                    for path in paths:
                        add(path, b'fixture-executable', 0o755)
                    if complete:
                        for prefix in ('usr/lib/qt/qml/SddmComponents/', 'usr/lib/qt6/qml/SddmComponents/'):
                            add(prefix + 'qmldir', b'fixture-component', 0o644)
                            add(prefix + 'Component.qml', b'Item {}', 0o644)
                package = root / 'native.pkg.tar.zst'
                package.write_bytes(subprocess.check_output(['zstd', '-q', '-c'], input=payload.getvalue()))
                result = subprocess.run(['python3', str(ROOT / 'tools/finish-release.py'), str(root), str(package)], capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0 if complete else 1, result.stderr)
                if complete:
                    manifest = json.loads((root / 'companion/native-payload.json').read_text())
                    self.assertEqual(len(manifest['files']), 10)
                    self.assertEqual(manifest['artifact_sha256'], hashlib.sha256(package.read_bytes()).hexdigest())
                else:
                    self.assertFalse((root / 'companion/native-payload.json').exists())
