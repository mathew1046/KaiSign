import time
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np

INGREDIENTS = {"cheese", "butter", "sugar", "salt"}
ACTIONS = {"more", "less", "double", "without", "add", "no"}
ACTION_PHRASE = {"more": "Extra", "less": "Less", "double": "Double", "without": "No", "no": "No", "add": "Add"}
DISPLAY_WORD = {word: word.capitalize() for word in INGREDIENTS | ACTIONS}
TRAINING_WORDS = ["more", "less", "double", "cheese", "butter", "sugar", "without", "add", "no", "salt"]
FEATURE_WIDTH = 60 * 42 * 3

def order_hands(result) -> np.ndarray:
    frame = np.zeros((42, 3), dtype=np.float32)
    if not result.hand_landmarks:
        return frame
    hands = []
    for landmarks in result.hand_landmarks[:2]:
        pts = np.array([[p.x, p.y, p.z] for p in landmarks], dtype=np.float32)
        hands.append((float(np.mean(pts[:, 0])), pts))
    hands.sort(key=lambda item: item[0])
    for idx, (_, pts) in enumerate(hands[:2]):
        frame[idx * 21 : (idx + 1) * 21] = pts
    return frame

def normalize_frame(frame: np.ndarray) -> np.ndarray:
    out = np.zeros_like(frame, dtype=np.float32)
    for start in (0, 21):
        hand = frame[start : start + 21]
        if not np.any(hand):
            continue
        wrist = hand[0:1]
        centered = hand - wrist
        scale = np.linalg.norm(centered[1:], axis=1).max()
        scale = max(float(scale), 1e-6)
        out[start : start + 21] = centered / scale
    return out

def resample_sequence(seq: np.ndarray, target_len: int = 60) -> np.ndarray:
    valid = seq[np.any(seq != 0, axis=1)]
    if len(valid) == 0: valid = seq[:1]
    if len(valid) == 1: sampled = np.repeat(valid, target_len, axis=0)
    else:
        src = np.linspace(0, len(valid) - 1, target_len); lo = np.floor(src).astype(int); hi = np.minimum(lo + 1, len(valid) - 1)
        w = (src - lo).astype(np.float32)[:, None]; sampled = (1 - w) * valid[lo] + w * valid[hi]
    return sampled.astype(np.float32).reshape(1, -1)

@dataclass
class ScanState:
    scan_id: str | None = None; buffer: list = field(default_factory=list); detected: int = 0
    pending_action: str | None = None; pending_ingredient: str | None = None; paused: bool = False
    last_completed: str | None = None; timestamp_ms: int = 0; last_seen: float = field(default_factory=time.time)
    window_start: float | None = None; last_frame_seq: int = 0; landmarker: object | None = None
    inference_requests: int = 0

    def reset_window(self, now: float | None = None):
        self.buffer.clear(); self.detected = 0; self.window_start = now

    def window_elapsed(self, now: float, buffer_seconds: float) -> bool:
        return self.window_start is not None and (now - self.window_start) >= buffer_seconds

    def close_landmarker(self):
        lm = self.landmarker
        close = getattr(lm, "close", None)
        if callable(close):
            try: close()
            except Exception: pass
        self.landmarker = None

def aggregate_label(state: ScanState, label: str):
    if label in INGREDIENTS: state.pending_ingredient = label
    if label in ACTIONS: state.pending_action = label
    if state.pending_action and state.pending_ingredient:
        phrase = f"{ACTION_PHRASE[state.pending_action]} {state.pending_ingredient}"
        state.paused = True; state.last_completed = phrase
        return phrase
    return None

def confidence_passes(confidence: float, threshold: float) -> bool:
    return confidence > threshold

class InferenceEngine:
    def __init__(self, root: Path, model_path: str, landmarker_path: str, min_prob: float, min_frames: int, buffer_seconds: int):
        self.ready = False; self.error = None; self.model = None; self.landmarker_path = root / landmarker_path; self.model_path = root / model_path
        self.min_prob = min_prob; self.min_frames = min_frames; self.buffer_seconds = buffer_seconds; self.sessions = {}; self.labels = []; self.prediction_windows = 0; self.inference_requests = 0

    def startup(self):
        try:
            import joblib, mediapipe as mp
            from mediapipe.tasks.python import vision
            self.model = joblib.load(self.model_path)
            classes = list(getattr(self.model, "classes_", []))
            if len(classes) != len(TRAINING_WORDS) or set(map(int, classes)) != set(range(len(TRAINING_WORDS))):
                raise ValueError(f"unexpected classes_: {classes}")
            self.labels = TRAINING_WORDS[:]
            n_features = getattr(self.model, "n_features_in_", FEATURE_WIDTH)
            if int(n_features) != FEATURE_WIDTH:
                raise ValueError(f"unexpected feature width {n_features}; expected {FEATURE_WIDTH}")
            if not self.landmarker_path.exists(): raise FileNotFoundError(str(self.landmarker_path))
            self.mp = mp; self.vision = vision; self.ready = True
        except Exception as exc:
            self.ready = False; self.error = f"model_unavailable: {type(exc).__name__}: {exc}"

    def get_state(self, sid):
        now=time.time()
        stale = [k for k, v in self.sessions.items() if now-v.last_seen >= 1800]
        for k in stale:
            self.sessions[k].close_landmarker(); del self.sessions[k]
        return self.sessions.setdefault(sid, ScanState())

    def reset_if_new_scan(self, state, scan_id):
        if state.scan_id != scan_id:
            state.scan_id = scan_id; state.reset_window(None); state.pending_action=None; state.pending_ingredient=None; state.paused=False; state.last_completed=None; state.last_frame_seq=0

    def class_to_label(self, class_id) -> str:
        return TRAINING_WORDS[int(class_id)]
