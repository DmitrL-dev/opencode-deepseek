"""A configured developer machine must produce the same results as CI."""

import json
import os
import subprocess
import sys
import unittest

from tests import ROOT, configured_keys


class EnvironmentIsolationTests(unittest.TestCase):
    def test_local_dotenv_and_exported_flags_do_not_reach_bridge_config(self):
        """A real .env plus exported overrides must not change server.config."""
        code = (
            "import json, os\n"
            "import tests\n"
            "import server.config as config\n"
            "print(json.dumps({\n"
            "  'deepseek': config.DEEPSEEK_ENABLED,\n"
            "  'qwen': config.QWEN_ENABLED,\n"
            "  'models': sorted(config.MODEL_MAP),\n"
            "  'repair': os.environ.get('TOOLCALL_FORMAT_REPAIR'),\n"
            "  'rate': config.RATE_LIMIT_PER_MINUTE,\n"
            "  'pause': os.environ.get('PROVIDER_ACCESS_PAUSE'),\n"
            "  'leaked': sorted(k for k in tests.configured_keys(\n"
            "      tests.ROOT / '.env.example', tests.ROOT / '.env') if k in os.environ),\n"
            "}))\n"
        )
        hostile = {
            "DEEPSEEK_ENABLED": "1",
            "QWEN_ENABLED": "1",
            "QWEN_TRANSPORT": "browser",
            "GROK_ENABLED": "1",  # flag name is built dynamically in config
            "TOOLCALL_FORMAT_REPAIR": "1",
            "TOOL_INSTRUCTIONS_HTTP": "none",
            "PROVIDER_ACCESS_PAUSE": "0",
            "RATE_LIMIT_PER_MINUTE": "999",
        }
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT,
            env={**os.environ, **hostile}, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertFalse(observed["deepseek"])
        self.assertFalse(observed["qwen"])
        self.assertEqual(observed["models"], [])
        self.assertIsNone(observed["repair"])
        self.assertEqual(observed["rate"], 30)
        self.assertIsNone(observed["pause"])
        self.assertEqual(observed["leaked"], [])

    def test_isolation_covers_every_documented_setting(self):
        """Every key documented for operators is removed before bridge imports."""
        with open(ROOT / ".env.example", encoding="utf-8") as source:
            documented = configured_keys(ROOT / ".env.example")
        self.assertTrue(documented)
        self.assertIn("DEEPSEEK_ENABLED", documented)
        self.assertIn("TOOLCALL_FORMAT_REPAIR", documented)
        # The guard test above proves removal; this only keeps the source of
        # truth (the example file) from silently losing documented settings.
        self.assertEqual({key for key in documented if not key.isupper()}, set())


if __name__ == "__main__":
    unittest.main()
