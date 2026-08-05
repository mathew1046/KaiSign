# Restaurant Ordering Kiosk

Dependency-free frontend assets for the restaurant kiosk. The real integrated experience must be served by the backend so the same-origin API routes below are available; running these files with `python -m http.server` is only useful for static asset checks and will not provide live preferences or order persistence.

## Normalized API contract

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
