import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PluginGenerationTest(unittest.TestCase):
    def test_actual_lock_plugin_matches_deterministic_pinned_generation(self):
        subprocess.run(['/usr/bin/python3','-I',str(ROOT/'plugin/generate.py'),'--check'],check=True,capture_output=True)
        manifest = json.loads((ROOT/'manifest.json').read_text())
        self.assertEqual(manifest['id'], '9spokes.himmelblau-lock')
        self.assertEqual(manifest['omarchy']['clonedFrom'], 'omarchy.lock')
        self.assertEqual(manifest['entryPoints'], {'service':'plugin/Service.qml'})
        self.assertEqual(manifest['kinds'], ['service'])
        self.assertIs(manifest['keepLoaded'], True)
        self.assertEqual((ROOT/'plugin/LICENSE').read_bytes(), (ROOT/'LICENSE').read_bytes())
        self.assertIn('David Heinemeier Hansson', (ROOT/'plugin/UPSTREAM-LICENSE').read_text())
        self.assertIn('Qt.resolvedUrl("poster.sh")', (ROOT/'plugin/Service.qml').read_text())
