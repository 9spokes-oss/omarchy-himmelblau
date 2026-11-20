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
PAM_STACK = ROOT / 'tests/pam-stack'
spec = importlib.util.spec_from_file_location('companion', ROOT / 'src/omarchy_himmelblau.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CompanionTest(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.paths = {}
        for name, path in {
            'STATE': 'state/state.json', 'STOCK_PAM': 'etc/pam.d/omarchy-lock-password',
            'PAM': 'etc/pam.d/omarchy-himmelblau-lock-password',
            'RECOVERY_ACCOUNT': 'etc/omarchy-himmelblau/recovery-account',
            'NATIVE_PAYLOAD': 'usr/share/omarchy-himmelblau/native-payload.json',
            'PIN_PAM': 'etc/pam.d/omarchy-himmelblau-pin-change',
            'SDDM_PIN_PAM': 'etc/pam.d/sddm-himmelblau-pin-change',
            'LOCK': 'usr/share/omarchy/shell/plugins/lock/Service.qml',
            'CONFIG': 'etc/himmelblau/himmelblau.conf', 'THEME': 'themes/omarchy',
            'COPY_THEME': 'themes/omarchy-himmelblau', 'OVERRIDE': 'etc/sddm.conf.d/99-z-himmelblau.conf',
            'SUDOERS': 'etc/sudoers.d/90-himmelblau-user', 'LOCAL_PASSWD': 'etc/passwd',
            'SERVER_CONFIG': 'cache/generated.conf', 'CACHE_ALIAS': 'cache/himmelblaud',
            'CACHE_TARGET': 'cache/private/himmelblaud',
            'SYSTEM_AUTH': 'etc/pam.d/system-auth', 'SYSTEM_LOGIN': 'etc/pam.d/system-login',
        }.items():
            self.paths[name] = self.base / path
        self.paths['ASSETS'] = ROOT / 'assets'
        self.paths['CONFIG_LAYERS'] = (self.base / 'vendor/config.conf', self.base / 'run/config.conf')
        self.paths['SDDM_PAMS'] = (self.base / 'etc/pam.d/sddm', self.base / 'etc/pam.d/sddm-autologin')
        self.paths['SDDM_CONF'] = self.base / 'etc/sddm.conf'
        self.paths['SDDM_CONF_DIRS'] = (self.base / 'usr/lib/sddm/sddm.conf.d', self.base / 'etc/sddm.conf.d')
        self.mock_paths = patch.multiple(m, **self.paths)
        self.mock_paths.start(); self.addCleanup(self.mock_paths.stop)
        # The host sandbox maps / and /tmp ownership to nobody; production guards
        # remain enabled in the CLI and are separate from isolated temporary roots.
        self.real_ancestor_guard = m.check_state_ancestors
        ancestor_guard = patch.object(m, 'check_state_ancestors')
        ancestor_guard.start(); self.addCleanup(ancestor_guard.stop)
        self.real_user_path_guard = m.user_path_guard
        user_guard = patch.object(m, 'user_path_guard')
        user_guard.start(); self.addCleanup(user_guard.stop)
        self.real_live_guard = m.require_no_live_locker
        live_guard = patch.object(m, 'require_no_live_locker')
        live_guard.start(); self.addCleanup(live_guard.stop)
        self.output = patch('sys.stdout', new_callable=io.StringIO)
        self.output.start(); self.addCleanup(self.output.stop)
        native_run = m.subprocess.run
        def native_command(argv, **kw):
            if argv == ['/usr/bin/omarchy', 'plugin', 'list', '--json']:
                return SimpleNamespace(stdout='[]')
            if argv == ['/usr/bin/pacman', '-Q', 'sddm-himmelblau']:
                return SimpleNamespace(stdout=f'sddm-himmelblau {m.NATIVE_VERSION}\n')
            if argv == ['/usr/bin/pacman', '-Q', 'pambase']:
                return SimpleNamespace(stdout=f'pambase {self.pambase}\n')
            return native_run(argv, **kw)
        commands = patch.object(m.subprocess, 'run', native_command)
        commands.start(); self.addCleanup(commands.stop)
        m.RECOVERY_ACCOUNT.parent.mkdir(parents=True, mode=0o755)
        m.RECOVERY_ACCOUNT.parent.chmod(0o755)
        native_name = m.pwd.getpwnam; native_uid = m.pwd.getpwuid
        self.principal = SimpleNamespace(pw_uid=123456, pw_name='alice')
        names = patch.object(m.pwd, 'getpwnam', side_effect=lambda name: self.principal if name in ('alice@example.com', 'alice') else native_name(name))
        names.start(); self.addCleanup(names.stop)
        uids = patch.object(m.pwd, 'getpwuid', side_effect=lambda uid: self.principal if uid == 123456 else native_uid(uid))
        uids.start(); self.addCleanup(uids.stop)
        native_paths = tuple(self.base / 'native' / name.name for name in m.NATIVE_EXECUTABLES)
        native_paths_patch = patch.object(m, 'NATIVE_EXECUTABLES', native_paths)
        native_paths_patch.start(); self.addCleanup(native_paths_patch.stop)
        component_paths = tuple(self.base / 'components' / variant for variant in ('qt5','qt6'))
        components_patch = patch.object(m, 'NATIVE_COMPONENTS', component_paths)
        components_patch.start(); self.addCleanup(components_patch.stop)
        m.NATIVE_PAYLOAD.parent.mkdir(parents=True)
        native_files = {}
        for directory in component_paths:
            directory.mkdir(parents=True)
            for name in ('qmldir', 'Theme.qml'):
                path = directory / name; path.write_bytes(b'synthetic component'); path.chmod(0o644)
                native_files[str(path)] = m.digest(path.read_bytes())
        for path in native_paths:
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'synthetic native payload'); path.chmod(0o755)
            native_files[str(path)] = m.digest(path.read_bytes())
        m.NATIVE_PAYLOAD.write_text(json.dumps({'package':'sddm-himmelblau', 'version':m.NATIVE_VERSION,
            'artifact_sha256':'a'*64, 'source_commit':'b'*40, 'files':native_files}))
        m.NATIVE_PAYLOAD.chmod(0o644)
        m.SDDM_PIN_PAM.parent.mkdir(parents=True)
        m.SDDM_PIN_PAM.write_bytes(m.SDDM_PIN_PAM_CONTENT)
        m.SDDM_PIN_PAM.chmod(0o644)
        m.THEME.mkdir(parents=True)
        for path, fixture in [(m.STOCK_PAM, 'omarchy-lock-password'), (m.LOCK, 'Service.qml'), (m.LOCK.parent / 'LockView.qml', 'LockView.qml'),
                              (m.THEME / 'Main.qml', 'Main.qml')]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / 'tests/fixtures' / fixture).read_bytes())
        (m.THEME / 'logo.png').write_bytes(b'synthetic-image')
        self.pambase = '20260616-1'
        for path in (m.SYSTEM_AUTH, m.SYSTEM_LOGIN):
            path.write_bytes((PAM_STACK / 'pristine' / path.name).read_bytes()); path.chmod(0o644)
        m.CONFIG.parent.mkdir(parents=True)
        m.CONFIG.parent.chmod(0o755)
        m.CONFIG.write_text('# operator comment\n[global]\nconnection_timeout = 17\n\n[example.com]\ncustom = untouched\n')
        m.CONFIG.chmod(0o644)
        m.LOCAL_PASSWD.write_text('recovery:x:1000:1000::/home/recovery:/bin/bash\n')
        self.before = {path: path.read_bytes() for path in [m.STOCK_PAM, m.LOCK, m.CONFIG]}

    def setup(self, admin=False):
        m.setup('example.com', 'alice@example.com', admin)

    def test_installed_launcher_never_probes_development_paths(self):
        import runpy
        launcher = (ROOT / 'bin/omarchy-himmelblau').read_text()
        self.assertTrue(launcher.startswith('#!/usr/bin/python3 -I\n'))
        with patch.object(runpy, 'run_path') as execute:
            exec(compile(launcher, 'launcher', 'exec'), {'__file__':'/usr/bin/omarchy-himmelblau'})
        execute.assert_called_once_with('/usr/lib/omarchy-himmelblau/omarchy_himmelblau.py', run_name='__main__')
        with patch.dict(os.environ, {'PYTHONPATH':'/attacker', 'OMARCHY_ASSETS':'/attacker'}):
            namespace = runpy.run_path(str(ROOT / 'src/omarchy_himmelblau.py'))
        self.assertEqual(namespace['ASSETS'], Path('/usr/share/omarchy-himmelblau'))
        with patch.object(m.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(m.Error, 'refuses to run as root'):
                m.main(['--development', 'doctor'])
        installed = '/usr/lib/omarchy-himmelblau/omarchy_himmelblau.py'
        with patch.object(m, '__file__', installed), patch.object(m, 'plugin_status') as status:
            with self.assertRaisesRegex(m.Error, 'only as python3 src/omarchy_himmelblau.py'):
                m.main(['--development', 'status'])
            status.assert_not_called()

    def test_privileged_patch_ignores_hostile_path_and_environment(self):
        evil = self.base / 'evil'; evil.mkdir()
        (evil / 'patch').write_text('#!/bin/sh\nexit 99\n'); (evil / 'patch').chmod(0o755)
        run = m.subprocess.run
        observed = []
        def command(argv, **kwargs):
            observed.append((argv, kwargs))
            return run(argv, **kwargs)
        with patch.dict(os.environ, {'PATH':str(evil), 'LD_PRELOAD':'evil', 'BASH_ENV':'evil'}), patch.object(m.subprocess, 'run', command):
            m.patch_source('lock-pam', self.before[m.STOCK_PAM])
        self.assertEqual(observed[0][0][0], '/usr/bin/patch')
        self.assertEqual(observed[0][1]['env'], m.COMMAND_ENV)
        self.assertEqual(observed[0][1]['cwd'], '/')

    def test_doctor_does_not_invoke_patch_or_trust_inherited_environment(self):
        self.setup()
        run = m.subprocess.run
        def command(argv, **kwargs):
            self.assertIn(argv, (['/usr/bin/pacman', '-Q', 'sddm-himmelblau'],
                                 ['/usr/bin/pacman', '-Q', 'pambase']))
            self.assertEqual(kwargs['env'], m.COMMAND_ENV)
            return run(argv, **kwargs)
        with patch.dict(os.environ, {'PATH':'/attacker', 'BASH_ENV':'/attacker'}), patch.object(m.subprocess, 'run', command):
            m.doctor()

    def test_user_command_environment_drops_code_injection_variables(self):
        account = SimpleNamespace(pw_name='alice')
        with patch.object(m, 'canonical_user', return_value=(account, self.base)), \
             patch.dict(os.environ, {'LD_PRELOAD':'evil','BASH_ENV':'evil','PYTHONPATH':'evil','WAYLAND_DISPLAY':'wayland-1'}):
            environment = m.user_environment()
        for key in ('LD_PRELOAD','BASH_ENV','PYTHONPATH'): self.assertNotIn(key, environment)
        self.assertEqual(environment['WAYLAND_DISPLAY'], 'wayland-1')
        self.assertEqual(environment['PATH'], '/usr/bin:/bin')

    def test_setup_uses_one_validated_identity_for_all_outputs(self):
        resolver = m.administrator
        with patch.object(m, 'administrator', wraps=resolver) as resolve, patch.object(m, 'validate_sudo_rule'):
            self.setup(admin=True)
        resolve.assert_called_once_with('alice@example.com')
        principal = m.load_state()['identity']
        self.assertEqual(m.RECOVERY_ACCOUNT.read_bytes(), m.identity_file(principal))
        self.assertEqual(m.SUDOERS.read_bytes(), m.admin_rule(principal))

    def test_saved_principal_mapping_drift_never_rewrites_admin(self):
        with patch.object(m, 'validate_sudo_rule'): self.setup(admin=True)
        before = m.SUDOERS.read_bytes(); state = m.STATE.read_bytes()
        wrong = SimpleNamespace(pw_name='other', pw_uid=123456)
        with patch.object(m.pwd, 'getpwuid', return_value=wrong):
            for action in (m.doctor, m.repair):
                with self.assertRaises(m.Error): action()
        self.assertEqual(m.SUDOERS.read_bytes(), before); self.assertEqual(m.STATE.read_bytes(), state)
        self.assertEqual(m.load_state()['identity'], {'requested_upn':'alice@example.com','canonical_name':'alice','uid':123456})

    def test_admin_canonical_name_grammar_and_roundtrip(self):
        for name in ('#1234', '%wheel', '+netgroup', 'ALL', 'a,b', 'a:b', 'a\nb', 'a"b', '-option'):
            account = SimpleNamespace(pw_name=name, pw_uid=123456)
            with patch.object(m.pwd, 'getpwnam', return_value=account), patch.object(m.pwd, 'getpwuid', return_value=account):
                if name == 'ALL':
                    with self.assertRaises(m.Error): m.administrator('alice@example.com')
                else:
                    with self.assertRaises(m.Error): m.administrator('alice@example.com')

    def test_native_release_pin_rejects_metadata_only_or_incomplete_payload(self):
        original = m.NATIVE_PAYLOAD.read_bytes()
        manifest = json.loads(original); manifest['files'].pop(str(m.NATIVE_EXECUTABLES[0]))
        m.NATIVE_PAYLOAD.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(m.Error, 'Incomplete'): m.native_sddm_contract()
        m.NATIVE_PAYLOAD.write_bytes(original)
        target = m.NATIVE_EXECUTABLES[0]; target.write_bytes(b'changed binary')
        with self.assertRaisesRegex(m.Error, 'release pin'): m.native_sddm_contract()
        target.write_bytes(b'synthetic native payload'); target.chmod(0o777)
        with self.assertRaisesRegex(m.Error, 'metadata'): m.native_sddm_contract()

    def test_native_pin_accepts_only_release_or_marked_local_provenance(self):
        original = m.NATIVE_PAYLOAD.read_bytes()
        release = json.loads(original)
        local = dict(release, build='local', artifact_sha256=None)
        m.NATIVE_PAYLOAD.write_text(json.dumps(local))
        m.native_payload_contract()
        for changes in ({'build': 'local'},                      # local, but still names an artifact
                        {'artifact_sha256': None},               # no artifact, not marked local
                        {'build': 'release', 'artifact_sha256': None},
                        {'build': None, 'artifact_sha256': None}):
            m.NATIVE_PAYLOAD.write_text(json.dumps(dict(release, **changes)))
            with self.assertRaisesRegex(m.Error, 'provenance'): m.native_payload_contract()
        m.NATIVE_PAYLOAD.write_text(json.dumps(local))
        m.NATIVE_EXECUTABLES[0].write_bytes(b'changed binary')
        with self.assertRaisesRegex(m.Error, 'release pin'): m.native_payload_contract()
        m.NATIVE_EXECUTABLES[0].write_bytes(b'synthetic native payload')
        m.NATIVE_PAYLOAD.write_bytes(original)

    def test_native_component_trees_cannot_be_omitted_or_extended(self):
        original = m.NATIVE_PAYLOAD.read_bytes()
        manifest = json.loads(original)
        manifest['files'] = {path: value for path, value in manifest['files'].items()
                             if not Path(path).is_relative_to(m.NATIVE_COMPONENTS[0])}
        m.NATIVE_PAYLOAD.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(m.Error, 'complete installed file set'): m.native_payload_contract()
        m.NATIVE_PAYLOAD.write_bytes(original)
        payload = m.NATIVE_COMPONENTS[0] / 'Theme.qml'; payload.unlink()
        with self.assertRaisesRegex(m.Error, 'QML payload'): m.native_payload_contract()
        payload.write_bytes(b'synthetic component'); payload.chmod(0o644)
        extra = m.NATIVE_COMPONENTS[1] / 'Extra.qml'; extra.write_text('unlisted')
        with self.assertRaisesRegex(m.Error, 'complete installed file set'): m.native_payload_contract()
        extra.unlink()
        qml = m.NATIVE_COMPONENTS[1] / 'qmldir'; qml.unlink(); qml.symlink_to(m.NATIVE_COMPONENTS[0] / 'qmldir')
        with self.assertRaisesRegex(m.Error, 'Symlink'): m.native_payload_contract()

    def test_user_ancestry_rejects_writes_foreign_owners_and_acls(self):
        home = self.base / 'home'; home.mkdir(mode=0o700)
        config = home / '.config'; config.mkdir(mode=0o755)
        target = config / 'lock.qml'; target.write_text('synthetic'); target.chmod(0o644)
        stat = Path.stat
        def mapped(path, *args, **kwargs):
            data = stat(path, *args, **kwargs)
            if path != home and not path.is_relative_to(home):
                return SimpleNamespace(st_uid=0, st_mode=(0o41777 if path == Path('/tmp') else 0o40755))
            return data
        with patch.object(Path, 'stat', mapped):
            self.real_user_path_guard(home, target)
            config.chmod(0o777)
            with self.assertRaisesRegex(m.Error, 'Writable'): self.real_user_path_guard(home, target)
            config.chmod(0o755)
            def acl(path, name, **kwargs):
                if path == config and name == 'system.posix_acl_access': return b'fake extended ACL'
                raise OSError(m.errno.ENODATA, 'absent')
            with patch.object(m.os, 'getxattr', acl):
                with self.assertRaisesRegex(m.Error, 'Extended ACL'): self.real_user_path_guard(home, target)
            def foreign(path, *args, **kwargs):
                if path == config: return SimpleNamespace(st_uid=os.getuid()+1, st_mode=0o40755)
                return mapped(path, *args, **kwargs)
            with patch.object(Path, 'stat', foreign):
                with self.assertRaisesRegex(m.Error, 'Foreign owner'): self.real_user_path_guard(home, target)

    def test_rollback_runtime_guard_refuses_live_unknown_and_allows_recovery_override(self):
        cases = [('[]', None, 1, False), ('bad', None, 1, True),
                 ('[{"session":"7"}]','Type=wayland\nClass=user\n',1,True),
                 ('[{"session":"7"}]','Type=tty\nClass=user\n',0,True),
                 ('[{"session":"7"}]','Type=tty\nClass=user\n',1,False)]
        for listing, detail, processes, denied in cases:
            def command(argv, **kwargs):
                self.assertEqual(kwargs['env'], m.COMMAND_ENV)
                if argv[0] == '/usr/bin/pgrep': return SimpleNamespace(returncode=processes)
                return SimpleNamespace(stdout=detail if 'show-session' in argv else listing)
            with patch.object(m.subprocess, 'run', command):
                if denied:
                    with self.assertRaises(m.Error): self.real_live_guard()
                else: self.real_live_guard()
        with patch.object(m.subprocess, 'run', side_effect=AssertionError('override must not inspect runtime')):
            self.real_live_guard(True)

    def test_setup_interruption_recovers_pending_and_applied_files(self):
        original = m.atomic; interrupted = False
        def crash(path, data, mode, *owner):
            nonlocal interrupted
            if path == m.CONFIG and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): self.setup()
        # Initial setup: no state file before, and none yet when it crashed.
        self.assertFalse(m.STATE.exists())
        self.assertTrue(m.journal_path().exists())
        for action in (m.doctor, m.rollback, m.repair, self.setup):
            with self.assertRaisesRegex(m.Error, 'run recover'): action()
        m.recover()
        self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
        self.assertFalse(m.COPY_THEME.exists()); self.assertFalse(m.journal_path().exists())
        self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])
        self.setup(); m.doctor()

    def test_policy_interruption_recovers_with_unchanged_or_committed_state(self):
        self.setup()
        old_config = m.CONFIG.read_bytes(); old_state = m.STATE.read_bytes()
        original = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): m.policy(True)
        self.assertIn(b'apply_policy = true', m.CONFIG.read_bytes())
        m.recover()
        self.assertEqual(m.CONFIG.read_bytes(), old_config); self.assertEqual(m.STATE.read_bytes(), old_state)
        journal = m.journal_path(); unlink = Path.unlink
        def crash_unlink(path, *args, **kwargs):
            if path == journal: raise KeyboardInterrupt
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', crash_unlink):
            with self.assertRaises(KeyboardInterrupt): m.policy(True)
        m.recover()
        self.assertEqual(m.CONFIG.read_bytes(), old_config); self.assertEqual(m.STATE.read_bytes(), old_state)

    def test_recovery_refuses_drift_before_any_restoration(self):
        self.setup(); old_state = m.STATE.read_bytes(); original = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): m.policy(True)
        m.CONFIG.write_bytes(m.CONFIG.read_bytes()+b'# unknown drift\n')
        before = m.CONFIG.read_bytes()
        with self.assertRaisesRegex(m.Error, 'unknown target drift'): m.recover()
        self.assertEqual(m.CONFIG.read_bytes(), before); self.assertEqual(m.STATE.read_bytes(), old_state)

    def test_setup_crash_before_and_after_each_file_is_recoverable(self):
        self.setup(); targets = [Path(entry['path']) for entry in m.load_state()['files']]; m.rollback()
        original = m.atomic
        for target in targets:
            for after_write in (False, True):
                with self.subTest(target=target.name, after_write=after_write):
                    crashed = False
                    def crash(path, data, mode, *owner):
                        nonlocal crashed
                        if path == target and not crashed:
                            crashed = True
                            if after_write: original(path, data, mode, *owner)
                            raise KeyboardInterrupt
                        return original(path, data, mode, *owner)
                    with patch.object(m, 'atomic', crash):
                        with self.assertRaises(KeyboardInterrupt): self.setup()
                    m.recover()
                    self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
                    self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])

    def test_repair_and_rollback_crashes_can_resume_from_exact_before_after(self):
        self.setup(); original = m.atomic; old_state = m.STATE.read_bytes()
        m.PAM.unlink()
        def crash_repair(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash_repair):
            with self.assertRaises(KeyboardInterrupt): m.repair()
        self.assertTrue(m.PAM.exists())
        m.recover()
        self.assertFalse(m.PAM.exists()); self.assertEqual(m.STATE.read_bytes(), old_state)
        m.repair()
        m.rollback()
        self.assertFalse(m.STATE.exists()); self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])

    def crash_rollback_at_unlink(self, target):
        unlink = m.unlink_durable
        def crash(path, missing_ok=False):
            if path == target:
                if target != m.journal_path():
                    unlink(path, missing_ok)
                raise KeyboardInterrupt
            return unlink(path, missing_ok)
        with patch.object(m, 'unlink_durable', crash):
            with self.assertRaises(KeyboardInterrupt): m.rollback()

    def test_rollback_crash_after_deleting_a_managed_file(self):
        self.setup()
        pam, mode, state = m.PAM.read_bytes(), m.PAM.stat().st_mode, m.STATE.read_bytes()
        self.crash_rollback_at_unlink(m.PAM)
        self.assertFalse(m.PAM.exists())
        with self.assertRaisesRegex(m.Error, 'run recover'): m.rollback()
        m.recover()
        self.assertEqual((m.PAM.read_bytes(), m.PAM.stat().st_mode), (pam, mode))
        self.assertEqual(m.STATE.read_bytes(), state)
        m.doctor(); m.rollback()
        self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])

    def test_rollback_crash_after_deleting_state_before_its_journal(self):
        self.setup()
        state = m.STATE.read_bytes()
        self.crash_rollback_at_unlink(m.journal_path())
        # The rollback finished everything but deleting its journal.
        self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
        self.assertTrue(m.journal_path().exists())
        m.recover()  # Recovery never loads state, and always undoes.
        self.assertEqual(m.STATE.read_bytes(), state)
        m.doctor(); m.rollback()
        self.assertFalse(m.STATE.exists())

    def interrupted_policy(self):
        """A policy change that crashed at its state commit: CONFIG is at its after value."""
        self.setup()
        atomic = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return atomic(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): m.policy(True)
        return json.loads(m.journal_path().read_text())

    def assert_recover_refuses_untouched(self, message):
        files = {path: path.read_bytes() for path in (m.CONFIG, m.STATE, m.journal_path())}
        with self.assertRaisesRegex(m.Error, message): m.recover()
        for path, data in files.items():
            self.assertEqual(path.read_bytes(), data, path)

    def test_recover_refuses_after_side_file_metadata_drift(self):
        # A target with the expected bytes but another owner, group or mode is drift.
        journal = self.interrupted_policy()
        for key in ('uid', 'gid'):
            with self.subTest(key=key):
                edited = json.loads(json.dumps(journal))
                edited['files'][0]['after'][key] += 1
                m.journal_path().write_text(json.dumps(edited))
                self.assert_recover_refuses_untouched('unknown target drift')
        m.journal_path().write_text(json.dumps(journal))
        m.CONFIG.chmod(0o600)
        self.assert_recover_refuses_untouched('unknown target drift')
        m.CONFIG.chmod(0o644)
        m.recover()
        self.assertFalse(m.load_state()['apply_policy'])

    def test_recover_refuses_after_side_directory_metadata_drift(self):
        atomic = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.CONFIG: raise KeyboardInterrupt  # Theme directories already exist.
            return atomic(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): self.setup()
        journal = json.loads(m.journal_path().read_text())
        created = [entry for entry in journal['directories'] if entry['before'] is None]
        self.assertTrue(created)
        for key in ('uid', 'gid'):
            with self.subTest(key=key):
                edited = json.loads(json.dumps(journal))
                edited['directories'][0]['after'][key] += 1
                m.journal_path().write_text(json.dumps(edited))
                with self.assertRaisesRegex(m.Error, 'directory drift'): m.recover()
                self.assertTrue(m.COPY_THEME.is_dir())
        m.journal_path().write_text(json.dumps(journal))
        m.COPY_THEME.chmod(0o700)
        with self.assertRaisesRegex(m.Error, 'directory drift'): m.recover()
        self.assertTrue(m.COPY_THEME.is_dir())
        m.COPY_THEME.chmod(0o755)
        m.recover()
        self.assertFalse(m.COPY_THEME.exists())

    def test_recover_refuses_state_file_metadata_and_content_drift(self):
        self.interrupted_policy()
        m.STATE.chmod(0o640)
        self.assert_recover_refuses_untouched('Unsafe state ownership/mode')
        m.STATE.chmod(0o600)
        m.STATE.write_bytes(m.STATE.read_bytes() + b' ')
        self.assert_recover_refuses_untouched('unknown state drift')

    def test_recover_refuses_unsafe_or_unknown_journals(self):
        self.setup()
        with self.assertRaisesRegex(m.Error, 'No interrupted root transaction'): m.recover()
        m.rollback(); m.CONFIG.write_bytes(self.before[m.CONFIG])
        journal = self.interrupted_policy()
        m.journal_path().chmod(0o644)
        with self.assertRaisesRegex(m.Error, 'Unsafe recovery journal'): m.recover()
        m.journal_path().chmod(0o600)
        for edit in ({'journal': 2}, {'operation': 'migrate'}, {'files': {}},
                     {'directories': [{'path': '/etc', 'before': None, 'after': None}]}):
            with self.subTest(edit=edit):
                m.journal_path().write_text(json.dumps({**journal, **edit}))
                with self.assertRaisesRegex(m.Error, 'Unsupported recovery journal'): m.recover()
        m.journal_path().write_text(json.dumps(journal))
        m.recover()

    def test_state_from_another_version_only_rolls_back(self):
        self.setup()
        state = m.load_state(); state['version'] = '0.0.1'; m.save_state(state)
        for action in (m.doctor, m.repair, lambda: m.policy(True), self.setup):
            with self.subTest(action=action):
                with self.assertRaisesRegex(m.Error, 'run rollback, then setup'): action()
        m.rollback()
        self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
        self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])

    def test_state_without_schema_is_refused_by_every_command(self):
        self.setup()
        state = m.load_state(); del state['schema']; m.save_state(state)
        for action in (m.doctor, m.repair, m.rollback, lambda: m.policy(True), self.setup):
            with self.subTest(action=action):
                with self.assertRaisesRegex(m.Error, 'predates this companion'): action()
        self.assertTrue(m.PAM.exists())

    def crash_at_journal_unlink(self, action):
        unlink = m.unlink_durable
        def crash(path, missing_ok=False):
            if path == m.journal_path(): raise KeyboardInterrupt
            return unlink(path, missing_ok)
        with patch.object(m, 'unlink_durable', crash):
            with self.assertRaises(KeyboardInterrupt): action()

    def test_setup_crash_after_commit_is_undone_by_recover(self):
        self.crash_at_journal_unlink(self.setup)
        self.assertTrue(m.STATE.exists()); self.assertTrue(m.PAM.exists())
        m.recover()
        self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
        self.assertFalse(m.COPY_THEME.exists())
        self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])

    def test_repair_crash_after_commit_is_undone_by_recover(self):
        self.setup(); m.PAM.unlink(); state = m.STATE.read_bytes()
        self.crash_at_journal_unlink(m.repair)
        self.assertTrue(m.PAM.exists())
        m.recover()
        self.assertFalse(m.PAM.exists()); self.assertEqual(m.STATE.read_bytes(), state)

    def test_directories_are_created_with_their_exact_mode_under_any_umask(self):
        previous = os.umask(0o077)
        try:
            mkdir = Path.mkdir
            def crash_after_mkdir(path, *args, **kwargs):
                mkdir(path, *args, **kwargs)
                if path == m.COPY_THEME:
                    raise KeyboardInterrupt  # Before any chmod.
            with patch.object(Path, 'mkdir', crash_after_mkdir):
                with self.assertRaises(KeyboardInterrupt): self.setup()
            self.assertEqual(m.COPY_THEME.stat().st_mode & 0o777, 0o755)
            m.recover()
            self.assertFalse(m.COPY_THEME.exists())
        finally:
            os.umask(previous)

    def test_a_second_root_command_is_refused_while_one_holds_the_lock(self):
        self.setup()
        old = {path: path.read_bytes() for path in (m.STATE, m.CONFIG)}
        fd = os.open(m.STATE.parent / '.lock', os.O_RDWR)
        try:
            m.fcntl.flock(fd, m.fcntl.LOCK_EX | m.fcntl.LOCK_NB)  # Another process's command.
            for action in (self.setup, m.repair, m.rollback, m.recover, lambda: m.policy(True)):
                with self.subTest(action=action):
                    with self.assertRaisesRegex(m.Error, 'Another omarchy-himmelblau root command'):
                        action()
            self.assertFalse(m.journal_path().exists())
            for path, data in old.items():
                self.assertEqual(path.read_bytes(), data)
        finally:
            os.close(fd)
        m.policy(True)

    def test_journal_creation_never_replaces_an_existing_journal(self):
        self.setup()
        m.journal_path().write_text('{"other": 1}'); m.journal_path().chmod(0o600)
        with self.assertRaisesRegex(m.Error, 'journal exists'):
            m.create_exclusive(m.journal_path(), b'{}', 0o600)
        self.assertEqual(m.journal_path().read_text(), '{"other": 1}')
        self.assertEqual([p.name for p in m.STATE.parent.iterdir() if p.name.startswith('.himmelblau-')], [])

    def test_owner_is_set_on_the_temporary_inode_before_replacement(self):
        calls = []
        fchown, replace = os.fchown, os.replace
        def record_fchown(fd, uid, gid):
            calls.append('fchown'); return fchown(fd, uid, gid)
        def record_replace(source, target):
            calls.append('replace'); return replace(source, target)
        target = self.base / 'owned'
        with patch.object(m.os, 'fchown', record_fchown), patch.object(m.os, 'replace', record_replace):
            m.atomic(target, b'data', 0o640, os.geteuid(), os.getegid())
        self.assertEqual(calls, ['fchown', 'replace'])
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)

    def test_transactions_set_directory_owner_and_doctor_checks_group(self):
        self.setup()
        theme_dirs = set(m.theme_directories(m.load_state()['files']))
        chowned = []
        chown = os.chown
        def record(path, uid, gid, *args, **kwargs):
            chowned.append((Path(path), uid, gid)); return chown(path, uid, gid, *args, **kwargs)
        m.PAM.unlink()
        with patch.object(m.os, 'chown', record):
            m.repair()
        self.assertTrue(theme_dirs <= {path for path, *_ in chowned})
        self.assertTrue(all((uid, gid) == m.owner() for path, uid, gid in chowned if path in theme_dirs))
        uid, gid = m.owner(); state = m.load_state()
        with patch.object(m, 'owner', return_value=(uid, gid + 1)):  # Directories now off-group.
            issues = m.root_issues(state)
        self.assertTrue(any('companion theme directory' in issue for issue in issues), issues)

    def test_directory_recovery_accepts_each_field_at_before_or_after_only(self):
        before = {'mode': 0o700, 'uid': 1, 'gid': 1}
        after = {'mode': 0o755, 'uid': 1, 'gid': 2}
        self.assertTrue(m.matches_directory({'mode': 0o755, 'uid': 1, 'gid': 1}, before, after))
        self.assertTrue(m.matches_directory({'mode': 0o700, 'uid': 1, 'gid': 2}, before, after))
        self.assertFalse(m.matches_directory({'mode': 0o750, 'uid': 1, 'gid': 1}, before, after))
        self.assertFalse(m.matches_directory({'mode': 0o755, 'uid': 1, 'gid': 3}, before, after))
        self.assertFalse(m.matches_directory(None, before, after))
        self.assertTrue(m.matches_directory(None, None, after))
        self.assertFalse(m.matches_directory({'mode': 0o700, 'uid': 1, 'gid': 1}, None, after))

    def test_state_access_refuses_writable_or_foreign_ancestors(self):
        self.setup()
        state = m.load_state()
        stat = Path.stat
        foreign = set()
        def mapped(path, *args, **kwargs):
            # Ancestors outside the sandbox are root-owned 0755 on a real system.
            if not path.is_relative_to(self.base):
                return SimpleNamespace(st_uid=0, st_mode=0o40755)
            result = stat(path, *args, **kwargs)
            if path in foreign:
                return SimpleNamespace(st_uid=os.geteuid() + 1, st_mode=result.st_mode)
            return result
        actions = (m.load_state, lambda: m.save_state(state))
        with patch.object(m, 'check_state_ancestors', self.real_ancestor_guard), \
                patch.object(Path, 'stat', mapped):
            for action in actions:
                action()
            self.base.chmod(0o777)
            try:
                for action in actions:
                    with self.assertRaisesRegex(m.Error, 'Unsafe state ancestor'):
                        action()
            finally:
                self.base.chmod(0o700)
            foreign.add(self.base)
            for action in actions:
                with self.assertRaisesRegex(m.Error, 'Unsafe state ancestor'):
                    action()

    def test_atomic_replace_and_delete_fsync_parent_directory(self):
        target = self.base / 'durable'; calls=[]
        original = m.fsync_directory
        with patch.object(m, 'fsync_directory', side_effect=lambda path: calls.append(path) or original(path)):
            m.atomic(target, b'fixed', 0o600)
            m.unlink_durable(target)
        self.assertEqual(calls, [self.base, self.base])

    def fixed_plugin_fixture(self):
        self.setup()
        home = self.base / 'desktop'; home.mkdir(mode=0o700)
        directory = home / '.config/omarchy/plugins/9spokes.himmelblau-lock'
        directory.mkdir(parents=True)
        shutil.copytree(ROOT / 'plugin', directory / 'plugin')
        shutil.copy2(ROOT / 'manifest.json', directory / 'manifest.json')
        for path in directory.rglob('*'):
            path.chmod(0o755 if path.is_dir() or path.name == 'poster.sh' else 0o644)
        for name, fixture in [('FingerprintModel.js','FingerprintModel.js'), ('poster.sh','poster.sh'), ('manifest.json','lock-manifest.json')]:
            (m.LOCK.parent / name).write_bytes((ROOT / 'tests/fixtures' / fixture).read_bytes())
            (m.LOCK.parent / name).chmod(0o644)
        account = SimpleNamespace(pw_name='alice', pw_dir=str(home))
        canonical = patch.object(m, 'canonical_user', return_value=(account,home))
        canonical.start(); self.addCleanup(canonical.stop)
        listing = [{'id':'omarchy.lock','firstParty':True,'enabled':True,'clonedFrom':''},
                   {'id':'9spokes.himmelblau-lock','firstParty':False,'enabled':False,'clonedFrom':'omarchy.lock'}]
        return directory, listing

    def test_fixed_plugin_status_and_prerequisites_are_fail_closed(self):
        directory, listing = self.fixed_plugin_fixture()
        listing[0]['enabled'] = False; listing[1]['enabled'] = True
        with patch.object(m, 'lock_prerequisites'), patch.object(m, 'native_plugins', return_value=listing):
            status = lambda: m.main(['status'])
            m.plugin_status(); m.locker_preflight(); status()
            listing.append({'id':'other.lock','firstParty':False,'enabled':True,'clonedFrom':'omarchy.lock'})
            for action in (m.plugin_status, m.locker_preflight, status):
                with self.assertRaises(m.Error): action()
            listing.pop()
            service = directory / 'plugin/Service.qml'; service.write_bytes(service.read_bytes()+b'// drift\n')
            with self.assertRaisesRegex(m.Error, 'differs from companion release'): m.plugin_status()
        m.PAM.unlink()
        with self.assertRaisesRegex(m.Error, 'Dedicated lock PAM is missing'): m.lock_prerequisites()

    def test_locker_preflight_ignores_stock_pam_drift(self):
        directory, listing = self.fixed_plugin_fixture()
        listing[0]['enabled'] = False
        listing[1]['enabled'] = True
        m.STOCK_PAM.write_bytes(b'# updated by Omarchy\n')
        stat = Path.stat
        def root_owned_pam(path, *args, **kwargs):
            result = stat(path, *args, **kwargs)
            if path == m.PAM:
                return SimpleNamespace(st_uid=0, st_mode=result.st_mode)
            return result
        with patch.object(Path, 'stat', root_owned_pam), \
                patch.object(m, 'native_plugins', return_value=listing):
            m.locker_preflight()
            m.PAM.write_bytes(m.PAM.read_bytes() + b'# drift\n')
            with self.assertRaises(m.Error):
                m.locker_preflight()

    def test_previous_plugin_release_still_locks_but_must_be_updated(self):
        directory, listing = self.fixed_plugin_fixture()
        listing[0]['enabled'] = False; listing[1]['enabled'] = True
        service = directory / 'plugin/Service.qml'
        service.write_bytes(service.read_bytes() + b'// previous release\n')
        current = m.manifest('fixed-plugin')
        previous = dict(m.manifest('previous-release'))
        previous['plugin-files'] = dict(current['files'], **{'plugin/Service.qml': m.digest(service.read_bytes())})
        manifest = m.manifest
        with patch.object(m, 'manifest', lambda name: previous if name == 'previous-release' else manifest(name)), \
                patch.object(m, 'lock_prerequisites'), patch.object(m, 'native_plugins', return_value=listing), \
                patch.object(m.subprocess, 'run') as run:
            m.locker_preflight()
            m.main(['locker-preflight'])
            needs = f'{previous["version"]} needs updating to {current["version"]}'
            for action in (m.plugin_status, lambda: m.main(['status'])):
                with self.assertRaisesRegex(m.Error, needs): action()
            run.assert_not_called()
            # Neither release: refused everywhere, the preflight included.
            service.write_bytes(service.read_bytes() + b'// drift\n')
            for action in (m.locker_preflight, m.plugin_status):
                with self.assertRaisesRegex(m.Error, 'differs from companion release'): action()

    def test_previous_plugin_release_record_matches_current_layout(self):
        current = m.manifest('fixed-plugin')['files']
        previous = m.manifest('previous-release')['plugin-files']
        self.assertEqual(set(previous), set(current))
        # Files that never change between releases must agree; the rest must differ.
        for name in ('plugin/UPSTREAM-LICENSE', 'plugin/FingerprintModel.js', 'plugin/poster.sh', 'plugin/LICENSE'):
            self.assertEqual(previous[name], current[name], name)
        self.assertNotEqual(previous['manifest.json'], current['manifest.json'])

    def test_fixed_plugin_rejects_unlisted_and_symlink_runtime_files(self):
        directory, listing = self.fixed_plugin_fixture()
        extra = directory / 'plugin/Extra.qml'; extra.write_text('unlisted')
        with self.assertRaisesRegex(m.Error, 'runtime tree'): m.fixed_plugin_files()
        extra.unlink(); extra.symlink_to(directory / 'plugin/Service.qml')
        with self.assertRaises(m.Error): m.fixed_plugin_files()

    def test_preflight_refuses_a_second_enabled_lock_clone(self):
        directory, listing = self.fixed_plugin_fixture()
        listing[0]['enabled'] = False
        listing.append({'id':'alice.lock','firstParty':False,'enabled':True,'clonedFrom':'omarchy.lock'})
        with patch.object(m, 'lock_prerequisites'), patch.object(m, 'native_plugins', return_value=listing), \
             patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(m.Error, 'only enabled lock'): m.locker_preflight()
            with self.assertRaisesRegex(m.Error, 'only enabled lock'): m.plugin_status()
            run.assert_not_called()

    def test_policy_toggle_and_repair_preserve_original(self):
        before = m.CONFIG.read_bytes()
        m.setup('example.com', 'alice@example.com')
        state = m.load_state()
        m.doctor()
        original = next(item.copy() for item in state['files'] if Path(item['path']) == m.CONFIG)
        original_run = m.subprocess.run
        def read_only_command(argv, **kwargs):
            self.assertIn(argv[0], ('/usr/bin/patch', '/usr/bin/pacman'))
            return original_run(argv, **kwargs)
        with patch.object(m.subprocess, 'run', side_effect=read_only_command):
            m.policy(True)
            m.doctor()
            m.repair()
            m.policy(False)
            m.doctor()
        changed = next(item for item in m.load_state()['files'] if Path(item['path']) == m.CONFIG)
        self.assertEqual(changed['restore'], original['restore'])
        self.assertFalse(m.load_state()['apply_policy'])
        self.assertIn(b'connection_timeout = 17', m.CONFIG.read_bytes())
        self.assertEqual(m.CONFIG.stat().st_mode & 0o777, 0o644)
        self.assertEqual(m.STATE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(m.STATE.parent.stat().st_mode & 0o777, 0o700)
        m.rollback()
        self.assertEqual(m.CONFIG.read_bytes(), before)

    def test_policy_adopted_config_records_original_for_rollback(self):
        desired = m.managed_config(m.CONFIG.read_bytes(), 'example.com', 'alice@example.com')
        m.CONFIG.write_bytes(desired)
        m.setup('example.com', 'alice@example.com')
        self.assertFalse(any(Path(item['path']) == m.CONFIG for item in m.load_state()['files']))
        m.policy(True)
        m.doctor()
        m.rollback()
        self.assertEqual(m.CONFIG.read_bytes(), desired)

    def test_policy_preserves_comment_format_and_noop_writes_nothing(self):
        m.CONFIG.write_bytes(m.managed_config(m.CONFIG.read_bytes(), 'example.com', 'alice@example.com')
                             .replace(b'apply_policy = false', b'  APPLY_POLICY = FALSE  # operator note'))
        m.setup('example.com', 'alice@example.com')
        m.policy(True)
        self.assertIn(b'  APPLY_POLICY = true  # operator note\n', m.CONFIG.read_bytes())
        before = m.STATE.read_bytes()
        with patch.object(m, 'atomic', side_effect=AssertionError('Unexpected no-op write')):
            m.policy(True)
        self.assertEqual(m.STATE.read_bytes(), before)

    def test_policy_state_mode_is_strict_and_drift_is_not_adopted(self):
        m.setup('example.com', 'alice@example.com')
        old_config = m.CONFIG.read_bytes(); old_state = m.STATE.read_bytes()
        m.CONFIG.write_bytes(old_config.replace(b'apply_policy = false', b'apply_policy = true'))
        for action in (m.doctor, m.repair, lambda: m.policy(True)):
            with self.assertRaises(m.Error): action()
        self.assertEqual(m.STATE.read_bytes(), old_state)
        m.CONFIG.write_bytes(old_config)
        state = m.load_state(); state['apply_policy'] = 'false'; m.save_state(state)
        with self.assertRaises(m.Error): m.doctor()

    def test_policy_refuses_conflicting_hierarchy_before_mutation(self):
        m.setup('example.com', 'alice@example.com')
        old_config = m.CONFIG.read_bytes(); old_state = m.STATE.read_bytes()
        for content in ('[global]\napply_policy = false\n', '[example.com]\napply_policy = false\n'):
            m.SERVER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            m.SERVER_CONFIG.write_text(content)
            with self.assertRaises(m.Error): m.policy(True)
            self.assertEqual(m.CONFIG.read_bytes(), old_config)
            self.assertEqual(m.STATE.read_bytes(), old_state)
            m.SERVER_CONFIG.unlink()
        m.CONFIG.write_bytes(old_config + b'\n[other]\napply_policy = false\n')
        with self.assertRaises(m.Error): m.policy(True)
        self.assertEqual(m.STATE.read_bytes(), old_state)
        m.CONFIG.write_bytes(old_config)
        directory = Path(str(m.CONFIG) + '.d'); directory.mkdir()
        (directory / '50-operator.conf').write_text('[global]\napply_policy = true\n')
        with self.assertRaises(m.Error): m.policy(True)
        self.assertEqual(m.CONFIG.read_bytes(), old_config)

    def test_policy_transaction_restores_config_and_state_on_each_write_failure(self):
        m.setup('example.com', 'alice@example.com')
        original = m.atomic
        for target in (m.CONFIG, m.STATE):
            old_config = m.CONFIG.read_bytes(); old_state = m.STATE.read_bytes()
            failed = False
            def fail_once(path, data, mode, *owner):
                nonlocal failed
                if Path(path) == target and not failed:
                    failed = True
                    raise OSError('synthetic failure')
                return original(path, data, mode, *owner)
            with patch.object(m, 'atomic', fail_once):
                with self.assertRaises(m.Error): m.policy(True)
            self.assertTrue(failed)
            self.assertEqual(m.CONFIG.read_bytes(), old_config)
            self.assertEqual(m.STATE.read_bytes(), old_state)
            self.assertFalse(m.journal_path().exists())

    def test_policy_failed_restoration_retains_private_recovery_journal(self):
        m.setup('example.com', 'alice@example.com')
        old_config = m.CONFIG.read_bytes(); old_state = m.STATE.read_bytes()
        original = m.atomic
        def failure(path, data, mode, *owner):
            if Path(path) == m.STATE: raise OSError('synthetic persistent state failure')
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', failure):
            with self.assertRaisesRegex(m.Error, 'could not be undone; run recover'): m.policy(True)
        journal = m.journal_path()
        saved = json.loads(journal.read_text())
        self.assertEqual(m.base64.b64decode(saved['files'][0]['before']['data']), old_config)
        self.assertEqual(m.base64.b64decode(saved['state_before']), old_state)
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(m.Error, 'run recover'): m.policy(True)
        m.recover()
        self.assertEqual((m.CONFIG.read_bytes(), m.STATE.read_bytes()), (old_config, old_state))

    def test_pending_journal_and_stray_files_block_health_and_mutators(self):
        m.setup('example.com', 'alice@example.com')
        state = m.load_state()
        for name, message in (('transaction.json', 'Interrupted root transaction'),
                              ('repair-backup.json', 'Unexpected file in state directory')):
            journal = m.STATE.parent / name
            journal.write_text('{}'); journal.chmod(0o600)
            old_state = m.STATE.read_bytes(); old_config = m.CONFIG.read_bytes()
            def root_status():
                with patch.object(m, 'root_required'), patch.object(m, 'load_state', return_value=state):
                    m.main(['doctor'])
            actions = (m.doctor, m.repair, m.rollback, lambda: m.policy(True),
                       lambda: m.setup('example.com', 'alice@example.com'), root_status)
            for action in actions:
                with self.subTest(name=name, action=action):
                    with self.assertRaisesRegex(m.Error, message):
                        action()
                    self.assertEqual(m.STATE.read_bytes(), old_state)
                    self.assertEqual(m.CONFIG.read_bytes(), old_config)
            self.assertIn(message, '\n'.join(m.root_issues(state)))
            journal.unlink()

    def test_final_policy_journal_unlink_failure_is_not_reported_healthy(self):
        m.setup('example.com', 'alice@example.com')
        journal = m.journal_path()
        original = Path.unlink
        def fail_final_unlink(path, *args, **kwargs):
            if path == journal: raise OSError('synthetic journal unlink failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_final_unlink):
            with self.assertRaisesRegex(OSError, 'journal unlink failure'): m.policy(True)
        self.assertTrue(json.loads(m.STATE.read_text())['apply_policy'])
        self.assertTrue(journal.exists())
        with self.assertRaisesRegex(m.Error, 'Interrupted root transaction'): m.doctor()
        with self.assertRaisesRegex(m.Error, 'Interrupted root transaction'): m.repair()
        m.recover()
        self.assertFalse(m.load_state()['apply_policy'])

    def test_doctor_is_root_only_and_status_is_user_only(self):
        with patch.object(m.os, 'geteuid', return_value=1000), patch.object(m, 'doctor') as doctor:
            with self.assertRaisesRegex(m.Error, 'as root'):
                m.main(['doctor'])
            doctor.assert_not_called()
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'lock_prerequisites') as status:
            with self.assertRaisesRegex(m.Error, 'not root'):
                m.main(['status'])
            status.assert_not_called()

    def test_policy_cli_requires_explicit_choice_and_root(self):
        for args in (['policy'], ['policy', '--enable', '--disable']):
            with patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit): m.main(args)
        with patch.object(m.os, 'geteuid', return_value=1000), patch.object(m, 'policy') as action:
            with self.assertRaises(m.Error): m.main(['policy', '--enable'])
            action.assert_not_called()

    def test_setup_preserves_unrelated_settings_and_rollback(self):
        self.setup()
        self.assertIn(b'connection_timeout = 17', m.CONFIG.read_bytes())
        self.assertIn(b'[example.com]\ncustom = untouched', m.CONFIG.read_bytes())
        self.assertEqual(m.LOCK.read_bytes(), self.before[m.LOCK])  # packaged QML untouched
        self.assertEqual(m.STOCK_PAM.read_bytes(), self.before[m.STOCK_PAM])
        self.assertIn(b'pam_himmelblau.so', m.PAM.read_bytes())
        self.assertIn(b'config: \"omarchy-himmelblau-lock-password\"', m.patch_source('lock-conversation', self.before[m.LOCK]))
        self.assertEqual(m.digest((m.COPY_THEME / 'Main.qml').read_bytes()), json.loads((m.ASSETS / 'patches.json').read_text())['sddm-conversation']['after'])
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

    def write_autologin(self, text):
        path = self.base / 'etc/sddm.conf.d/autologin.conf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_setup_refuses_sddm_autologin(self):
        path = self.write_autologin('[Autologin]\nUser=alice\nSession=omarchy\n\n[Theme]\nCurrent=omarchy\n')
        with self.assertRaisesRegex(m.Error, 'autologin.*' + str(path)):
            self.setup()
        self.assertFalse(m.STATE.exists())
        self.assertFalse(m.OVERRIDE.exists())

    def test_setup_ignores_empty_or_commented_autologin_user(self):
        self.write_autologin('[Autologin]\nUser=\n#User=alice\nSession=omarchy\n[Other]\nUser=alice\n')
        self.setup()
        self.assertTrue(m.STATE.exists())

    def test_setup_finds_autologin_the_way_sddm_parses_it(self):
        conf_dir = self.base / 'etc/sddm.conf.d'
        cases = {
            conf_dir / 'autologin': '[Autologin]\nUser=alice\n',  # No .conf suffix.
            conf_dir / 'inline.conf': '[Autologin] # active\nUser=alice\n',
            conf_dir / 'value-comment.conf': '[Autologin]\nUser = alice # note\n',
            self.base / 'usr/lib/sddm/sddm.conf.d/vendor.conf': '[Autologin]\nUser=alice\n',
            self.base / 'etc/sddm.conf': '[General]\n[Autologin]\n  User=alice\n',
        }
        for path, text in cases.items():
            with self.subTest(path=path.name):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                self.assertEqual(m.autologin_sources(), [path])
                with self.assertRaisesRegex(m.Error, 'autologin'): self.setup()
                path.unlink()

    def test_setup_ignores_autologin_sddm_would_not_apply(self):
        self.write_autologin('[Autologin]\n# User=alice\nUser=\nUser= # none\n;User=alice\n'
                             '[Other]\nUser=alice\nUser=alice [Autologin]\n')
        (self.base / 'etc/sddm.conf.d/subdir').mkdir()
        self.assertEqual(m.autologin_sources(), [])
        self.setup()

    def test_doctor_reports_autologin_added_after_setup(self):
        self.setup()
        self.write_autologin('[Autologin]\nUser=alice\n')
        self.assertTrue(any('autologin' in issue for issue in m.root_issues(m.load_state())))

    def missing_pam_root(self):
        self.setup()
        m.PAM.unlink()
        return m.STATE.read_bytes()

    def test_repair_refuses_content_and_permission_drift_before_writes(self):
        old = self.missing_pam_root()
        main = m.COPY_THEME / 'Main.qml'
        original = main.read_bytes()
        for data, mode in [(original + b'// drift', 0o644), (original, 0o600)]:
            with self.subTest(mode=mode):
                main.write_bytes(data)
                main.chmod(mode)
                with self.assertRaises(m.Error):
                    m.repair()
                self.assertEqual(m.STATE.read_bytes(), old)
                self.assertFalse(m.PAM.exists())
                self.assertFalse(m.journal_path().exists())

    def test_repair_refuses_extra_theme_files_before_writes(self):
        old = self.missing_pam_root()
        (m.COPY_THEME / 'unexpected.qml').write_bytes(b'operator asset')
        with self.assertRaisesRegex(m.Error, 'Unexpected companion theme entry'):
            m.repair()
        self.assertFalse(m.PAM.exists())
        self.assertEqual(m.STATE.read_bytes(), old)

    def test_repair_rolls_back_files_and_state_on_write_failure(self):
        old = self.missing_pam_root()
        atomic = m.atomic
        for failed_path in (m.PAM, m.STATE):
            with self.subTest(path=failed_path):
                failed = False
                def injected(path, data, mode, *owner):
                    nonlocal failed
                    if path == failed_path and not failed:
                        failed = True
                        raise OSError('synthetic repair failure')
                    return atomic(path, data, mode, *owner)
                with patch.object(m, 'atomic', injected):
                    with self.assertRaisesRegex(m.Error, 'Repair failed and was undone'):
                        m.repair()
                self.assertFalse(m.PAM.exists())
                self.assertEqual(m.STATE.read_bytes(), old)
                self.assertFalse(m.journal_path().exists())

    def test_repair_failed_rollback_retains_recoverable_journal(self):
        old = self.missing_pam_root()
        atomic = m.atomic
        def injected(path, data, mode, *owner):
            if path == m.STATE:
                raise OSError('state write and restore failure')
            return atomic(path, data, mode, *owner)
        with patch.object(m, 'atomic', injected):
            with self.assertRaisesRegex(m.Error, 'could not be undone; run recover'):
                m.repair()
        journal = json.loads(m.journal_path().read_text())
        self.assertEqual(m.base64.b64decode(journal['state_before']), old)
        self.assertEqual(journal['files'], [{'path': str(m.PAM), 'before': None, 'after': {
            'sha256': m.digest(m.patch_source('lock-pam', self.before[m.STOCK_PAM])),
            'mode': 0o644, 'uid': os.geteuid(), 'gid': os.getegid()}}])
        with self.assertRaisesRegex(m.Error, 'run recover'):
            m.repair()
        m.recover()
        self.assertEqual(m.STATE.read_bytes(), old); self.assertFalse(m.PAM.exists())

    def test_repair_restores_deleted_recovery_account_and_pin_service(self):
        self.setup()
        recovery = m.RECOVERY_ACCOUNT.read_bytes()
        m.RECOVERY_ACCOUNT.unlink()
        m.PIN_PAM.unlink()
        with self.assertRaises(m.Error):
            m.doctor()
        m.repair()
        self.assertEqual(m.RECOVERY_ACCOUNT.read_bytes(), recovery)
        self.assertEqual(m.PIN_PAM.read_bytes(), m.PIN_PAM_CONTENT)
        m.doctor()

    def test_native_packaged_pin_pam_matches_companion_contract(self):
        # Read the real package payload, not the test's synthesized guest fixture.
        packaged = ROOT / 'packaging/sddm-himmelblau/sddm-himmelblau-pin-change.pam'
        self.assertEqual(packaged.read_bytes(), m.SDDM_PIN_PAM_CONTENT,
                         'Native package PAM bytes must match companion preflight exactly')

    def test_native_prerequisite_is_checked_and_never_managed(self):
        native = m.SDDM_PIN_PAM.read_bytes()
        with patch.object(m.subprocess, 'run', return_value=SimpleNamespace(stdout='sddm 0.21.0-1')):
            with self.assertRaisesRegex(m.Error, 'Unsupported native'): self.setup()
        self.assertFalse(m.STATE.exists())
        self.setup()
        self.assertNotIn(str(m.SDDM_PIN_PAM), [entry['path'] for entry in m.load_state()['files']])
        m.SDDM_PIN_PAM.write_bytes(native + b'# drift\n')
        with self.assertRaisesRegex(m.Error, 'Native SDDM PIN'): m.repair()
        m.rollback()
        self.assertEqual(m.SDDM_PIN_PAM.read_bytes(), native + b'# drift\n')

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
        m.STATE.parent.mkdir(exist_ok=True); m.STATE.parent.chmod(0o755)
        with self.assertRaisesRegex(m.Error, 'State directory must be'): self.setup()
        self.assertFalse(m.PAM.exists())

    def test_transaction_failure_restores_touched_files(self):
        original = m.atomic
        failed = False
        def injected(path, data, mode, *owner):
            nonlocal failed
            if path == m.CONFIG and not failed:
                failed = True
                raise OSError('synthetic write failure')
            return original(path, data, mode, *owner)
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

    def test_patched_stock_pam_rejected_before_changes(self):
        patched = m.patch_source('lock-pam', self.before[m.STOCK_PAM])
        m.STOCK_PAM.write_bytes(patched)
        with self.assertRaisesRegex(m.Error, 'pristine'):self.setup()
        self.assertFalse(m.STATE.exists())
        self.assertFalse(m.PAM.exists())
        self.assertEqual(m.STOCK_PAM.read_bytes(), patched)

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
            self.assertEqual(m.administrator('alice@example.com')['uid'], 123456)
            m.LOCAL_PASSWD.write_text('alice:x:2000:2000::/home/alice:/bin/bash\n')
            with self.assertRaises(m.Error): m.administrator('alice@example.com')
            m.LOCAL_PASSWD.write_text('local:x:123456:2000::/home/local:/bin/bash\n')
            with self.assertRaises(m.Error): m.administrator('alice@example.com')
        with patch.object(m.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=0, pw_name='root')):
            with self.assertRaises(m.Error): m.administrator('alice@example.com')

    def test_admin_option_only_grants_verified_uid(self):
        run = subprocess.run
        def command(argv, **kw):
            if argv[0] == '/usr/bin/visudo': return SimpleNamespace(returncode=0)
            return run(argv, **kw)
        with patch.object(m, 'administrator', return_value={'requested_upn':'alice@example.com', 'canonical_name':'alice', 'uid':123456}), patch.object(m.subprocess, 'run', command):
            self.setup(admin=True)
        self.assertIn(b'"alice" ALL=(ALL:ALL) ALL', m.SUDOERS.read_bytes())
        self.assertNotIn(b'NOPASSWD', m.SUDOERS.read_bytes())
        self.assertEqual(m.SUDOERS.stat().st_mode & 0o777, 0o440)

    def test_legitimate_nss_home_alias_and_descendant_symlink(self):
        home = self.base / 'actual-home'; home.mkdir()
        alias = self.base / 'upn-home'; alias.symlink_to(home, target_is_directory=True)
        account = SimpleNamespace(pw_name='alice', pw_dir=str(alias))
        with patch.object(m.pwd, 'getpwuid', return_value=account):
            self.assertEqual(m.canonical_user(), (account, home))
            clone = home / '.config/omarchy/plugins/9spokes.himmelblau-lock'
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

    def test_public_theme_directories_under_private_umask_and_repair(self):
        nested = m.THEME / 'assets/icons'; nested.mkdir(parents=True)
        (nested / 'lock.png').write_bytes(b'synthetic-icon')
        previous = os.umask(0o077)
        try:
            self.setup()
        finally:
            os.umask(previous)
        directories = [m.COPY_THEME, m.COPY_THEME / 'assets', m.COPY_THEME / 'assets/icons']
        for directory in directories:
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
        self.assertEqual(m.STATE.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(m.STATE.stat().st_mode & 0o777, 0o600)
        directories[0].chmod(0o700)
        with self.assertRaisesRegex(m.Error, '0755'):m.doctor()
        m.repair()
        self.assertEqual(directories[0].stat().st_mode & 0o777, 0o755)
        self.assertEqual(m.STATE.parent.stat().st_mode & 0o777, 0o700)
        # All files must pass preflight before directory permissions are repaired.
        directories[0].chmod(0o700)
        m.CONFIG.write_bytes(m.CONFIG.read_bytes() + b'# unexpected operator edit\n')
        with self.assertRaises(m.Error):m.repair()
        self.assertEqual(directories[0].stat().st_mode & 0o777, 0o700)

    def test_theme_directory_repair_refuses_foreign_owner_and_symlinks(self):
        nested = m.THEME / 'assets'; nested.mkdir()
        (nested / 'icon.png').write_bytes(b'icon')
        self.setup()
        m.COPY_THEME.chmod(0o700)
        original_stat = Path.stat
        def metadata(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if path == m.COPY_THEME:
                return SimpleNamespace(st_uid=os.geteuid() + 1, st_gid=result.st_gid, st_mode=result.st_mode)
            return result
        with patch.object(Path, 'stat', metadata):
            with self.assertRaisesRegex(m.Error, 'Unowned'):m.repair()
        self.assertEqual(m.COPY_THEME.stat().st_mode & 0o777, 0o700)
        outside = self.base / 'moved-assets'
        (m.COPY_THEME / 'assets').rename(outside)
        (m.COPY_THEME / 'assets').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(m.Error, 'Symlink'):m.repair()
        self.assertEqual(m.COPY_THEME.stat().st_mode & 0o777, 0o700)

    def test_fresh_main_config_is_public_and_protected_config_is_refused(self):
        m.CONFIG.parent.chmod(0o700)
        with self.assertRaisesRegex(m.Error, 'directory'):self.setup()
        self.assertEqual(m.CONFIG.parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse(m.STATE.exists())
        m.CONFIG.parent.chmod(0o755)
        m.CONFIG.chmod(0o600)
        before = m.CONFIG.read_bytes()
        with self.assertRaisesRegex(m.Error, 'root-owned 0644'):self.setup()
        self.assertEqual(m.CONFIG.read_bytes(), before)
        self.assertEqual(m.CONFIG.stat().st_mode & 0o777, 0o600)
        self.assertFalse(m.PAM.exists())
        self.assertFalse(m.STATE.exists())
        m.CONFIG.unlink()
        previous = os.umask(0o077)
        try:
            self.setup()
        finally:
            os.umask(previous)
        self.assertEqual(m.CONFIG.stat().st_mode & 0o777, 0o644)
        self.assertEqual(m.CONFIG.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(m.STATE.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(m.STATE.stat().st_mode & 0o777, 0o600)
        m.doctor()

    def test_repair_view_preflight_precedes_new_pin_service_and_state_write(self):
        self.setup()
        m.PIN_PAM.unlink()
        before = m.STATE.read_bytes()
        view=m.LOCK.parent/'LockView.qml';view.write_bytes(view.read_bytes()+b'// unknown\n')
        with self.assertRaises(m.Error):m.repair()
        self.assertEqual(m.STATE.read_bytes(),before);self.assertFalse(m.PIN_PAM.exists())

    def test_malformed_state_exits_with_one_line_error(self):
        self.setup()
        state = m.load_state()
        cases = [b'not json', b'[]', json.dumps({**state, 'files': [{}]}).encode(),
                 json.dumps({**state, 'admin': 'no'}).encode(),
                 json.dumps({**state, 'schema': '1'}).encode()]
        for data in cases:
            with self.subTest(data=data[:30]), patch.object(m, 'root_required'):
                m.STATE.write_bytes(data)
                with self.assertRaises(SystemExit) as exit:
                    m.cli(['repair'])
                self.assertRegex(exit.exception.code, f'^Invalid (JSON|state file): {m.STATE}$')

    def test_malformed_patch_manifest_exits_with_one_line_error(self):
        assets = self.base / 'assets'
        assets.mkdir()
        m.PAM.write_bytes(b'synthetic')
        for data in ('not json', '[]', '{"lock-pam": {"before": 1}}'):
            with self.subTest(data=data), patch.object(m, 'ASSETS', assets):
                (assets / 'patches.json').write_text(data)
                m._MANIFESTS.clear()
                with self.assertRaises(SystemExit) as exit:
                    m.cli(['plugin-status'])
                self.assertTrue(exit.exception.code.startswith(f'Invalid {assets}/patches.json'))
        m._MANIFESTS.clear()

    def test_identity_validation(self):
        self.assertEqual(m.identity('EXAMPLE.COM', 'Alice@EXAMPLE.COM'), ('example.com', 'alice@example.com'))
        for domain, upn in [('example.com','alice@other.com'), ('x;echo','a@x;echo'), ('example.com','a\n@EXAMPLE.COM')]:
            with self.assertRaises(m.Error):m.identity(domain, upn)


    # pambase system-auth / system-login management

    def pristine(self, name):
        return (PAM_STACK / 'pristine' / name).read_bytes()

    def aad_tool(self, name):
        return (PAM_STACK / 'aad-tool' / name).read_bytes()

    def omarchy(self, aad_tool=False):
        """system-auth after Omarchy's install-time lockout tuning (captured with its sed),
        optionally followed by aad-tool configure-pam 4.0.8."""
        return (PAM_STACK / ('omarchy-aad-tool' if aad_tool else 'omarchy') / 'system-auth').read_bytes()

    def baselines(self):
        """Each supported starting layout: (label, kind, system-auth, system-login, baseline)."""
        return [('pambase', 'pambase', self.pristine('system-auth'), self.pristine('system-login'),
                 self.pristine('system-auth')),
                ('aad-tool on pambase', 'pambase', self.aad_tool('system-auth'),
                 self.aad_tool('system-login'), self.pristine('system-auth')),
                ('omarchy', 'omarchy', self.omarchy(), self.pristine('system-login'), self.omarchy()),
                ('aad-tool on omarchy', 'omarchy', self.omarchy(aad_tool=True),
                 self.aad_tool('system-login'), self.omarchy()),
                # aad-tool with himmelblau-idm/himmelblau#1995: system-login left pristine.
                ('fixed aad-tool on pambase', 'pambase', self.aad_tool('system-auth'),
                 self.pristine('system-login'), self.pristine('system-auth')),
                ('fixed aad-tool on omarchy', 'omarchy', self.omarchy(aad_tool=True),
                 self.pristine('system-login'), self.omarchy())]

    def fake_pambase_release(self, change=b'# pambase 2027\n', version='20270101-1', omarchy=None):
        """A supported release whose pristine system-auth has one more line between
        the patches' two hunks (none if change is empty). omarchy overrides the hash
        recorded for its Omarchy baseline."""
        assets = self.base / 'assets'
        shutil.copytree(ROOT / 'assets', assets)
        lines = self.pristine('system-auth').splitlines(keepends=True)
        auth = b''.join(lines[:23]) + change + b''.join(lines[23:])
        login = self.pristine('system-login') + b'# pambase 2027\n'
        data = json.loads((assets / 'patches.json').read_text())
        baselines, results = {}, []
        for kind, suffix, source in (('pambase', '', auth), ('omarchy', '-omarchy', m.omarchy_tuned(auth))):
            work = self.base / f'patchwork{suffix}'; work.mkdir()
            (work / 'system-auth').write_bytes(source)
            subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', str(work)], check=True, capture_output=True,
                           input=(assets / f'system-auth-20260616-1{suffix}.patch').read_bytes())
            results.append((work / 'system-auth').read_bytes())
            shutil.copy(assets / f'system-auth-20260616-1{suffix}.patch', assets / f'system-auth-{version}{suffix}.patch')
            data[f'system-auth-{version}{suffix}'] = {'filename': 'system-auth', 'before': m.digest(source),
                                                      'after': m.digest(results[-1])}
            baselines[kind] = {'system-auth': m.digest(source), 'aad-tool': '0' * 64,
                               'patch': f'system-auth-{version}{suffix}'}
        self.assertEqual(results[0], results[1])
        if omarchy is not None:
            baselines['omarchy']['system-auth'] = omarchy
        data['pambase'][version] = {'system-login': m.digest(login), 'aad-tool-system-login': '0' * 64,
                                    'baselines': baselines}
        (assets / 'patches.json').write_text(json.dumps(data))
        patched = results[0]
        assets_patch = patch.object(m, 'ASSETS', assets)
        assets_patch.start(); self.addCleanup(assets_patch.stop)
        return auth, login, patched

    def upgrade_pambase(self, auth, login, version='20270101-1', keep_system_auth=False):
        """What pacman does: system-auth is ours, so the new copy lands as a .pacnew
        (unless the package's copy didn't change); an unmodified system-login is replaced."""
        if not keep_system_auth:
            pacnew = m.pacnew(m.SYSTEM_AUTH); pacnew.write_bytes(auth); pacnew.chmod(0o644)
        m.SYSTEM_LOGIN.write_bytes(login)
        self.pambase = version

    def pam_files(self):
        return {path: path.read_bytes() if path.exists() else None
                for path in (m.SYSTEM_AUTH, m.SYSTEM_LOGIN, m.pacnew(m.SYSTEM_AUTH))}

    def test_patched_pam_files_equal_the_pam_stack_golden_files(self):
        release = m.pambase_release('20260616-1')
        golden = (PAM_STACK / 'stacks/system-auth').read_bytes()
        for kind, baseline in (('pambase', self.pristine('system-auth')), ('omarchy', self.omarchy())):
            self.assertEqual(m.digest(baseline), release['baselines'][kind]['system-auth'])
            self.assertEqual(m.patched_system_auth(release, kind, baseline), golden)
        self.assertEqual(self.before[m.STOCK_PAM], self.pristine('omarchy-lock-password'))
        self.assertEqual(m.patch_source('lock-pam', self.before[m.STOCK_PAM]),
                         (PAM_STACK / 'stacks/omarchy-himmelblau-lock-password').read_bytes())

    def test_omarchy_tuning_matches_its_sed_and_leaves_our_output_alone(self):
        self.assertEqual(m.omarchy_tuned(self.pristine('system-auth')), self.omarchy())
        self.assertEqual(m.omarchy_tuned(self.omarchy()), self.omarchy())
        golden = (PAM_STACK / 'stacks/system-auth').read_bytes()
        self.assertEqual(m.omarchy_tuned(golden), golden)  # Omarchy re-running its sed changes nothing.

    def test_aad_tool_fixtures_are_their_baseline_plus_exactly_four_lines(self):
        release = m.pambase_release('20260616-1')
        cases = [(release['baselines']['pambase']['aad-tool'], self.aad_tool('system-auth'), self.pristine('system-auth')),
                 (release['baselines']['omarchy']['aad-tool'], self.omarchy(aad_tool=True), self.omarchy()),
                 (release['aad-tool-system-login'], self.aad_tool('system-login'), self.pristine('system-login'))]
        for wanted, aad, baseline in cases:
            self.assertEqual(m.digest(aad), wanted)
            self.assertEqual(m.without_aad_tool(aad), baseline)
            self.assertIsNone(m.without_aad_tool(baseline))
            self.assertIsNone(m.without_aad_tool(aad + m.AAD_TOOL_LINES[0]))

    def test_setup_patches_system_auth_and_watches_system_login(self):
        self.setup()
        self.assertEqual(m.SYSTEM_AUTH.read_bytes(), (PAM_STACK / 'stacks/system-auth').read_bytes())
        self.assertEqual(m.SYSTEM_LOGIN.read_bytes(), self.pristine('system-login'))
        state = m.load_state()
        self.assertEqual(state['pambase'], '20260616-1')
        self.assertEqual(state['watched'], [{'path': str(m.SYSTEM_LOGIN), 'pambase': '20260616-1',
                                             'sha256': m.digest(self.pristine('system-login'))}])
        entry = m.system_auth_entry(state)
        self.assertEqual((entry['restore_kind'], m.base64.b64decode(entry['restore'])),
                         ('pambase', self.pristine('system-auth')))
        m.doctor(); m.rollback()
        self.assertEqual(m.SYSTEM_AUTH.read_bytes(), self.pristine('system-auth'))
        self.assertEqual(m.SYSTEM_LOGIN.read_bytes(), self.pristine('system-login'))

    def test_setup_from_each_baseline_rolls_back_to_the_baseline_never_aad_tool(self):
        golden = (PAM_STACK / 'stacks/system-auth').read_bytes()
        for label, kind, auth, login, baseline in self.baselines():
            with self.subTest(label):
                m.SYSTEM_AUTH.write_bytes(auth); m.SYSTEM_LOGIN.write_bytes(login)
                self.setup()
                self.assertEqual(m.SYSTEM_AUTH.read_bytes(), golden)
                self.assertEqual(m.SYSTEM_LOGIN.read_bytes(), self.pristine('system-login'))
                state = m.load_state()
                self.assertNotIn(str(m.SYSTEM_LOGIN), [entry['path'] for entry in state['files']])
                entry = m.system_auth_entry(state)
                self.assertEqual((entry['restore_kind'], m.base64.b64decode(entry['restore'])), (kind, baseline))
                m.doctor(); m.rollback()
                self.assertEqual(m.SYSTEM_AUTH.read_bytes(), baseline)
                self.assertEqual(m.SYSTEM_LOGIN.read_bytes(), self.pristine('system-login'))

    def test_setup_refuses_unrecognised_pam_before_any_change(self):
        golden = (PAM_STACK / 'stacks/system-auth').read_bytes()
        cases = [
            ('aad-tool on system-login only', {m.SYSTEM_LOGIN: self.aad_tool('system-login')}, 'both'),
            ('aad-tool on system-login only', {m.SYSTEM_AUTH: self.omarchy(), m.SYSTEM_LOGIN: self.aad_tool('system-login')}, 'both'),
            ('fixed aad-tool lines moved', {m.SYSTEM_AUTH: self.pristine('system-auth') + b''.join(m.AAD_TOOL_LINES)}, 'both'),
            ('fixed aad-tool twice', {m.SYSTEM_AUTH: self.aad_tool('system-auth') + m.AAD_TOOL_LINES[3]}, 'both'),
            ('patched', {m.SYSTEM_AUTH: golden}, 'both'),
            ('edited', {m.SYSTEM_AUTH: self.pristine('system-auth') + b'# local\n'}, 'both'),
            ('edited omarchy', {m.SYSTEM_AUTH: self.omarchy() + b'# local\n'}, 'both'),
            ('half omarchy', {m.SYSTEM_AUTH: self.pristine('system-auth').replace(
                b'pam_faillock.so      authfail', b'pam_faillock.so authfail deny=10 unlock_time=120')}, 'both'),
            ('aad-tool lines on omarchy moved', {m.SYSTEM_AUTH: self.omarchy() + b''.join(m.AAD_TOOL_LINES),
                                                 m.SYSTEM_LOGIN: self.aad_tool('system-login')}, 'both'),
            ('aad-tool lines moved', {m.SYSTEM_AUTH: self.pristine('system-auth') + b''.join(m.AAD_TOOL_LINES),
                                      m.SYSTEM_LOGIN: self.aad_tool('system-login')}, 'both'),
            ('aad-tool twice', {m.SYSTEM_AUTH: self.aad_tool('system-auth') + m.AAD_TOOL_LINES[3],
                                m.SYSTEM_LOGIN: self.aad_tool('system-login')}, 'both'),
            ('pacnew', {m.pacnew(m.SYSTEM_AUTH): self.pristine('system-auth')}, 'Pending pambase'),
            ('pacnew', {m.pacnew(m.SYSTEM_LOGIN): self.pristine('system-login')}, 'Pending pambase'),
        ]
        for label, files, message in cases:
            with self.subTest(label):
                for path, data in files.items(): path.write_bytes(data)
                with self.assertRaisesRegex(m.Error, message): self.setup()
                self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
                self.assertEqual({path: path.read_bytes() for path in files}, files)
                for name, path in (('system-auth', m.SYSTEM_AUTH), ('system-login', m.SYSTEM_LOGIN)):
                    path.write_bytes(self.pristine(name))
                for path in (m.pacnew(m.SYSTEM_AUTH), m.pacnew(m.SYSTEM_LOGIN)):
                    path.unlink(missing_ok=True)
        self.pambase = '20990101-1'
        with self.assertRaisesRegex(m.Error, 'Unsupported pambase 20990101-1'): self.setup()
        self.assertFalse(m.STATE.exists())

    def test_aad_tool_hash_must_strip_back_to_the_baseline_and_pristine_login(self):
        """A recorded aad-tool hash alone is not enough: stripping its lines must land on
        the recorded baseline and pristine system-login too."""
        release = m.pambase_release('20260616-1')
        for label, auth, login in [
                ('system-auth', self.aad_tool('system-auth') + b'# extra\n', self.aad_tool('system-login')),
                ('system-auth alone', self.aad_tool('system-auth') + b'# extra\n', self.pristine('system-login')),
                ('system-login', self.aad_tool('system-auth'), self.aad_tool('system-login') + b'# extra\n')]:
            with self.subTest(label):
                lying = json.loads(json.dumps(release))
                lying['baselines']['pambase']['aad-tool'] = m.digest(auth)
                lying['aad-tool-system-login'] = m.digest(login)
                m.SYSTEM_AUTH.write_bytes(auth); m.SYSTEM_LOGIN.write_bytes(login)
                with patch.object(m, 'pambase_release', return_value=lying):
                    with self.assertRaisesRegex(m.Error, 'both'): self.setup()
                self.assertFalse(m.STATE.exists())

    def test_release_needs_both_baselines(self):
        manifest = m.manifest
        for label, change in [('no omarchy', lambda r: r['baselines'].pop('omarchy')),
                              ('extra', lambda r: r['baselines'].update(stock=r['baselines']['pambase'])),
                              ('no patch', lambda r: r['baselines']['omarchy'].pop('patch')),
                              ('no aad-tool login', lambda r: r.pop('aad-tool-system-login'))]:
            with self.subTest(label):
                data = json.loads(json.dumps(manifest('pambase'))); change(data['20260616-1'])
                with patch.object(m, 'manifest', lambda name: data if name == 'pambase' else manifest(name)):
                    with self.assertRaisesRegex(m.Error, 'Unsupported pambase 20260616-1'):
                        m.pambase_release('20260616-1')

    def test_interrupted_setup_from_aad_tool_layout_recovers_its_exact_bytes(self):
        for name, path in (('system-auth', m.SYSTEM_AUTH), ('system-login', m.SYSTEM_LOGIN)):
            path.write_bytes(self.aad_tool(name)); path.chmod(0o640)
        original = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): self.setup()
        self.assertEqual(m.SYSTEM_LOGIN.read_bytes(), self.pristine('system-login'))
        m.recover()
        for name, path in (('system-auth', m.SYSTEM_AUTH), ('system-login', m.SYSTEM_LOGIN)):
            self.assertEqual((path.read_bytes(), path.stat().st_mode & 0o777), (self.aad_tool(name), 0o640))
        self.assertFalse(m.STATE.exists())

    def test_doctor_reports_aad_tool_rerun_pacnew_and_pambase_change(self):
        self.setup()
        m.SYSTEM_LOGIN.write_bytes(self.aad_tool('system-login'))
        with self.assertRaisesRegex(m.Error, 'Watched file changed.*aad-tool'): m.doctor()
        m.SYSTEM_LOGIN.write_bytes(self.pristine('system-login'))
        m.pacnew(m.SYSTEM_AUTH).write_bytes(self.pristine('system-auth'))
        with self.assertRaisesRegex(m.Error, 'Pending pambase update'): m.doctor()
        m.pacnew(m.SYSTEM_AUTH).unlink()
        m.pacnew(m.SYSTEM_LOGIN).write_bytes(self.pristine('system-login'))
        with self.assertRaisesRegex(m.Error, 'merge it by hand'): m.doctor()
        m.pacnew(m.SYSTEM_LOGIN).unlink()
        self.pambase = '20270101-1'
        with self.assertRaisesRegex(m.Error, 'pambase 20270101-1 is installed'): m.doctor()
        self.pambase = '20260616-1'
        m.doctor()
        good = m.STATE.read_bytes()
        state = json.loads(good); m.system_auth_entry(state)['restore_kind'] = 'omarchy'
        m.STATE.write_text(json.dumps(state))
        with self.assertRaisesRegex(m.Error, 'rollback target is not its omarchy baseline'): m.doctor()
        m.STATE.write_bytes(good)

    def test_repair_repatches_the_recorded_baseline_only(self):
        for kind, own, other in (('pambase', self.pristine('system-auth'), self.omarchy()),
                                 ('omarchy', self.omarchy(), self.pristine('system-auth'))):
            with self.subTest(kind):
                self.start_from(kind)
                golden = m.SYSTEM_AUTH.read_bytes(); state = m.STATE.read_bytes()
                m.SYSTEM_AUTH.write_bytes(own)
                with self.assertRaisesRegex(m.Error, 'drift'): m.doctor()
                m.repair()
                self.assertEqual(m.SYSTEM_AUTH.read_bytes(), golden); self.assertEqual(m.STATE.read_bytes(), state)
                m.SYSTEM_AUTH.write_bytes(other)
                with self.assertRaisesRegex(m.Error, 'unsupported managed drift'): m.repair()
                self.assertEqual(m.SYSTEM_AUTH.read_bytes(), other); self.assertEqual(m.STATE.read_bytes(), state)
                m.SYSTEM_AUTH.write_bytes(golden)
                m.rollback()

    def test_repair_refuses_other_system_pam_drift(self):
        self.setup()
        golden = m.SYSTEM_AUTH.read_bytes(); state = m.STATE.read_bytes()
        for label, path, data in [('aad-tool on ours', m.SYSTEM_AUTH, m.AAD_TOOL_LINES[0] + golden),
                                  ('deleted', m.SYSTEM_AUTH, None),
                                  ('aad-tool system-login', m.SYSTEM_LOGIN, self.aad_tool('system-login')),
                                  ('same-version pacnew', m.pacnew(m.SYSTEM_AUTH), self.pristine('system-auth'))]:
            with self.subTest(label):
                before = path.read_bytes() if path.exists() else None
                if data is None: path.unlink()
                else: path.write_bytes(data)
                with self.assertRaises(m.Error): m.repair()
                self.assertEqual(path.read_bytes() if path.exists() else None, data)
                self.assertEqual(m.STATE.read_bytes(), state)
                if before is None: path.unlink()
                else: path.write_bytes(before)
        m.doctor()

    def start_from(self, baseline):
        """Set up at 20260616-1 from the pambase or Omarchy baseline."""
        self.pambase = '20260616-1'
        m.SYSTEM_AUTH.write_bytes(self.omarchy() if baseline == 'omarchy' else self.pristine('system-auth'))
        m.SYSTEM_LOGIN.write_bytes(self.pristine('system-login'))
        self.setup()

    def test_repair_moves_to_a_supported_pambase_upgrade(self):
        auth, login, patched = self.fake_pambase_release()
        for kind, target in (('pambase', auth), ('omarchy', m.omarchy_tuned(auth))):
            with self.subTest(kind):
                self.start_from(kind)
                self.upgrade_pambase(auth, login)
                with self.assertRaisesRegex(m.Error, 'run repair'): m.doctor()
                m.repair()
                self.assertEqual(self.pam_files(), {m.SYSTEM_AUTH: patched, m.SYSTEM_LOGIN: login,
                                                    m.pacnew(m.SYSTEM_AUTH): None})
                state = m.load_state()
                self.assertEqual(state['pambase'], '20270101-1')
                self.assertEqual(state['watched'][0]['sha256'], m.digest(login))
                entry = m.system_auth_entry(state)
                self.assertEqual((entry['restore_kind'], m.base64.b64decode(entry['restore'])), (kind, target))
                m.doctor(); m.rollback()
                self.assertEqual(self.pam_files(), {m.SYSTEM_AUTH: target, m.SYSTEM_LOGIN: login,
                                                    m.pacnew(m.SYSTEM_AUTH): None})

    def test_repair_accepts_an_upgrade_that_kept_system_auth(self):
        auth, login, patched = self.fake_pambase_release(change=b'')
        for kind in ('pambase', 'omarchy'):
            with self.subTest(kind):
                self.start_from(kind)
                golden = m.SYSTEM_AUTH.read_bytes()
                self.assertEqual(patched, golden)
                self.upgrade_pambase(auth, login, keep_system_auth=True)
                m.repair()
                self.assertEqual(self.pam_files(), {m.SYSTEM_AUTH: golden, m.SYSTEM_LOGIN: login,
                                                    m.pacnew(m.SYSTEM_AUTH): None})
                self.assertEqual(m.load_state()['pambase'], '20270101-1')
                m.doctor(); m.rollback()

    def test_rollback_after_a_supported_pambase_upgrade_installs_the_pacnew(self):
        auth, login, _ = self.fake_pambase_release()
        for kind, target in (('pambase', auth), ('omarchy', m.omarchy_tuned(auth))):
            with self.subTest(kind):
                self.start_from(kind)
                self.upgrade_pambase(auth, login)
                m.rollback()
                self.assertEqual(self.pam_files(), {m.SYSTEM_AUTH: target, m.SYSTEM_LOGIN: login,
                                                    m.pacnew(m.SYSTEM_AUTH): None})
                self.assertFalse(m.STATE.exists())

    def test_upgrade_refuses_when_omarchy_tuning_of_the_pacnew_is_not_supported(self):
        auth, login, _ = self.fake_pambase_release(omarchy='0' * 64)
        self.start_from('omarchy')
        self.upgrade_pambase(auth, login)
        state = m.STATE.read_bytes(); files = self.pam_files()
        for action in (m.repair, m.rollback):
            with self.assertRaisesRegex(m.Error, "Omarchy's lockout tuning.*by hand"): action()
            self.assertEqual(self.pam_files(), files); self.assertEqual(m.STATE.read_bytes(), state)

    def test_upgrade_with_unrecognised_pacnew_or_release_is_refused(self):
        self.setup()
        auth, login, _ = self.fake_pambase_release()
        state = m.STATE.read_bytes()
        for label, version, pacnew, new_login in [
                ('edited pacnew', '20270101-1', auth + b'# local\n', login),
                ('edited system-login', '20270101-1', auth, login + b'# local\n'),
                ('unsupported release', '20990101-1', auth, login)]:
            with self.subTest(label):
                self.upgrade_pambase(pacnew, new_login, version)
                files = self.pam_files()
                for action in (m.repair, m.rollback):
                    with self.assertRaisesRegex(m.Error, f'pambase {version}.*by hand'): action()
                    self.assertEqual(self.pam_files(), files); self.assertEqual(m.STATE.read_bytes(), state)

    def test_upgrade_without_pacnew_needs_matching_stored_pristine(self):
        self.setup()
        auth, login, _ = self.fake_pambase_release()
        self.upgrade_pambase(auth, login, keep_system_auth=True)
        state = m.STATE.read_bytes(); files = self.pam_files()
        for action in (m.repair, m.rollback):
            with self.assertRaisesRegex(m.Error, 'No .*pacnew.*stored .*rollback target'): action()
            self.assertEqual(self.pam_files(), files); self.assertEqual(m.STATE.read_bytes(), state)

    def test_rollback_with_unchanged_pambase_needs_no_supported_list(self):
        self.setup()
        with patch.object(m, 'pambase_release', side_effect=AssertionError('list consulted')):
            m.rollback()
        self.assertEqual(m.SYSTEM_AUTH.read_bytes(), self.pristine('system-auth'))

    def test_rollback_refuses_a_changed_watched_file_or_stray_pacnew(self):
        self.setup()
        state = m.STATE.read_bytes(); golden = m.SYSTEM_AUTH.read_bytes()
        for path, data, message in [(m.SYSTEM_LOGIN, self.aad_tool('system-login'), 'aad-tool'),
                                    (m.pacnew(m.SYSTEM_AUTH), self.pristine('system-auth'), 'unchanged')]:
            with self.subTest(str(path)):
                before = path.read_bytes() if path.exists() else None
                path.write_bytes(data)
                with self.assertRaisesRegex(m.Error, message): m.rollback()
                self.assertEqual(m.SYSTEM_AUTH.read_bytes(), golden); self.assertEqual(m.STATE.read_bytes(), state)
                if before is None: path.unlink()
                else: path.write_bytes(before)

    def test_unsafe_pam_metadata_is_refused(self):
        uid, gid = m.owner()
        for mode, ids, safe in [(0o644, (uid, gid), True), (0o600, (uid, gid), True),
                                (0o666, (uid, gid), False), (0o664, (uid, gid), False),
                                (0o4644, (uid, gid), False), (0o644, (uid + 1, gid), False),
                                (0o644, (uid, gid + 1), False)]:
            with self.subTest(oct(mode), ids=ids):
                self.assertEqual(m.safe_pam_metadata(mode, *ids), safe)
        directory = self.base / 'not-a-file'; directory.mkdir(); directory.chmod(0o755)
        with self.assertRaisesRegex(m.Error, 'regular file'): m.require_safe_pam_file(directory)
        real = os.getxattr
        def acl(path, attribute, **kwargs):
            if Path(path) == m.SYSTEM_LOGIN and attribute == 'system.posix_acl_access': return b'acl'
            return real(path, attribute, **kwargs)
        with patch.object(m.os, 'getxattr', acl):
            with self.assertRaisesRegex(m.Error, 'Extended ACL'): m.require_safe_pam_file(m.SYSTEM_LOGIN)
            with self.assertRaisesRegex(m.Error, 'Extended ACL'): self.setup()
        self.assertFalse(m.STATE.exists())

    def test_setup_refuses_writable_system_pam_files(self):
        for path in (m.SYSTEM_AUTH, m.SYSTEM_LOGIN):
            for mode in (0o666, 0o664):
                with self.subTest(path.name, mode=oct(mode)):
                    path.chmod(mode)
                    with self.assertRaisesRegex(m.Error, f'Unsafe ownership/mode on {path}'): self.setup()
                    self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
                    self.assertEqual(path.stat().st_mode & 0o7777, mode)
                    path.chmod(0o644)

    def test_writable_system_login_is_reported_and_blocks_repair_and_rollback(self):
        self.setup()
        state = m.STATE.read_bytes(); files = self.pam_files()
        m.SYSTEM_LOGIN.chmod(0o666)
        with self.assertRaisesRegex(m.Error, f'Unsafe ownership/mode on {m.SYSTEM_LOGIN}'): m.doctor()
        for action in (m.repair, m.rollback):
            with self.assertRaisesRegex(m.Error, f'Unsafe ownership/mode on {m.SYSTEM_LOGIN}'): action()
            self.assertEqual(self.pam_files(), files); self.assertEqual(m.STATE.read_bytes(), state)
            self.assertEqual(m.SYSTEM_LOGIN.stat().st_mode & 0o777, 0o666)
        m.SYSTEM_LOGIN.chmod(0o644)
        m.doctor()

    def acl_on(self, *targets):
        """getxattr reporting an ACL for each (path, attribute) in targets."""
        real = os.getxattr
        def getxattr(path, attribute, **kwargs):
            if (Path(path), attribute) in targets: return b'acl'
            return real(path, attribute, **kwargs)
        return patch.object(m.os, 'getxattr', getxattr)

    def test_pam_parent_directories_must_be_root_only(self):
        # A non-root user who can write /etc/pam.d can replace a root-owned 0644 file.
        def guard(path):
            if path == m.SYSTEM_LOGIN: raise m.Error('Unsafe state ancestor ownership/mode')
        with patch.object(m, 'check_state_ancestors', guard):
            with self.assertRaisesRegex(m.Error, f'parent directory of {m.SYSTEM_LOGIN}: each must be owned by root'):
                self.setup()
        self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
        pam_d, etc = m.SYSTEM_AUTH.parent, m.SYSTEM_AUTH.parent.parent
        access, default = 'system.posix_acl_access', 'system.posix_acl_default'
        # A default ACL on /etc/pam.d is inherited by the file setup writes there.
        for target in ((pam_d, default), (pam_d, access), (etc, access)):
            with self.subTest(target), self.acl_on(target):
                with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {target[0]}$'): self.setup()
                self.assertFalse(m.STATE.exists())
        with self.acl_on((etc, default)):
            # A replacement in /etc/pam.d inherits only that directory's default ACL ...
            m.require_safe_pam_file(m.SYSTEM_AUTH)
            # ... but setup also creates /etc/sddm.conf.d, which would inherit /etc's.
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {etc}$'): self.setup()
            self.assertFalse(m.STATE.exists())
        self.setup()
        with patch.object(m, 'check_state_ancestors', guard):
            with self.assertRaisesRegex(m.Error, 'parent directory of'): m.doctor()
        with self.acl_on((pam_d, default)):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {pam_d}'): m.doctor()

    def test_root_changes_refuse_to_drop_an_acl(self):
        # The journal records bytes, owner and mode: an ACL on a file a root command would
        # replace or delete could never be put back.
        self.setup()
        state = m.STATE.read_bytes(); managed = m.SYSTEM_AUTH.read_bytes()
        baseline = m.base64.b64decode(m.system_auth_entry(json.loads(state))['restore'])
        for action, target, data in ((m.rollback, m.PAM, managed), (m.rollback, m.SYSTEM_AUTH, managed),
                                     (m.repair, m.SYSTEM_AUTH, baseline)):
            with self.subTest(action.__name__, target=target.name), \
                    self.acl_on((target, 'system.posix_acl_access')):
                m.SYSTEM_AUTH.write_bytes(data)
                with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {target}$'): action()
                self.assertEqual(m.SYSTEM_AUTH.read_bytes(), data); self.assertTrue(m.PAM.exists())
                self.assertEqual(m.STATE.read_bytes(), state); self.assertFalse(m.journal_path().exists())
        m.SYSTEM_AUTH.write_bytes(managed)
        m.doctor()

    def test_repair_accepts_system_auth_restored_with_its_baseline_metadata(self):
        m.SYSTEM_AUTH.chmod(0o640)
        baseline = m.SYSTEM_AUTH.read_bytes()
        self.setup()
        managed = m.SYSTEM_AUTH.read_bytes()
        self.assertEqual(m.SYSTEM_AUTH.stat().st_mode & 0o777, 0o644)
        m.SYSTEM_AUTH.write_bytes(baseline); m.SYSTEM_AUTH.chmod(0o640)
        with self.assertRaisesRegex(m.Error, 'Managed file drift'): m.doctor()
        m.repair()
        self.assertEqual(m.SYSTEM_AUTH.read_bytes(), managed)
        self.assertEqual(m.SYSTEM_AUTH.stat().st_mode & 0o777, 0o644)
        m.doctor()
        # Only the baseline may carry the baseline's mode, and only that mode.
        for data, mode in ((baseline, 0o600), (managed, 0o640)):
            with self.subTest(oct(mode), baseline=data == baseline):
                m.SYSTEM_AUTH.write_bytes(data); m.SYSTEM_AUTH.chmod(mode)
                with self.assertRaisesRegex(m.Error, 'permission drift'): m.repair()
                self.assertEqual(m.SYSTEM_AUTH.stat().st_mode & 0o777, mode)
        m.SYSTEM_AUTH.write_bytes(managed); m.SYSTEM_AUTH.chmod(0o644)
        m.doctor()

    def test_upgrade_refuses_a_writable_pacnew_or_system_login(self):
        auth, login, _ = self.fake_pambase_release()
        self.start_from('pambase')
        self.upgrade_pambase(auth, login)
        state = m.STATE.read_bytes()
        for path in (m.pacnew(m.SYSTEM_AUTH), m.SYSTEM_LOGIN):
            with self.subTest(path.name):
                path.chmod(0o666); files = self.pam_files()
                for action in (m.repair, m.rollback):
                    with self.assertRaisesRegex(m.Error, f'Unsafe ownership/mode on {path}'): action()
                    self.assertEqual(self.pam_files(), files); self.assertEqual(m.STATE.read_bytes(), state)
                path.chmod(0o644)
        m.repair()
        self.assertEqual(m.SYSTEM_AUTH.stat().st_mode & 0o777, 0o644)

    def test_doctor_checks_system_login_against_the_release_not_only_the_watch(self):
        self.setup()
        m.SYSTEM_LOGIN.write_bytes(self.aad_tool('system-login'))
        state = json.loads(m.STATE.read_text())
        state['watched'][0]['sha256'] = m.digest(self.aad_tool('system-login'))
        m.STATE.write_text(json.dumps(state))
        with self.assertRaisesRegex(m.Error, 'Stored .*system-login hash is not pristine pambase'): m.doctor()

    def sddm_fixture(self, kind, name):
        return (PAM_STACK / 'sddm' / kind / name).read_bytes()

    def replace_sddm(self, omarchy=None):
        """What pacman does when the native package replaces Omarchy's sddm: Omarchy's
        edited files become .pacsave and the package's defaults are installed."""
        for path in m.SDDM_PAMS:
            path.write_bytes(self.sddm_fixture('native', path.name)); path.chmod(0o644)
            saved = m.pacsave(path)
            saved.write_bytes((omarchy or {}).get(path.name, self.sddm_fixture('omarchy', path.name)))
            saved.chmod(0o644)

    def test_omarchy_sddm_pam_edits_match_its_installer(self):
        for name in ('sddm', 'sddm-autologin'):
            with self.subTest(name):
                path = m.SDDM_PAMS[0].with_name(name)
                native, omarchy = self.sddm_fixture('native', name), self.sddm_fixture('omarchy', name)
                self.assertNotEqual(native, omarchy)
                self.assertEqual(m.omarchy_sddm_pam(path, native), omarchy)
                self.assertEqual(m.omarchy_sddm_pam(path, omarchy), omarchy)

    def test_setup_restores_omarchy_sddm_pam_edits_and_rollback_keeps_them(self):
        self.replace_sddm()
        for path in m.SDDM_PAMS: path.chmod(0o640)
        self.setup()
        for path in m.SDDM_PAMS:
            self.assertEqual(path.read_bytes(), self.sddm_fixture('omarchy', path.name))
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertFalse(m.pacsave(path).exists())
        state = m.load_state()
        self.assertFalse({str(path) for path in m.SDDM_PAMS} & {entry['path'] for entry in state['files']})
        m.doctor(); m.rollback()
        for path in m.SDDM_PAMS:
            self.assertEqual(path.read_bytes(), self.sddm_fixture('omarchy', path.name))

    def test_setup_refuses_an_sddm_pacsave_that_is_not_omarchys_edit(self):
        for name in ('sddm', 'sddm-autologin'):
            with self.subTest(name):
                self.replace_sddm({name: self.sddm_fixture('omarchy', name) + b'# local\n'})
                files = {path: path.read_bytes() for path in (*m.SDDM_PAMS, *map(m.pacsave, m.SDDM_PAMS))}
                with self.assertRaisesRegex(m.Error, f"pacsave is not Omarchy's edit of .*{name}; review"):
                    self.setup()
                self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
                self.assertEqual({path: path.read_bytes() for path in files}, files)
        self.replace_sddm(); m.SDDM_PAMS[1].chmod(0o666)
        with self.assertRaisesRegex(m.Error, 'Unsafe ownership/mode'): self.setup()
        self.assertFalse(m.STATE.exists())

    def test_native_sddm_pam_hashes_match_the_package_fixtures(self):
        for path in m.SDDM_PAMS:
            self.assertEqual(m.digest(self.sddm_fixture('native', path.name)), m.NATIVE_SDDM_PAMS[path.name])

    def test_setup_keeps_an_administrators_edit_to_the_installed_sddm_pam(self):
        # Omarchy's edit drops the whole faillock preauth line, so an edited preauth line
        # still produces the .pacsave: only the package's own bytes prove nothing is lost.
        self.replace_sddm()
        path = m.SDDM_PAMS[1]
        edited = path.read_bytes().replace(b'pam_faillock.so preauth', b'pam_faillock.so preauth deny=3')
        self.assertNotEqual(edited, path.read_bytes())
        self.assertEqual(m.omarchy_sddm_pam(path, edited), m.pacsave(path).read_bytes())
        path.write_bytes(edited)
        with self.assertRaisesRegex(m.Error, f'{path} is not sddm-himmelblau {m.NATIVE_VERSION} as installed'):
            self.setup()
        self.assertEqual(path.read_bytes(), edited); self.assertTrue(m.pacsave(path).exists())
        self.assertFalse(m.STATE.exists())

    def test_setup_refuses_an_sddm_pacsave_with_an_acl(self):
        self.replace_sddm()
        saved = m.pacsave(m.SDDM_PAMS[0])
        with self.acl_on((saved, 'system.posix_acl_access')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {saved}$'): self.setup()
        self.assertTrue(saved.exists()); self.assertFalse(m.STATE.exists())

    def test_interrupted_sddm_pam_restore_recovers_the_package_files(self):
        self.replace_sddm()
        before = {path: path.read_bytes() for path in (*m.SDDM_PAMS, *map(m.pacsave, m.SDDM_PAMS))}
        original = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): self.setup()
        m.recover()
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertFalse(m.STATE.exists())

    def test_setup_refuses_a_destination_that_would_inherit_a_default_acl(self):
        # The recovery-account file would inherit it; rollback could then never delete it.
        parent = m.RECOVERY_ACCOUNT.parent
        with self.acl_on((parent, 'system.posix_acl_default')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {parent}$'): self.setup()
        self.assertFalse(m.STATE.exists()); self.assertFalse(m.RECOVERY_ACCOUNT.exists())
        self.assertFalse(m.journal_path().exists())

    def test_written_inodes_are_checked_for_an_inherited_acl(self):
        # Backstop for a default ACL added after preflight: the temporary inode and any new
        # directory are checked before they become visible, and the change is undone.
        target = self.base / 'etc/acl-check'
        real = os.getxattr
        def getxattr(path, attribute, **kwargs):
            if Path(path).name.startswith('.himmelblau-') or Path(path) == self.base / 'new-directory':
                return b'acl'
            return real(path, attribute, **kwargs)
        with patch.object(m.os, 'getxattr', getxattr):
            with self.assertRaisesRegex(m.Error, 'Extended ACL refused'): m.atomic(target, b'data', 0o644)
            with self.assertRaisesRegex(m.Error, 'Extended ACL refused'):
                m.make_directory(self.base / 'new-directory', 0o755)
        self.assertFalse(target.exists())
        self.assertEqual([path.name for path in target.parent.iterdir() if path.name.startswith('.himmelblau-')], [])

    def test_recover_refuses_to_drop_an_acl(self):
        original = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): self.setup()
        pam = m.PAM.read_bytes()
        for target in (m.PAM, m.SYSTEM_AUTH):
            with self.subTest(target.name), self.acl_on((target, 'system.posix_acl_access')):
                with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {target}$'): m.recover()
                self.assertEqual(m.PAM.read_bytes(), pam); self.assertTrue(m.journal_path().exists())
        m.recover()
        self.assertFalse(m.PAM.exists()); self.assertFalse(m.journal_path().exists())

    def test_root_changes_refuse_a_state_directory_with_a_default_acl(self):
        # state.json and the journal would inherit it.
        self.setup()
        state = m.STATE.read_bytes()
        with self.acl_on((m.STATE.parent, 'system.posix_acl_default')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {m.STATE.parent}$'): m.policy(True)
        self.assertEqual(m.STATE.read_bytes(), state); self.assertFalse(m.journal_path().exists())

    def test_recover_refuses_to_recreate_a_file_that_would_inherit_an_acl(self):
        original = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): self.setup()
        managed = m.SYSTEM_AUTH.read_bytes()
        with self.acl_on((m.SYSTEM_AUTH.parent, 'system.posix_acl_default')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {m.SYSTEM_AUTH.parent}$'): m.recover()
        self.assertEqual(m.SYSTEM_AUTH.read_bytes(), managed); self.assertTrue(m.journal_path().exists())
        m.recover()
        self.assertNotEqual(m.SYSTEM_AUTH.read_bytes(), managed)

    def test_root_changes_refuse_to_drop_an_acl_on_the_state_file(self):
        self.setup()
        state = m.STATE.read_bytes()
        for action in (m.rollback, lambda: m.policy(True)):
            with self.subTest(action), self.acl_on((m.STATE, 'system.posix_acl_access')):
                with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {m.STATE}$'): action()
                self.assertEqual(m.STATE.read_bytes(), state); self.assertTrue(m.PAM.exists())

    def changed_before_transaction(self, path, data):
        """transact() that first changes path, as an administrator or package might
        between a command's checks and its transaction."""
        original = m.transact
        def transact(*args, **kwargs):
            path.write_bytes(data)
            return original(*args, **kwargs)
        return patch.object(m, 'transact', transact)

    def test_setup_refuses_files_changed_after_its_checks(self):
        self.replace_sddm()
        sddm, saved = m.SDDM_PAMS[1], m.pacsave(m.SDDM_PAMS[1])
        for path in (sddm, saved, m.SYSTEM_AUTH, m.SYSTEM_LOGIN, m.CONFIG):
            with self.subTest(path.name):
                before = path.read_bytes()
                edited = before + b'# edited during setup\n'
                with self.changed_before_transaction(path, edited):
                    with self.assertRaisesRegex(m.Error, f'{path} changed while setup was checking it'):
                        self.setup()
                self.assertEqual(path.read_bytes(), edited)
                self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
                self.assertTrue(saved.exists())
                path.write_bytes(before)
        self.setup()

    def test_setup_refuses_changes_made_before_it_reads_a_file(self):
        # Setup computes himmelblau.conf from what it reads after the snapshot; an edit in
        # between must not become the accepted before-image. Files that appear (.pacnew,
        # .pacsave) and permission-only changes count as changes too.
        self.replace_sddm()
        sddm = m.SDDM_PAMS[0]
        for label, change, path in (
                ('config', lambda: m.CONFIG.write_bytes(m.CONFIG.read_bytes() + b'# edited\n'), m.CONFIG),
                ('new pacnew', lambda: m.pacnew(m.SYSTEM_LOGIN).write_bytes(b'new'), m.pacnew(m.SYSTEM_LOGIN)),
                ('sddm mode', lambda: sddm.chmod(0o600), sddm)):
            with self.subTest(label):
                original = m.administrator
                def administrator(user):
                    change()
                    return original(user)
                with patch.object(m, 'administrator', administrator):
                    with self.assertRaisesRegex(m.Error, f'{path} changed while setup was checking it'):
                        self.setup()
                self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
                self.replace_sddm()
                m.pacnew(m.SYSTEM_LOGIN).unlink(missing_ok=True)
                m.CONFIG.write_bytes(self.before[m.CONFIG])
        m.pacsave(m.SDDM_PAMS[0]).unlink(); m.pacsave(m.SDDM_PAMS[1]).unlink()
        saved = m.pacsave(sddm)
        with self.changed_before_transaction(saved, b'appeared\n'):
            with self.assertRaisesRegex(m.Error, f'{saved} changed while setup was checking it'): self.setup()
        saved.unlink()
        self.setup()

    def test_rollback_refuses_an_undo_it_could_not_complete(self):
        # Deleting the recovery-account file is undone by recreating it, which a default ACL
        # on its directory would be inherited by: refuse before the journal is written.
        self.setup()
        state = m.STATE.read_bytes()
        parent = m.RECOVERY_ACCOUNT.parent
        with self.acl_on((parent, 'system.posix_acl_default')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {parent}$'): m.rollback()
        self.assertEqual(m.STATE.read_bytes(), state); self.assertTrue(m.RECOVERY_ACCOUNT.exists())
        self.assertFalse(m.journal_path().exists())
        # A directory record holds owner and mode only: never remove one with an ACL.
        directory = m.COPY_THEME
        with self.acl_on((directory, 'system.posix_acl_access')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {directory}$'): m.rollback()
        self.assertTrue(directory.is_dir()); self.assertEqual(m.STATE.read_bytes(), state)
        m.rollback()

    def test_setup_refuses_a_theme_copy_that_appears_after_its_checks(self):
        original = m.transact
        def transact(*args, **kwargs):
            m.COPY_THEME.mkdir(); (m.COPY_THEME / 'logo.png').write_bytes(b'planted')
            return original(*args, **kwargs)
        with patch.object(m, 'transact', transact):
            with self.assertRaisesRegex(m.Error, f'{m.COPY_THEME / "logo.png"} changed while setup'): self.setup()
        self.assertEqual((m.COPY_THEME / 'logo.png').read_bytes(), b'planted'); self.assertFalse(m.STATE.exists())

    def test_rollback_and_recover_refuse_to_recreate_a_directory_under_a_default_acl(self):
        self.setup()
        state = m.STATE.read_bytes()
        themes = m.COPY_THEME.parent
        with self.acl_on((themes, 'system.posix_acl_default')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {themes}$'): m.rollback()
        self.assertEqual(m.STATE.read_bytes(), state); self.assertTrue(m.COPY_THEME.is_dir())
        # Interrupted after removing the theme copy: recover would recreate the directory.
        original = m.unlink_durable
        def crash(path, missing_ok=False):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, missing_ok=missing_ok)
        with patch.object(m, 'unlink_durable', crash):
            with self.assertRaises(KeyboardInterrupt): m.rollback()
        self.assertFalse(m.COPY_THEME.exists())
        with self.acl_on((themes, 'system.posix_acl_default')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {themes}$'): m.recover()
        self.assertFalse(m.COPY_THEME.exists()); self.assertTrue(m.journal_path().exists())
        m.recover()
        self.assertTrue(m.COPY_THEME.is_dir()); m.doctor()

    def test_recover_refuses_to_change_a_directory_with_an_acl(self):
        original = m.atomic
        def crash(path, data, mode, *owner):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode, *owner)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): self.setup()
        with self.acl_on((m.COPY_THEME, 'system.posix_acl_access')):
            with self.assertRaisesRegex(m.Error, f'Extended ACL refused .*: {m.COPY_THEME}$'): m.recover()
        self.assertTrue(m.COPY_THEME.is_dir()); self.assertTrue(m.journal_path().exists())
        m.recover()
        self.assertFalse(m.COPY_THEME.exists())

    def test_repair_and_rollback_refuse_files_changed_after_their_checks(self):
        self.setup()
        state = m.STATE.read_bytes()
        before = m.CONFIG.read_bytes()
        with self.changed_before_transaction(m.CONFIG, before + b'# edited\n'):
            with self.assertRaisesRegex(m.Error, f'{m.CONFIG} changed while policy was checking it'):
                m.policy(True)
        self.assertEqual(m.STATE.read_bytes(), state); m.CONFIG.write_bytes(before)
        m.PAM.unlink()  # Something for repair to do.
        for action, name, path in ((m.repair, 'repair', m.SYSTEM_LOGIN), (m.repair, 'repair', m.CONFIG),
                                   (m.rollback, 'rollback', m.SYSTEM_LOGIN), (m.rollback, 'rollback', m.CONFIG)):
            with self.subTest(name, path=path.name):
                before = path.read_bytes()
                with self.changed_before_transaction(path, before + b'# edited\n'):
                    with self.assertRaisesRegex(m.Error, f'{path} changed while {name} was checking it'):
                        action()
                self.assertEqual(m.STATE.read_bytes(), state); self.assertFalse(m.PAM.exists())
                path.write_bytes(before)
        # A permission-only change counts too.
        original = m.transact
        def chmod_first(*args, **kwargs):
            m.CONFIG.chmod(0o600)
            return original(*args, **kwargs)
        for action, name in ((m.repair, 'repair'), (m.rollback, 'rollback')):
            with self.subTest(f'{name} mode'), patch.object(m, 'transact', chmod_first):
                with self.assertRaisesRegex(m.Error, f'{m.CONFIG} changed while {name} was checking it'): action()
                self.assertEqual(m.STATE.read_bytes(), state)
            m.CONFIG.chmod(0o644)
        m.repair(); m.doctor()

    def test_doctor_reports_an_sddm_pacsave_after_setup(self):
        self.setup()
        for path in m.SDDM_PAMS:
            with self.subTest(path.name):
                m.pacsave(path).write_bytes(self.sddm_fixture('omarchy', path.name))
                with self.assertRaisesRegex(m.Error, f'{m.pacsave(path)} exists: replacing sddm'): m.doctor()
                m.pacsave(path).unlink()
        m.doctor()

    def test_setup_says_when_the_account_has_not_signed_in(self):
        with patch.object(m.pwd, 'getpwuid', side_effect=KeyError(123456)):
            with self.assertRaisesRegex(m.Error, '^alice@example.com has not signed in on this machine yet: run '
                                        '"aad-tool auth-test --name alice@example.com"'):
                self.setup()
        with patch.object(m.pwd, 'getpwnam', side_effect=KeyError('alice@example.com')):
            with self.assertRaisesRegex(m.Error, 'does not resolve through NSS; check that himmelblaud'):
                self.setup()
        self.assertFalse(m.STATE.exists())

    def test_state_requires_pambase_fields_and_a_pambase_system_auth_entry(self):
        self.setup()
        good = json.loads(m.STATE.read_text())
        def broken(change):
            state = json.loads(json.dumps(good)); change(state); return state
        auth = next(i for i, entry in enumerate(good['files']) if entry['path'] == str(m.SYSTEM_AUTH))
        for label, state in [
                ('no pambase', broken(lambda s: s.pop('pambase'))),
                ('no watched', broken(lambda s: s.pop('watched'))),
                ('bad watched', broken(lambda s: s['watched'][0].pop('sha256'))),
                ('empty watched', broken(lambda s: s.update(watched=[]))),
                ('two watched', broken(lambda s: s['watched'].append(dict(s['watched'][0])))),
                ('other watched path', broken(lambda s: s['watched'][0].update(path=str(m.SYSTEM_AUTH)))),
                ('other watched release', broken(lambda s: s['watched'][0].update(pambase='20270101-1'))),
                ('writable restore', broken(lambda s: s['files'][auth].update(restore_mode=0o666))),
                ('restore owned by a user', broken(lambda s: s['files'][auth].update(
                    restore_uid=s['files'][auth]['restore_uid'] + 1))),
                ('restore group', broken(lambda s: s['files'][auth].update(
                    restore_gid=s['files'][auth]['restore_gid'] + 1))),
                ('original kind', broken(lambda s: s['files'][auth].update(restore_kind='original'))),
                ('unknown kind', broken(lambda s: s['files'][auth].update(restore_kind='stock'))),
                ('omarchy kind elsewhere', broken(lambda s: s['files'][0 if auth else 1].update(restore_kind='omarchy'))),
                ('no restore', broken(lambda s: s['files'][auth].update(
                    restore=None, restore_mode=None, restore_uid=None, restore_gid=None))),
                ('pambase kind elsewhere', broken(lambda s: s['files'][0 if auth else 1].update(restore_kind='pambase'))),
                ('no system-auth', broken(lambda s: s['files'].pop(auth))),
                ('managed twice', broken(lambda s: s['files'].append(dict(s['files'][1 if auth == 0 else 0])))),
                ('watched and managed', broken(lambda s: s['files'].append(dict(
                    s['files'][1 if auth == 0 else 0], path=str(m.SYSTEM_LOGIN))))),
                ('watched and managed, spelled differently', broken(lambda s: s['files'].append(dict(
                    s['files'][1 if auth == 0 else 0], path=str(m.SYSTEM_LOGIN.parent) + '//system-login')))),
                ('managed system-auth mode', broken(lambda s: s['files'][auth].update(mode=0o640))),
                *((f'noncanonical {spelling}', broken(lambda s, spelling=spelling: s['files'][0 if auth else 1].update(
                    path=spelling.format(s['files'][0 if auth else 1]['path']))))
                  for spelling in ('/{}', '{}/', '{}/.', '/tmp/../{}', 'relative/path')),
                *((f'noncanonical watched {spelling}', broken(lambda s, spelling=spelling: s['watched'][0].update(
                    path=spelling.format(s['watched'][0]['path']))))
                  for spelling in ('/{}', '{}/.', str(m.SYSTEM_LOGIN.parent) + '/../pam.d/system-login'))]:
            with self.subTest(label):
                with self.assertRaisesRegex(m.Error, 'Invalid state'): m.check_state(state)
        m.check_state(good)


if __name__ == '__main__': unittest.main()
