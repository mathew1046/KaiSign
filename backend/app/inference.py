import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
import numpy as np

INGREDIENTS = {"cheese", "butter", "sugar", "salt"}
ACTIONS = {"more", "less", "double", "without", "add", "no"}
ACTION_PHRASE = {"more": "Extra", "less": "Less", "double": "Double", "without": "No", "no": "No", "add": "Add"}
DISPLAY_WORD = {word: word.capitalize() for word in INGREDIENTS | ACTIONS}
TRAINING_WORDS = ["more", "less", "double", "cheese", "butter", "sugar", "without", "add", "no", "salt"]
FEATURE_WIDTH = 60 * 42 * 3
SUPPORTED_NPZ_FORMAT_VERSIONS = {"1", "1.0"}

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
    window_start: float | None = None; last_frame_seq: int = 0
    inference_requests: int = 0

    def reset_window(self, now: float | None = None):
        self.buffer.clear(); self.detected = 0; self.window_start = now

    def window_elapsed(self, now: float, buffer_seconds: float) -> bool:
        return self.window_start is not None and (now - self.window_start) >= buffer_seconds

class RuntimeLogisticModel:
    def __init__(self, path: Path):
        data = np.load(path, allow_pickle=False)
        required = {"format_version", "feature_width", "mean", "scale", "coef", "intercept", "classes"}
        missing = required - set(data.files)
        if missing: raise ValueError(f"missing npz keys: {sorted(missing)}")
        self.format_version = str(np.asarray(data["format_version"]).item())
        if self.format_version not in SUPPORTED_NPZ_FORMAT_VERSIONS: raise ValueError(f"unsupported npz format_version: {self.format_version}")
        self.n_features_in_ = int(np.asarray(data["feature_width"]).item())
        if self.n_features_in_ != FEATURE_WIDTH: raise ValueError(f"unexpected feature width {self.n_features_in_}; expected {FEATURE_WIDTH}")
        self.mean = np.asarray(data["mean"], dtype=np.float32).reshape(1, -1)
        self.scale = np.asarray(data["scale"], dtype=np.float32).reshape(1, -1)
        self.coef = np.asarray(data["coef"], dtype=np.float32)
        self.intercept = np.asarray(data["intercept"], dtype=np.float32).reshape(1, -1)
        self.classes_ = np.asarray(data["classes"])
        if self.mean.shape[1] != FEATURE_WIDTH or self.scale.shape[1] != FEATURE_WIDTH or self.coef.shape[1] != FEATURE_WIDTH:
            raise ValueError("invalid npz feature dimensions")
        if self.coef.shape[0] != self.intercept.shape[1] or self.coef.shape[0] != len(self.classes_):
            raise ValueError("invalid npz class dimensions")
        self.scale = np.where(self.scale == 0, 1.0, self.scale).astype(np.float32)

    def predict_proba(self, x):
        x = np.asarray(x, dtype=np.float32)
        if x.ndim != 2 or x.shape[1] != FEATURE_WIDTH: raise ValueError(f"expected (n, {FEATURE_WIDTH})")
        z = ((x - self.mean) / self.scale) @ self.coef.T + self.intercept
        z = z - np.max(z, axis=1, keepdims=True)
        exp = np.exp(z, dtype=np.float32)
        return exp / np.sum(exp, axis=1, keepdims=True)

def load_runtime_model(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".npz": return RuntimeLogisticModel(path)
    if suffix == ".joblib":
        import joblib
        return joblib.load(path)
    raise ValueError(f"unsupported model format: {path.suffix}")

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
    def __init__(self, root: Path, model_path: str, landmarker_path: str, min_prob: float, min_frames: int, buffer_seconds: int, max_sessions: int = 1, session_ttl_seconds: int = 120, frame_interval_ms: int = 200, max_released_scans: int = 16):
        self.ready = False; self.error = None; self.model = None; self.landmarker_path = root / landmarker_path; self.model_path = root / model_path
        self.min_prob = min_prob; self.min_frames = min_frames; self.buffer_seconds = buffer_seconds; self.sessions = {}; self.labels = []; self.prediction_windows = 0; self.inference_requests = 0
        self.max_sessions = max_sessions; self.session_ttl_seconds = session_ttl_seconds; self.frame_interval_ms = frame_interval_ms; self.max_released_scans = max_released_scans
        self.released_scans = {}; self._landmarker = None; self._landmarker_lock = Lock(); self._timestamp_ms = 0; self.mp = None; self.vision = None; self.BaseOptions = None

    def startup(self):
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            self.model = load_runtime_model(self.model_path)
            classes = list(getattr(self.model, "classes_", []))
            if len(classes) != len(TRAINING_WORDS) or set(map(int, classes)) != set(range(len(TRAINING_WORDS))):
                raise ValueError(f"unexpected classes_: {classes}")
            self.labels = TRAINING_WORDS[:]
            n_features = getattr(self.model, "n_features_in_", FEATURE_WIDTH)
            if int(n_features) != FEATURE_WIDTH:
                raise ValueError(f"unexpected feature width {n_features}; expected {FEATURE_WIDTH}")
            if not self.landmarker_path.exists(): raise FileNotFoundError(str(self.landmarker_path))
            self.mp = mp; self.vision = vision; self.BaseOptions = BaseOptions; self.ready = True
        except Exception as exc:
            self.ready = False; self.error = f"model_unavailable: {type(exc).__name__}: {exc}"

    def _prune_released_scans(self, now):
        stale = [k for k, ts in self.released_scans.items() if now - ts >= self.session_ttl_seconds]
        for k in stale: self.released_scans.pop(k, None)
        while len(self.released_scans) > self.max_released_scans:
            oldest = min(self.released_scans, key=self.released_scans.get)
            self.released_scans.pop(oldest, None)

    def is_released(self, sid, scan_id):
        now = time.time(); self._prune_released_scans(now)
        return (sid, scan_id) in self.released_scans

    def get_state(self, sid, scan_id=None):
        now=time.time()
        self._prune_released_scans(now)
        if scan_id is not None and (sid, scan_id) in self.released_scans: return None
        stale = [k for k, v in self.sessions.items() if now-v.last_seen >= self.session_ttl_seconds]
        for k in stale: del self.sessions[k]
        if not self.sessions: self.close_landmarker()
        if sid not in self.sessions and len(self.sessions) >= self.max_sessions:
            oldest = min(self.sessions, key=lambda k: self.sessions[k].last_seen)
            del self.sessions[oldest]
        return self.sessions.setdefault(sid, ScanState())

    def release_state(self, sid, scan_id=None):
        self.sessions.pop(sid, None)
        if scan_id is not None:
            now = time.time(); self.released_scans[(sid, scan_id)] = now; self._prune_released_scans(now)
        if not self.sessions: self.close_landmarker()

    def close_landmarker(self):
        with self._landmarker_lock:
            lm = self._landmarker; self._landmarker = None
            close = getattr(lm, "close", None)
            if callable(close):
                try: close()
                except Exception: pass

    def close(self):
        self.sessions.clear(); self.close_landmarker()

    def detect_frame(self, image, min_detection_confidence, min_tracking_confidence):
        with self._landmarker_lock:
            if self._landmarker is None:
                opts = self.vision.HandLandmarkerOptions(base_options=self.BaseOptions(model_asset_path=str(self.landmarker_path)), running_mode=self.vision.RunningMode.VIDEO, num_hands=2, min_hand_detection_confidence=min_detection_confidence, min_tracking_confidence=min_tracking_confidence)
                self._landmarker = self.vision.HandLandmarker.create_from_options(opts)
            self._timestamp_ms += max(1, int(self.frame_interval_ms))
            return self._landmarker.detect_for_video(image, self._timestamp_ms)

    def reset_if_new_scan(self, state, scan_id):
        if state.scan_id != scan_id:
            state.scan_id = scan_id; state.reset_window(None); state.pending_action=None; state.pending_ingredient=None; state.paused=False; state.last_completed=None; state.last_frame_seq=0

    def class_to_label(self, class_id) -> str:
        return TRAINING_WORDS[int(class_id)]
