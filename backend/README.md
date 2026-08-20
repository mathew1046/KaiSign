# Kiosk Backend

Local FastAPI service for the restaurant kiosk. API routes are registered under `/api/*` before the static mount that serves `../restaurant-order-ui` from the same origin.

## Runtime

Create a pinned virtualenv under this directory, then run one worker. Raspberry Pi ARM64 deployments must use uv-managed Python 3.12; the Pi's generic/local Python 3.13 path is unsupported for MediaPipe because `mediapipe` has no aarch64 cp313 wheel.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-runtime.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

On the Pi, provision and install with Python 3.12 only. Use the two-phase installer so pip does not resolve MediaPipe's heavy unused transitive dependencies into the 450 MB `/tmp` tmpfs:

```bash
../deploy/pi/install-runtime.sh
```

The installer uses a disk-backed temporary directory at repo root, installs `requirements-pi-base.txt` normally with binary wheels only, then installs `mediapipe==0.10.18` from `requirements-pi-mediapipe.txt` with `--no-deps`. It finishes with an import smoke test for `import mediapipe as mp` and `from mediapipe.tasks.python import BaseOptions, vision`.

Defaults, relative to repo root:

- `KIOSK_MODEL_PATH=backend/runtime_assets/logistic_sign_classifier.npz` on Pi/runtime deployments
- `KIOSK_HAND_LANDMARKER_PATH=../wlasl_signs_model/hand_landmarker.task`
- `KIOSK_MIN_PROBABILITY=0.85` (a label is usable only when confidence is strictly greater than this value)
- `KIOSK_BUFFER_SECONDS=2`
- `KIOSK_MIN_DETECTED_FRAMES=5`
- `KIOSK_MIN_HAND_DETECTION_CONFIDENCE=0.35`
- `KIOSK_MIN_TRACKING_CONFIDENCE=0.35`

Startup attempts to load the configured logistic NPZ (Pi/runtime) or development model and the MediaPipe task. If assets or ABI-compatible dependencies are missing, `/api/health` and the static UI still work; `/api/infer` returns a generic 503 without exposing paths or raw exceptions.

The model is validated for exactly 7,560 features and numeric classes mapped to this training word order: `more, less, double, cheese, butter, sugar, without, add, no, salt`.

## Inference protocol

`POST /api/infer` requires JSON:

```json
{"scan_id":"UUID", "clip_seq":1, "item":{"id":"burger","quantity":1}, "frames":["data:image/jpeg;base64,..."]}
```

`scan_id` must be a UUID and `clip_seq` must be positive and increasing per scan; stale/out-of-order clips are ignored. Legacy per-frame payloads using `frame`/`frame_seq` are rejected. Each request must contain 8-12 JPEG data URLs, representing one roughly two-second browser-sampled clip at ~0.2s per frame. The backend processes the submitted frames sequentially through one MediaPipe VIDEO landmarker using strictly increasing 200ms timestamps, normalizes landmarks exactly as training, resamples the complete clip to 60 frames, and calls `predict_proba` exactly once per request when enough hand frames are detected. Partial action/ingredient state is retained between clips, ordinary no-hand/low-confidence responses have empty `display_text`, and inference pauses only after a complete server-owned preference phrase. Every processed clip returns `window_complete: true` and `cooldown_ms: 300`; high-confidence single tokens return friendly text such as `Cheese` or `No`. Pausing inference never stops browser-owned video.

## Supabase

Apply `schema.sql`. Set `SUPABASE_URL` and server-only `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_SECRET_KEY`). Without credentials, orders return `persistence_unavailable` and are not falsely accepted.

Fresh setup uses `payload_hash text not null` to bind each `Idempotency-Key` to the canonical server-normalized order payload. Existing development tables should run the migration notes in `schema.sql`. RLS is enabled with no broad anon/authenticated policies; the service-role key remains server-only, is used only by this backend's REST calls, and bypasses RLS.

## Gemini Live voice relay

Set server-only `GEMINI_API_KEY`. Settings are loaded from `backend/.env`; if only `GEMINI_API_KEY` is missing there, the backend safely falls back to repo-root `.env` without logging or returning the value. Optional: `GEMINI_LIVE_MODEL` (default `gemini-3.1-flash-live-preview`), `GEMINI_LIVE_VOICE` (default `Kore`), and `GEMINI_LIVE_LANGUAGE` (default `en-US`). The browser never receives provider keys; it connects to same-origin `WS /ws/live`.

Client events: `start` with `mode` (`wake`, `normal`, `blind`), UUID `session_id`, optional context; `stop`. Voice input is server-owned: the browser does not call `getUserMedia` or send microphone audio for voice. A single process-local ESP relay binds UDP on `ESP_AUDIO_UDP_HOST` (default `0.0.0.0`) and `ESP_AUDIO_UDP_PORT` (default `12345`) and routes PCM through an exclusive generation/token sink to exactly one Gemini Live session. Run one Uvicorn worker for this process-local source ownership invariant. Expected ESP datagrams are raw PCM, mono, 16 kHz, signed 16-bit little-endian, exactly 512 bytes / 256 frames; malformed datagrams are dropped. The relay uses nonblocking UDP receive, drains pending datagrams aggressively, and forwards only the newest valid packet to minimize latency. Server events: `ready`, optional `input_ready` after the first ESP PCM chunk reaches Gemini, PCM Gemini output `audio` with sample rate/mime, normalized `state` VoiceActionEnvelope, `wake_detected`, `interrupted`, `go_away`, and generic `error`. `/ws/live` requires an exact same-origin browser origin (or explicit `LIVE_WS_ALLOWED_ORIGINS`) and reserves one active in-process connection per `kiosk_session` cookie; duplicates are closed with try-again/policy close codes, and registry entries are released on all exits. Wake activation is terminal server-side: before `wake_detected`, the wake sink is detached and its provider session is stopped to prevent stale audio or duplicate activation events. Blind mode receives one backend-initiated opening turn so the first audible response greets, lists available categories, and asks what to order. Wake mode remains silent. Gemini tool arguments are validated and canonicalized against backend `MENU`; categories are `mains`, `breakfast`, `bowls`, `drinks`; `add_item` moves to `preferences`, `finish_customization`/`continue_ordering` returns to menu, `review_order` sets checkout only on explicit review/checkout request with a non-empty cart, and `confirm_order` is the deliberate spoken-affirmative submission signal (`submit_pending`). Free-form notes are plain normalized text with control-character, count, dedupe, and length bounds. Optional `GEMINI_LIVE_MAX_SESSIONS` tunes total in-process concurrency.

## Pi runtime artifact

The Pi deployment lane uses `backend/runtime_assets/logistic_sign_classifier.npz`, exported from the sklearn logistic-regression pipeline by `training/export_logistic_runtime.py`. The NPZ contains only NumPy arrays (`format_version`, `feature_width`, `mean`, `scale`, `coef`, `intercept`, `classes`) so production installs do not need scikit-learn or joblib. Training data, `training/`, original `.joblib` models, tests, and all other models are not deployed. On Pi 3, set `GEMINI_LIVE_MAX_SESSIONS=1`. Runtime requirements pin `mediapipe==0.10.18` for Python 3.12 ARM64 wheel compatibility.

## Verification

```bash
python -m compileall app tests
python -m pytest tests
python scripts/verify_runtime.py
python scripts/replay_video.py [optional/path/to/captured.mp4]
python scripts/api_replay_clip.py [optional/path/to/captured.mp4]
```

The runtime verification script checks real artifacts only when pinned dependencies and files are available. `replay_video.py` runs the actual MediaPipe + configured sign model feature pipeline over a supplied or discovered captured video and prints the real label/probability without storing media. `api_replay_clip.py` posts one clip to `/api/infer` and verifies the request counter increments once. Use `requirements-dev.txt` when running exporter/tests/legacy joblib scripts; Pi production installs use `requirements-runtime.txt` with uv-managed Python 3.12, not local Python 3.13.
