import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('prefetch', Path(__file__).resolve().parents[1] / 'scripts/prefetch-sdl.py')
prefetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prefetch)


class PrefetchTest(unittest.TestCase):
    def test_verified_archive_can_be_reused_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'sdl.tar.gz'
            archive.write_bytes(b'verified archive')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.object(prefetch.urllib.request, 'urlopen') as download:
                prefetch.prefetch(archive, 'a' * 40, digest)
                download.assert_not_called()

    def test_corrupt_cache_is_replaced_only_by_verified_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'sdl.tar.gz'
            archive.write_bytes(b'corrupt cached archive')
            with patch.object(prefetch.urllib.request, 'urlopen') as download:
                download.return_value.__enter__.return_value.read.side_effect = [b'valid archive', b'']
                prefetch.prefetch(archive, 'a' * 40, hashlib.sha256(b'valid archive').hexdigest())
            self.assertEqual(archive.read_bytes(), b'valid archive')

    def test_wrong_download_cannot_replace_existing_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'sdl.tar.gz'
            archive.write_bytes(b'original')
            with patch.object(prefetch.urllib.request, 'urlopen') as download:
                download.return_value.__enter__.return_value.read.side_effect = [b'wrong download', b'']
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    prefetch.prefetch(archive, 'a' * 40, hashlib.sha256(b'expected').hexdigest())
            self.assertEqual(archive.read_bytes(), b'original')
            self.assertFalse(archive.with_suffix('.download').exists())


if __name__ == '__main__':
    unittest.main()
