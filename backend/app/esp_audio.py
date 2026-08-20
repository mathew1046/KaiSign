import asyncio
import os
import socket
from collections.abc import AsyncIterator

DEFAULT_ESP_AUDIO_UDP_HOST = "0.0.0.0"
DEFAULT_ESP_AUDIO_UDP_PORT = 12345
ESP_AUDIO_PACKET_BYTES = 512
QUEUE_MAX_CHUNKS = 1


class ESPAudioError(ValueError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def esp_audio_bind_config() -> tuple[str, int]:
    host = os.getenv("ESP_AUDIO_UDP_HOST", DEFAULT_ESP_AUDIO_UDP_HOST).strip()
    port_text = os.getenv("ESP_AUDIO_UDP_PORT", str(DEFAULT_ESP_AUDIO_UDP_PORT)).strip()
    if not host:
        raise ESPAudioError("invalid ESP audio UDP host")
    try:
        port = int(port_text, 10)
    except ValueError as exc:
        raise ESPAudioError("invalid ESP audio UDP port") from exc
    if not 0 <= port <= 65535:
        raise ESPAudioError("invalid ESP audio UDP port")
    return host, port


def _is_valid_pcm_datagram(packet: bytes) -> bool:
    return len(packet) == ESP_AUDIO_PACKET_BYTES


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
        task: asyncio.Task | None = None
        async with self._lock:
            if self._sink_token is token:
                self._sink_token = None
                self._queue = None
                self._generation += 1
                task = self._reader_task
                self._reader_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

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
        while queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass

    async def _wait_readable(self, sock: socket.socket):
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def ready():
            if not future.done():
                future.set_result(None)

        loop.add_reader(sock.fileno(), ready)
        try:
            await future
        finally:
            loop.remove_reader(sock.fileno())

    def _open_socket(self) -> socket.socket:
        host, port = esp_audio_bind_config()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setblocking(False)
            sock.bind((host, port))
        except Exception:
            sock.close()
            raise
        return sock

    async def _reader_loop(self):
        sock: socket.socket | None = None
        try:
            sock = self._open_socket()
            while True:
                async with self._lock:
                    has_sink = self._sink_token is not None
                if not has_sink:
                    return
                await self._wait_readable(sock)
                newest: bytes | None = None
                while True:
                    try:
                        packet, _ = sock.recvfrom(ESP_AUDIO_PACKET_BYTES + 1)
                    except BlockingIOError:
                        break
                    if _is_valid_pcm_datagram(packet):
                        newest = packet
                if newest is not None:
                    await self._publish(newest)
        except asyncio.CancelledError:
            raise
        except (OSError, ESPAudioError) as exc:
            await self._publish(ESPAudioError(str(exc), retryable=isinstance(exc, OSError)))
        finally:
            if sock is not None:
                sock.close()


async def read_esp_pcm_chunks(stop: asyncio.Event, source: ESPAudioSource, lease) -> AsyncIterator[bytes]:
    _, _, queue = lease
    while not stop.is_set() and await source.owns(lease):
        item = await queue.get()
        if isinstance(item, ESPAudioError):
            raise item
        if _is_valid_pcm_datagram(item):
            yield item


esp_audio_source = ESPAudioSource()
