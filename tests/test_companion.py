import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('companion', ROOT / 'src/omarchy_himmelblau.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CompanionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.paths = {}
        for name, path in {
            'STATE': 'state/state.json', 'STOCK_PAM': 'etc/pam.d/omarchy-lock-password',
            'PAM': 'etc/pam.d/omarchy-himmelblau-lock-password',
            'LOCK': 'usr/share/omarchy/shell/plugins/lock/Service.qml',
            'CONFIG': 'etc/himmelblau/himmelblau.conf', 'THEME': 'themes/omarchy',
            'COPY_THEME': 'themes/omarchy-himmelblau', 'OVERRIDE': 'etc/sddm.conf.d/99-z-himmelblau.conf',
            'SUDOERS': 'etc/sudoers.d/90-himmelblau-user', 'LOCAL_PASSWD': 'etc/passwd',
            'SERVER_CONFIG': 'cache/generated.conf', 'CACHE_ALIAS': 'cache/himmelblaud',
            'CACHE_TARGET': 'cache/private/himmelblaud',
        }.items():
            self.paths[name] = self.base / path
        self.paths['CONFIG_LAYERS'] = (self.base / 'vendor/config.conf', self.base / 'run/config.conf')
        self.mock_paths = patch.multiple(m, **self.paths)
        self.mock_paths.start(); self.addCleanup(self.mock_paths.stop)
        # The host sandbox maps / and /tmp ownership to nobody; production guards
        # remain enabled in the CLI and are separate from isolated temporary roots.
        ancestor_guard = patch.object(m, 'check_state_ancestors')
        ancestor_guard.start(); self.addCleanup(ancestor_guard.stop)
        self.output = patch('sys.stdout', new_callable=io.StringIO)
        self.output.start(); self.addCleanup(self.output.stop)
        m.THEME.mkdir(parents=True)
        for path, fixture in [(m.STOCK_PAM, 'omarchy-lock-password'), (m.LOCK, 'Service.qml'),
                              (m.THEME / 'Main.qml', 'Main.qml')]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / 'tests/fixtures' / fixture).read_bytes())
        (m.THEME / 'logo.png').write_bytes(b'synthetic-image')
        m.CONFIG.parent.mkdir(parents=True)
        m.CONFIG.write_text('# operator comment\n[global]\nconnection_timeout = 17\n\n[example.com]\ncustom = untouched\n')
        m.LOCAL_PASSWD.write_text('recovery:x:1000:1000::/home/recovery:/bin/bash\n')
        self.before = {path: path.read_bytes() for path in [m.STOCK_PAM, m.LOCK, m.CONFIG]}

    def setup(self, admin=False):
        m.setup('example.com', 'alice@example.com', admin)

    def test_setup_preserves_unrelated_settings_and_rollback(self):
        self.setup()
        self.assertIn(b'connection_timeout = 17', m.CONFIG.read_bytes())
        self.assertIn(b'[example.com]\ncustom = untouched', m.CONFIG.read_bytes())
        self.assertEqual(m.LOCK.read_bytes(), self.before[m.LOCK])  # packaged QML untouched
        self.assertEqual(m.STOCK_PAM.read_bytes(), self.before[m.STOCK_PAM])
        self.assertIn(b'pam_himmelblau.so', m.PAM.read_bytes())
        self.assertIn(b'config: \"omarchy-himmelblau-lock-password\"', m.patch_source('lock-single-response', self.before[m.LOCK]))
        self.assertIn(b'Username / UPN', (m.COPY_THEME / 'Main.qml').read_bytes())
        self.assertNotIn(b'local recovery:', (m.COPY_THEME / 'Main.qml').read_bytes())
        self.assertEqual(m.STATE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(m.STATE.parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse(m.SUDOERS.exists())
        state_before = m.STATE.read_bytes()
        self.setup()
        self.assertEqual(m.STATE.read_bytes(), state_before)
        m.rollback()
        for path, before in self.before.items():
            self.assertEqual(path.read_bytes(), before)
        self.assertFalse(m.PAM.exists())
        self.assertFalse(m.OVERRIDE.exists())
        self.assertFalse(m.COPY_THEME.exists())

    def test_conflicting_config_prevents_all_changes(self):
        for setting in ['domain = other.example', 'pam_allow_groups = other@example.com',
                        'apply_policy = true', 'use_etc_skel = false']:
            with self.subTest(setting=setting):
                m.CONFIG.write_text('[global]\n' + setting + '\n')
                with self.assertRaises(m.Error): self.setup()
                self.assertFalse(m.PAM.exists())
                self.assertFalse(m.STATE.exists())

    def test_source_drift_and_symlinks_refused_before_changes(self):
        m.LOCK.write_bytes(self.before[m.LOCK] + b'// unexpected\n')
        with self.assertRaises(m.Error): self.setup()
        self.assertFalse(m.PAM.exists())
        m.LOCK.write_bytes(self.before[m.LOCK])
        m.CONFIG.unlink(); m.CONFIG.symlink_to(self.base / 'other')
        with self.assertRaises(m.Error): self.setup()
        self.assertFalse(m.STATE.exists())

    def test_parent_symlink_and_state_mode_refused(self):
        m.CONFIG.unlink(); m.CONFIG.parent.rmdir()
        elsewhere = self.base / 'elsewhere'; elsewhere.mkdir()
        m.CONFIG.parent.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(m.Error): self.setup()
        m.CONFIG.parent.unlink();m.CONFIG.parent.mkdir();m.CONFIG.write_bytes(self.before[m.CONFIG])
        m.STATE.parent.mkdir(mode=0o755)
        with self.assertRaises(m.Error): self.setup()
        self.assertFalse(m.PAM.exists())

    def test_transaction_failure_restores_touched_files(self):
        original = m.atomic
        failed = False
        def injected(path, data, mode):
            nonlocal failed
            if path == m.CONFIG and not failed:
                failed = True
                raise OSError('synthetic write failure')
            return original(path, data, mode)
        with patch.object(m, 'atomic', injected):
            with self.assertRaises(m.Error): self.setup()
        for path, before in self.before.items(): self.assertEqual(path.read_bytes(), before)
        self.assertFalse(m.STATE.exists())

    def test_rollback_refuses_drift_before_restoring_anything(self):
        self.setup()
        pam = m.PAM.read_bytes()
        m.CONFIG.write_bytes(m.CONFIG.read_bytes() + b'# operator edit\n')
        with self.assertRaises(m.Error): m.rollback()
        self.assertEqual(m.PAM.read_bytes(), pam)
        self.assertTrue(m.STATE.exists())

    def test_legacy_shared_pam_rejected_before_changes(self):
        legacy = m.patch_source('lock-pam', self.before[m.STOCK_PAM])
        m.STOCK_PAM.write_bytes(legacy)
        with self.assertRaisesRegex(m.Error, 'pristine'):self.setup()
        self.assertFalse(m.STATE.exists())
        self.assertFalse(m.PAM.exists())
        self.assertEqual(m.STOCK_PAM.read_bytes(), legacy)

    def test_repair_known_regenerated_pam_only(self):
        self.setup()
        expected = m.PAM.read_bytes()
        m.PAM.write_bytes(self.before[m.STOCK_PAM])
        with self.assertRaises(m.Error): m.doctor()
        m.repair()
        self.assertEqual(m.PAM.read_bytes(), expected)
        m.PAM.unlink(); m.repair()
        self.assertEqual(m.PAM.read_bytes(), expected)
        m.PAM.write_bytes(self.before[m.STOCK_PAM])
        m.CONFIG.write_bytes(m.CONFIG.read_bytes() + b'# operator edit\n')
        with self.assertRaises(m.Error): m.repair()
        self.assertEqual(m.PAM.read_bytes(), self.before[m.STOCK_PAM])

    def test_admin_opt_in_resolves_actual_nss_uid_and_refuses_local_collision(self):
        account = SimpleNamespace(pw_uid=123456, pw_name='alice')
        with patch.object(m.pwd, 'getpwnam', return_value=account):
            self.assertEqual(m.administrator('alice@example.com'), 123456)
            m.LOCAL_PASSWD.write_text('alice:x:2000:2000::/home/alice:/bin/bash\n')
            with self.assertRaises(m.Error): m.administrator('alice@example.com')
            m.LOCAL_PASSWD.write_text('local:x:123456:2000::/home/local:/bin/bash\n')
            with self.assertRaises(m.Error): m.administrator('alice@example.com')
        with patch.object(m.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=0, pw_name='root')):
            with self.assertRaises(m.Error): m.administrator('alice@example.com')

    def test_admin_option_only_grants_verified_uid(self):
        run = subprocess.run
        def command(argv, **kw):
            if argv[0] == 'visudo': return SimpleNamespace(returncode=0)
            return run(argv, **kw)
        with patch.object(m, 'administrator', return_value=123456), patch.object(m.subprocess, 'run', command):
            self.setup(admin=True)
        self.assertIn(b'#123456 ALL=(ALL:ALL) ALL', m.SUDOERS.read_bytes())
        self.assertNotIn(b'NOPASSWD', m.SUDOERS.read_bytes())
        self.assertEqual(m.SUDOERS.stat().st_mode & 0o777, 0o440)

    def test_enable_user_native_directory_clone_and_read_only_hook(self):
        self.setup()
        clone = self.base / 'home/plugins/alice.lock'
        statefile = self.base / 'home/state/state.json'
        hook = self.base / 'home/hooks/post-update.d/check'
        paths = ('alice', clone, statefile, hook)
        run = subprocess.run
        commands = []
        def command(argv, **kw):
            commands.append(argv)
            if argv[:3] == ['omarchy-shell', 'lock', 'status']:
                self.assertEqual(kw['env']['OMARCHY_PATH'], '/usr/share/omarchy')
                return SimpleNamespace(stdout=json.dumps({key: False for key in
                    ('locked','requested','pending','sessionLocked','secure','authenticating')}))
            if argv[:3] == ['omarchy', 'plugin', 'clone']:
                clone.mkdir(parents=True)
                shutil.copy2(m.LOCK, clone / 'Service.qml')  # manifest.json directory branch copies exactly
                return SimpleNamespace(returncode=0)
            return run(argv, **kw)
        with patch.object(m, 'user_paths', return_value=paths), patch.object(m.subprocess, 'run', command):
            m.enable_user(); m.user_status(); m.enable_user()
        self.assertEqual(sum(cmd[0] == 'omarchy' for cmd in commands), 1)
        self.assertEqual((clone / 'Service.qml').read_bytes(), m.patch_source('lock-single-response', self.before[m.LOCK]))
        self.assertIn('omarchy-himmelblau status', hook.read_text())
        self.assertNotIn('sudo ', hook.read_text().split('printf')[0])
        self.assertNotIn('systemctl', hook.read_text())
        with patch.object(m, 'user_paths', return_value=paths):
            (m.LOCK.parent / 'FingerprintModel.js').write_text('// upstream helper update\n')
            with self.assertRaisesRegex(m.Error, 'Upstream lock plugin changed'):m.user_status()

    def test_native_clone_unexpected_rewrite_is_partial_failure(self):
        self.setup()
        clone = self.base / 'home/plugins/alice.lock'
        statefile = self.base / 'home/state/state.json'
        hook = self.base / 'home/hooks/post-update.d/check'
        run = subprocess.run
        def command(argv, **kw):
            if argv[0] == 'omarchy-shell':
                return SimpleNamespace(stdout=json.dumps({key: False for key in
                    ('locked','requested','pending','sessionLocked','secure','authenticating')}))
            if argv[0] == 'omarchy':
                clone.mkdir(parents=True)
                (clone / 'Service.qml').write_bytes(m.LOCK.read_bytes().replace(b'import qs.Commons\n', b'import "unexpected"\n'))
                return SimpleNamespace(returncode=0)
            return run(argv, **kw)
        with patch.object(m, 'user_paths', return_value=('alice', clone, statefile, hook)), patch.object(m.subprocess, 'run', command):
            with self.assertRaisesRegex(m.Error, 'may be enabled'):m.enable_user()
            self.assertEqual(json.loads(statefile.read_text())['phase'], 'preparing')
            with self.assertRaisesRegex(m.Error, 'Partial native clone'):m.user_status()
        self.assertFalse(hook.exists())

    def test_locked_user_clone_refused(self):
        with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(stdout='{"locked":true}')):
            with self.assertRaises(m.Error): m.unlocked()

    def test_legitimate_nss_home_alias_and_descendant_symlink(self):
        home = self.base / 'actual-home'; home.mkdir()
        alias = self.base / 'upn-home'; alias.symlink_to(home, target_is_directory=True)
        account = SimpleNamespace(pw_name='alice', pw_dir=str(alias))
        with patch.object(m.pwd, 'getpwuid', return_value=account), patch.object(m.os, 'geteuid', return_value=1000):
            user, clone, statefile, hook = m.user_paths()
            self.assertEqual(clone, home / '.config/omarchy/plugins/alice.lock')
            self.assertEqual(m.user_environment()['HOME'], str(home))
            self.assertEqual(m.user_environment()['USER'], 'alice')
            elsewhere = self.base / 'elsewhere'; elsewhere.mkdir()
            (home / '.config').symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaises(m.Error):m.read(clone / 'Service.qml')

    def test_other_section_allowlist_and_dropins_are_rejected(self):
        m.CONFIG.write_text('[global]\n[example.com]\npam_allow_groups = bob@example.com\n')
        with self.assertRaises(m.Error): self.setup()
        self.assertFalse(m.PAM.exists())
        m.CONFIG.write_bytes(self.before[m.CONFIG])
        directory = Path(str(m.CONFIG_LAYERS[0]) + '.d'); directory.mkdir(parents=True)
        (directory / '50-policy.conf').write_text('[global]\napply_policy = true\n')
        with self.assertRaises(m.Error): self.setup()
        self.assertFalse(m.STATE.exists())

    def test_unchanged_adopted_configuration_must_retain_allowlist(self):
        m.CONFIG.write_bytes(m.managed_config(m.CONFIG.read_bytes(), 'example.com', 'alice@example.com'))
        self.setup()
        state = json.loads(m.STATE.read_text())
        self.assertNotIn(str(m.CONFIG), [entry['path'] for entry in state['files']])
        lines = [line for line in m.CONFIG.read_text().splitlines(keepends=True) if not line.startswith('pam_allow_groups')]
        m.CONFIG.write_text(''.join(lines))
        with self.assertRaises(m.Error):m.doctor()
        with self.assertRaises(m.Error):m.repair()

    def test_systemd_dynamicuser_cache_alias_is_read_only_and_verified(self):
        cache = self.base / 'var/cache'; cache.mkdir(parents=True)
        private = cache / 'private'; private.mkdir(mode=0o700)
        target = private / 'himmelblaud'; target.mkdir(mode=0o755)
        config = target / 'himmelblau.conf'; config.write_text('[example.com]\nserver_option = retained\n')
        alias = cache / 'himmelblaud'; alias.symlink_to('private/himmelblaud', target_is_directory=True)
        original_lstat = Path.lstat
        def metadata(path, *args, **kwargs):
            result = original_lstat(path, *args, **kwargs)
            if path == alias:
                return SimpleNamespace(st_uid=0, st_gid=0, st_mode=result.st_mode)
            if path == private:
                return SimpleNamespace(st_uid=0, st_gid=0, st_mode=result.st_mode)
            if path == target:
                return SimpleNamespace(st_uid=target_owner, st_gid=target_owner, st_mode=result.st_mode)
            return result
        # Ordinary CI cannot chown root/nobody; mock only the exact root-owned
        # systemd storage metadata, while exercising real symlinks and contents.
        with patch.multiple(m, CACHE_ALIAS=alias, CACHE_TARGET=target, SERVER_CONFIG=alias / 'himmelblau.conf'), patch.object(Path, 'lstat', metadata):
            for target_owner in (0, 65534):
                self.assertEqual(m.read_generated_config(), config.read_bytes())
            config.write_text('[global]\napply_policy = true\n')
            with self.assertRaises(m.Error):m.configuration_scope('example.com', 'alice@example.com')
            config.write_text('[example.com]\nserver_option = retained\n')
            target_owner = 1234
            with self.assertRaises(m.Error):m.read_generated_config()
            target_owner = 65534
            target.chmod(0o777)
            with self.assertRaises(m.Error):m.read_generated_config()
            target.chmod(0o755)
            private.chmod(0o755)
            with self.assertRaises(m.Error):m.read_generated_config()
            private.chmod(0o700)
            alias.unlink(); alias.symlink_to('../untrusted')
            with self.assertRaises(m.Error):m.read_generated_config()
            alias.unlink(); alias.symlink_to('private/himmelblaud', target_is_directory=True)
            # The general write/read path guard remains strict for this alias.
            with self.assertRaises(m.Error):m.safe(alias / 'himmelblau.conf')
            with self.assertRaises(m.Error):m.atomic(alias / 'new-file', b'data', 0o600)
            config.unlink(); config.symlink_to(target / 'unexpected-config')
            with self.assertRaises(m.Error):m.read_generated_config()
        self.assertFalse((target / 'new-file').exists())

    def test_identity_validation(self):
        self.assertEqual(m.identity('EXAMPLE.COM', 'Alice@EXAMPLE.COM'), ('example.com', 'alice@example.com'))
        for domain, upn in [('example.com','alice@other.com'), ('x;echo','a@x;echo'), ('example.com','a\n@EXAMPLE.COM')]:
            with self.assertRaises(m.Error):m.identity(domain, upn)


if __name__ == '__main__': unittest.main()
