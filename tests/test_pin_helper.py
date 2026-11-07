import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class PinHelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('cc'): raise unittest.SkipTest('C compiler unavailable')
        cls.work=tempfile.TemporaryDirectory(); cls.binary=Path(cls.work.name)/'pin-change'
        cls.policy=Path(cls.work.name)/'policy';cls.policy.mkdir(mode=0o755)
        cls.account=cls.policy/'recovery-account';cls.account.write_text('alice@example.test\nfixture-user\n1000\n');cls.account.chmod(0o644)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'tests/fake-pam'),
            '-DOH_RECOVERY_DIR="'+str(cls.policy)+'"','-DOH_RECOVERY_OWNER='+str(os.getuid()),
            '-Dgetpwnam_r=fixture_getpwnam_r','-Dgetuid=fixture_getuid','-Dgeteuid=fixture_geteuid','-Dgetpwuid_r=fixture_getpwuid_r',
            str(ROOT/'src/pin-change.c'),str(ROOT/'tests/fake-pam/pam.c'),'-o',str(cls.binary)],check=True,capture_output=True)
    @classmethod
    def tearDownClass(cls): cls.work.cleanup()
    def run_helper(self, data, args=(), env=None):
        return subprocess.run([str(self.binary),*args],input=data,capture_output=True,timeout=5,env={**os.environ,**(env or {})})
    def test_current_user_fixed_service_full_conversation_and_private_output(self):
        result=self.run_helper(b'response p%C3%A4ss\nresponse p%C3%A4ss\nresponse test-otp\n')
        self.assertEqual(result.returncode,0,result.stderr)
        messages=[json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([msg['type'] for msg in messages],['info','prompt','prompt','prompt','error','result'])
        self.assertFalse(messages[1]['visible']);self.assertTrue(messages[3]['visible'])
        self.assertIn('42',messages[0]['text']);self.assertTrue(messages[-1]['success'])
        self.assertNotIn(b'p%C3',result.stdout);self.assertNotIn(b'test-secret',result.stdout);self.assertEqual(result.stderr,b'')
    def test_eof_malformed_and_oversized_responses_fail_closed(self):
        for data in [b'',b'response %00\n',b'response %QX\n',b'response '+b'a'*20000+b'\n']:
            with self.subTest(data_length=len(data)):
                result=self.run_helper(data);self.assertNotEqual(result.returncode,0)
                self.assertFalse(json.loads(result.stdout.splitlines()[-1])['success'])
    def test_no_target_or_service_arguments(self):
        result=self.run_helper(b'',('--user','someone'));self.assertNotEqual(result.returncode,0);self.assertEqual(result.stdout,b'')

    def test_account_denial_or_pam_user_remap_never_requests_a_pin(self):
        for flag in ('FIXTURE_ACCOUNT_DENY','FIXTURE_REMAP','FIXTURE_NSS_FAIL'):
            with self.subTest(flag=flag):
                result=self.run_helper(b'',env={flag:'1'})
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn(b'"prompt"',result.stdout)
    def test_policy_rejects_wrong_principal_uid_malformed_permissions_and_symlink(self):
        original=self.account.read_bytes()
        try:
            for content in [b'other@example.test\nfixture-user\n1000\n',b'alice@example.test\nfixture-user\n0\n',b'alice@example.test\nfixture-user\n1001\n',original+b'extra\n',original.replace(b'1000',b'1000\0')]:
                self.account.write_bytes(content)
                self.assertEqual(self.run_helper(b'').returncode,2)
            self.account.write_bytes(original);self.account.chmod(0o666)
            self.assertEqual(self.run_helper(b'').returncode,2)
            self.account.chmod(0o644);self.account.unlink();self.account.symlink_to('/dev/null')
            self.assertEqual(self.run_helper(b'').returncode,2)
        finally:
            self.account.unlink(missing_ok=True);self.account.write_bytes(original);self.account.chmod(0o644)

    def test_every_non_success_account_result_stops_before_pin(self):
        for code in (6,10,12,25):  # PERM_DENIED, USER_UNKNOWN, NEW_AUTHTOK_REQD, IGNORE
            result=self.run_helper(b'',env={'FIXTURE_ACCOUNT_CODE':str(code)})
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn(b'"prompt"',result.stdout)
    def test_policy_change_during_account_check_cannot_replace_bound_identity(self):
        original=self.account.read_bytes()
        try:
            result=self.run_helper(b'',env={'FIXTURE_UID_DRIFT':'1'})
            self.assertNotEqual(result.returncode,0)
            self.assertNotIn(b'"prompt"',result.stdout)
        finally:
            self.account.write_bytes(original)
