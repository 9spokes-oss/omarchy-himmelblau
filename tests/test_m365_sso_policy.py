import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('m365_policy', ROOT / 'tools/m365-sso-policy.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class M365PolicyTest(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o755)
        self.directory = self.root / 'policies/managed'
        self.policy = m.generate('example.sharepoint.com', 'example-my.sharepoint.com')

    def install(self):
        return m._install(self.policy, self.directory, self.root, os.getuid())

    def test_preserves_actual_global_preset_and_normalizes_additions(self):
        preset = json.loads(m.PRESET.read_text())
        policy = m.generate('EXAMPLE.sharepoint.com', 'OTHER-my.sharepoint.com', ['APP.example.com', 'app.example.com'])
        known = m.apps_in(policy)
        for host, value in m.apps_in(preset).items():
            self.assertIs(known[host], value)
        self.assertEqual(len(known), len(m.apps_in(preset)) + 3)
        self.assertIs(known['example.sharepoint.com'], True)
        self.assertIs(known['other-my.sharepoint.com'], True)
        self.assertIs(known['app.example.com'], True)

    def test_invalid_preset_hosts_values_and_normalization_collisions_are_refused(self):
        preset = self.root / 'preset.json'
        for hosts in [{}, {'*.example.com': True}, {'https://app.example.com': True},
                      {'app.example.com': False}, {'app.example.com': 1},
                      {'APP.example.com': True, 'app.example.com': True}]:
            preset.write_text(json.dumps({'3rdparty': {'extensions': {
                m.EXTENSION: {'wellKnownApps': hosts}}}}))
            with self.subTest(hosts=hosts), patch.object(m, 'PRESET', preset), self.assertRaises(m.PolicyError):
                m.generate('example.sharepoint.com', 'example-my.sharepoint.com')

    def test_unrelated_preset_settings_are_not_emitted(self):
        preset = self.root / 'preset.json'
        preset.write_text(json.dumps({'BrowserSignin': 0, '3rdparty': {'extensions': {
            'unrelated': {'setting': True},
            m.EXTENSION: {'wellKnownApps': {'APP.example.com': True}, 'unrelated': True}}}}))
        with patch.object(m, 'PRESET', preset):
            policy = m.generate('example.sharepoint.com', 'other-my.sharepoint.com')
        self.assertEqual(policy, {'3rdparty': {'extensions': {m.EXTENSION: {'wellKnownApps': {
            'app.example.com': True, 'example.sharepoint.com': True, 'other-my.sharepoint.com': True}}}}})

    def test_rejects_nonliteral_dns_names(self):
        for host in ['https://app.example.com', '*.example.com', 'app.example.com/path',
                     'app.example.com:443', '-x.example.com', '--install', 'a..example.com',
                     'a.example.com.', 'a_b.example.com', 'é.example.com', 'a.example.com\n',
                     'a' * 64 + '.example.com', 'a.example.com\x00', '127.0.0.1']:
            with self.subTest(host=host), self.assertRaises(m.PolicyError):
                m.hostname(host)

    def test_requires_sharepoint_and_personal_onedrive_shapes(self):
        for sharepoint, onedrive in [('example.com', 'example-my.sharepoint.com'),
                                     ('a.b.sharepoint.com', 'example-my.sharepoint.com'),
                                     ('example-my.sharepoint.com', 'example-my.sharepoint.com'),
                                     ('example.sharepoint.com', 'example.sharepoint.com'),
                                     ('example.sharepoint.com', 'example-my.sharepoint.us')]:
            with self.subTest(hosts=(sharepoint, onedrive)), self.assertRaises(m.PolicyError):
                m.generate(sharepoint, onedrive)

    def test_stdout_generation_has_no_install_or_network_effect(self):
        output = io.StringIO()
        with patch.object(m.os, 'geteuid', return_value=1000), patch.object(m, '_install') as install, contextlib.redirect_stdout(output):
            self.assertEqual(m.main(['--sharepoint-host', 'example.sharepoint.com',
                                     '--onedrive-host', 'example-my.sharepoint.com']), 0)
        install.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), self.policy)
        self.assertFalse(self.directory.exists())

    def test_explicit_install_requires_root(self):
        with patch.object(m.os, 'geteuid', return_value=1000), patch.object(m, '_install') as install:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                m.main(['--sharepoint-host', 'example.sharepoint.com',
                        '--onedrive-host', 'example-my.sharepoint.com', '--install'])
        install.assert_not_called()

    def test_root_checkout_refused_before_reading_preset_or_installing(self):
        args = ['--sharepoint-host', 'example.sharepoint.com',
                '--onedrive-host', 'example-my.sharepoint.com']
        for extra in ([], ['--install']):
            with self.subTest(extra=extra), patch.object(m.os, 'geteuid', return_value=0), \
                    patch.object(m, 'generate') as generate, patch.object(m, '_install') as install, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                m.main(args + extra)
            generate.assert_not_called()
            install.assert_not_called()

    def test_root_sources_require_fixed_preset_isolation_and_trusted_ancestry(self):
        script = self.root / 'usr/lib/omarchy-himmelblau/m365-sso-policy.py'
        preset = self.root / 'usr/share/omarchy-himmelblau/chromium-m365-sso.json'
        for source in (script, preset):
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text('{}')
        check = m.trusted
        def local_trusted(path, owner_uid, directory):
            self.assertEqual(owner_uid, 0)
            # The isolated fixture is user-owned; system ancestors retain root ownership.
            return check(path, os.getuid() if path == self.root or self.root in path.parents else 0, directory)
        # /tmp is intentionally writable, so model only the fixture's trusted boundary.
        def fixture_trusted(path, owner_uid, directory):
            if path == self.root or self.root in path.parents:
                return local_trusted(path, owner_uid, directory)
        with patch.object(m, '__file__', str(script)), patch.object(m, 'INSTALLED_SCRIPT', script), \
                patch.object(m, 'INSTALLED_PRESET', preset), patch.object(m, 'PRESET', preset), \
                patch.object(m.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(m, 'trusted', side_effect=fixture_trusted):
            m.root_sources()
            with patch.object(m, 'PRESET', self.root / 'checkout.json'), self.assertRaises(m.PolicyError):
                m.root_sources()
            with patch.object(m.sys, 'flags', SimpleNamespace(isolated=0)), self.assertRaises(m.PolicyError):
                m.root_sources()
            preset.parent.chmod(0o777)
            with self.assertRaises(m.PolicyError):
                m.root_sources()
            preset.parent.chmod(0o755)
            preset.unlink(); preset.symlink_to(script)
            with self.assertRaises(m.PolicyError):
                m.root_sources()

    def test_staged_install_ships_generator_and_unchanged_preset(self):
        checkout = self.root / 'checkout'; checkout.mkdir()
        for name in ('assets', 'docs', 'LICENSES', 'bin', 'src', 'tools', 'examples'):
            shutil.copytree(ROOT / name, checkout / name)
        for name in ('Makefile', 'LICENSE', 'THIRD-PARTY-NOTICES.md', 'README.md'):
            shutil.copyfile(ROOT / name, checkout / name)
        (checkout / 'build').mkdir()
        (checkout / 'build/pin-change').write_text('test fixture')
        stage = self.root / 'stage'
        subprocess.run(['make', '-s', '-o', 'build/pin-change', 'install', 'DESTDIR=' + str(stage)],
                       cwd=checkout, check=True, capture_output=True)
        script = stage / 'usr/lib/omarchy-himmelblau/m365-sso-policy.py'
        preset = stage / 'usr/share/omarchy-himmelblau/chromium-m365-sso.json'
        self.assertEqual(script.read_bytes(), (ROOT / 'tools/m365-sso-policy.py').read_bytes())
        self.assertTrue(script.read_text().startswith('#!/usr/bin/python3 -I\n'))
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)
        self.assertEqual(preset.read_bytes(), (ROOT / 'examples/chromium-m365-sso.json').read_bytes())
        self.assertEqual(stat.S_IMODE(preset.stat().st_mode), 0o644)

    def test_cli_install_reports_new_or_identical_policy(self):
        for installed, expected in [(True, 'M365 SSO policy installed.'),
                                    (False, 'M365 SSO policy already installed.')]:
            output = io.StringIO()
            with self.subTest(installed=installed), patch.object(m.os, 'geteuid', return_value=0), \
                    patch.object(m, 'root_sources'), patch.object(m, '_install', return_value=installed), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(m.main(['--sharepoint-host', 'example.sharepoint.com',
                                         '--onedrive-host', 'example-my.sharepoint.com', '--install']), 0)
            self.assertEqual(output.getvalue(), expected + '\n')

    def test_install_under_restrictive_umask_is_public_and_idempotent(self):
        previous = os.umask(0o077)
        try:
            self.assertTrue(self.install())
        finally:
            os.umask(previous)
        target = self.directory / m.POLICY_NAME
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.directory.parent.stat().st_mode), 0o755)
        before = target.stat().st_ino
        self.assertFalse(self.install())
        self.assertEqual(target.stat().st_ino, before)

    def test_conflicting_target_or_other_extension_policy_refused_without_writes(self):
        self.directory.mkdir(parents=True)
        for name, value in [(m.POLICY_NAME, {}), ('another.json', {
                '3rdparty': {'extensions': {m.EXTENSION: {'wellKnownApps': {'other.example.com': True}}}}}),
                ('null.json', {'3rdparty': {'extensions': {m.EXTENSION: {'wellKnownApps': None}}}})]:
            candidate = self.directory / name
            candidate.write_text(json.dumps(value)); candidate.chmod(0o644)
            before = candidate.read_bytes()
            with self.subTest(name=name), self.assertRaises(m.PolicyError):
                self.install()
            self.assertEqual(candidate.read_bytes(), before)
            self.assertEqual(list(self.directory.iterdir()), [candidate])
            candidate.unlink()

    def test_unrelated_policy_is_preserved_and_identical_apps_are_allowed(self):
        self.directory.mkdir(parents=True)
        unrelated = self.directory / 'unrelated.json'
        unrelated.write_text('{"BrowserSignin": 1}')
        same = self.directory / 'same.json'
        same.write_text(json.dumps(self.policy))
        before = unrelated.read_bytes()
        self.assertTrue(self.install())
        self.assertEqual(unrelated.read_bytes(), before)

    def test_symlink_and_writable_ancestry_refused(self):
        outside = self.root / 'outside'; outside.mkdir()
        (self.root / 'policies').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(m.PolicyError):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / 'policies').unlink()
        self.directory.mkdir(parents=True)
        self.directory.parent.chmod(0o777)
        with self.assertRaises(m.PolicyError):
            self.install()
        self.assertFalse((self.directory / m.POLICY_NAME).exists())

    def test_target_symlink_and_protected_existing_file_refused(self):
        self.directory.mkdir(parents=True)
        target = self.directory / m.POLICY_NAME
        outside = self.root / 'outside.json'; outside.write_text(json.dumps(self.policy))
        target.symlink_to(outside)
        with self.assertRaises(m.PolicyError):
            self.install()
        target.unlink(); target.write_text(json.dumps(self.policy)); target.chmod(0o600)
        with self.assertRaises(m.PolicyError):
            self.install()
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
