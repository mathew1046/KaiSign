# Kiosk Backend

Local FastAPI service for the restaurant kiosk. API routes are registered under `/api/*` before the static mount that serves `../restaurant-order-ui` from the same origin.

## Runtime

Create a pinned virtualenv under this directory, then run one worker:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Defaults, relative to repo root:

- `KIOSK_MODEL_PATH=training/runs/custom_10_words/models/knn_3.joblib`
- `KIOSK_HAND_LANDMARKER_PATH=../wlasl_signs_model/hand_landmarker.task`
- `KIOSK_MIN_PROBABILITY=0.85` (a label is usable only when confidence is strictly greater than this value)
- `KIOSK_BUFFER_SECONDS=2`
- `KIOSK_MIN_DETECTED_FRAMES=5`
- `KIOSK_MIN_HAND_DETECTION_CONFIDENCE=0.35`
- `KIOSK_MIN_TRACKING_CONFIDENCE=0.35`

Startup attempts to load the real KNN and MediaPipe task. If assets or ABI-compatible dependencies are missing, `/api/health` and the static UI still work; `/api/infer` returns a generic 503 without exposing paths or raw exceptions.

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

Client events: `start` with `mode` (`wake`, `normal`, `blind`), UUID `session_id`, optional context; `audio` with 16 kHz mono PCM16 base64; `stop`. Server events: `ready`, PCM `audio` with sample rate/mime, normalized `state` VoiceActionEnvelope, `wake_detected`, `interrupted`, `go_away`, and generic `error`. `/ws/live` requires an exact same-origin browser origin (or explicit `LIVE_WS_ALLOWED_ORIGINS`) and reserves one active in-process connection per `kiosk_session` cookie; duplicates are closed with try-again/policy close codes, and registry entries are released on all exits. Wake activation is terminal server-side: after one `wake_detected`, that provider session is stopped to prevent duplicate activation events. Blind mode receives one backend-initiated opening turn so the first audible response greets and asks “What would you like to order?” Wake mode remains silent. Gemini tool arguments are validated and canonicalized against backend `MENU`; categories are `mains`, `breakfast`, `bowls`, `drinks`; `add_item` moves to `preferences`, `finish_customization`/`continue_ordering` returns to menu, `review_order` sets checkout only on explicit review/checkout request with a non-empty cart, and `confirm_order` is the deliberate spoken-affirmative submission signal (`submit_pending`). Free-form notes are plain normalized text with control-character, count, dedupe, and length bounds. Optional `GEMINI_LIVE_MAX_SESSIONS` tunes total in-process concurrency.

## Verification

```bash
python -m compileall app tests
python -m pytest tests
python scripts/verify_runtime.py
python scripts/replay_video.py [optional/path/to/captured.mp4]
python scripts/api_replay_clip.py [optional/path/to/captured.mp4]
```

The runtime verification script checks real artifacts only when pinned dependencies and files are available. `replay_video.py` runs the actual MediaPipe + KNN feature pipeline over a supplied or discovered captured video and prints the real label/probability without storing media. `api_replay_clip.py` posts one clip to `/api/infer` and verifies the request counter increments once.
