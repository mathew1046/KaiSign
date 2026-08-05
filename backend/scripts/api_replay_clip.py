"""Replay one 8-12 frame JPEG clip through /api/infer; one request == one KNN window."""
from pathlib import Path
import base64, sys, uuid
BACKEND = Path(__file__).resolve().parents[1]; ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

def main():
    import cv2
    from fastapi.testclient import TestClient
    from app.main import app, engine
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else next((ROOT / "dataset").glob("*/*.mp4"), None)
    cap = cv2.VideoCapture(str(video)); frames = []
    while len(frames) < 10:
        ok, frame = cap.read()
        if not ok: break
        ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        if ok: frames.append("data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode("ascii"))
    cap.release()
    before_req, before_pred = engine.inference_requests, engine.prediction_windows
    with TestClient(app) as client:
        r = client.post("/api/infer", json={"scan_id": str(uuid.uuid4()), "clip_seq": 1, "item": {"id": "burger", "quantity": 1}, "frames": frames})
        print({"status_code": r.status_code, "response": r.json(), "request_delta": engine.inference_requests - before_req, "prediction_delta": engine.prediction_windows - before_pred})
        raise SystemExit(0 if r.status_code == 200 and engine.inference_requests - before_req == 1 else 1)

if __name__ == "__main__": main()
