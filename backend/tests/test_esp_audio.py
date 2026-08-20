import asyncio
import socket

import pytest

from app.esp_audio import (
    DEFAULT_ESP_AUDIO_UDP_HOST,
    DEFAULT_ESP_AUDIO_UDP_PORT,
    ESP_AUDIO_PACKET_BYTES,
    ESPAudioError,
    ESPAudioSource,
    _is_valid_pcm_datagram,
    esp_audio_bind_config,
    read_esp_pcm_chunks,
)


@pytest.fixture(autouse=True)
def use_ephemeral_udp_port(monkeypatch):
    monkeypatch.setenv("ESP_AUDIO_UDP_HOST", "127.0.0.1")
    monkeypatch.setenv("ESP_AUDIO_UDP_PORT", "0")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_esp_audio_udp_config_defaults(monkeypatch):
    monkeypatch.delenv("ESP_AUDIO_UDP_HOST", raising=False)
    monkeypatch.delenv("ESP_AUDIO_UDP_PORT", raising=False)

    assert esp_audio_bind_config() == (DEFAULT_ESP_AUDIO_UDP_HOST, DEFAULT_ESP_AUDIO_UDP_PORT)


def test_esp_audio_udp_config_overrides_and_validation(monkeypatch):
    monkeypatch.setenv("ESP_AUDIO_UDP_HOST", "127.0.0.1")
    monkeypatch.setenv("ESP_AUDIO_UDP_PORT", "23456")
    assert esp_audio_bind_config() == ("127.0.0.1", 23456)

    monkeypatch.setenv("ESP_AUDIO_UDP_HOST", " ")
    with pytest.raises(ESPAudioError):
        esp_audio_bind_config()

    monkeypatch.setenv("ESP_AUDIO_UDP_HOST", "127.0.0.1")
    monkeypatch.setenv("ESP_AUDIO_UDP_PORT", "65536")
    with pytest.raises(ESPAudioError):
        esp_audio_bind_config()

    monkeypatch.setenv("ESP_AUDIO_UDP_PORT", "not-a-port")
    with pytest.raises(ESPAudioError):
        esp_audio_bind_config()


def test_pcm_datagram_acceptance_rejection():
    assert _is_valid_pcm_datagram(b"\x00" * ESP_AUDIO_PACKET_BYTES)
    assert not _is_valid_pcm_datagram(b"\x00" * (ESP_AUDIO_PACKET_BYTES - 1))
    assert not _is_valid_pcm_datagram(b"\x00" * (ESP_AUDIO_PACKET_BYTES + 1))


@pytest.mark.anyio
async def test_read_chunks_yields_only_valid_pcm_datagrams():
    source = ESPAudioSource()
    lease = await source.acquire()
    assert lease is not None
    await source._publish(b"bad")
    good = b"\x12\x34" * (ESP_AUDIO_PACKET_BYTES // 2)
    await source._publish(good)

    stop = asyncio.Event()
    chunk = await asyncio.wait_for(read_esp_pcm_chunks(stop, source, lease).__anext__(), timeout=1)

    assert chunk == good
    await source.release(lease)


@pytest.mark.anyio
async def test_latest_packet_wins_when_internal_queue_has_backlog():
    source = ESPAudioSource()
    lease = await source.acquire()
    assert lease is not None
    queue = lease[2]

    first = b"\x01" * ESP_AUDIO_PACKET_BYTES
    second = b"\x02" * ESP_AUDIO_PACKET_BYTES
    await source._publish(first)
    await source._publish(second)

    assert queue.qsize() == 1
    assert await queue.get() == second
    await source.release(lease)


@pytest.mark.anyio
async def test_latest_packet_wins_when_socket_has_backlog(monkeypatch):
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.bind(("127.0.0.1", 0))
    recv_sock.setblocking(False)
    host, port = recv_sock.getsockname()
    monkeypatch.setattr(ESPAudioSource, "_open_socket", lambda self: recv_sock)

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    source = ESPAudioSource()
    lease = await source.acquire()
    assert lease is not None
    try:
        sender.sendto(b"x", (host, port))
        sender.sendto(b"\x01" * ESP_AUDIO_PACKET_BYTES, (host, port))
        sender.sendto(b"\x02" * ESP_AUDIO_PACKET_BYTES, (host, port))

        chunk = await asyncio.wait_for(lease[2].get(), timeout=1)
        assert chunk == b"\x02" * ESP_AUDIO_PACKET_BYTES
        await asyncio.sleep(0)
        assert lease[2].empty()
    finally:
        sender.close()
        await source.release(lease)


@pytest.mark.anyio
async def test_esp_audio_source_lease_is_exclusive_and_token_safe():
    source = ESPAudioSource()
    first = await source.acquire()
    assert first
    assert await source.acquire() is None
    await source.release(object())
    assert await source.acquire() is None
    await source.release(first)
    second = await source.acquire()
    assert second
    await source.release(first)
    assert await source.acquire() is None
    await source.release(second)


@pytest.mark.anyio
async def test_source_generation_invalidates_stale_lease():
    source = ESPAudioSource()
    first = await source.acquire()
    assert await source.owns(first)
    await source.release(first)
    second = await source.acquire()

    assert not await source.owns(first)
    assert await source.owns(second)
    await source.release(second)
