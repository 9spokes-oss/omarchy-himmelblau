"""Parsing and decisions of tools/check-upstream-sddm.py (no network, no git)."""
import importlib.util
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('check_upstream', ROOT / 'tools/check-upstream-sddm.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

PICK = '228778c2b4b7e26db1e1d69fe484ed75c5791c3a'
# Trimmed from Arch's sddm PKGBUILD at 0.21.0-7.
ARCH_PKGBUILD = f"""pkgname=sddm
pkgver=0.21.0
pkgrel=7
source=(git+https://github.com/$pkgname/$pkgname#tag=v$pkgver)
sha256sums=('67394c93f331fc02f89559f68e149a992efaed07690f548e6a83ec384ebb8000')

prepare() {{
  git -C $pkgname cherry-pick -n {PICK} # Fix build with cmake 4
}}

build() {{
  cmake -B build -S $pkgname
}}
"""

PATCH_OUTPUT = """patching file src/daemon/Display.cpp
Hunk #4 succeeded at 159 (offset 6 lines).
patching file src/daemon/Display.h
Hunk #3 FAILED at 77.
Hunk #4 FAILED at 109.
2 out of 4 hunks FAILED -- saving rejects to file src/daemon/Display.h.rej
patching file src/greeter/GreeterApp.cpp
Reversed (or previously applied) patch detected!  Skipping patch.
1 out of 1 hunk ignored -- saving rejects to file src/greeter/GreeterApp.cpp.rej
"""


class PkgbuildTest(unittest.TestCase):
    def test_reads_version_and_cherry_picks(self):
        self.assertEqual(c.parse_pkgbuild(ARCH_PKGBUILD),
                         {'pkgver': '0.21.0', 'pkgrel': '7', 'cherry_picks': [PICK], 'unknown': []})

    def test_steps_it_cannot_replay_are_reported(self):
        text = ARCH_PKGBUILD.replace(
            'source=(git+https://github.com/$pkgname/$pkgname#tag=v$pkgver)',
            "source=(git+https://github.com/$pkgname/$pkgname#tag=v$pkgver\n        'fix.patch')"
        ).replace('# Fix build with cmake 4\n', '\n  patch -d $pkgname -Np1 -i ../fix.patch\n')
        self.assertEqual(c.parse_pkgbuild(text)['unknown'],
                         ['source: fix.patch', 'prepare: patch -d $pkgname -Np1 -i ../fix.patch'])

    def test_no_prepare_means_no_cherry_picks(self):
        text = re.sub(r'prepare\(\) \{.*?\n\}\n', '', ARCH_PKGBUILD, flags=re.S)
        self.assertEqual(c.parse_pkgbuild(text)['cherry_picks'], [])

    def test_drift(self):
        baseline = c.parse_pkgbuild(ARCH_PKGBUILD)
        self.assertEqual(c.arch_drift(baseline, baseline), [])
        bumped = c.parse_pkgbuild(ARCH_PKGBUILD.replace('pkgrel=7', 'pkgrel=8').replace(
            f'-n {PICK}', f'-n {PICK}\n  git -C $pkgname cherry-pick -n {"a" * 40}'))
        self.assertEqual(c.arch_drift(baseline, bumped), [
            'pkgrel: 7 -> 8', f"cherry_picks: ['{PICK}'] -> ['{PICK}', '{'a' * 40}']"])
        odd = c.parse_pkgbuild(ARCH_PKGBUILD.replace('# Fix', '\n  sed -i s/a/b/ x # Fix'))
        self.assertEqual(c.arch_drift(baseline, odd), ['cannot replay prepare: sed -i s/a/b/ x'])


class UpstreamTest(unittest.TestCase):
    def test_newer_release_tags(self):
        tags = ['v0.20.0', 'v0.21.0', 'v0.21.1', 'v0.22.0', 'v0.21.10', 'v0.22.0-rc1', 'latest']
        self.assertEqual(c.newer_tags(tags, 'v0.21.0'), ['v0.21.1', 'v0.21.10', 'v0.22.0'])
        self.assertEqual(c.newer_tags(['v0.20.0', 'v0.21.0'], 'v0.21.0'), [])

    def test_failed_hunks(self):
        failures = c.failed_hunks(PATCH_OUTPUT)
        self.assertEqual(failures, {'src/daemon/Display.h': ['#3', '#4'],
                                    'src/greeter/GreeterApp.cpp': ['already applied']})
        self.assertEqual(c.describe('0001.patch', failures, 1),
                         '0001.patch: src/daemon/Display.h #3 #4; src/greeter/GreeterApp.cpp already applied')
        self.assertEqual(c.describe('0001.patch', {}, 2), '0001.patch: patch exited 2')

    def test_series_matches_native_recipe(self):
        recipe = (ROOT / 'packaging/sddm-himmelblau/PKGBUILD').read_text()
        applied = re.findall(r'patch --batch --fuzz=0 -p1 < "\$srcdir/(\S+)"', recipe)
        self.assertEqual([name for name, _ in c.SERIES], applied)
        provenance = json.loads((ROOT / 'packaging/sddm-himmelblau/provenance.json').read_text())
        for _, key in c.SERIES:
            if key:
                self.assertRegex(provenance[key], r'^[0-9a-f]{40}$')
        for key in ('arch_packaging_commit', 'baseline_commit', 'baseline_tag'):
            self.assertIn(key, provenance)


class IssueDecisionTest(unittest.TestCase):
    def test_fingerprint_ignores_order(self):
        self.assertEqual(c.fingerprint(['a', 'b']), c.fingerprint(['b', 'a']))
        self.assertNotEqual(c.fingerprint(['a']), c.fingerprint(['a', 'b']))

    def test_decide(self):
        seen = {'number': '7', 'texts': ['report\n<!-- upstream-sddm-fingerprint: abc -->', 'ok']}
        self.assertEqual(c.decide(1, None, 'abc'), 'open')
        self.assertIsNone(c.decide(1, seen, 'abc'))
        self.assertEqual(c.decide(1, seen, 'def'), 'comment')
        self.assertEqual(c.decide(0, seen, None), 'close')
        self.assertIsNone(c.decide(0, None, None))
        # A check that could not run neither opens nor closes anything.
        self.assertIsNone(c.decide(2, None, None))
        self.assertIsNone(c.decide(2, seen, None))


if __name__ == '__main__':
    unittest.main()
