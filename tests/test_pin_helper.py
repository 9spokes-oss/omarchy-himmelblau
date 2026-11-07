import json
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
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'tests/fake-pam'),
            '-Dgetuid=fixture_getuid','-Dgeteuid=fixture_geteuid','-Dgetpwuid_r=fixture_getpwuid_r',
            str(ROOT/'src/pin-change.c'),str(ROOT/'tests/fake-pam/pam.c'),'-o',str(cls.binary)],check=True,capture_output=True)
    @classmethod
    def tearDownClass(cls): cls.work.cleanup()
    def run_helper(self, data, args=()):
        return subprocess.run([str(self.binary),*args],input=data,capture_output=True)
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
