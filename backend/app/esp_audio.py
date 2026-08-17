import asyncio
import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx

DEFAULT_ESP_AUDIO_STREAM_URL = "http://172.16.162.9/stream"
MAX_WAV_HEADER_BYTES = 4096
PCM_CHUNK_BYTES = 3200
QUEUE_MAX_CHUNKS = 8


class ESPAudioError(ValueError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def esp_audio_stream_url() -> str:
    url = os.getenv("ESP_AUDIO_STREAM_URL", DEFAULT_ESP_AUDIO_STREAM_URL).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment:
        raise ESPAudioError("invalid ESP audio stream URL")
    return url


def parse_wav_header(buffer: bytes) -> tuple[int | None, int]:
    if len(buffer) < 12:
        return None, 0
    if buffer[:4] != b"RIFF" or buffer[8:12] != b"WAVE":
        raise ESPAudioError("unsupported wav header")
    pos = 12
    fmt_ok = False
    while pos + 8 <= len(buffer):
        chunk_id = buffer[pos:pos + 4]
        size = int.from_bytes(buffer[pos + 4:pos + 8], "little")
        start = pos + 8
        end = start + size
        padded_end = end + (size % 2)
        if chunk_id == b"data":
            if not fmt_ok:
                raise ESPAudioError("wav data before fmt")
            return start, size
        if end > len(buffer):
            if len(buffer) > MAX_WAV_HEADER_BYTES:
                raise ESPAudioError("wav header too large")
            return None, 0
        if chunk_id == b"fmt ":
            if size < 16:
                raise ESPAudioError("invalid wav fmt chunk")
            audio_format = int.from_bytes(buffer[start:start + 2], "little")
            channels = int.from_bytes(buffer[start + 2:start + 4], "little")
            rate = int.from_bytes(buffer[start + 4:start + 8], "little")
            byte_rate = int.from_bytes(buffer[start + 8:start + 12], "little")
            block_align = int.from_bytes(buffer[start + 12:start + 14], "little")
            bits = int.from_bytes(buffer[start + 14:start + 16], "little")
            if audio_format != 1 or channels != 1 or rate != 16000 or byte_rate != 32000 or block_align != 2 or bits != 16:
                raise ESPAudioError("unsupported wav format")
            fmt_ok = True
        pos = padded_end
    if len(buffer) > MAX_WAV_HEADER_BYTES:
        raise ESPAudioError("wav header too large")
    return None, 0


async def pcm_chunks_from_wav_stream(byte_chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    pending = b""
    data_start = None
    async for chunk in byte_chunks:
        if not chunk:
            continue
        pending += chunk
        if data_start is None:
            data_start, _ = parse_wav_header(pending)
            if data_start is None:
                continue
            pending = pending[data_start:]
        while len(pending) >= PCM_CHUNK_BYTES:
            yield pending[:PCM_CHUNK_BYTES]
            pending = pending[PCM_CHUNK_BYTES:]
    if data_start is not None and len(pending) > 1:
        if len(pending) % 2:
            pending = pending[:-1]
        yield pending


class ESPAudioSource:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._sink_token: object | None = None
        self._generation = 0
        self._queue: asyncio.Queue[bytes | ESPAudioError] | None = None
        self._reader_task: asyncio.Task | None = None

    async def acquire(self):
        token = object()
        async with self._lock:
            if self._sink_token is not None:
                return None
            self._generation += 1
            self._sink_token = token
            self._queue = asyncio.Queue(maxsize=QUEUE_MAX_CHUNKS)
            if self._reader_task is None or self._reader_task.done():
                self._reader_task = asyncio.create_task(self._reader_loop())
            return token, self._generation, self._queue

    async def release(self, lease):
        token = lease[0] if isinstance(lease, tuple) else lease
        async with self._lock:
            if self._sink_token is token:
                self._sink_token = None
                self._queue = None
                self._generation += 1

    async def owns(self, lease) -> bool:
        if isinstance(lease, tuple):
            token, generation = lease[0], lease[1]
        else:
            token, generation = lease, None
        async with self._lock:
            return self._sink_token is token and (generation is None or self._generation == generation)

    async def _publish(self, item: bytes | ESPAudioError):
        async with self._lock:
            queue = self._queue
        if queue is None:
            return
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def _reader_loop(self):
        backoff = 0.25
        while True:
            async with self._lock:
                has_sink = self._sink_token is not None
            if not has_sink:
                await asyncio.sleep(0.05)
                continue
            try:
                url = esp_audio_stream_url()
                timeout = httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=3.0)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        saw_pcm = False
                        async for pcm in pcm_chunks_from_wav_stream(response.aiter_bytes()):
                            saw_pcm = True
                            backoff = 0.25
                            await self._publish(pcm)
                        if not saw_pcm:
                            raise ESPAudioError("esp stream ended before audio", retryable=True)
            except ESPAudioError as exc:
                if not exc.retryable:
                    await self._publish(exc)
                    await asyncio.sleep(1.0)
                    continue
            except (httpx.HTTPError, OSError):
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)


async def read_esp_pcm_chunks(stop: asyncio.Event, source: ESPAudioSource, lease) -> AsyncIterator[bytes]:
    _, _, queue = lease
    while not stop.is_set() and await source.owns(lease):
        item = await queue.get()
        if isinstance(item, ESPAudioError):
            raise item
        if len(item) % 2:
            item = item[:-1]
        if item:
            yield item


esp_audio_source = ESPAudioSource()
