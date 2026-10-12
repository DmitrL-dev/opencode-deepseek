import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from integrations.harness import render, yaml_codec, patch_document as patch_models
from integrations.harness_shell_safety import install, patch_document


class ShellSafetyTests(unittest.TestCase):
    def test_model_reconfiguration_preserves_installed_safety(self):
        document = patch_document([])
        updated = patch_models(document, 'http://127.0.0.1:8000/v1',
                               [{'id': 'deepseek-chat'}])
        self.assertEqual(updated[0], document[0])
        self.assertEqual(patch_document(updated), updated)

    def test_installer_preserves_other_config_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            profile = home / 'profiles/desktop'
            profile.mkdir(parents=True)
            path = profile / 'cordis.patch.yml'
            original = [{'id': 'existing', 'config': {'value': 'untouched'}}]
            path.write_bytes(render(original))
            install(home, 'desktop')
            first = path.read_bytes()
            self.assertEqual(yaml_codec().load(first)[0], original[0])
            install(home, 'desktop')
            self.assertEqual(path.read_bytes(), first)
            self.assertEqual(len(list(profile.glob('*.shell-backup-*'))), 1)

    def test_shared_insert_is_not_overwritten(self):
        with self.assertRaises(ValueError):
            patch_document([{'insert': [{'id': 'local-bridge-shell-safety'}, {'id': 'other'}]}])

    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_shell_policy_fixture(self):
        fixture = Path(__file__).with_name('harness_shell_safety_fixture.mjs')
        result = subprocess.run(['node', str(fixture)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
