import importlib.util
from pathlib import Path
import plistlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('archive_check', Path(__file__).resolve().parents[1] / 'scripts/verify-ios-archive.py')
archive_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive_check)


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.archive = Path(self.temporary.name) / 'TapDemo.xcarchive'
        self.app = self.archive / 'Products/Applications/TapDemo.app'
        self.app.mkdir(parents=True)
        self.write(self.archive / 'Info.plist', {'ApplicationProperties': {'ApplicationPath': 'Applications/TapDemo.app'}})
        self.write(self.app / 'Info.plist', {'CFBundleIdentifier': 'com.example.tapdemo', 'CFBundleExecutable': 'TapDemo'})
        (self.app / 'TapDemo').write_bytes(b'executable')

    def write(self, path, content):
        with path.open('wb') as handle:
            plistlib.dump(content, handle)

    def test_installed_app_archive_passes(self):
        archive_check.verify(self.archive, 'com.example.tapdemo')

    def test_empty_generic_archive_reports_install_setting(self):
        self.write(self.archive / 'Info.plist', {'ArchiveVersion': 2})
        with self.assertRaisesRegex(ValueError, 'SKIP_INSTALL=NO'):
            archive_check.verify(self.archive, 'com.example.tapdemo')

    def test_installed_library_makes_archive_generic(self):
        library = self.archive / 'Products/usr/local/lib'
        library.mkdir(parents=True)
        (library / 'SDL.a').write_bytes(b'library')
        with self.assertRaisesRegex(ValueError, 'SKIP_INSTALL=YES'):
            archive_check.verify(self.archive, 'com.example.tapdemo')

    def test_wrong_game_and_missing_executable_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'bundle identifier'):
            archive_check.verify(self.archive, 'com.example.other')
        (self.app / 'TapDemo').unlink()
        with self.assertRaisesRegex(ValueError, 'executable'):
            archive_check.verify(self.archive, 'com.example.tapdemo')

    def test_application_path_cannot_escape_archive(self):
        self.write(self.archive / 'Info.plist', {'ApplicationProperties': {'ApplicationPath': '../../outside.app'}})
        with self.assertRaisesRegex(ValueError, 'no installed app'):
            archive_check.verify(self.archive, 'com.example.tapdemo')


if __name__ == '__main__':
    unittest.main()
