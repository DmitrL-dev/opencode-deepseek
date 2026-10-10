"""Entry point — run the OpenAI-compatible DeepSeek/Qwen server.

    python app.py            # serves on http://0.0.0.0:8000 (localhost + LAN IP)

On first use (no saved session) a browser window opens for you to sign in by
hand; run `python -m deepseek.auth` to do that ahead of time. Set
DEEPSEEK_PROFILE_DIR to reuse an existing signed-in Chrome profile.
"""

import os

import uvicorn
from settings import load_environment

load_environment()

if __name__ == "__main__":
    uvicorn.run(
        "server.api:app",
        # 0.0.0.0: localhost and this machine's LAN IP. Set HOST=127.0.0.1 to
        # keep the API loopback-only (recommended on untrusted networks).
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
        proxy_headers=False,
    )
