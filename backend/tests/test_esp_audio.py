import pytest

from app.esp_audio import ESPAudioSource, esp_audio_stream_url, parse_wav_header, pcm_chunks_from_wav_stream


def wav_header(fmt_extra=b"", prefix_chunks=b""):
    fmt_size = 16 + len(fmt_extra)
    fmt = b"fmt " + fmt_size.to_bytes(4, "little") + b"\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00" + fmt_extra
    data = b"data" + (0xFFFFFFFF).to_bytes(4, "little")
    return b"RIFF" + (0xFFFFFFFF).to_bytes(4, "little") + b"WAVE" + prefix_chunks + fmt + data


def test_parse_standard_streaming_wav_header():
    header = wav_header()

    data_start, data_size = parse_wav_header(header)

    assert data_start == len(header)
    assert data_size == 0xFFFFFFFF


def test_parse_extended_and_arbitrary_chunks():
    junk = b"JUNK" + (3).to_bytes(4, "little") + b"abc" + b"\x00"
    header = wav_header(fmt_extra=b"\x00\x00", prefix_chunks=junk)

    data_start, _ = parse_wav_header(header[:10] + header[10:])

    assert data_start == len(header)


def test_parse_rejects_unsupported_format():
    bad = bytearray(wav_header())
    bad[20:22] = b"\x03\x00"

    with pytest.raises(ValueError):
        parse_wav_header(bytes(bad))


def test_parse_rejects_bad_block_align_and_byte_rate():
    bad_align = bytearray(wav_header())
    bad_align[32:34] = b"\x04\x00"
    with pytest.raises(ValueError):
        parse_wav_header(bytes(bad_align))
    bad_rate = bytearray(wav_header())
    bad_rate[28:32] = (44100).to_bytes(4, "little")
    with pytest.raises(ValueError):
        parse_wav_header(bytes(bad_rate))


def test_parse_rejects_malformed_header():
    with pytest.raises(ValueError):
        parse_wav_header(b"not a wav header")


@pytest.mark.asyncio
async def test_pcm_chunks_from_arbitrary_boundaries():
    payload = b"\x01\x02" * 2000
    chunks = [wav_header()[:5], wav_header()[5:] + payload[:17], payload[17:3300], payload[3300:]]

    async def source():
        for chunk in chunks:
            yield chunk

    out = [chunk async for chunk in pcm_chunks_from_wav_stream(source())]

    assert b"".join(out) == payload
    assert all(len(chunk) <= 3200 for chunk in out)


@pytest.mark.asyncio
async def test_pcm_chunks_discard_odd_trailing_byte():
    payload = b"\x01\x02\x03"

    async def source():
        yield wav_header() + payload

    out = [chunk async for chunk in pcm_chunks_from_wav_stream(source())]

    assert b"".join(out) == b"\x01\x02"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_source_generation_invalidates_stale_lease():
    source = ESPAudioSource()
    first = await source.acquire()
    assert await source.owns(first)
    await source.release(first)
    second = await source.acquire()

    assert not await source.owns(first)
    assert await source.owns(second)
    await source.release(second)


@pytest.mark.asyncio
async def test_source_queue_drops_oldest_when_bounded():
    source = ESPAudioSource()
    lease = await source.acquire()
    assert lease is not None
    queue = lease[2]

    for index in range(10):
        await source._publish(bytes([index]))

    assert queue.qsize() == 8
    assert await queue.get() == b"\x02"
    await source.release(lease)


def test_esp_audio_stream_url_validation(monkeypatch):
    monkeypatch.setenv("ESP_AUDIO_STREAM_URL", "http://example.test/stream")
    assert esp_audio_stream_url() == "http://example.test/stream"
    monkeypatch.setenv("ESP_AUDIO_STREAM_URL", "http://user@example.test/stream")
    with pytest.raises(ValueError):
        esp_audio_stream_url()
    monkeypatch.setenv("ESP_AUDIO_STREAM_URL", "http://example.test/stream?x=1")
    with pytest.raises(ValueError):
        esp_audio_stream_url()
