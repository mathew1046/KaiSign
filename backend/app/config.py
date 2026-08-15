import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def load_settings():
    load_dotenv(BACKEND_ROOT / ".env")
    if not os.getenv("GEMINI_API_KEY"):
        load_dotenv(ROOT / ".env", override=False)


def gemini_api_key_loaded() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def gemini_model() -> str:
    return os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
