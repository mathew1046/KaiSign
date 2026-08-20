# Restaurant Ordering Kiosk

Dependency-free frontend assets for the restaurant kiosk. The real integrated experience must be served by the backend so the same-origin API and WebSocket routes below are available; running these files with `python -m http.server` is only useful for static asset checks and will not provide live voice, gesture preferences, or order persistence.

The first screen shows only two choices: **Normal** and **Deaf**. Deaf keeps the existing camera/configured sign-classifier preference path. Normal uses keypad menu selection, then a Live voice preference flow for the selected item. Blind mode is not shown on the first screen; it starts only after `/ws/live` sends `wake_detected` for “Hey Kaizen”.

## Keypad-only kiosk interaction

Normal and Deaf users operate the frontend with the physical numeric keypad only. The app listens only to `KeyboardEvent.code` values `Numpad0` through `Numpad9`; top-row number keys are not substitutes. Mouse, click, touch, context-menu, and wheel input are suppressed globally.

The same directional mapping is used throughout:

- `Numpad8`: move up
- `Numpad2`: move down
- `Numpad4`: move left
- `Numpad6`: move right
- `Numpad5`: select the focused control
- `Numpad0`: back / cancel

The focused control is visually highlighted and scrolled into view during numpad navigation. Disabled controls are skipped. If there is no control farther up or down, the up/down keys scroll the page. This reaches mode selection, categories, menu item add/remove quantity controls, preferences, Deaf camera retry, checkout actions, reset, and new-order success.

Normal mode still receives server-side voice preference updates. Deaf mode still runs the existing camera/sign-classifier preference capture.

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

The browser does not capture or send voice microphone audio. Voice input is relayed server-side from the ESP UDP raw PCM source configured by `ESP_AUDIO_UDP_HOST` and `ESP_AUDIO_UDP_PORT` (defaults `0.0.0.0:12345`). The stream is 16 kHz mono signed PCM16 in 512-byte datagrams; the browser WebSocket carries only `start`/`stop`, state, wake/blind transition, and Gemini output playback.

Handled server events:

- `ready`
- `input_ready` after the backend has forwarded the first valid ESP PCM chunk to Gemini; optional and safe to ignore
- `audio` with 24 kHz PCM16 as `pcm16_base64` for smooth Web Audio playback
- `state` with validated `action` payloads
- `wake_detected`
- `error` (kept out of the visible UI); ESP source failures use code `input_unavailable`

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

Normal mode applies confirmed `add_note` and `remove_note` actions to the active item, and accepts `finish_customization` to continue the item flow. Blind mode applies validated action events to local cart, category, preference, checkout, and submit state. An `add_item` snapshot can move the visual screen to `preferences` with `active_item_id`; notes remain on that active item. `finish_customization` and `continue_ordering` return the visual screen to the menu. `review_order` opens checkout, and only `confirm_order` is treated as the explicit spoken final confirmation and guarded submit. Blind screen changes do not restart or replace the active Live socket or ESP audio source. `end_session` closes voice and playback, then returns to the two-card start screen so wake listening can restart. Notes are capped at 10 per item, 160 characters each, and deduped. Provider audio plays only in Blind mode. The browser never shows transcripts, model labels, raw errors, recording labels, or Gemini keys/config.

Voice ownership is serialized through one lifecycle controller. At any moment there is at most one `/ws/live` socket; voice input is owned by the backend ESP relay, not the browser. Each socket callback checks the current ownership epoch before acting, so stale renders or async completions cannot open duplicate Live sessions. Rendering itself does not start or stop wake/normal voice sessions; lifecycle changes happen only from explicit mode and navigation events.

### `POST /api/infer`

The browser collects JPEG frames locally every 200 ms for one continuous 2-second window, then sends exactly one clip request. No network requests are made for individual frames.

Deaf-mode gesture capture requests a square 640×640 camera feed at 8 fps. The preview is rendered as a responsive square, and inference frames are center-cropped to 640×640 to match the visible preview without stretching.

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
