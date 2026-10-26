"""Fast packaging guards; a real relocated bundle is verified separately."""
import importlib.util
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('package_release',
    Path(__file__).resolve().parents[1] / 'deploy/package_release.py')
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PackageTests(unittest.TestCase):
    def test_missing_linked_library_rejected(self):
        with patch.object(PACKAGE, 'command', return_value='libdatachannel.so.0.24 => not found'):
            with self.assertRaisesRegex(ValueError, 'unresolved'):
                PACKAGE.libraries(Path('/unused'))

    def test_non_native_executable_rejected(self):
        with patch.object(PACKAGE, 'command', return_value='linux-vdso.so.1 (0x1234)'):
            with self.assertRaisesRegex(ValueError, 'native Linux'):
                PACKAGE.libraries(Path('/unused'))

    def test_sanitizer_rejected_even_when_static(self):
        for symbol in ['__asan_init', '__tsan_init', '__ubsan_handle_type_mismatch_v1']:
            with self.subTest(symbol=symbol), patch.object(PACKAGE, 'command', return_value=symbol):
                with self.assertRaisesRegex(ValueError, 'sanitizer'):
                    PACKAGE.reject_instrumented(Path('/unused'))

    def test_numeric_glibc_versions(self):
        with patch.object(PACKAGE, 'command', side_effect=['GLIBC_2.9 GLIBC_2.34', 'GLIBC_2.38 GLIBC_2.3.4']):
            self.assertEqual(PACKAGE.glibc_required([Path('/one'), Path('/two')]), '2.38')

    def test_debug_refused_before_output_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'CMakeCache.txt').write_text('CMAKE_BUILD_TYPE:STRING=Debug\n')
            with patch.object(PACKAGE.platform, 'system', return_value='Linux'), \
                 patch.object(PACKAGE.platform, 'libc_ver', return_value=('glibc', '2.39')):
                with self.assertRaisesRegex(ValueError, 'unsanitized'):
                    PACKAGE.build_release(root, root, root / 'output', root / 'LICENSE')
            self.assertFalse((root / 'output').exists())

    def test_release_sanitizer_cache_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'CMakeCache.txt').write_text('CMAKE_BUILD_TYPE:STRING=Release\nSPEEDTEST_SANITIZER:STRING=thread\n')
            with patch.object(PACKAGE.platform, 'system', return_value='Linux'), \
                 patch.object(PACKAGE.platform, 'libc_ver', return_value=('glibc', '2.39')):
                with self.assertRaisesRegex(ValueError, 'unsanitized'):
                    PACKAGE.build_release(root, root, root / 'output', root / 'LICENSE')
            self.assertFalse((root / 'output').exists())

    def test_deterministic_archive_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'data').write_bytes(b'release fixture')
            outputs = [root / 'one.tar.gz', root / 'two.tar.gz']
            for output in outputs:
                PACKAGE.write_archive(bundle, output)
            self.assertEqual(hashlib.sha256(outputs[0].read_bytes()).digest(),
                             hashlib.sha256(outputs[1].read_bytes()).digest())
            with self.assertRaises(FileExistsError):
                PACKAGE.write_archive(bundle, outputs[0])

    def test_archive_in_bundle_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'outside'):
                PACKAGE.write_archive(root, root / 'bad.tar.gz')


if __name__ == '__main__':
    unittest.main()
