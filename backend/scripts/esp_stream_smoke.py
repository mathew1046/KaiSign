import asyncio
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.esp_audio import ESP_AUDIO_PACKET_BYTES, esp_audio_bind_config


async def main():
    host, port = esp_audio_bind_config()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        sock.bind((host, port))
    except OSError as exc:
        print(f"ESP UDP smoke: failed to bind {host}:{port} ({exc})")
        return 1

    loop = asyncio.get_running_loop()
    print(f"ESP UDP smoke: listening on {host}:{port} for one {ESP_AUDIO_PACKET_BYTES}-byte PCM datagram")
    try:
        while True:
            packet, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, ESP_AUDIO_PACKET_BYTES + 1), timeout=10)
            if len(packet) == ESP_AUDIO_PACKET_BYTES:
                print(f"ESP UDP smoke: success (16 kHz mono signed 16-bit little-endian PCM from {addr[0]}:{addr[1]})")
                return 0
            print(f"ESP UDP smoke: ignored malformed datagram ({len(packet)} bytes)")
    except TimeoutError:
        print("ESP UDP smoke: failed (no valid UDP PCM datagram within 10s)")
        return 1
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
