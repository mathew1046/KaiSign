# Restaurant Ordering Kiosk

Dependency-free frontend assets for the restaurant kiosk. The real integrated experience must be served by the backend so the same-origin API and WebSocket routes below are available; running these files with `python -m http.server` is only useful for static asset checks and will not provide live voice, gesture preferences, or order persistence.

The first screen shows only two choices: **Normal** and **Deaf**. Deaf keeps the existing camera/KNN preference path. Normal uses touch menu selection, then a Live voice preference flow for the selected item. Blind mode is not shown on the first screen; it starts only after `/ws/live` sends `wake_detected` for “HEY KAISIGN”.

## Normalized API contract

### `GET /ws/live`

Shared browser Live protocol for wake listening, Normal preferences, and Blind ordering.

Client start message:

```json
{ "type": "start", "mode": "wake", "session_id": "550e8400-e29b-41d4-a716-446655440000" }
```

Normal and Blind sessions include context when useful:

```json
{
  "type": "start",
  "mode": "normal",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "context": {
    "active_item_id": "burger",
    "order": [{ "id": "burger", "quantity": 1, "preferences": [] }]
  }
}
```

The browser captures one microphone stream at a time through `pcm-worklet.js`, resamples to 16 kHz mono PCM16, and sends:

```json
{ "type": "audio", "pcm16_base64": "..." }
```

Handled server events:

- `ready`
- `audio` with 24 kHz PCM16 as `pcm16_base64` for smooth Web Audio playback
- `state` with validated `action` payloads
- `wake_detected`
- `error` (kept out of the visible UI)

State events must use the corrected envelope:

```json
{
  "type": "state",
  "action": {
    "schema_version": "voice-action.v1",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "mode": "blind",
    "action": "add_item",
    "payload": { "item_id": "burger", "quantity": 1 },
    "state": {
      "category": "mains",
      "items": [{ "id": "burger", "quantity": 1, "notes": [] }],
      "screen": "menu",
      "active_item_id": "burger"
    }
  }
}
```

The reducer validates `schema_version`, `session_id`, and `mode`, then consumes only these action names: `select_category`, `add_item`, `set_quantity`, `remove_item`, `add_note`, `remove_note`, `finish_customization`, `continue_ordering`, `review_order`, `confirm_order`, and `end_session`. Category ids `mains`, `breakfast`, `bowls`, and `drinks` map to the existing visual categories, with Market Salad in `bowls`. Server snapshots are applied with the local menu as the source of names, prices, and icons.

Normal mode applies confirmed `add_note` and `remove_note` actions to the active item, and accepts `finish_customization` to continue the item flow. Blind mode applies validated action events to local cart, category, preference, checkout, and submit state. An `add_item` snapshot can move the visual screen to `preferences` with `active_item_id`; notes remain on that active item. `finish_customization` and `continue_ordering` return the visual screen to the menu. `review_order` opens checkout, and only `confirm_order` is treated as the explicit spoken final confirmation and guarded submit. Blind screen changes do not restart or replace the active Live socket or microphone. `end_session` closes voice, mic, and playback, then returns to the two-card start screen so wake listening can restart. Notes are capped at 10 per item, 160 characters each, and deduped. Audio is queued briefly until `ready`; provider audio plays only in Blind mode. The browser never shows transcripts, model labels, raw errors, recording labels, or Gemini keys/config.

Voice ownership is serialized through one lifecycle controller. At any moment there is at most one `/ws/live` socket and one microphone pipeline. Each socket callback, queued-audio flush, microphone start step, and audio chunk checks the current ownership epoch before acting, so stale renders or async completions cannot open duplicate Live sessions or fetch duplicate worklets. Rendering itself does not start or stop wake/normal voice sessions; lifecycle changes happen only from explicit mode and navigation events.

### `POST /api/infer`

The browser collects JPEG frames locally every 200 ms for one continuous 2-second window, then sends exactly one clip request. No network requests are made for individual frames.

Request JSON:

```json
{
  "scan_id": "550e8400-e29b-41d4-a716-446655440000",
  "clip_seq": 1,
  "item": { "id": "burger", "quantity": 1 },
  "frames": [
    "data:image/jpeg;base64,...",
    "data:image/jpeg;base64,..."
  ]
}
```

Response JSON:

```json
{
  "scan_id": "550e8400-e29b-41d4-a716-446655440000",
  "accepted": true,
  "inference_paused": true,
  "window_complete": true,
  "cooldown_ms": 300,
  "status": "completed",
  "display_text": "No cheese"
}
```

Notes:

- `scan_id` is rotated for each item, retry, return to preferences, and new order.
- `scan_id` is a UUID v4.
- `clip_seq` starts at `1` for each new `scan_id`.
- `accepted: true` is treated as complete only when `inference_paused: true` is also present.
- The capture indicator is on only during local clip capture, then off while the single clip request is in flight and during `cooldown_ms`.
- Model output is displayed only from non-empty `display_text` values returned by the backend.
- When complete, frame submission stops but the video stream remains visible.

### `POST /api/orders`

The browser sends a client-generated `Idempotency-Key` header. The same key is retained across checkout retries and reset for a new order.

Request headers:

```http
Content-Type: application/json
Idempotency-Key: 7f4a8c7a-4d16-4b6f-84f1-0965156f5c3a
```

Request JSON:

```json
{
  "items": [
    {
      "id": "burger",
      "quantity": 2,
      "preferences": ["No cheese"]
    }
  ]
}
```

Success response JSON:

```json
{
  "persisted": true,
  "order_id": "ORDER-123"
}
```

The UI only shows order success when `persisted` is `true` and `order_id` is present.
