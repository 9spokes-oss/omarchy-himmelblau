import json
import re
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PluginGenerationTest(unittest.TestCase):
    def test_marketplace_discovers_only_root_plugin_manifest(self):
        # scripts/build-catalog.mjs discovers root or one-directory-deep manifests.
        candidates = [ROOT / 'manifest.json', *ROOT.glob('*/manifest.json')]
        discovered = sorted(path.relative_to(ROOT).as_posix() for path in candidates
                            if path.is_file() and re.fullmatch(r'(?:[^/]+/)?manifest\.json', path.relative_to(ROOT).as_posix()))
        self.assertEqual(discovered, ['manifest.json'])
        self.assertTrue((ROOT / 'assets/patches.json').is_file())
        self.assertFalse((ROOT / 'assets/manifest.json').exists())

    def test_actual_lock_plugin_matches_deterministic_pinned_generation(self):
        subprocess.run(['/usr/bin/python3','-I',str(ROOT/'plugin/generate.py'),'--check'],check=True,capture_output=True)
        manifest = json.loads((ROOT/'manifest.json').read_text())
        self.assertEqual(manifest['version'], '0.5.2')
        self.assertEqual(manifest['id'], '9spokes.himmelblau-lock')
        self.assertEqual(manifest['omarchy']['clonedFrom'], 'omarchy.lock')
        self.assertEqual(manifest['entryPoints'], {'service':'plugin/Service.qml'})
        self.assertEqual(manifest['kinds'], ['service'])
        self.assertIs(manifest['keepLoaded'], True)
        self.assertEqual((ROOT/'plugin/LICENSE').read_bytes(), (ROOT/'LICENSE').read_bytes())
        self.assertIn('David Heinemeier Hansson', (ROOT/'plugin/UPSTREAM-LICENSE').read_text())
        self.assertIn('Qt.resolvedUrl("poster.sh")', (ROOT/'plugin/Service.qml').read_text())
