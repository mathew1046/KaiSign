import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.esp_audio import esp_audio_stream_url, parse_wav_header


async def main():
    try:
        url = esp_audio_stream_url()
        timeout = httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                header = b""
                async for chunk in response.aiter_bytes():
                    header += chunk
                    data_start, _ = parse_wav_header(header)
                    if data_start is not None:
                        print("ESP stream smoke: success (PCM mono 16000 Hz 16-bit WAV)")
                        return 0
                    if len(header) > 4096:
                        raise ValueError("header too large")
        print("ESP stream smoke: failed (no WAV data)")
        return 1
    except Exception:
        print("ESP stream smoke: failed (stream unavailable or unsupported format)")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
