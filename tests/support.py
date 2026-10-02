from unittest.mock import patch

from providers.access import AccessGuard
from server import api
from server.config import DEEPSEEK_MODEL_MAP, QWEN_MODEL_MAP


def isolated_access(case):
    """Legacy transport fixtures explicitly enable models; never touch real state."""
    for patcher in (patch.object(api, "guard", AccessGuard(path=None, interval=0)),
                    patch.dict(api.MODEL_MAP, {**DEEPSEEK_MODEL_MAP, **QWEN_MODEL_MAP}, clear=True)):
        patcher.start()
        case.addCleanup(patcher.stop)
