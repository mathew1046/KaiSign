"""Replay one captured video through the real MediaPipe/runtime model pipeline without persisting media.

Usage: python scripts/replay_video.py [path/to/video.mp4]
If omitted, the first repo dataset mp4 is used.
"""
from pathlib import Path
import os, sys
BACKEND = Path(__file__).resolve().parents[1]; ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
from app.inference import TRAINING_WORDS, order_hands, normalize_frame, resample_sequence, FEATURE_WIDTH, load_runtime_model

def main():
    video = Path(sys.argv[1]) if len(sys.argv) > 1 else next((ROOT / "dataset").glob("*/*.mp4"), None)
    if not video or not video.exists(): raise FileNotFoundError("provide a captured video path")
    model = ROOT / os.getenv("KIOSK_MODEL_PATH", "backend/runtime_assets/logistic_sign_classifier.npz")
    task = ROOT / os.getenv("KIOSK_HAND_LANDMARKER_PATH", "../wlasl_signs_model/hand_landmarker.task")
    import cv2, numpy as np, mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    clf = load_runtime_model(model)
    assert int(getattr(clf, "n_features_in_", FEATURE_WIDTH)) == FEATURE_WIDTH
    opts = vision.HandLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(task)), running_mode=vision.RunningMode.VIDEO, num_hands=2, min_hand_detection_confidence=0.35, min_tracking_confidence=0.35)
    seq = []; ts = 0
    cap = cv2.VideoCapture(str(video)); fps = cap.get(cv2.CAP_PROP_FPS) or 30
    with vision.HandLandmarker.create_from_options(opts) as landmarker:
        while True:
            ok, frame = cap.read()
            if not ok: break
            ts += max(1, int(1000 / fps))
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
            seq.append(normalize_frame(order_hands(res)).reshape(-1))
    cap.release()
    arr = np.asarray(seq, dtype=np.float32)
    detected = int(np.count_nonzero(np.any(arr != 0, axis=1)))
    sample = resample_sequence(arr)
    probs = clf.predict_proba(sample)[0]; idx = int(np.argmax(probs)); label = TRAINING_WORDS[int(clf.classes_[idx])]
    print({"ok": True, "video": str(video), "frames": len(seq), "detected_frames": detected, "label": label, "probability": float(probs[idx])})

if __name__ == "__main__": main()
