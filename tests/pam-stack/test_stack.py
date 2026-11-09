"""PAM stack harness: the proposed system-auth and lock-screen service, on Arch's real
libpam and service files, with stub Himmelblau PAM and NSS modules.

Runs as root inside the image built by run.sh. Accounts:
  alice                local, password alice-pw
  root                 local, password root-pw
  carol@example.test   Entra (resolved by the stub NSS module, shadow password "x")
  dave@example.test    Entra, shell not in /etc/shells
  homer                non-local, resolvable, unknown to Himmelblau; a systemd-homed
                       account (the homed stub accepts it when told to)
  ghost                resolvable by nothing
"""
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

CONTROL = Path('/etc/pamstub/control')
CALLS = Path('/run/pamstub/calls')
HOMED_CALLS = Path('/run/pamstub/homed-calls')
ENTRA = 'carol@example.test'
NOSHELL = 'dave@example.test'
HOMED = 'homer'
GHOST = 'ghost'
LOCK = 'omarchy-himmelblau-lock-password'
FAILURES = ('AUTH_ERR', 'IGNORE', 'USER_UNKNOWN', 'SERVICE_ERR', 'PERM_DENIED',
            'AUTHINFO_UNAVAIL', 'CRED_INSUFFICIENT')

# Services and the PAM stages each one defines.
SERVICES = {
    'login': ('auth', 'account', 'session'),
    'sddm': ('auth', 'account', 'session'),
    'sshd': ('auth', 'account', 'session'),
    'sudo': ('auth', 'account', 'session'),
    'polkit-1': ('auth', 'account', 'session'),
    'passwd': ('auth', 'account'),
    LOCK: ('auth', 'account'),
}


class Run:
    def __init__(self, out):
        self.out = out
        self.results = dict(re.findall(r'^result (\S+) (\S+)$', out, re.M))
        self.prompts = re.findall(r'^prompt (.*)$', out, re.M)
        self.calls = CALLS.read_text().split('\n')[:-1] if CALLS.exists() else []

    def __getitem__(self, op):
        return self.results[op]

    def stub(self, stage=None):
        return [c for c in self.calls if stage is None or c.endswith(' ' + stage)]


def drive(service, user, *ops, answers='wrong-pw', uid=None, suid=False):
    CALLS.write_text('')
    HOMED_CALLS.write_text('')
    command = ['/usr/local/bin/pamdrive-suid' if suid else '/usr/local/bin/pamdrive',
               service, user, *ops]
    if uid is not None:
        command = ['setpriv', f'--reuid={uid}', f'--regid={uid}', '--clear-groups'] + command
    out = subprocess.run(command, capture_output=True, text=True, timeout=60,
                         env={'PATH': '/usr/bin', 'PAMDRIVE_ANSWERS': answers}).stdout
    return Run(out)


def stub(**stages):
    """stub(carol='auth=SUCCESS account=SUCCESS') style control lines."""
    lines = []
    for key, spec in stages.items():
        user = {'carol': ENTRA, 'dave': NOSHELL, 'homer': HOMED}[key]
        for item in spec.split():
            stage, result = item.split('=')
            lines.append(f'{user} {stage} {result}')
    CONTROL.write_text('\n'.join(lines) + '\n')


ALL_OK = 'auth=SUCCESS account=SUCCESS prelim=SUCCESS update=SUCCESS session=SUCCESS'
HOMED_OK = ' '.join('homed-' + item for item in ALL_OK.split())


def tally(user):
    out = subprocess.run(['faillock', '--user', user], capture_output=True, text=True).stdout
    return len(re.findall(r'^\d{4}-\d\d-\d\d ', out, re.M))


class StackTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        CALLS.parent.mkdir(mode=0o1777, exist_ok=True)
        CALLS.parent.chmod(0o1777)
        for log in (CALLS, HOMED_CALLS):
            log.write_text('')
            log.chmod(0o666)
        CONTROL.write_text('')
        shutil.rmtree('/run/faillock', ignore_errors=True)
        Path('/etc/nologin').unlink(missing_ok=True)
        subprocess.run(['chpasswd'], input='alice:alice-pw\nroot:root-pw\n', text=True, check=True)

    def assertRefused(self, run, op, msg=None):
        self.assertIn(op, run.results, run.out)
        self.assertNotEqual(run[op], 'SUCCESS', msg or run.out)


class LocalAccounts(StackTest):
    def test_local_accounts_never_reach_himmelblau(self):
        stub(carol=ALL_OK)
        for service, stages in SERVICES.items():
            for user, password in (('alice', 'alice-pw'), ('root', 'root-pw')):
                with self.subTest(service=service, user=user):
                    run = drive(service, user, *stages, answers=password)
                    for stage in stages:
                        self.assertEqual(run[stage], 'SUCCESS', run.out)
                    self.assertEqual(run.calls, [])

    def test_local_wrong_password_is_refused_without_himmelblau(self):
        run = drive('sudo', 'alice', 'auth')
        self.assertRefused(run, 'auth')
        self.assertEqual(run.calls, [])

    def test_local_password_change_never_reaches_himmelblau(self):
        run = drive('passwd', 'alice', 'chauthtok', answers='new-alice-pw')
        self.assertEqual(run['chauthtok'], 'SUCCESS', run.out)
        self.assertEqual(run.calls, [])


class EntraAccounts(StackTest):
    def test_success_in_every_service_and_stage(self):
        stub(carol=ALL_OK)
        for service, stages in SERVICES.items():
            with self.subTest(service=service):
                run = drive(service, ENTRA, *stages)
                for stage in stages:
                    self.assertEqual(run[stage], 'SUCCESS', run.out)
                # Entra success never falls through to pam_unix's password prompt.
                self.assertEqual(run.prompts, [], run.out)
                self.assertEqual(len(run.stub('auth')), 1, run.calls)

    def test_every_auth_failure_is_refused_and_counted(self):
        for result in FAILURES:
            for service in SERVICES:
                with self.subTest(service=service, result=result):
                    shutil.rmtree('/run/faillock', ignore_errors=True)
                    stub(carol=f'auth={result} account=SUCCESS')
                    run = drive(service, ENTRA, 'auth')
                    self.assertRefused(run, 'auth')
                    if service != LOCK:  # The lock service runs as root here too.
                        self.assertEqual(tally(ENTRA), 1)

    def test_every_account_failure_is_refused_after_successful_auth(self):
        for result in FAILURES + ('ACCT_EXPIRED',):
            for service, stages in SERVICES.items():
                with self.subTest(service=service, result=result):
                    stub(carol=f'auth=SUCCESS account={result}')
                    run = drive(service, ENTRA, 'auth', 'account')
                    self.assertEqual(run['auth'], 'SUCCESS', run.out)
                    self.assertRefused(run, 'account')

    def test_account_new_authtok_required_is_passed_to_the_application(self):
        stub(carol='auth=SUCCESS account=NEW_AUTHTOK_REQD')
        run = drive('sddm', ENTRA, 'auth', 'account')
        self.assertEqual(run['account'], 'NEW_AUTHTOK_REQD', run.out)

    def test_success_resets_the_failure_tally(self):
        stub(carol='auth=AUTH_ERR')
        for _ in range(3):
            drive('sudo', ENTRA, 'auth')
        self.assertEqual(tally(ENTRA), 3)
        stub(carol='auth=SUCCESS')
        self.assertEqual(drive('sudo', ENTRA, 'auth')['auth'], 'SUCCESS')
        self.assertEqual(tally(ENTRA), 0)

    def test_session_failure_does_not_block_the_session(self):
        for result in FAILURES:
            for service in ('login', 'sddm', 'sshd', 'sudo', 'polkit-1'):
                with self.subTest(service=service, result=result):
                    stub(carol=f'auth=SUCCESS account=SUCCESS session={result}')
                    run = drive(service, ENTRA, 'auth', 'account', 'session')
                    self.assertEqual(run['session'], 'SUCCESS', run.out)
                    self.assertEqual(len(run.stub('session')), 1, run.calls)


class PasswordChange(StackTest):
    RESULTS = ('SUCCESS',) + FAILURES + ('NEW_AUTHTOK_REQD',)

    def test_both_passes_must_succeed(self):
        for prelim in self.RESULTS:
            for update in self.RESULTS:
                with self.subTest(prelim=prelim, update=update):
                    stub(carol=f'prelim={prelim} update={update}')
                    run = drive('passwd', ENTRA, 'chauthtok', answers='new-pw')
                    if (prelim, update) == ('SUCCESS', 'SUCCESS'):
                        self.assertEqual(run['chauthtok'], 'SUCCESS', run.out)
                    else:
                        self.assertRefused(run, 'chauthtok')
                    # pam_unix never runs for an Entra account: it would prompt.
                    self.assertEqual(run.prompts, [], run.out)
                    expected = ['passwd carol@example.test prelim']
                    if prelim == 'SUCCESS':
                        expected.append('passwd carol@example.test update')
                    self.assertEqual(run.calls, expected)

    def test_non_entra_non_local_accounts_are_refused(self):
        stub(homer=HOMED_OK)
        for user in (HOMED, GHOST):
            with self.subTest(user=user):
                run = drive('passwd', user, 'chauthtok', answers='new-pw')
                self.assertRefused(run, 'chauthtok')
                self.assertEqual(run.prompts, [], run.out)


class NonLocalUnknown(StackTest):
    def test_homed_shaped_and_unknown_names_are_refused(self):
        stub(homer=HOMED_OK)  # Even when systemd-homed would accept the account.
        for user in (HOMED, GHOST):
            for service in SERVICES:
                with self.subTest(service=service, user=user):
                    run = drive(service, user, 'auth')
                    if (service, user) != (LOCK, HOMED):  # See the next test.
                        self.assertRefused(run, 'auth')
                    run = drive(service, user, 'account')
                    self.assertRefused(run, 'account')

    def test_lock_screen_keeps_stock_homed_unlock(self):
        # Known difference from system-auth, kept on purpose: the lock service's
        # Himmelblau line passes IGNORE on, so a homed account still unlocks through
        # pam_systemd_home as on stock Omarchy. The identity is fixed (the session
        # owner), and a homed account can't sign in through system-auth to own a
        # session. Its account stage, via system-auth, still refuses it.
        stub(homer=HOMED_OK)
        self.assertEqual(drive(LOCK, HOMED, 'auth')['auth'], 'SUCCESS')
        self.assertRefused(drive(LOCK, HOMED, 'account'), 'account')

    def test_unresolvable_names_get_no_tally(self):
        for _ in range(3):
            drive('sudo', GHOST, 'auth')
        self.assertEqual(tally(GHOST), 0)
        for _ in range(3):
            drive('sudo', HOMED, 'auth')
        self.assertEqual(tally(HOMED), 3)


class Lockout(StackTest):
    def lock_out(self, user, service='sudo'):
        stub(carol='auth=AUTH_ERR')
        for _ in range(10):
            self.assertRefused(drive(service, user, 'auth'), 'auth')
        self.assertEqual(tally(user), 10)

    def test_locked_entra_account_never_reaches_himmelblau(self):
        self.lock_out(ENTRA)
        stub(carol='auth=SUCCESS account=SUCCESS')
        for service in SERVICES:
            with self.subTest(service=service):
                run = drive(service, ENTRA, 'auth')
                self.assertRefused(run, 'auth')
                self.assertEqual(run.stub('auth'), [])
                self.assertEqual(run.prompts, [], run.out)

    def test_reset_unlocks(self):
        self.lock_out(ENTRA)
        subprocess.run(['faillock', '--user', ENTRA, '--reset'], check=True)
        stub(carol='auth=SUCCESS')
        self.assertEqual(drive('sudo', ENTRA, 'auth')['auth'], 'SUCCESS')

    def test_local_account_locks_too(self):
        for _ in range(10):
            drive('sudo', 'alice', 'auth')
        run = drive('sudo', 'alice', 'auth', answers='alice-pw')
        self.assertRefused(run, 'auth')

    def test_root_never_locks(self):
        for _ in range(12):
            drive('sudo', 'root', 'auth')
        self.assertEqual(drive('sudo', 'root', 'auth', answers='root-pw')['auth'], 'SUCCESS')

    @unittest.skipIf(os.environ.get('PAM_STACK_FAST'), 'PAM_STACK_FAST set')
    def test_lock_expires_after_120_seconds(self):
        self.lock_out(ENTRA)
        stub(carol='auth=SUCCESS')
        self.assertRefused(drive('sudo', ENTRA, 'auth'), 'auth')
        time.sleep(121)
        self.assertEqual(drive('sudo', ENTRA, 'auth')['auth'], 'SUCCESS')


class LoginChecks(StackTest):
    def test_nologin_refuses_entra_before_himmelblau(self):
        stub(carol=ALL_OK)
        Path('/etc/nologin').write_text('maintenance\n')
        for service in ('login', 'sddm', 'sshd'):
            with self.subTest(service=service):
                run = drive(service, ENTRA, 'auth')
                self.assertRefused(run, 'auth')
                self.assertEqual(run.calls, [])
                run = drive(service, 'root', 'auth', answers='root-pw')
                self.assertEqual(run['auth'], 'SUCCESS', run.out)

    def test_shell_check_refuses_entra_that_himmelblau_accepts(self):
        stub(dave=ALL_OK)
        for service in ('login', 'sddm', 'sshd'):
            with self.subTest(service=service):
                self.assertRefused(drive(service, NOSHELL, 'auth'), 'auth')


class GuardErrors(StackTest):
    """pam_localuser can't read /etc/passwd: every stage dies before Himmelblau."""

    def setUp(self):
        super().setUp()
        stub(carol=ALL_OK)
        os.rename('/etc/passwd', '/etc/passwd.moved')

    def tearDown(self):
        os.rename('/etc/passwd.moved', '/etc/passwd')

    def test_every_stage_dies_and_himmelblau_is_never_called(self):
        cases = [(service, stage) for service, stages in SERVICES.items() for stage in stages]
        cases.append(('passwd', 'chauthtok'))
        for service, stage in cases:
            with self.subTest(service=service, stage=stage):
                run = drive(service, ENTRA, stage)
                self.assertRefused(run, stage)
                self.assertEqual(run.calls, [])


class AsCallingUser(StackTest):
    """The lock screen runs as the locked user; sudo is setuid root."""

    def test_lock_screen_as_local_user(self):
        run = drive(LOCK, 'alice', 'auth', answers='alice-pw', uid=1000)
        self.assertEqual(run['auth'], 'SUCCESS', run.out)
        self.assertEqual(run.calls, [])

    def test_lock_screen_as_entra_user(self):
        stub(carol='auth=SUCCESS')
        run = drive(LOCK, ENTRA, 'auth', uid=50001)
        self.assertEqual(run['auth'], 'SUCCESS', run.out)
        stub(carol='auth=AUTH_ERR')
        self.assertRefused(drive(LOCK, ENTRA, 'auth', uid=50001), 'auth')

    def test_setuid_sudo(self):
        stub(carol='auth=SUCCESS account=SUCCESS')
        run = drive('sudo', 'alice', 'auth', 'account', answers='alice-pw', uid=1000, suid=True)
        self.assertEqual((run['auth'], run['account']), ('SUCCESS', 'SUCCESS'), run.out)
        run = drive('sudo', ENTRA, 'auth', 'account', uid=50001, suid=True)
        self.assertEqual((run['auth'], run['account']), ('SUCCESS', 'SUCCESS'), run.out)


SYSTEM_AUTH = Path('/etc/pam.d/system-auth')
SYSTEM_LOGIN = Path('/etc/pam.d/system-login')
LOCK_FILE = Path('/etc/pam.d') / LOCK
AAD_TOOL_AUTH = 'auth       sufficient   pam_himmelblau.so ignore_unknown_user set_authtok\n'

# Each mutant is a known-bad stack the suite must reject: (file, old, new, tests that must fail).
MUTANTS = {
    'aad-tool-layout': [
        (SYSTEM_AUTH, None, 'pristine/system-auth',
         ['Lockout.test_locked_entra_account_never_reaches_himmelblau']),
        (SYSTEM_LOGIN, '#%PAM-1.0\n', '#%PAM-1.0\n' + AAD_TOOL_AUTH,
         ['LoginChecks.test_nologin_refuses_entra_before_himmelblau']),
    ],
    'preauth-required': [
        (SYSTEM_AUTH, 'requisite                   pam_faillock.so      preauth',
         'required                    pam_faillock.so      preauth',
         ['Lockout.test_locked_entra_account_never_reaches_himmelblau']),
    ],
    'auth-guard-ignores-errors': [
        (SYSTEM_AUTH, 'auth       [success=1 perm_denied=ignore default=die]',
         'auth       [success=1 default=ignore]',
         ['GuardErrors.test_every_stage_dies_and_himmelblau_is_never_called']),
    ],
    'account-ignore-passes': [
        (SYSTEM_AUTH, '[success=ok default=die]', '[success=ok ignore=ignore default=die]',
         ['EntraAccounts.test_every_account_failure_is_refused_after_successful_auth']),
    ],
    'password-sufficient': [
        (SYSTEM_AUTH, '[success=done new_authtok_reqd=done default=die] pam_himmelblau.so',
         'sufficient pam_himmelblau.so',
         ['PasswordChange.test_non_entra_non_local_accounts_are_refused']),
    ],
    'session-guard-ignores-errors': [
        (SYSTEM_AUTH, 'session    [success=1 perm_denied=ignore default=die]',
         'session    [success=1 default=ignore]',
         ['GuardErrors.test_every_stage_dies_and_himmelblau_is_never_called']),
    ],
    'lock-guard-0.5.3': [
        (LOCK_FILE, '[success=1 perm_denied=ignore default=die] pam_localuser.so',
         '[success=1 default=ignore] pam_localuser.so',
         ['GuardErrors.test_every_stage_dies_and_himmelblau_is_never_called']),
    ],
}


def run_mutants():
    """Apply each mutant and require its named tests to fail."""
    here = Path(__file__).parent
    survivors = []
    for name, edits in MUTANTS.items():
        saved = {path: path.read_text() for path, *_ in edits}
        tests = []
        try:
            for path, old, new, expect in edits:
                text = path.read_text()
                if old is None:
                    text = (here / new).read_text()
                    text = text.replace('#%PAM-1.0\n', '#%PAM-1.0\n' + AAD_TOOL_AUTH, 1)
                else:
                    assert text.count(old) == 1, (name, old)
                    text = text.replace(old, new)
                path.write_text(text)
                tests += expect or []
            result = subprocess.run([sys.executable, __file__, *tests],
                                    capture_output=True, text=True, timeout=1800)
            caught = result.returncode != 0
            print(f'mutant {name}: {"caught" if caught else "SURVIVED"}', flush=True)
            if not caught:
                survivors.append(name)
        finally:
            for path, text in saved.items():
                path.write_text(text)
    return survivors


if __name__ == '__main__':
    if sys.argv[1:] == ['--mutants']:
        sys.exit(1 if run_mutants() else 0)
    unittest.main()
