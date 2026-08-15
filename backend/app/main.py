import os, uuid, time, base64, hashlib, json
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from .config import ROOT, load_settings
from .menu import MENU, validate_and_total
from .live import live_ws
from .inference import InferenceEngine, resample_sequence, aggregate_label, DISPLAY_WORD, confidence_passes

load_settings()
MAX_BODY = 1_500_000
SESSION_COOKIE = "kiosk_session"

def env(name, default): return os.getenv(name, default)
engine = InferenceEngine(ROOT, env("KIOSK_MODEL_PATH", "training/runs/custom_10_words/models/knn_3.joblib"), env("KIOSK_HAND_LANDMARKER_PATH", "../wlasl_signs_model/hand_landmarker.task"), float(env("KIOSK_MIN_PROBABILITY", "0.85")), int(env("KIOSK_MIN_DETECTED_FRAMES", "5")), int(env("KIOSK_BUFFER_SECONDS", "2")))
MIN_HAND_DETECTION_CONFIDENCE = float(env("KIOSK_MIN_HAND_DETECTION_CONFIDENCE", "0.35"))
MIN_TRACKING_CONFIDENCE = float(env("KIOSK_MIN_TRACKING_CONFIDENCE", "0.35"))
CLIP_MIN_FRAMES = int(env("KIOSK_CLIP_MIN_FRAMES", "8"))
CLIP_MAX_FRAMES = int(env("KIOSK_CLIP_MAX_FRAMES", "12"))
app = FastAPI()

@app.middleware("http")
async def limits_and_session(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"}:
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if origin and host and not origin.endswith(f"//{host}"):
            return JSONResponse({"error":{"code":"bad_origin","message":"same-origin required"}}, status_code=403)
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_BODY:
            return JSONResponse({"error":{"code":"request_too_large","message":"request too large"}}, status_code=413)
    sid = request.cookies.get(SESSION_COOKIE) or uuid.uuid4().hex
    request.state.session_id = sid
    resp = await call_next(request)
    if SESSION_COOKIE not in request.cookies:
        resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", secure=False, max_age=86400)
    return resp

@app.on_event("startup")
async def startup(): engine.startup()

@app.get("/api/health")
async def health():
    return {"ok": True, "inference_ready": engine.ready, "prediction_windows": engine.prediction_windows, "inference_requests": engine.inference_requests, "supabase_configured": bool(os.getenv("SUPABASE_URL") and (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY")))}

def error(code, msg, status): return JSONResponse({"error":{"code":code,"message":msg}}, status_code=status)

@app.post("/api/infer")
async def infer(request: Request):
    if not engine.ready: return error("inference_unavailable", "Preference recognition is temporarily unavailable.", 503)
    try: data = await request.json()
    except Exception: return error("bad_json", "invalid JSON", 400)
    scan_id = data.get("scan_id")
    if not isinstance(scan_id, str): return error("bad_scan_id", "scan_id must be a UUID", 422)
    try: scan_id = str(uuid.UUID(scan_id))
    except Exception: return error("bad_scan_id", "scan_id must be a UUID", 422)
    if "frame" in data or "frame_seq" in data: return error("legacy_payload", "clip frames payload required", 422)
    clip_seq = data.get("clip_seq")
    if not isinstance(clip_seq, int) or clip_seq < 1: return error("bad_clip_seq", "clip_seq must be positive", 422)
    item = data.get("item") or {}
    if item.get("id") not in MENU or not isinstance(item.get("quantity", 1), int): return error("bad_item", "invalid item", 422)
    frames = data.get("frames")
    if not isinstance(frames, list) or not (CLIP_MIN_FRAMES <= len(frames) <= CLIP_MAX_FRAMES): return error("bad_frames", f"frames must contain {CLIP_MIN_FRAMES}-{CLIP_MAX_FRAMES} JPEG data URLs", 422)
    if any((not isinstance(f, str) or not f.startswith("data:image/jpeg;base64,")) for f in frames): return error("bad_frame", "JPEG data URLs required", 415)
    state = engine.get_state(request.state.session_id); engine.reset_if_new_scan(state, scan_id); state.last_seen = time.time()
    if state.paused:
        return {"scan_id": scan_id, "accepted": bool(state.last_completed), "inference_paused": True, "status": "completed", "display_text": state.last_completed or "Inference paused", "recognized": {"preference": state.last_completed} if state.last_completed else None}
    if clip_seq <= state.last_frame_seq:
        return {"scan_id":scan_id,"accepted":False,"inference_paused":False,"status":"stale_clip","window_complete":False,"display_text":""}
    state.last_frame_seq = clip_seq
    try:
        import cv2, numpy as np, mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        if state.landmarker is None:
            opts = vision.HandLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(engine.landmarker_path)), running_mode=vision.RunningMode.VIDEO, num_hands=2, min_hand_detection_confidence=MIN_HAND_DETECTION_CONFIDENCE, min_tracking_confidence=MIN_TRACKING_CONFIDENCE)
            state.landmarker = vision.HandLandmarker.create_from_options(opts)
        from .inference import order_hands, normalize_frame
        state.reset_window(None)
        total_decoded = 0
        for frame in frames:
            raw = base64.b64decode(frame.split(",",1)[1], validate=True); total_decoded += len(raw)
            if total_decoded > 1_000_000: return error("clip_too_large", "clip too large", 413)
            arr = np.frombuffer(raw, dtype=np.uint8); bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is None: return error("bad_frame", "invalid JPEG", 415)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            state.timestamp_ms += 200
            res = state.landmarker.detect_for_video(img, state.timestamp_ms)
            vec = normalize_frame(order_hands(res)).reshape(-1)
            state.buffer.append(vec)
            if vec.any(): state.detected += 1
        engine.inference_requests += 1
        state.inference_requests += 1
        sample = resample_sequence(np.asarray(state.buffer, dtype=np.float32))
        detected = state.detected
        probs = engine.model.predict_proba(sample)[0]; idx = int(np.argmax(probs)); prob = float(probs[idx]); label = engine.class_to_label(engine.model.classes_[idx])
        engine.prediction_windows += 1
        state.reset_window(None)
        if detected < engine.min_frames:
            return {"scan_id":scan_id,"accepted":False,"inference_paused":False,"status":"no_hand","window_complete":True,"cooldown_ms":300,"confidence":prob,"display_text":""}
        if not confidence_passes(prob, engine.min_prob): return {"scan_id":scan_id,"accepted":False,"inference_paused":False,"status":"low_confidence","window_complete":True,"cooldown_ms":300,"confidence":prob,"display_text":""}
        complete = aggregate_label(state, label)
        if complete: return {"scan_id":scan_id,"accepted":True,"inference_paused":True,"status":"completed","window_complete":True,"cooldown_ms":300,"confidence":prob,"display_text":complete,"recognized":{"label":label,"preference":complete},"pending":{"action":state.pending_action,"ingredient":state.pending_ingredient}}
        return {"scan_id":scan_id,"accepted":False,"inference_paused":False,"status":"partial","window_complete":True,"cooldown_ms":300,"confidence":prob,"display_text":DISPLAY_WORD.get(label, label.capitalize()),"recognized":{"label":label},"pending":{"action":state.pending_action,"ingredient":state.pending_ingredient}}
    except Exception as exc:
        return error("inference_failed", "Preference recognition is temporarily unavailable.", 503)

ORDERS_CACHE = {}

def canonical_payload_hash(normalized):
    body = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()

def cached_idempotency_response(cache, key, payload_hash):
    cached = cache.get(key)
    if not cached: return None
    if cached["payload_hash"] != payload_hash:
        return {"conflict": True}
    return {"persisted": True, "order_id": cached["order_id"], "duplicate": True}

@app.post("/api/orders")
async def orders(request: Request):
    key = request.headers.get("Idempotency-Key")
    if not key: return error("missing_idempotency_key", "Idempotency-Key header required", 400)
    try: data = await request.json(); normalized = validate_and_total(data.get("items"))
    except ValueError as exc: return error("invalid_order", str(exc), 422)
    except Exception: return error("bad_json", "invalid JSON", 400)
    payload_hash = canonical_payload_hash(normalized)
    cached = cached_idempotency_response(ORDERS_CACHE, key, payload_hash)
    if cached:
        if cached.get("conflict"): return error("idempotency_conflict", "Idempotency-Key was already used for a different order", 409)
        return cached
    url = os.getenv("SUPABASE_URL"); secret = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY")
    if not url or not secret: return error("persistence_unavailable", "Supabase credentials are not configured", 503)
    oid = str(uuid.uuid4()); payload = {"id": oid, "idempotency_key": key, "payload_hash": payload_hash, "items": normalized["items"], "subtotal_cents": normalized["subtotal_cents"], "tax_cents": normalized["tax_cents"], "total_cents": normalized["total_cents"]}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{url.rstrip('/')}/rest/v1/orders", headers={"apikey": secret, "Authorization": f"Bearer {secret}", "Content-Type":"application/json", "Prefer":"return=representation"}, json=payload)
    except httpx.HTTPError:
        return error("persistence_failed", "Supabase transport failed", 503)
    if r.status_code == 409:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                q = await client.get(f"{url.rstrip('/')}/rest/v1/orders", headers={"apikey": secret, "Authorization": f"Bearer {secret}"}, params={"idempotency_key": f"eq.{key}", "select": "id,payload_hash"})
        except httpx.HTTPError:
            return error("persistence_failed", "Supabase transport failed", 503)
        if q.status_code < 300 and q.json():
            row = q.json()[0]
            if row.get("payload_hash") != payload_hash:
                return error("idempotency_conflict", "Idempotency-Key was already used for a different order", 409)
            ORDERS_CACHE[key] = {"order_id": row["id"], "payload_hash": payload_hash}
            return {"persisted": True, "order_id": row["id"], "duplicate": True}
        return error("duplicate_conflict", "idempotency key already exists", 409)
    if r.status_code >= 300: return error("persistence_failed", "Supabase insert failed", 503)
    ORDERS_CACHE[key] = {"order_id": oid, "payload_hash": payload_hash}
    return {"persisted": True, "order_id": oid, "duplicate": False}

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await live_ws(websocket)

ui = ROOT / "restaurant-order-ui"
app.mount("/", StaticFiles(directory=ui, html=True), name="ui")
