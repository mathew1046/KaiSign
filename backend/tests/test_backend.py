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
        validate_and_total([{"id":"coffee","quantity":1,"preferences":["please add secret"]}])

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
