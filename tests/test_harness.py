import json
from pathlib import Path
import tempfile
import unittest
import warnings
from unittest.mock import patch

import httpx
from ruamel.yaml.error import ReusedAnchorWarning, YAMLError

from integrations import harness


CATALOG = {"data": [
    {"id": "qwen3.8-omni-flash", "owned_by": "qwen"},
    {"id": "glm-web", "owned_by": "glm"},
]}
BASE = "http://127.0.0.1:8000/v1"


class HarnessSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.directory = self.home / "profiles" / "desktop"
        self.directory.mkdir(parents=True)
        self.path = self.directory / "cordis.patch.yml"
        self.path.write_text("# my settings\n[]\n")
        self.manifest = self.directory / "package.json"
        self.manifest.write_text(json.dumps({"dsh": {"profile": {"bundles": [
            "@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]}}}))
        self.models = harness.catalog_models(CATALOG)

    def test_discovery_only_reads_catalog_without_proxy_or_redirects(self):
        requests = []
        real_client = httpx.Client

        def respond(request):
            requests.append((request.method, str(request.url)))
            return httpx.Response(200, json=CATALOG)

        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            return real_client(transport=httpx.MockTransport(respond), **kwargs)

        with patch.object(harness.httpx, "Client", side_effect=client):
            self.assertEqual(harness.discover(BASE), self.models)
        self.assertEqual(requests, [("GET", BASE + "/models")])

    def test_remote_credentialed_and_ambiguous_endpoints_are_rejected(self):
        for url in ("https://example.com/v1", "http://localhost:8000/v1", "http://127.0.0.1.evil/v1",
                    "http://user:secret@127.0.0.1/v1", BASE + "?key=x", BASE + "#fragment",
                    "http://127.0.0.1:0/v1", "http://127.0.0.1:bad/v1", "http://127.0.0.1/v2"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                harness.local_url(url)
        self.assertEqual(harness.local_url("http://[::1]:8000/v1/"), "http://[::1]:8000/v1")

    def test_catalog_never_invents_disabled_models_and_rejects_invalid_identities(self):
        self.assertEqual([model["id"] for model in self.models], ["qwen3.8-omni-flash", "glm-web"])
        for catalog in ({"data": []}, {"data": CATALOG["data"] * 2}, {"data": [None]},
                        {"data": [{"id": "x\n- injected", "owned_by": "qwen"}]},
                        {"data": [{"id": "x", "owned_by": "unexpected"}]}):
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                harness.catalog_models(catalog)

    def test_install_preserves_unrelated_routes_comments_and_js_without_execution(self):
        original = """# Keep this comment
- id: llm-pi-ai
  config:
    providers:
      other:
        apiKeyEnv: EXISTING_KEY
        models: [{id: existing}]
- id: unrelated
  disabled: !!js 'process.env.SOME_FLAG'
"""
        self.path.write_text(original)
        before = harness.yaml_codec().load(original)
        path, backup = harness.install(self.home, "desktop", BASE, self.models, "glm-web")
        self.assertEqual(backup.read_text(), original)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        after = harness.yaml_codec().load(path)
        self.assertEqual(after[0]["config"]["providers"]["other"], before[0]["config"]["providers"]["other"])
        self.assertIn("Keep this comment", path.read_text())
        self.assertIn("!!js", path.read_text())
        route = after[0]["config"]["providers"][harness.ROUTE]
        self.assertEqual(route["retryPolicy"]["maxRetries"], 0)
        self.assertEqual(route["models"], self.models)
        self.assertTrue(next(row for row in after if row["id"] == "session-title-llm")["disabled"])
        self.assertEqual(after[-1]["config"], {"provider": harness.ROUTE, "model": "glm-web"})
        repeated = path.read_bytes()
        self.assertIsNone(harness.install(self.home, "desktop", BASE, self.models, "glm-web")[1])
        self.assertEqual(path.read_bytes(), repeated)

    def test_no_implicit_default_or_account_state_changes(self):
        pause = self.home / "provider-pauses.json"
        pause.write_text('{"deepseek":"account_restricted"}')
        before = pause.read_bytes()
        harness.install(self.home, "desktop", BASE, self.models)
        rows = harness.yaml_codec().load(self.path)
        self.assertNotIn("agent-default-model", [row["id"] for row in rows])
        self.assertEqual(pause.read_bytes(), before)
        self.assertFalse((self.home / ".credentials.yaml").exists())

    def test_shared_yaml_anchors_do_not_change_other_rows(self):
        document = harness.yaml_codec().load('''
- id: other
  config: &shared
    providers:
      a: &route {apiKeyEnv: EXISTING_KEY}
      b: *route
- id: another
  config: *shared
- id: llm-pi-ai
  config: *shared
''')
        result = harness.patch_document(document, BASE, self.models)
        with warnings.catch_warnings():
            warnings.simplefilter("error", ReusedAnchorWarning)
            restored = harness.yaml_codec().load(harness.render(result))
        self.assertEqual(restored[0]["config"], document[0]["config"])
        self.assertEqual(restored[1]["config"], document[1]["config"])
        self.assertIn(harness.ROUTE, restored[2]["config"]["providers"])

    def test_custom_layers_are_refused_before_writing(self):
        original = self.path.read_bytes()
        for file in (self.home / "cordis.patch.yml", self.directory / "cordis.yml"):
            file.write_text("- id: llm-pi-ai\n  disabled: true\n")
            with self.assertRaises(ValueError):
                harness.install(self.home, "desktop", BASE, self.models)
            self.assertEqual(self.path.read_bytes(), original)
            file.unlink()
        self.manifest.write_text('{"dsh":{"profile":{"bundles":["custom"]}}}')
        with self.assertRaises(ValueError):
            harness.install(self.home, "desktop", BASE, self.models)
        self.assertEqual(self.path.read_bytes(), original)

    def test_ambiguous_or_dynamic_edits_and_unadvertised_defaults_are_refused(self):
        documents = [[{"insert": [{"id": "llm-pi-ai"}]}],
                     [{"id": "llm-pi-ai"}, {"id": "llm-pi-ai"}],
                     [{"id": "llm-pi-ai", "disabled": True}],
                     harness.yaml_codec().load('- id: llm-pi-ai\n  config: !!js "dynamic()"'),
                     [{"id": "llm-pi-ai", "config": {"providers": "dynamic"}}]]
        for document in documents:
            original = harness.render(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                harness.patch_document(document, BASE, self.models)
            self.assertEqual(harness.render(document), original)
        with self.assertRaises(ValueError):
            harness.patch_document([], BASE, self.models, "deepseek-chat")

    def test_duplicate_yaml_keys_and_symlinks_cannot_silently_replace_config(self):
        self.path.write_text('- id: llm-pi-ai\n  config: {}\n  config: {}\n')
        original = self.path.read_bytes()
        with self.assertRaises(YAMLError):
            harness.install(self.home, "desktop", BASE, self.models)
        self.assertEqual(self.path.read_bytes(), original)
        link = self.directory / "link.yml"
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            harness.write_private(link, b"[]\n", original)
        self.assertEqual(self.path.read_bytes(), original)

    def test_concurrent_edit_is_retained(self):
        before = self.path.read_bytes()
        self.path.write_text("# a newer edit\n[]\n")
        with self.assertRaises(ValueError):
            harness.write_private(self.path, b"[]\n", before)
        self.assertIn("newer edit", self.path.read_text())
