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
        }.items():
            self.paths[name] = self.base / path
        self.paths['ASSETS'] = ROOT / 'assets'
        self.paths['CONFIG_LAYERS'] = (self.base / 'vendor/config.conf', self.base / 'run/config.conf')
        self.mock_paths = patch.multiple(m, **self.paths)
        self.mock_paths.start(); self.addCleanup(self.mock_paths.stop)
        # The host sandbox maps / and /tmp ownership to nobody; production guards
        # remain enabled in the CLI and are separate from isolated temporary roots.
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
                return SimpleNamespace(stdout='sddm-himmelblau 0.21.0-3\n')
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
        m.NATIVE_PAYLOAD.write_text(json.dumps({'package':'sddm-himmelblau', 'version':'0.21.0-3',
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
        with patch.object(m, '__file__', installed), patch.object(m, 'user_status') as status:
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
            self.assertEqual(argv, ['/usr/bin/pacman', '-Q', 'sddm-himmelblau'])
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

    def test_legacy_identity_and_numeric_admin_migrate_only_with_explicit_repair(self):
        original_config = m.CONFIG.read_bytes()
        with patch.object(m, 'validate_sudo_rule'): self.setup(admin=True)
        state = m.load_state(); state.pop('identity')
        state['files'] = [entry for entry in state['files'] if Path(entry['path']) != m.RECOVERY_ACCOUNT]
        m.RECOVERY_ACCOUNT.unlink()
        legacy = b'# Explicit single-identity admin opt-in; NSS UID, password/PIN required.\n#123456 ALL=(ALL:ALL) ALL\n'
        m.SUDOERS.chmod(0o640); m.SUDOERS.write_bytes(legacy); m.SUDOERS.chmod(0o440)
        for entry in state['files']:
            if Path(entry['path']) == m.SUDOERS: entry['after'] = m.digest(legacy)
            if Path(entry['path']) == m.PIN_PAM: entry['after'] = m.digest(m.LEGACY_PIN_PAM_CONTENT)
        m.PIN_PAM.write_bytes(m.LEGACY_PIN_PAM_CONTENT); m.save_state(state)
        with self.assertRaises(m.Error): m.doctor()
        self.assertEqual(m.SUDOERS.read_bytes(), legacy)
        with patch.object(m, 'validate_sudo_rule'): m.repair()
        self.assertEqual(m.RECOVERY_ACCOUNT.read_bytes(), b'alice@example.com\nalice\n123456\n')
        self.assertIn(b'"alice" ALL=', m.SUDOERS.read_bytes())
        self.assertIn(b'account [success=ok default=die] pam_himmelblau.so', m.PIN_PAM.read_bytes())
        m.doctor(); m.rollback()
        self.assertFalse(m.RECOVERY_ACCOUNT.exists()); self.assertFalse(m.SUDOERS.exists())
        self.assertEqual(m.CONFIG.read_bytes(), original_config)

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
        def crash(path, data, mode):
            nonlocal interrupted
            if path == m.CONFIG and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return original(path, data, mode)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): self.setup()
        state = m.load_state()
        self.assertEqual(state['transaction']['kind'], 'setup')
        self.assertIn('applied', state['transaction']['progress'].values())
        self.assertIn('pending', state['transaction']['progress'].values())
        with self.assertRaises(m.Error): m.doctor()
        m.recover()
        self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
        self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])

    def test_policy_interruption_recovers_with_unchanged_or_committed_state(self):
        self.setup()
        old_config = m.CONFIG.read_bytes(); old_state = m.STATE.read_bytes()
        original = m.atomic
        def crash(path, data, mode):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode)
        with patch.object(m, 'atomic', crash):
            with self.assertRaises(KeyboardInterrupt): m.policy(True)
        self.assertIn(b'apply_policy = true', m.CONFIG.read_bytes())
        m.recover()
        self.assertEqual(m.CONFIG.read_bytes(), old_config); self.assertEqual(m.STATE.read_bytes(), old_state)
        journal = m.STATE.parent / 'policy-backup.json'; unlink = Path.unlink
        def crash_unlink(path, *args, **kwargs):
            if path == journal: raise KeyboardInterrupt
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', crash_unlink):
            with self.assertRaises(KeyboardInterrupt): m.policy(True)
        m.recover()
        self.assertEqual(m.CONFIG.read_bytes(), old_config); self.assertEqual(m.STATE.read_bytes(), old_state)

    def test_recovery_refuses_drift_before_any_restoration(self):
        self.setup(); old_state = m.STATE.read_bytes(); original = m.atomic
        def crash(path, data, mode):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode)
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
                    def crash(path, data, mode):
                        nonlocal crashed
                        if path == target and not crashed:
                            crashed = True
                            if after_write: original(path, data, mode)
                            raise KeyboardInterrupt
                        return original(path, data, mode)
                    with patch.object(m, 'atomic', crash):
                        with self.assertRaises(KeyboardInterrupt): self.setup()
                    m.recover()
                    self.assertFalse(m.STATE.exists()); self.assertFalse(m.PAM.exists())
                    self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])

    def test_repair_and_rollback_crashes_can_resume_from_exact_before_after(self):
        self.setup(); original = m.atomic; old_state = m.STATE.read_bytes()
        m.PAM.unlink()
        def crash_repair(path, data, mode):
            if path == m.STATE: raise KeyboardInterrupt
            return original(path, data, mode)
        with patch.object(m, 'atomic', crash_repair):
            with self.assertRaises(KeyboardInterrupt): m.repair()
        self.assertTrue(m.PAM.exists())
        m.recover()
        self.assertFalse(m.PAM.exists()); self.assertEqual(m.STATE.read_bytes(), old_state)
        m.repair()
        def crash_rollback(path, data, mode):
            if path == m.STATE:
                state = json.loads(data)
                progress = state.get('transaction', {})
                if progress.get('kind') == 'rollback' and 'restored' in progress['progress'].values():
                    raise KeyboardInterrupt
            return original(path, data, mode)
        with patch.object(m, 'atomic', crash_rollback):
            with self.assertRaises(KeyboardInterrupt): m.rollback()
        m.recover()
        self.assertFalse(m.STATE.exists()); self.assertEqual(m.CONFIG.read_bytes(), self.before[m.CONFIG])

    def test_atomic_replace_and_delete_fsync_parent_directory(self):
        target = self.base / 'durable'; calls=[]
        original = m.fsync_directory
        with patch.object(m, 'fsync_directory', side_effect=lambda path: calls.append(path) or original(path)):
            m.atomic(target, b'fixed', 0o600)
            m.unlink_durable(target)
        self.assertEqual(calls, [self.base, self.base])

    def fixed_plugin_fixture(self, legacy=False):
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
        listing = [{'id':'omarchy.lock','firstParty':True,'enabled':not legacy,'clonedFrom':''},
                   {'id':'9spokes.himmelblau-lock','firstParty':False,'enabled':False,'clonedFrom':'omarchy.lock'}]
        if legacy:
            user, clone, statefile, hook = m.user_paths()
            shutil.copytree(m.LOCK.parent, clone)
            for name, asset in [('Service.qml','tests/fixtures/lock-0.5.0.patch'), ('LockView.qml','assets/lock-view.patch')]:
                subprocess.run(['/usr/bin/patch','--batch','--fuzz=0','-p1','-d',str(clone)],
                               input=(ROOT / asset).read_bytes(),check=True,capture_output=True)
            hook_data = b'#!/bin/sh\n/usr/bin/omarchy-himmelblau status\n'
            m.atomic(hook, hook_data, 0o755)
            upstream = m.upstream_hashes(); upstream['Service.qml'] = m.digest(read_bytes := (clone / 'Service.qml').read_bytes())
            state = {'phase':'ready','version':'0.4.0','clone_hash':m.digest(read_bytes), 'clone_files':m.clone_hashes(clone),
                     'upstream_files':upstream,'hook_hash':m.digest(hook_data)}
            m.atomic(statefile, (json.dumps(state)+'\n').encode(), 0o600)
            listing.append({'id':'alice.lock','firstParty':False,'enabled':True,'clonedFrom':'omarchy.lock'})
        return directory, listing

    def test_fixed_plugin_status_and_prerequisites_are_fail_closed(self):
        directory, listing = self.fixed_plugin_fixture()
        listing[0]['enabled'] = False; listing[1]['enabled'] = True
        with patch.object(m, 'lock_prerequisites'), patch.object(m, 'native_plugins', return_value=listing):
            m.plugin_status(); m.locker_preflight(); m.user_status()
            listing.append({'id':'other.lock','firstParty':False,'enabled':True,'clonedFrom':'omarchy.lock'})
            for action in (m.plugin_status, m.locker_preflight, m.user_status):
                with self.assertRaises(m.Error): action()
            listing.pop()
            service = directory / 'plugin/Service.qml'; service.write_bytes(service.read_bytes()+b'// drift\n')
            with self.assertRaisesRegex(m.Error, 'differs from companion release'): m.plugin_status()
        m.PAM.unlink()
        with self.assertRaisesRegex(m.Error, 'Dedicated lock PAM missing'): m.lock_prerequisites()

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

    def test_fixed_plugin_rejects_unlisted_and_symlink_runtime_files(self):
        directory, listing = self.fixed_plugin_fixture()
        extra = directory / 'plugin/Extra.qml'; extra.write_text('unlisted')
        with self.assertRaisesRegex(m.Error, 'runtime tree'): m.fixed_plugin_files()
        extra.unlink(); extra.symlink_to(directory / 'plugin/Service.qml')
        with self.assertRaises(m.Error): m.fixed_plugin_files()

    def test_fixed_plugin_activation_requires_native_exclusivity_and_no_root_actions(self):
        directory, listing = self.fixed_plugin_fixture()
        calls = []
        def command(argv, **kwargs):
            self.assertEqual(argv[:3], ['/usr/bin/omarchy','plugin','enable'])
            calls.append(argv)
            for plugin in listing: plugin['enabled'] = plugin['id'] == argv[3]
            return SimpleNamespace(returncode=0)
        with patch.object(m, 'unlocked'), patch.object(m, 'lock_prerequisites'), \
             patch.object(m, 'native_plugins', side_effect=lambda: json.loads(json.dumps(listing))), \
             patch.object(m.subprocess, 'run', side_effect=command):
            m.activate_plugin()
        self.assertEqual(calls, [['/usr/bin/omarchy','plugin','enable','9spokes.himmelblau-lock']])
        self.assertTrue(listing[1]['enabled']); self.assertFalse(listing[0]['enabled'])

    def test_fixed_plugin_fresh_activation_does_not_require_legacy_username(self):
        directory, listing = self.fixed_plugin_fixture()
        account, home = m.canonical_user.return_value
        for name in ('omarchy', 'alice@example.com'):
            account.pw_name = name
            listing[0]['enabled'] = True; listing[1]['enabled'] = False
            def command(argv, **kwargs):
                self.assertEqual(argv, ['/usr/bin/omarchy','plugin','enable','9spokes.himmelblau-lock'])
                for plugin in listing: plugin['enabled'] = plugin['id'] == argv[3]
                return SimpleNamespace(returncode=0)
            with self.subTest(name=name), patch.object(m, 'unlocked'), patch.object(m, 'lock_prerequisites'), \
                 patch.object(m, 'user_paths', side_effect=AssertionError('legacy ID must not be required')), \
                 patch.object(m, 'native_plugins', side_effect=lambda: json.loads(json.dumps(listing))), \
                 patch.object(m.subprocess, 'run', side_effect=command):
                m.activate_plugin()

    def test_fixed_plugin_migration_keeps_legacy_files_and_requires_opt_in(self):
        directory, listing = self.fixed_plugin_fixture(legacy=True)
        user, clone, statefile, hook = m.user_paths()
        previous = (m.clone_hashes(clone), statefile.read_bytes(), hook.read_bytes())
        calls = []
        def command(argv, **kwargs):
            self.assertEqual(argv[:2], ['/usr/bin/omarchy','plugin'])
            action, name = argv[2:]
            self.assertIn(action, ('disable','enable')); calls.append((action,name))
            for plugin in listing:
                if action == 'disable' and plugin['id'] == name: plugin['enabled'] = False
                elif action == 'enable': plugin['enabled'] = plugin['id'] == name
            return SimpleNamespace(returncode=0)
        with patch.object(m, 'unlocked'), patch.object(m, 'lock_prerequisites'), \
             patch.object(m, 'native_plugins', side_effect=lambda: json.loads(json.dumps(listing))), \
             patch.object(m.subprocess, 'run', side_effect=command):
            with self.assertRaisesRegex(m.Error, '--migrate'): m.activate_plugin()
            self.assertEqual(calls, [])
            m.activate_plugin(migrate=True)
            m.plugin_status()
            with self.assertRaisesRegex(m.Error, 'Fixed lock plugin is enabled'): m.enable_user()
        self.assertEqual(calls, [('disable','alice.lock'), ('enable','9spokes.himmelblau-lock')])
        self.assertEqual((m.clone_hashes(clone),statefile.read_bytes(),hook.read_bytes()), previous)

    def test_fixed_plugin_refuses_locked_unknown_or_failed_switch(self):
        directory, listing = self.fixed_plugin_fixture()
        with patch.object(m, 'unlocked', side_effect=m.Error('locked')), patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(m.Error, 'locked'): m.activate_plugin()
            run.assert_not_called()
        listing[0]['enabled'] = False
        listing.append({'id':'unknown.lock','firstParty':False,'enabled':True,'clonedFrom':'omarchy.lock'})
        with patch.object(m, 'unlocked'), patch.object(m, 'lock_prerequisites'), \
             patch.object(m, 'native_plugins', return_value=listing), patch.object(m.subprocess, 'run') as run:
            with self.assertRaisesRegex(m.Error, 'Unknown/conflicting'): m.activate_plugin(migrate=True)
            run.assert_not_called()
        listing.pop(); listing[0]['enabled'] = True
        with patch.object(m, 'unlocked'), patch.object(m, 'lock_prerequisites'), \
             patch.object(m, 'native_plugins', return_value=listing), \
             patch.object(m.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
            with self.assertRaisesRegex(m.Error, 'activation incomplete'): m.activate_plugin()

    def test_policy_legacy_default_and_explicit_toggle_preserve_original(self):
        before = m.CONFIG.read_bytes()
        m.setup('example.com', 'alice@example.com')
        state = m.load_state(); state.pop('apply_policy'); state['version'] = '0.3.0'; m.save_state(state)
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
        self.assertEqual(changed['before'], original['before'])
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
            def fail_once(path, data, mode):
                nonlocal failed
                if Path(path) == target and not failed:
                    failed = True
                    raise OSError('synthetic failure')
                return original(path, data, mode)
            with patch.object(m, 'atomic', fail_once):
                with self.assertRaises(m.Error): m.policy(True)
            self.assertTrue(failed)
            self.assertEqual(m.CONFIG.read_bytes(), old_config)
            self.assertEqual(m.STATE.read_bytes(), old_state)
            self.assertFalse((m.STATE.parent / 'policy-backup.json').exists())

    def test_policy_failed_restoration_retains_private_recovery_journal(self):
        m.setup('example.com', 'alice@example.com')
        old_config = m.CONFIG.read_bytes(); old_state = m.STATE.read_bytes()
        original = m.atomic
        def failure(path, data, mode):
            if Path(path) == m.STATE: raise OSError('synthetic persistent state failure')
            return original(path, data, mode)
        with patch.object(m, 'atomic', failure):
            with self.assertRaisesRegex(m.Error, 'recoverable originals retained'): m.policy(True)
        journal = m.STATE.parent / 'policy-backup.json'
        saved = json.loads(journal.read_text())
        self.assertEqual(m.base64.b64decode(saved['config']), old_config)
        self.assertEqual(m.base64.b64decode(saved['state']), old_state)
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(m.Error, 'Pending policy recovery'): m.policy(True)

    def test_pending_recovery_journals_block_health_and_mutators(self):
        m.setup('example.com', 'alice@example.com')
        for operation in ('policy', 'repair'):
            journal = m.STATE.parent / f'{operation}-backup.json'
            journal.write_text('{}'); journal.chmod(0o600)
            old_state = m.STATE.read_bytes(); old_config = m.CONFIG.read_bytes()
            def root_status():
                state = m.load_state()
                with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'load_state', return_value=state):
                    m.main(['doctor'])
            actions = (m.doctor, m.repair, m.rollback, lambda: m.policy(True),
                       lambda: m.setup('example.com', 'alice@example.com'), root_status)
            for action in actions:
                with self.subTest(operation=operation, action=action):
                    with self.assertRaisesRegex(m.Error, f'Pending {operation} recovery'):
                        action()
                    self.assertEqual(m.STATE.read_bytes(), old_state)
                    self.assertEqual(m.CONFIG.read_bytes(), old_config)
            self.assertIn(f'Pending {operation} recovery', '\n'.join(m.root_issues(m.load_state())))
            journal.unlink()

    def test_final_policy_journal_unlink_failure_is_not_reported_healthy(self):
        m.setup('example.com', 'alice@example.com')
        journal = m.STATE.parent / 'policy-backup.json'
        original = Path.unlink
        def fail_final_unlink(path, *args, **kwargs):
            if path == journal: raise OSError('synthetic journal unlink failure')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_final_unlink):
            with self.assertRaisesRegex(OSError, 'journal unlink failure'): m.policy(True)
        self.assertTrue(m.load_state()['apply_policy'])
        self.assertTrue(journal.exists())
        with self.assertRaisesRegex(m.Error, 'Pending policy recovery'): m.doctor()
        with self.assertRaisesRegex(m.Error, 'Pending policy recovery'): m.repair()

    def test_doctor_is_root_only_and_status_is_user_only(self):
        with patch.object(m.os, 'geteuid', return_value=1000), patch.object(m, 'doctor') as doctor:
            with self.assertRaisesRegex(m.Error, 'as root'):
                m.main(['doctor'])
            doctor.assert_not_called()
        with patch.object(m.os, 'geteuid', return_value=0), patch.object(m, 'user_status') as status:
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

    def legacy_root(self):
        self.setup()
        state = m.load_state()
        state['version'] = '0.2.0'
        legacy = m.patch_source('sddm-username', (m.THEME / 'Main.qml').read_bytes())
        main = m.COPY_THEME / 'Main.qml'
        main.write_bytes(legacy)
        for entry in state['files']:
            if entry['path'] == str(main):
                entry['after'] = m.digest(legacy)
        m.save_state(state)
        return main, legacy, m.STATE.read_bytes()

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

    def test_known_02_theme_upgrade_preserves_originals_and_locker(self):
        main, legacy, old = self.legacy_root()
        old_state = json.loads(old)
        unchanged = {Path(e['path']): Path(e['path']).read_bytes() for e in old_state['files'] if e['path'] != str(main)}
        with self.assertRaisesRegex(m.Error, 'explicit root repair'): m.doctor()
        with self.assertRaises(m.Error): self.setup()
        self.assertEqual(main.read_bytes(), legacy)
        m.repair()
        upgraded = m.load_state()
        self.assertEqual(upgraded['version'], '0.5.2')
        self.assertEqual(main.read_bytes(), m.patch_source('sddm-conversation', (m.THEME / 'Main.qml').read_bytes()))
        for previous, current in zip(old_state['files'], upgraded['files']):
            self.assertEqual({k:v for k,v in previous.items() if k != 'after'}, {k:v for k,v in current.items() if k != 'after'})
        for path, data in unchanged.items(): self.assertEqual(path.read_bytes(), data)
        m.rollback()
        self.assertFalse(main.exists())

    def test_01_root_label_with_completed_02_pin_repair_upgrades(self):
        main, legacy, old = self.legacy_root()
        state = json.loads(old); state['version'] = '0.1.0'; m.save_state(state)
        m.repair()
        self.assertEqual(m.load_state()['version'], '0.5.2')
        self.assertNotEqual(main.read_bytes(), legacy)

    def test_01_root_label_without_02_pin_state_refuses_upgrade(self):
        main, legacy, old = self.legacy_root()
        state = json.loads(old); state['version'] = '0.1.0'
        m.save_state(state); m.PIN_PAM.unlink(); before = m.STATE.read_bytes()
        with self.assertRaisesRegex(m.Error, 'matching managed 0.2 history'): m.repair()
        self.assertEqual(m.STATE.read_bytes(), before)
        self.assertFalse(m.PIN_PAM.exists())
        m.PIN_PAM.write_bytes(m.PIN_PAM_CONTENT)
        state['files'] = [e for e in state['files'] if e['path'] != str(m.PIN_PAM)]
        m.save_state(state); m.PIN_PAM.unlink(); before = m.STATE.read_bytes()
        with self.assertRaisesRegex(m.Error, 'matching managed 0.2 history'): m.repair()
        self.assertEqual(m.STATE.read_bytes(), before)
        self.assertEqual(main.read_bytes(), legacy)

    def test_02_upgrade_refuses_drift_state_and_permission_changes(self):
        main, legacy, old = self.legacy_root()
        for data, mode, version in [(legacy + b'// drift', 0o644, '0.2.0'),
                                    (legacy, 0o600, '0.2.0'), (legacy, 0o644, 'unknown')]:
            with self.subTest(mode=mode, version=version):
                main.write_bytes(data); main.chmod(mode)
                state = json.loads(old); state['version'] = version; m.save_state(state)
                before = m.STATE.read_bytes()
                with self.assertRaises(m.Error): m.repair()
                self.assertEqual(m.STATE.read_bytes(), before)
                self.assertEqual(main.read_bytes(), data)
                self.assertFalse((m.STATE.parent / 'repair-backup.json').exists())

    def test_02_upgrade_refuses_extra_theme_files_before_writes(self):
        main, legacy, old = self.legacy_root()
        (m.COPY_THEME / 'unexpected.qml').write_bytes(b'operator asset')
        with self.assertRaisesRegex(m.Error, 'Unexpected companion theme entry'): m.repair()
        self.assertEqual(main.read_bytes(), legacy)
        self.assertEqual(m.STATE.read_bytes(), old)

    def test_02_upgrade_rolls_back_theme_and_state_on_write_failure(self):
        main, legacy, old = self.legacy_root()
        atomic = m.atomic
        for failed_path in (main, m.STATE):
            with self.subTest(path=failed_path):
                failed = False
                def injected(path, data, mode):
                    nonlocal failed
                    if path == failed_path and not failed:
                        failed = True
                        raise OSError('synthetic upgrade failure')
                    return atomic(path, data, mode)
                with patch.object(m, 'atomic', injected):
                    with self.assertRaisesRegex(m.Error, 'previous files and state restored'): m.repair()
                self.assertEqual(main.read_bytes(), legacy)
                self.assertEqual(m.STATE.read_bytes(), old)
                self.assertFalse((m.STATE.parent / 'repair-backup.json').exists())

    def test_02_failed_rollback_retains_recoverable_journal(self):
        main, legacy, old = self.legacy_root()
        atomic = m.atomic
        def injected(path, data, mode):
            if path == m.STATE: raise OSError('state write and restore failure')
            return atomic(path, data, mode)
        with patch.object(m, 'atomic', injected):
            with self.assertRaisesRegex(m.Error, 'recoverable originals retained'): m.repair()
        journal = json.loads((m.STATE.parent / 'repair-backup.json').read_text())
        self.assertEqual(m.base64.b64decode(journal['state']), old)
        self.assertEqual(m.base64.b64decode(journal['files'][0]['data']), legacy)
        with self.assertRaisesRegex(m.Error, 'Pending repair recovery'): m.repair()

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
            if argv[:3] == ['/usr/bin/omarchy-shell', 'lock', 'status']:
                self.assertEqual(kw['env']['OMARCHY_PATH'], '/usr/share/omarchy')
                return SimpleNamespace(stdout=json.dumps({key: False for key in
                    ('locked','requested','pending','sessionLocked','secure','authenticating')}))
            if argv == ['/usr/bin/omarchy', 'plugin', 'list', '--json']:
                return SimpleNamespace(stdout=json.dumps([{'id': 'alice.lock', 'enabled': True,
                    'firstParty': False, 'clonedFrom': 'omarchy.lock'}]))
            if argv[:3] == ['/usr/bin/omarchy', 'plugin', 'clone']:
                clone.mkdir(parents=True)
                shutil.copy2(m.LOCK.parent / 'LockView.qml', clone / 'LockView.qml')
                shutil.copy2(m.LOCK, clone / 'Service.qml')  # manifest.json directory branch copies exactly
                return SimpleNamespace(returncode=0)
            return run(argv, **kw)
        with patch.object(m, 'user_paths', return_value=paths), patch.object(m.subprocess, 'run', command):
            m.enable_user()
            # A working 0.2 locker clone remains valid and is never upgraded by 0.3.
            legacy_state = json.loads(statefile.read_text()); legacy_state['version'] = '0.2.0'
            statefile.write_text(json.dumps(legacy_state))
            before_clone = m.clone_hashes(clone); before_user_state = statefile.read_bytes()
            m.user_status(); m.enable_user()
            self.assertEqual(m.clone_hashes(clone), before_clone)
            self.assertEqual(statefile.read_bytes(), before_user_state)
        self.assertEqual(sum(cmd[:3] == ['/usr/bin/omarchy', 'plugin', 'clone'] for cmd in commands), 1)
        self.assertIn(['/usr/bin/omarchy', 'plugin', 'list', '--json'], commands)
        self.assertEqual((clone / 'Service.qml').read_bytes(), m.patch_source('lock-conversation', self.before[m.LOCK]))
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
            if argv[0] == '/usr/bin/omarchy-shell':
                return SimpleNamespace(stdout=json.dumps({key: False for key in
                    ('locked','requested','pending','sessionLocked','secure','authenticating')}))
            if argv == ['/usr/bin/omarchy', 'plugin', 'list', '--json']:
                return SimpleNamespace(stdout=json.dumps([{'id': 'alice.lock', 'enabled': True,
                    'firstParty': False, 'clonedFrom': 'omarchy.lock'}]))
            if argv[0] == '/usr/bin/omarchy':
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
        with self.assertRaisesRegex(m.Error, 'protected/foreign'):self.setup()
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

    def test_reserved_user_clone_namespace_refused_before_any_mutation(self):
        for name in ('omarchy', 'omarchy.alice'):
            with self.subTest(name=name), patch.object(m.os, 'geteuid', return_value=1000), \
                    patch.object(m, 'canonical_user', return_value=(SimpleNamespace(pw_name=name), self.base)), \
                    patch.object(m, 'atomic') as write, patch.object(m.subprocess, 'run') as command:
                with self.assertRaisesRegex(m.Error, 'reserved omarchy. namespace'):m.enable_user()
                write.assert_not_called()
                command.assert_not_called()

    def test_native_discovery_bad_postcondition_keeps_partial_state(self):
        self.setup()
        valid = {'id': 'alice.lock', 'enabled': True, 'firstParty': False, 'clonedFrom': 'omarchy.lock'}
        cases = [
            [{'id': 'omarchy.lock', 'enabled': True, 'firstParty': True, 'clonedFrom': ''}],
            [dict(valid, firstParty=True)], [dict(valid, enabled=False)],
            [dict(valid, clonedFrom='other.lock')], {'plugins': [valid]},
        ]
        run = subprocess.run
        for index, listing in enumerate(cases):
            with self.subTest(listing=listing):
                home = self.base / f'user-{index}'
                clone = home / 'plugins/alice.lock'; statefile = home / 'state/state.json'; hook = home / 'hooks/check'
                discoveries = 0
                def command(argv, **kw):
                    nonlocal discoveries
                    if argv[0] == '/usr/bin/omarchy-shell':
                        return SimpleNamespace(stdout=json.dumps({key: False for key in
                            ('locked','requested','pending','sessionLocked','secure','authenticating')}))
                    if argv[:3] == ['/usr/bin/omarchy','plugin','clone']:
                        clone.mkdir(parents=True)
                        shutil.copy2(m.LOCK, clone / 'Service.qml')
                        return SimpleNamespace(returncode=0)
                    if argv == ['/usr/bin/omarchy','plugin','list','--json']:
                        discoveries += 1
                        return SimpleNamespace(stdout=json.dumps([] if discoveries == 1 else listing))
                    return run(argv, **kw)
                with patch.object(m, 'user_paths', return_value=('alice', clone, statefile, hook)), \
                        patch.object(m.subprocess, 'run', command):
                    with self.assertRaisesRegex(m.Error, 'preparation incomplete'):m.enable_user()
                self.assertEqual(json.loads(statefile.read_text())['phase'], 'preparing')
                self.assertEqual((clone / 'Service.qml').read_bytes(), m.LOCK.read_bytes())
                self.assertFalse(hook.exists())

    def legacy_clone(self):
        clone = self.base / 'home/plugins/alice.lock'; clone.mkdir(parents=True)
        shutil.copy2(m.LOCK, clone / 'Service.qml');shutil.copy2(m.LOCK.parent / 'LockView.qml', clone / 'LockView.qml')
        subprocess.run(['/usr/bin/patch','--batch','--fuzz=0','-p1','-d',str(clone)],
            input=(ROOT/'tests/fixtures/lock-0.1.0.patch').read_bytes(),check=True,capture_output=True)
        hook = self.base / 'home/hooks/check';m.atomic(hook,b'#!/bin/sh\ntrue\n',0o755)
        statefile = self.base / 'home/state/state.json'
        upstream=m.clone_hashes(m.LOCK.parent);upstream['Service.qml']=m.digest((clone/'Service.qml').read_bytes())
        state={'phase':'ready','version':'0.1.0','clone_hash':upstream['Service.qml'],
            'clone_files':m.clone_hashes(clone),'upstream_files':upstream,'hook_hash':m.digest(hook.read_bytes())}
        m.atomic(statefile,json.dumps(state).encode(),0o600)
        return clone,statefile,hook

    def test_atomic_legacy_clone_upgrade_and_state_failure_restore(self):
        self.setup();clone,statefile,hook=self.legacy_clone()
        old_service=(clone/'Service.qml').read_bytes();old_view=(clone/'LockView.qml').read_bytes();old_state=statefile.read_bytes()
        atomic=m.atomic;failed=False
        def fail_once(path,data,mode):
            nonlocal failed
            if path==statefile and not failed:
                failed=True;raise OSError('synthetic state failure')
            return atomic(path,data,mode)
        with patch.object(m,'unlocked'),patch.object(m,'verify_native_clone'),patch.object(m,'atomic',fail_once):
            with self.assertRaises(OSError):m.upgrade_user('alice',clone,statefile,hook)
        self.assertEqual((clone/'Service.qml').read_bytes(),old_service)
        self.assertEqual((clone/'LockView.qml').read_bytes(),old_view)
        self.assertEqual(statefile.read_bytes(),old_state)
        with patch.object(m,'unlocked') as unlocked,patch.object(m,'verify_native_clone'):
            m.upgrade_user('alice',clone,statefile,hook)
            unlocked.assert_called_once()
        self.assertEqual(json.loads(statefile.read_text())['version'],'0.5.2')
        self.assertEqual((clone/'Service.qml').read_bytes(),m.patch_source('lock-conversation',m.LOCK.read_bytes()))
        self.assertEqual((clone/'LockView.qml').read_bytes(),m.patch_source('lock-view',(m.LOCK.parent/'LockView.qml').read_bytes()))
        self.assertEqual(clone.stat().st_uid,os.getuid())

    def test_failed_exchange_rollback_retains_old_tree_and_state_backup(self):
        self.setup();clone,statefile,hook=self.legacy_clone();old=statefile.read_bytes();old_service=(clone/'Service.qml').read_bytes()
        exchange=m.exchange_directories;calls=0;atomic=m.atomic
        def fail_rollback(left,right):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('synthetic rollback failure')
            return exchange(left,right)
        def fail_state(path,data,mode):
            if path==statefile:raise OSError('synthetic state failure')
            return atomic(path,data,mode)
        with patch.object(m,'unlocked'),patch.object(m,'verify_native_clone'),patch.object(m,'exchange_directories',fail_rollback),patch.object(m,'atomic',fail_state):
            with self.assertRaisesRegex(m.Error,'backups retained'):m.upgrade_user('alice',clone,statefile,hook)
        retained=list(clone.parent.parent.glob('.himmelblau-stage-*'));self.assertEqual(len(retained),1)
        self.assertEqual((retained[0]/'clone/Service.qml').read_bytes(),old_service)
        self.assertEqual((retained[0]/'previous-state.json').read_bytes(),old)

    def test_unknown_legacy_clone_and_root_pam_drift_refuse_upgrade(self):
        self.setup();clone,statefile,hook=self.legacy_clone();old=statefile.read_bytes()
        (clone/'Service.qml').write_bytes((clone/'Service.qml').read_bytes()+b'// operator edit\n')
        with self.assertRaises(m.Error):m.upgrade_user('alice',clone,statefile,hook)
        self.assertEqual(statefile.read_bytes(),old)
        m.PAM.write_bytes(b'unknown')
        with self.assertRaisesRegex(m.Error,'authentication PAM'):m.upgrade_user('alice',clone,statefile,hook)

    def test_repair_view_preflight_precedes_new_pin_service_and_state_write(self):
        self.setup();state=json.loads(m.STATE.read_text());state['files']=[entry for entry in state['files'] if Path(entry['path'])!=m.PIN_PAM]
        m.save_state(state);m.PIN_PAM.unlink();before=m.STATE.read_bytes()
        view=m.LOCK.parent/'LockView.qml';view.write_bytes(view.read_bytes()+b'// unknown\n')
        with self.assertRaises(m.Error):m.repair()
        self.assertEqual(m.STATE.read_bytes(),before);self.assertFalse(m.PIN_PAM.exists())

    def test_malformed_state_exits_with_one_line_error(self):
        self.setup()
        state = m.load_state()
        cases = [b'not json', b'[]', json.dumps({**state, 'files': [{}]}).encode(),
                 json.dumps({**state, 'admin': 'no'}).encode(),
                 json.dumps({**state, 'transaction': {'kind': 'setup'}}).encode()]
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


if __name__ == '__main__': unittest.main()
