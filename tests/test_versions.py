"""Every version pin agrees with its one source.

Sources: `manifest.json` for the plugin (generate.py copies it into patches.json),
`VERSION` for the companion, and the native PKGBUILD's pkgver-pkgrel for sddm-himmelblau
(finish-release.py reads it from there). The installed companion cannot read a PKGBUILD,
so the copies it needs are checked here instead.
"""
import importlib.util
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('companion', ROOT / 'src/omarchy_himmelblau.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def pkgbuild(path, key):
    return re.search(rf'^{key}=(\S+)$', (ROOT / path).read_text(), re.M)[1]


class VersionPinTest(unittest.TestCase):
    def test_plugin_and_companion_versions_agree(self):
        plugin = json.loads((ROOT / 'manifest.json').read_text())['version']
        fixed = json.loads((ROOT / 'assets/patches.json').read_text())['fixed-plugin']['version']
        self.assertEqual(fixed, plugin)
        self.assertEqual(m.VERSION, plugin)
        self.assertEqual(pkgbuild('packaging/PKGBUILD', 'pkgver'), m.VERSION)

    def test_native_version_agrees(self):
        native = 'packaging/sddm-himmelblau/PKGBUILD'
        pkgver = pkgbuild(native, 'pkgver')
        self.assertEqual(f"{pkgver}-{pkgbuild(native, 'pkgrel')}", m.NATIVE_VERSION)
        depends = re.findall(r"'sddm-himmelblau=([^']+)'", (ROOT / 'packaging/PKGBUILD').read_text())
        self.assertEqual(depends, [m.NATIVE_VERSION])
        provenance = json.loads((ROOT / 'packaging/sddm-himmelblau/provenance.json').read_text())
        self.assertEqual(provenance['baseline_tag'], 'v' + pkgver)


if __name__ == '__main__':
    unittest.main()
