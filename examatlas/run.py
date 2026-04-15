"""
run.py
Development entrypoint — runs uvicorn with hot-reload.
"""

import uvicorn
from app.config import get_settings

if __name__ == "__main__":
    s = get_settings()
    uvicorn.run(
        "app.main:app",
        host=s.app_host,
        port=s.app_port,
        reload=(s.app_env == "development"),
        log_level="info",
    )
