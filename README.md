# KaiSign: Accessible Dining for Everyone

KaiSign is a restaurant-ordering prototype by **Team KAIZEN**, designed to help customers select food, communicate preferences, review their cart, and confirm an order through an accessible, shared kiosk experience.

## Award

Our team won a prize for KaiSign in Seeed Studio's **Interactive Signage Contest 2026**.

[Read Seeed Studio's official winners announcement](https://www.seeedstudio.com/blog/2026/10/09/who-won-the-2026-interactive-signage-contest/).

## Ordering experience

| Mode | Current interaction | Customer journey |
|---|---|---|
| Normal | Physical numeric keypad and live voice preferences | Browse the menu, choose quantities, customise the selected item by voice, review, and confirm. |
| Deaf | Physical numeric keypad and camera-based ASL preferences | Select an item, sign supported preference words, review the resulting preference and cart, and confirm. |
| Blind | Voice-led ordering, activated by “Hey Kaizen” | Hear spoken guidance, choose and customise items, review the order, and explicitly confirm by voice. |

Normal and Deaf are the two choices on the start screen. Blind mode begins through the wake phrase rather than a screen button. The current frontend accepts `Numpad0`–`Numpad9` and suppresses mouse, touch, and wheel input; top-row number keys are not substitutes.

Keypad navigation: **8** up, **2** down, **4** left, **6** right, **5** select, and **0** back/cancel.

ASL recognition is limited to the ten words documented below. It supports item preferences, rather than unrestricted sign-language translation. Uncertain predictions do not complete a preference.

## Architecture

| Component | Responsibility |
|---|---|
| [Kiosk frontend](restaurant-order-ui/) | HTML, CSS, and vanilla JavaScript for mode selection, menu, cart, preferences, checkout, camera capture, and audio playback. |
| [FastAPI backend](backend/) | Serves the UI and validates sign requests, voice actions, and confirmed orders. |
| ASL inference | Browser captures a short JPEG clip; MediaPipe extracts hand landmarks; a NumPy logistic-regression runtime predicts supported words with confidence gates. |
| Voice pipeline | ESP32 microphone audio reaches the backend over UDP; Gemini Live handles conversation; `/ws/live` carries browser control, validated state updates, and output audio. |
| Order storage | Supabase persists confirmed orders. Idempotency keys prevent duplicate checkout records on retries. |

The runtime defaults to the tracked `backend/runtime_assets/logistic_sign_classifier.npz` artifact. Voice requires Gemini configuration and an ESP audio stream; order persistence requires Supabase configuration. This is not a fully offline ordering system. Provider credentials remain on the backend.

## Hardware and build demonstration

The [published project build](https://www.hackster.io/kaizen/kaisign-accessible-dining-reimagined-e8b724) documents a Raspberry Pi 3 Model B, Seeed Studio XIAO ESP32-S3 Sense, MPR121 capacitive keypad, camera, display, and speaker, with build photos and demonstration material.

The published hardware uses capacitive controls; the current browser implementation expects numeric-keypad events. Hardware integration must supply those events. ESP32 firmware and enclosure files are not included in this repository.

## Run and deploy

For Raspberry Pi ARM64, use **uv-managed Python 3.12** and the two-phase installer:

```bash
cd backend
../deploy/pi/install-runtime.sh
```

Before starting the backend:

1. Provide the external MediaPipe `hand_landmarker.task` and configure `KIOSK_HAND_LANDMARKER_PATH`.
2. Configure local `backend/.env` using [the environment example](backend/.env.example). Keep Gemini and Supabase secrets server-side.
3. Apply [the Supabase schema](backend/schema.sql) for order persistence and configure the ESP UDP microphone source.
4. Start a single worker and open `http://127.0.0.1:8000` in the kiosk browser:

```bash
cd backend # from the repository root
. .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

See [Pi deployment instructions](deploy/pi/README.md) for the runtime copy manifest, external assets, systemd service, Chromium launcher, and memory checks. Pi 3 deployments require `GEMINI_LIVE_MAX_SESSIONS=1`. Serving the frontend with a standalone static server does not provide inference, voice, or persistence.

## Documentation and limitations

- [Backend configuration and verification](backend/README.md)
- [Frontend interaction and API contract](restaurant-order-ui/README.md)
- [Raspberry Pi deployment](deploy/pi/README.md)
- [Detailed project and experiment notes](PROJECT_DOCUMENTATION.md)

This repository includes the kiosk app, runtime model, deployment helpers, and processed dataset. Some historical experiment notes reference training scripts and model outputs absent from this snapshot. In particular, `training/export_logistic_runtime.py` is referenced by the original deployment pointer below but is not currently tracked; the exported runtime NPZ is included.

The ASL dataset is a small experiment. Broad accuracy, universal accessibility, and production readiness require further independent testing.

---

# Custom ASL Landmark Dataset

This repository contains the preprocessed landmark dataset for a 10-word ASL recognition experiment.

The raw camera videos are intentionally excluded. Only processed hand landmark sequences and their metadata are tracked for evaluation.

## Contents

- `training/runs/custom_10_words/processed/hand_sequences.npz`
- `training/runs/custom_10_words/processed/metadata.json`

## Labels

The dataset includes samples for:

`more`, `less`, `double`, `cheese`, `butter`, `sugar`, `without`, `add`, `no`, `salt`

## Data Format

`hand_sequences.npz` stores fixed-length hand landmark tensors extracted from the source clips. `metadata.json` stores the word label, numeric label, and detected sequence length for each sample.

## Deployment pointer

Pi deployment resources live in `deploy/pi/`. Export the NumPy-only logistic runtime artifact with `training/export_logistic_runtime.py`; do not deploy training data or sklearn/joblib models. Pi ARM64 runtime requires uv-managed Python 3.12 and the two-phase `deploy/pi/install-runtime.sh`; local Python 3.13 is unsupported for Pi MediaPipe.
