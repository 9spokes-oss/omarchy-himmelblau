"""The AUR recipes stay in step with the release recipes and version pins."""
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('companion', ROOT / 'src/omarchy_himmelblau.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

RELEASE_NATIVE = ROOT / 'packaging/sddm-himmelblau/PKGBUILD'
AUR_NATIVE = ROOT / 'packaging/aur/sddm-himmelblau/PKGBUILD'
AUR_COMPANION = ROOT / 'packaging/aur/omarchy-himmelblau/PKGBUILD'
SHARED = ('depends', 'makedepends', 'checkdepends', 'optdepends', 'provides', 'conflicts', 'backup')


def recipe(path, *names, functions=()):
    """Source a PKGBUILD in bash and return the named variables and functions."""
    script = f'source {str(path)!r} >/dev/null; ' + '; '.join(
        [f'declare -p {name}' for name in names] + [f'declare -f {name}' for name in functions])
    return subprocess.run(['bash', '-c', script], capture_output=True, text=True, check=True,
                          timeout=10, env={'PATH': '/usr/bin:/bin'}).stdout


def value(path, name):
    return re.search(rf'^declare -- {name}="([^"]*)"$', recipe(path, name), re.M)[1]


@unittest.skipUnless(shutil.which('bash'), 'bash is required to read PKGBUILDs')
class AurRecipeTest(unittest.TestCase):
    def test_versions_agree(self):
        self.assertEqual(f"{value(AUR_NATIVE, 'pkgver')}-{value(AUR_NATIVE, 'pkgrel')}", m.NATIVE_VERSION)
        self.assertEqual(value(AUR_NATIVE, '_release'), m.VERSION)
        self.assertEqual(value(AUR_COMPANION, 'pkgver'), m.VERSION)
        self.assertEqual(value(AUR_COMPANION, '_native'), m.NATIVE_VERSION)

    def test_native_recipe_matches_release_recipe(self):
        self.assertEqual(recipe(AUR_NATIVE, *SHARED, functions=['build']),
                         recipe(RELEASE_NATIVE, *SHARED, functions=['build']))
        self.assertIn("'f895de2683627e969e4849dbfbbb2b500787481ca5ba0de6d6dfdae5f1549abf'",
                      AUR_NATIVE.read_text(), 'upstream SDDM archive checksum differs from the release recipe')

    def test_companion_dependencies_match_release_except_omarchy(self):
        def depends(path):
            script = f'source {str(path)!r} >/dev/null; printf "%s\\n" "${{depends[@]}}"'
            return subprocess.run(['bash', '-c', script], capture_output=True, text=True, check=True,
                                  timeout=10, env={'PATH': '/usr/bin:/bin'}).stdout.split()
        release = depends(ROOT / 'packaging/PKGBUILD')
        self.assertIn('omarchy', release)
        self.assertEqual(depends(AUR_COMPANION), [d for d in release if d != 'omarchy'])

    def test_aur_repositories_are_publishable(self):
        for path in (AUR_NATIVE, AUR_COMPANION):
            self.assertTrue(path.read_text().startswith('# Maintainer: @MAINTAINER@\n'), path)
            self.assertIn('Permission to use, copy, modify, and/or distribute',
                          (path.parent / 'LICENSE').read_text(), path.parent)

    def test_release_recipe_checksums_match_committed_inputs(self):
        script = (f'source {str(RELEASE_NATIVE)!r} >/dev/null; '
                  'for i in "${!source[@]}"; do printf "%s %s\\n" "${source[i]}" "${sha256sums[i]}"; done')
        lines = subprocess.run(['bash', '-c', script], capture_output=True, text=True, check=True,
                               timeout=10, env={'PATH': '/usr/bin:/bin'}).stdout.splitlines()
        committed = {name: digest for name, digest in (line.split() for line in lines)
                     if (ROOT / 'packaging/sddm-himmelblau' / name).is_file()}
        self.assertEqual(len(committed), 5, lines)
        for name, digest in committed.items():
            data = (ROOT / 'packaging/sddm-himmelblau' / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)


if __name__ == '__main__':
    unittest.main()
