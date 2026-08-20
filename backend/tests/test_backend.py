import pytest
from app.inference import ScanState, aggregate_label
from app.menu import validate_and_total

def test_preference_aggregation_either_order_and_pause():
    s = ScanState(); assert aggregate_label(s, "cheese") is None
    assert aggregate_label(s, "more") == "Extra cheese"; assert s.paused
    s2 = ScanState(); assert aggregate_label(s2, "without") is None
    assert aggregate_label(s2, "salt") == "No salt"

def test_canonical_total_ignores_client_price():
    out = validate_and_total([{"id":"burger","quantity":2,"unit_price":0,"preferences":["Extra cheese"]}])
    assert out["subtotal_cents"] == 2300
    assert out["tax_cents"] == 190
    assert out["total_cents"] == 2490

def test_rejects_non_server_preference():
    with pytest.raises(ValueError):
        validate_and_total([{"id":"coffee","quantity":1,"preferences":[""]}])

def test_accepts_sanitized_free_form_note():
    out = validate_and_total([{"id":"coffee","quantity":1,"preferences":["  oat milk <b>please</b>  "]}])
    assert out["items"][0]["preferences"] == ["oat milk <b>please</b>"]

def test_voice_action_schema_and_normalization():
    from uuid import uuid4
    from app.voice import VoiceMode, normalize_tool_action
    sid = uuid4()
    env = normalize_tool_action("add_item", {"item_id":"burger", "quantity":2}, mode=VoiceMode.blind, session_id=sid)
    assert env.schema_version == "voice-action.v1" and env.payload == {"item_id":"burger", "quantity":2}
    assert env.state.items == [{"id":"burger", "quantity":2, "preferences":[]}]

def test_voice_mode_restrictions_and_context_note():
    from uuid import uuid4
    from app.voice import LiveContext, VoiceMode, normalize_tool_action
    sid = uuid4(); ctx = LiveContext(active_item_id="coffee")
    env = normalize_tool_action("add_note", {"note":"less ice<script>"}, mode=VoiceMode.normal, session_id=sid, context=ctx)
    assert env.payload["item_id"] == "coffee" and "<script>" in env.payload["note"]
    with pytest.raises(ValueError):
        normalize_tool_action("add_item", {"item_id":"burger"}, mode=VoiceMode.normal, session_id=sid)

def test_voice_tool_argument_validation():
    from uuid import uuid4
    from app.voice import VoiceMode, normalize_tool_action
    sid = uuid4()
    with pytest.raises(ValueError):
        normalize_tool_action("add_item", {"item_id":"made-up", "quantity":1}, mode=VoiceMode.blind, session_id=sid)
    with pytest.raises(ValueError):
        normalize_tool_action("set_quantity", {"item_id":"burger", "quantity":99}, mode=VoiceMode.blind, session_id=sid)

def test_voice_categories_and_state_screens():
    from uuid import uuid4
    from app.voice import LiveCart, VoiceMode, canonical_category, make_envelope, validate_payload
    sid = uuid4(); cart = LiveCart()
    assert canonical_category("food") == "mains"
    action, payload = validate_payload("select_category", {"category":"breakfast"}, mode=VoiceMode.blind)
    cart.apply(action, payload)
    assert make_envelope(session_id=sid, mode=VoiceMode.blind, action=action, payload=payload, cart=cart).state.category == "breakfast"
    action, payload = validate_payload("add_item", {"item_id":"pancakes"}, mode=VoiceMode.blind); cart.apply(action, payload)
    action, payload = validate_payload("review_order", {}, mode=VoiceMode.blind); cart.apply(action, payload); assert cart.snapshot().screen == "checkout"
    action, payload = validate_payload("confirm_order", {}, mode=VoiceMode.blind); cart.apply(action, payload); assert cart.snapshot().screen == "submit_pending"

def test_cart_total_quantity_and_note_caps():
    from app.voice import LiveCart, VoiceAction
    cart = LiveCart(); cart.apply(VoiceAction.add_item, {"item_id":"burger", "quantity":20})
    cart.apply(VoiceAction.add_item, {"item_id":"coffee", "quantity":20})
    assert len(cart.snapshot().items) == 2
    cart = LiveCart(); cart.apply(VoiceAction.add_item, {"item_id":"coffee", "quantity":1})
    for i in range(10): cart.apply(VoiceAction.add_note, {"item_id":"coffee", "note":f"note {i}"})
    with pytest.raises(ValueError): cart.apply(VoiceAction.add_note, {"item_id":"coffee", "note":"extra"})

def test_ui_category_mapping_and_tool_schemas():
    from app.voice import CATEGORIES, tool_declarations
    assert CATEGORIES == {"mains":["burger", "pizza"], "breakfast":["pancakes", "toast"], "bowls":["corn", "salad"], "drinks":["iced-tea", "lemonade", "coffee"]}
    decls = {d["name"]: d["parameters"] for d in tool_declarations()[0]["function_declarations"]}
    assert decls["select_category"]["properties"]["category"]["enum"] == ["mains", "breakfast", "bowls", "drinks"]
    assert decls["set_quantity"]["required"] == ["item_id", "quantity"]
    assert decls["add_note"]["required"] == ["item_id", "note"]
    assert decls["review_order"] == {"type":"object", "properties":{}}
    assert decls["finish_customization"] == {"type":"object", "properties":{}}

def test_normal_prompt_contains_canonical_active_item_and_cart():
    from app.voice import LiveContext, VoiceMode, system_prompt
    prompt = system_prompt(VoiceMode.normal, LiveContext(active_item_id="burger", order=[{"id":"burger", "quantity":1, "preferences":["No onions"]}]))
    assert "active_item_id" in prompt and "burger" in prompt and "No onions" in prompt
    assert "unit_price" not in prompt and "price_cents" not in prompt

def test_live_cart_dedupes_notes_case_insensitively():
    from app.voice import LiveCart, LiveContext, VoiceAction
    cart = LiveCart(LiveContext(order=[{"id":"coffee", "quantity":1, "preferences":["Less ice", "less ICE"]}]))
    assert cart.snapshot().items[0]["preferences"] == ["Less ice"]
    cart.apply(VoiceAction.add_note, {"item_id":"coffee", "note":"LESS ice"})
    assert cart.snapshot().items[0]["preferences"] == ["Less ice"]

def test_blind_item_customization_flow_and_explicit_review():
    from app.voice import LiveCart, VoiceAction
    cart = LiveCart()
    assert cart.snapshot().screen == "menu"
    cart.apply(VoiceAction.add_item, {"item_id":"burger", "quantity":1})
    assert cart.snapshot().screen == "preferences" and cart.snapshot().active_item_id == "burger"
    cart.apply(VoiceAction.finish_customization, {})
    assert cart.snapshot().screen == "menu"
    cart.apply(VoiceAction.continue_ordering, {})
    assert cart.snapshot().screen == "menu"
    cart.apply(VoiceAction.review_order, {})
    assert cart.snapshot().screen == "checkout"

def test_review_requires_explicit_action_and_non_empty_cart():
    from app.voice import LiveCart, VoiceAction
    cart = LiveCart()
    with pytest.raises(ValueError):
        cart.apply(VoiceAction.review_order, {})
    cart.apply(VoiceAction.add_item, {"item_id":"pizza", "quantity":1})
    assert cart.snapshot().screen == "preferences"

@pytest.mark.asyncio
async def test_blind_opening_turn_only_for_blind_mode():
    from app.live import maybe_send_blind_opening_turn
    from app.voice import VoiceMode
    class FakeSession:
        def __init__(self): self.calls = 0
        async def send_client_content(self, **kwargs): self.calls += 1
    class FakeTypes:
        class Content:
            def __init__(self, **kwargs): pass
        class Part:
            def __init__(self, **kwargs): pass
    blind = FakeSession(); wake = FakeSession()
    assert await maybe_send_blind_opening_turn(blind, FakeTypes, VoiceMode.blind) is True
    assert blind.calls == 1
    assert await maybe_send_blind_opening_turn(wake, FakeTypes, VoiceMode.wake) is False
    assert wake.calls == 0

@pytest.mark.asyncio
async def test_live_registry_duplicate_reservation_and_release():
    from app import live
    live._active_connections.clear()
    token = await live.reserve_live_connection("kiosk-a")
    assert token
    assert await live.reserve_live_connection("kiosk-a") is None
    assert await live.bind_live_connection("kiosk-a", token, "client-session") is True
    assert await live.owns_live_connection("kiosk-a", token) is True
    await live.release_live_connection("kiosk-a", token)
    assert live._active_connections == {}

def test_wake_activation_is_terminal_server_side():
    from app.live import is_terminal_wake_activation
    from app.voice import VoiceMode
    assert is_terminal_wake_activation(VoiceMode.wake, "activate_blind_mode") is True
    assert is_terminal_wake_activation(VoiceMode.blind, "activate_blind_mode") is False

@pytest.mark.asyncio
async def test_live_ws_cleanup_on_no_key_and_invalid_start(monkeypatch):
    from app import live
    live._active_connections.clear()
    class FakeWS:
        def __init__(self, text=None):
            self.headers = {"origin":"http://localhost:8000", "host":"localhost:8000"}
            self.cookies = {live.SESSION_COOKIE:"kiosk-cleanup"}
            self.text = text; self.accepted = False; self.closed = False; self.sent = []
        async def accept(self): self.accepted = True
        async def close(self, code=None): self.closed = True; self.code = code
        async def send_json(self, payload): self.sent.append(payload)
        async def receive_text(self): return self.text
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    await live.live_ws(FakeWS())
    assert live._active_connections == {}
    monkeypatch.setenv("GEMINI_API_KEY", "configured-but-not-used")
    await live.live_ws(FakeWS('{"type":"bad"}'))
    assert live._active_connections == {}

def test_scan_reset_pause_behavior():
    from app.inference import InferenceEngine
    e = InferenceEngine(__import__("pathlib").Path("."), "missing", "missing", .67, 5, 2)
    s = ScanState(scan_id="old", paused=True, last_completed="No salt", last_frame_seq=9)
    e.reset_if_new_scan(s, "new")
    assert s.scan_id == "new" and not s.paused and s.last_completed is None and s.buffer == [] and s.last_frame_seq == 0

def test_numeric_class_ids_map_to_training_words():
    from app.inference import InferenceEngine, TRAINING_WORDS
    e = InferenceEngine(__import__("pathlib").Path("."), "missing", "missing", .67, 5, 2)
    assert e.class_to_label(0) == "more"
    assert e.class_to_label(9) == "salt"
    assert TRAINING_WORDS == ["more", "less", "double", "cheese", "butter", "sugar", "without", "add", "no", "salt"]

def test_npz_runtime_loader_softmax(tmp_path):
    import numpy as np
    from app.inference import FEATURE_WIDTH, load_runtime_model
    p = tmp_path / "model.npz"
    coef = np.zeros((2, FEATURE_WIDTH), dtype=np.float32); coef[1, 0] = 2.0
    np.savez(p, format_version=np.array("1"), feature_width=np.array(FEATURE_WIDTH), mean=np.zeros(FEATURE_WIDTH, dtype=np.float32), scale=np.ones(FEATURE_WIDTH, dtype=np.float32), coef=coef, intercept=np.array([1000.0, 1000.0], dtype=np.float32), classes=np.array([0, 1]))
    m = load_runtime_model(p)
    probs = m.predict_proba(np.array([[1.0] + [0.0] * (FEATURE_WIDTH - 1)], dtype=np.float32))[0]
    assert list(m.classes_) == [0, 1]
    assert probs[1] == pytest.approx(0.880797, rel=1e-5)
    assert float(probs.sum()) == pytest.approx(1.0)

def test_npz_runtime_loader_rejects_unsupported_format(tmp_path):
    import numpy as np
    from app.inference import FEATURE_WIDTH, load_runtime_model
    p = tmp_path / "model.npz"
    np.savez(p, format_version=np.array("999"), feature_width=np.array(FEATURE_WIDTH), mean=np.zeros(FEATURE_WIDTH, dtype=np.float32), scale=np.ones(FEATURE_WIDTH, dtype=np.float32), coef=np.zeros((2, FEATURE_WIDTH), dtype=np.float32), intercept=np.zeros(2, dtype=np.float32), classes=np.array([0, 1]))
    with pytest.raises(ValueError, match="unsupported npz format_version"):
        load_runtime_model(p)

def test_inference_sessions_bounded_ttl_and_release():
    from app.inference import InferenceEngine
    e = InferenceEngine(__import__("pathlib").Path("."), "missing", "missing", .67, 5, 2, max_sessions=1, session_ttl_seconds=1)
    s1 = e.get_state("a"); s1.last_seen = 10
    s2 = e.get_state("b")
    assert "a" not in e.sessions and e.sessions["b"] is s2
    e.sessions["old"] = ScanState(last_seen=0)
    import time as _time
    now = _time.time()
    e.sessions["old"].last_seen = now - 2
    e.get_state("c")
    assert "old" not in e.sessions
    e._landmarker = type("LM", (), {"close": lambda self: setattr(self, "closed", True)})()
    e.release_state("c")
    assert e.sessions == {} and e._landmarker is None

def test_inference_ttl_prune_closes_shared_landmarker():
    from app.inference import InferenceEngine
    e = InferenceEngine(__import__("pathlib").Path("."), "missing", "missing", .67, 5, 2, session_ttl_seconds=1)
    e.sessions["old"] = ScanState(last_seen=0)
    e._landmarker = type("LM", (), {"close": lambda self: None})()
    import time as _time
    e.sessions["old"].last_seen = _time.time() - 2
    e.get_state("new")
    assert "old" not in e.sessions and e._landmarker is None

def test_released_scan_tombstone_blocks_same_scan_and_bounds():
    from app.inference import InferenceEngine
    e = InferenceEngine(__import__("pathlib").Path("."), "missing", "missing", .67, 5, 2, max_released_scans=2)
    e.release_state("sid", "scan-a")
    assert e.get_state("sid", "scan-a") is None
    assert e.get_state("sid", "scan-b") is not None
    e.release_state("sid", "scan-b"); e.release_state("sid", "scan-c")
    assert len(e.released_scans) == 2 and ("sid", "scan-a") not in e.released_scans

def test_released_scan_infer_response_is_benign_not_paused():
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from app import main
    scan_id = str(uuid4()); sid = "released-session"
    main.engine.ready = True
    main.engine.release_state(sid, scan_id)
    with TestClient(main.app) as client:
        r = client.post("/api/infer", cookies={main.SESSION_COOKIE: sid}, json={"scan_id": scan_id, "clip_seq": 1, "item": {"id":"burger", "quantity":1}, "frames": ["data:image/jpeg;base64,AA=="] * main.CLIP_MIN_FRAMES})
    assert r.status_code == 200
    assert r.json() == {"scan_id":scan_id,"accepted":False,"inference_paused":False,"status":"released","window_complete":False,"display_text":""}

def test_shared_landmarker_timestamp_uses_browser_frame_interval():
    from app.inference import InferenceEngine
    class FakeLandmarker:
        def __init__(self): self.timestamps = []
        def detect_for_video(self, image, timestamp): self.timestamps.append(timestamp); return object()
    class FakeHL:
        @staticmethod
        def create_from_options(opts): return fake
    class FakeVision:
        HandLandmarkerOptions = lambda **kwargs: kwargs
        RunningMode = type("RM", (), {"VIDEO": "VIDEO"})
        HandLandmarker = FakeHL
    fake = FakeLandmarker()
    e = InferenceEngine(__import__("pathlib").Path("."), "missing", "missing", .67, 5, 2, frame_interval_ms=200)
    e.vision = FakeVision; e.BaseOptions = lambda **kwargs: kwargs
    e.detect_frame("img", .35, .35); e.detect_frame("img", .35, .35)
    assert fake.timestamps == [200, 400]

def test_window_reset_retains_partial_token():
    s = ScanState(window_start=1.0, detected=5)
    aggregate_label(s, "add")
    s.buffer.append("frame")
    s.reset_window(3.0)
    assert s.pending_action == "add"
    assert s.pending_ingredient is None
    assert s.buffer == [] and s.detected == 0 and s.window_start == 3.0

def test_no_early_prediction_before_two_seconds():
    s = ScanState(window_start=10.0, detected=5)
    s.buffer.extend(["frame"] * 10)
    assert not s.window_elapsed(11.99, 2.0)
    assert s.window_elapsed(12.0, 2.0)

def test_completed_window_resets_buffer_after_evaluation():
    s = ScanState(window_start=1.0, detected=6)
    s.buffer.extend(["frame"] * 10)
    assert s.window_elapsed(3.1, 2.0)
    s.reset_window(3.1)
    assert s.buffer == [] and s.detected == 0 and s.window_start == 3.1

def test_accepted_pair_pauses_only_when_complete():
    s = ScanState()
    assert aggregate_label(s, "more") is None
    assert not s.paused
    assert aggregate_label(s, "cheese") == "Extra cheese"
    assert s.paused and s.last_completed == "Extra cheese"

def test_strict_confidence_gating_does_not_mutate_grammar_at_threshold():
    from app.inference import confidence_passes
    s = ScanState()
    assert not confidence_passes(0.60, 0.60)
    assert confidence_passes(0.60001, 0.60)
    if confidence_passes(0.60, 0.60):
        aggregate_label(s, "cheese")
    assert s.pending_ingredient is None and s.pending_action is None

def test_high_confidence_incomplete_token_display_and_partial_state():
    from app.inference import DISPLAY_WORD, confidence_passes
    s = ScanState()
    if confidence_passes(0.61, 0.60):
        assert aggregate_label(s, "cheese") is None
    assert DISPLAY_WORD["cheese"] == "Cheese"
    assert s.pending_ingredient == "cheese" and not s.paused

def test_clip_frame_bounds_and_legacy_rejection_constants():
    from app.main import CLIP_MIN_FRAMES, CLIP_MAX_FRAMES
    assert (CLIP_MIN_FRAMES, CLIP_MAX_FRAMES) == (8, 12)

def test_clip_byte_limits_cover_square_jpeg_base64_overhead():
    from app.main import CLIP_DECODED_JPEG_BYTES_LIMIT, REQUEST_BODY_BYTES_LIMIT
    assert CLIP_DECODED_JPEG_BYTES_LIMIT == 3_000_000
    assert REQUEST_BODY_BYTES_LIMIT == 4_250_000
    assert CLIP_DECODED_JPEG_BYTES_LIMIT < REQUEST_BODY_BYTES_LIMIT
    assert int(CLIP_DECODED_JPEG_BYTES_LIMIT * 4 / 3) + 200_000 < REQUEST_BODY_BYTES_LIMIT

def test_request_body_limit_rejects_oversized_payload():
    from fastapi.testclient import TestClient
    from app import main
    with TestClient(main.app) as client:
        r = client.post("/api/orders", content=b"x" * (main.REQUEST_BODY_BYTES_LIMIT + 1), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "request_too_large"

def test_infer_decoded_jpeg_limit_rejects_oversized_clip_before_decode():
    import base64
    from uuid import uuid4
    from fastapi.testclient import TestClient
    from app import main
    scan_id = str(uuid4())
    main.engine.ready = True
    too_large_frame = "data:image/jpeg;base64," + base64.b64encode(b"x" * (main.CLIP_DECODED_JPEG_BYTES_LIMIT + 1)).decode("ascii")
    tiny_frame = "data:image/jpeg;base64,AA=="
    payload = {"scan_id": scan_id, "clip_seq": 1, "item": {"id":"burger", "quantity":1}, "frames": [too_large_frame] + [tiny_frame] * (main.CLIP_MIN_FRAMES - 1)}
    with TestClient(main.app) as client:
        r = client.post("/api/infer", json=payload)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "clip_too_large"

def test_one_clip_request_counter_model():
    from app.inference import InferenceEngine
    e = InferenceEngine(__import__("pathlib").Path("."), "missing", "missing", .60, 5, 2)
    e.inference_requests += 1
    e.prediction_windows += 1
    assert e.inference_requests == 1 and e.prediction_windows == 1

def test_idempotency_same_key_same_payload_duplicate():
    from app.main import canonical_payload_hash, cached_idempotency_response
    a = validate_and_total([{"id":"burger","quantity":1,"preferences":[]}])
    h = canonical_payload_hash(a)
    cache = {"key": {"order_id": "order-1", "payload_hash": h}}
    assert cached_idempotency_response(cache, "key", h) == {"persisted": True, "order_id": "order-1", "duplicate": True}

def test_idempotency_same_key_different_payload_conflicts():
    from app.main import canonical_payload_hash, cached_idempotency_response
    a = validate_and_total([{"id":"burger","quantity":1,"preferences":[]}])
    b = validate_and_total([{"id":"burger","quantity":2,"preferences":[]}])
    cache = {"key": {"order_id": "order-1", "payload_hash": canonical_payload_hash(a)}}
    assert cached_idempotency_response(cache, "key", canonical_payload_hash(b)) == {"conflict": True}

def test_orders_cache_ttl_and_max_eviction(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "ORDERS_CACHE_TTL_SECONDS", 10)
    monkeypatch.setattr(main, "ORDERS_CACHE_MAX_ENTRIES", 2)
    cache = {"old": {"order_id":"o", "payload_hash":"h", "created_at": 0}, "a": {"order_id":"a", "payload_hash":"h", "created_at": 90}, "b": {"order_id":"b", "payload_hash":"h", "created_at": 91}, "c": {"order_id":"c", "payload_hash":"h", "created_at": 92}}
    main.prune_orders_cache(cache, now=95)
    assert set(cache) == {"b", "c"}
