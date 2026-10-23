"""Official profile setup: local-only, preserving unrelated state and secrets."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from integrations import harness_official as h
from integrations.harness import render, yaml_codec


class OfficialInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'dsh'
        self.home.mkdir()
        self.original = [
            {'id': 'llm-pi-ai', 'config': {'providers': {'local-browser-bridge': {'apiKey': 'SECRET_KEEP'}}}},
            {'id': 'agent-default-model', 'config': {'provider': 'local-browser-bridge', 'model': 'deepseek-expert', 'custom': 7}},
            {'id': 'llm-deepseek', 'config': {'custom': 'keep', 'retryPolicy': {'mode': 'normal', 'maxRetries': 4, 'custom': 9}}},
            {'insert': [{'id': 'local-bridge-shell-safety', 'name': '/owned/plugin.mjs'}]},
        ]
        for profile in h.PROFILES:
            d = self.home / 'profiles' / profile
            d.mkdir(parents=True)
            bundles = ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-' + ('headless' if profile == 'headless' else 'web-app')]
            (d / 'package.json').write_text(json.dumps({'dsh': {'profile': {'bundles': bundles}}}))
            (d / 'cordis.patch.yml').write_bytes(render(self.original))
        for name in ('credentials.yml', 'sessions/history', 'provider-pauses.json', 'budget.json'):
            p = self.home / name
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(b'PRIVATE_UNCHANGED')
        self.profile = self.home / 'profiles/desktop/cordis.patch.yml'

    def snapshot(self):
        return {str(p.relative_to(self.home)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                p.stat().st_mtime_ns, p.stat().st_mode) for p in self.home.rglob('*') if p.is_file()}

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                h.main(['--install', 'desktop', '--home', str(self.home), *args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue() + err.getvalue()

    def test_standard_profiles_merge_preserve_and_keep_private_backups(self):
        for profile in h.PROFILES:
            with self.subTest(profile=profile):
                p = self.home / 'profiles' / profile / 'cordis.patch.yml'
                before = p.read_bytes()
                protected = {n: (self.home/n).read_bytes() for n in ('credentials.yml', 'sessions/history', 'provider-pauses.json', 'budget.json')}
                path, backup = h.install(self.home, profile)
                self.assertEqual(path, p)
                self.assertEqual(backup.read_bytes(), before)
                if os.name != 'nt':
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                doc = yaml_codec().load(path.read_text())
                self.assertEqual(doc[0:2], self.original[0:2])
                self.assertEqual(doc[3], self.original[3])
                native = doc[2]['config']
                self.assertEqual(native['custom'], 'keep')
                self.assertEqual(native['retryPolicy'], {'mode': 'normal', 'maxRetries': 0, 'custom': 9})
                self.assertEqual(native['maxTokens'], 32768)
                self.assertEqual(native['baseURL'], h.BASE_URL)
                self.assertTrue(next(r for r in doc if r.get('id') == 'session-title-llm')['disabled'])
                self.assertFalse(next(r for r in doc if r.get('id') == 'session-log-deepseek')['config']['enabled'])
                self.assertEqual(protected, {n:(self.home/n).read_bytes() for n in protected})

    def test_default_requires_explicit_selection(self):
        h.install(self.home, 'desktop', 'deepseek-flash')
        doc = yaml_codec().load(self.profile.read_text())
        self.assertEqual(doc[1]['config'], {'provider': h.ROUTE, 'model': 'deepseek-flash', 'custom': 7})

    def test_import_does_not_load_provider_settings_or_credentials(self):
        code = ('import sys; import integrations.harness_official; '
                'assert "settings" not in sys.modules; '
                'assert "providers.access" not in sys.modules')
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                cwd=Path(__file__).resolve().parents[1], timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_empty_profile_does_not_add_a_default_implicitly(self):
        self.profile.write_bytes(b'[]\n')
        h.install(self.home, 'desktop')
        self.assertNotIn('agent-default-model', [r['id'] for r in yaml_codec().load(self.profile.read_text())])

    def test_native_entry_without_config_is_populated(self):
        self.profile.write_text('- id: llm-deepseek\n')
        h.install(self.home, 'desktop')
        self.assertEqual(yaml_codec().load(self.profile.read_text())[0]['config']['maxTokens'], 32768)

    def test_idempotent_and_dry_run_do_not_touch_files(self):
        before = self.snapshot()
        self.assertIsNone(h.install(self.home, 'desktop', dry_run=True)[1])
        self.assertEqual(before, self.snapshot())
        h.install(self.home, 'desktop')
        before = self.snapshot()
        self.assertIsNone(h.install(self.home, 'desktop')[1])
        self.assertEqual(before, self.snapshot())
        h.install(self.home, 'desktop', 'deepseek-flash', dry_run=True)
        self.assertEqual(before, self.snapshot())

    def test_cli_respects_dsh_home_and_never_reports_credentials(self):
        out = io.StringIO()
        with patch.dict(os.environ, {'DSH_HOME': str(self.home)}), contextlib.redirect_stdout(out):
            h.main(['--install', 'desktop', '--dry-run'])
        self.assertIn(str(self.profile), out.getvalue())
        self.assertNotIn('SECRET_KEEP', out.getvalue())
        self.assertFalse(list(self.home.glob('.bridge-install-*')))

    def test_rejects_ambiguous_and_dynamic_yaml_without_leaking_or_writing(self):
        samples = [
            '- id: llm-deepseek\n- id: llm-deepseek\n',
            '- id: llm-deepseek\n  config: !!js SECRET_KEEP\n',
            '- id: llm-deepseek\n  config: &shared {key: SECRET_KEEP}\n- id: other\n  config: *shared\n',
            '- id: x\n  config: &base {key: SECRET_KEEP}\n- id: llm-deepseek\n  config: {<<: *base}\n',
            '- id: llm-deepseek\n  config: [SECRET_KEEP\n',
            '- id: llm-deepseek\n  config: {a: 1, a: SECRET_KEEP}\n',
            '- id: llm-deepseek\n  disabled: true\n',
            '- id: llm-deepseek\n  config: {retryPolicy: SECRET_KEEP}\n',
            '- insert: [{id: llm-deepseek}]\n',
            '- insert: [{id: x, insert: [{id: llm-deepseek}]}]\n',
            '- remove: llm-deepseek\n',
            '- id: [SECRET_KEEP]\n',
            '- id: llm-deepseek\n  name: SECRET_KEEP\n',
            '- id: x\n  config: &same {key: SECRET_KEEP}\n- id: y\n  config: &same {key: SECRET_KEEP}\n',
        ]
        for raw in samples:
            with self.subTest(raw=raw):
                self.profile.write_text(raw)
                before = self.snapshot()
                code, text = self.cli()
                self.assertEqual(code, 1, text)
                self.assertNotIn('SECRET_KEEP', text)
                self.assertEqual(before, self.snapshot())

    def test_missing_profile_and_nonempty_override_are_refused(self):
        (self.home/'cordis.patch.yml').write_text('- id: llm-deepseek\n  config: {secret: SECRET_KEEP}\n')
        before = self.snapshot()
        self.assertEqual(self.cli()[0], 1)
        self.assertEqual(before, self.snapshot())
        (self.home/'cordis.patch.yml').unlink()
        self.profile.unlink()
        before = self.snapshot()
        self.assertEqual(self.cli()[0], 1)
        self.assertEqual(before, self.snapshot())

    def test_custom_manifest_is_refused(self):
        p = self.profile.with_name('package.json')
        p.write_text('{"dsh":{"profile":{"bundles":["custom"]}}}')
        before = self.snapshot()
        self.assertEqual(self.cli()[0], 1)
        self.assertEqual(before, self.snapshot())

    @unittest.skipIf(os.name == 'nt', 'requires local symlink privileges')
    def test_symlink_parents_files_and_locks_are_refused(self):
        alias = self.root/'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            h.install(alias/'dsh', 'desktop')
        target = self.root/'outside'
        target.write_bytes(b'OUTSIDE')
        for path in (self.profile, self.profile.with_name('package.json'),
                     self.home/'cordis.patch.yml', self.profile.with_name('cordis.yml'),
                     self.home/'.bridge-install-desktop.lock'):
            before = path.read_bytes() if path.exists() else None
            if path.exists():path.unlink()
            path.symlink_to(target)
            with self.subTest(path=path), self.assertRaises(ValueError):
                h.install(self.home, 'desktop')
            path.unlink()
            if before is not None:path.write_bytes(before)
            self.assertEqual(target.read_bytes(), b'OUTSIDE')
        self.profile.unlink()
        self.profile.symlink_to(self.root/'missing')
        self.assertEqual(self.cli()[0], 1)
        self.assertFalse((self.root/'missing').exists())

    def test_hardlinked_lock_is_not_chmodded(self):
        target = self.root/'outside-lock'
        target.write_bytes(b'OUTSIDE')
        os.chmod(target, 0o640)
        os.link(target, self.home/'.bridge-install-desktop.lock')
        before = target.stat().st_mode
        self.assertEqual(self.cli()[0], 1)
        self.assertEqual(target.stat().st_mode, before)
        self.assertEqual(target.read_bytes(), b'OUTSIDE')

    def test_failed_replace_retains_original_and_private_backup(self):
        before = self.profile.read_bytes()
        with patch('integrations.harness.os.replace', side_effect=OSError('owned fixture failure')):
            self.assertEqual(self.cli()[0], 1)
        self.assertEqual(self.profile.read_bytes(), before)
        backups = list(self.profile.parent.glob('*.bridge-backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertFalse(list(self.profile.parent.glob('.bridge-*')))


if __name__ == '__main__':
    unittest.main()
