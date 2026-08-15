import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import gemini_api_key_loaded, gemini_model, load_settings


async def main():
    load_settings()
    if not gemini_api_key_loaded():
        print("Gemini Live smoke: skipped (key not configured)")
        return 0
    try:
        from google import genai
        from google.genai import types
        import os
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        config = types.LiveConnectConfig(response_modalities=["AUDIO"])
        async with client.aio.live.connect(model=gemini_model(), config=config):
            print("Gemini Live smoke: success")
        return 0
    except ImportError:
        print("Gemini Live smoke: failed (sdk unavailable)")
        return 1
    except Exception:
        print("Gemini Live smoke: failed (provider/connectivity/config)")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
