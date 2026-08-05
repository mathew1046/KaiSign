from pathlib import Path
import os, sys
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.inference import TRAINING_WORDS, FEATURE_WIDTH
ROOT = Path(__file__).resolve().parents[2]
model = ROOT / os.getenv("KIOSK_MODEL_PATH", "training/runs/custom_10_words/models/knn_3.joblib")
task = ROOT / os.getenv("KIOSK_HAND_LANDMARKER_PATH", "../wlasl_signs_model/hand_landmarker.task")
try:
    import joblib, sklearn, mediapipe  # noqa
    clf = joblib.load(model)
    assert hasattr(clf, "predict_proba")
    classes = list(getattr(clf, "classes_", []))
    assert len(classes) == len(TRAINING_WORDS) and set(map(int, classes)) == set(range(len(TRAINING_WORDS))), classes
    assert int(getattr(clf, "n_features_in_", FEATURE_WIDTH)) == FEATURE_WIDTH
    if not task.exists(): raise FileNotFoundError(task)
    print({"ok": True, "labels": TRAINING_WORDS, "numeric_classes": list(map(int, classes)), "feature_width": FEATURE_WIDTH, "model": str(model), "landmarker": str(task)})
except Exception as exc:
    print({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    sys.exit(1)
