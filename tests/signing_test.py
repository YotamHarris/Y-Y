import base64
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('signing', Path(__file__).resolve().parents[1] / 'scripts/ios-signing.py')
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


class SigningTest(unittest.TestCase):
    def test_certificate_match_uses_content(self):
        pem = '-----BEGIN CERTIFICATE-----\n' + base64.b64encode(b'correct certificate').decode() + '\n-----END CERTIFICATE-----'
        with patch.object(signing.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, pem, '')):
            self.assertTrue(signing.has_certificate(b'correct certificate', 'build.keychain'))
            self.assertFalse(signing.has_certificate(b'wrong certificate', 'build.keychain'))

    def test_existing_intermediate_is_not_imported(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(signing.urllib.request, 'urlopen') as download, \
                patch.object(signing, 'has_certificate', side_effect=[False, True]), \
                patch.object(signing, 'security') as security:
            download.return_value.__enter__.return_value.read.return_value = b'certificate'
            signing.install_intermediate(Path('build.keychain'), Path(temporary))
            security.assert_not_called()

    def test_missing_intermediate_is_imported(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(signing.urllib.request, 'urlopen') as download, \
                patch.object(signing, 'has_certificate', return_value=False), \
                patch.object(signing, 'security') as security:
            download.return_value.__enter__.return_value.read.return_value = b'certificate'
            signing.install_intermediate(Path('build.keychain'), Path(temporary))
            self.assertEqual(security.call_args.args[0], 'import')

    def test_failure_reports_error_without_password_or_argv(self):
        with patch.dict(os.environ, {'P12_PASSWORD': 'private-p12-password'}), \
                patch.object(signing.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 1, '', 'cannot import private-p12-password keychain-password')):
            with self.assertRaises(SystemExit) as failure:
                signing.security('import', 'private-file-path', '-P', 'private-p12-password', sensitive=('keychain-password',))
            message = str(failure.exception)
            self.assertIn('cannot import', message)
            for secret in ('private-p12-password', 'keychain-password', 'private-file-path'):
                self.assertNotIn(secret, message)


if __name__ == '__main__':
    unittest.main()
