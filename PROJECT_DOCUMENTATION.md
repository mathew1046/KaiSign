# ASL Restaurant Kiosk — Project Documentation

## 1. Project Overview

This project is a restaurant ordering kiosk that supports several ways for a customer to place and customise an order:

- touch and live voice interaction;
- camera-based recognition of a small set of American Sign Language (ASL) words; and
- a voice-led accessibility flow for blind users.

The sign-recognition part of the project was built from a custom, 10-word dataset. We processed the recorded clips into hand-landmark sequences, trained several machine-learning and deep-learning models, compared their results, and kept the strongest candidates for live use and further testing.

---

## 2. Model Training

### Relevant Words and Dataset

The custom dataset contains these ten ASL words:

| Word | Restaurant use |
|---|---|
| `more` | Ask for more of something |
| `less` | Ask for less of something |
| `double` | Request a double portion |
| `cheese` | Refer to cheese |
| `butter` | Refer to butter |
| `sugar` | Refer to sugar |
| `without` | Request that something is removed |
| `add` | Request that something is added |
| `no` | Reject or remove an option |
| `salt` | Refer to salt |

The original dataset was captured as camera video clips in one folder per word. Raw videos are deliberately not stored in this repository. Instead, the repository keeps the processed hand-landmark sequences and metadata used for training and evaluation. This keeps the project smaller and avoids tracking the original camera media.

The recorded dataset snapshot contains:

- **100 valid original clips** across the ten words;
- a stratified split of **70 training clips**, **15 validation clips**, and **15 test clips**;
- **630 augmented training samples** after augmentation is applied to the training split only.

This is a small experimental dataset. It is useful for comparing approaches and building a prototype, but it is not large enough to make broad production-accuracy claims without further independent testing.

### How the Video Data Becomes Model Input

The training pipeline works with hand and finger landmarks rather than full video pixels. This makes the classifier focus on signing movement and hand shape instead of background, lighting, or a person's appearance.

1. The system reads clips from `dataset/<word>/*.mp4`.
2. **MediaPipe** detects up to two hands in each frame.
3. For each frame, it records 42 hand landmarks (21 landmarks for each hand), using their x, y, and z positions.
4. Landmark positions are normalised relative to each wrist. This reduces the effect of where the person is standing in the camera frame.
5. Clips with too few successful hand detections are filtered out.
6. Each usable clip is resampled to a fixed length of **60 frames**.
7. The data is split into training, validation, and test sets while keeping the word classes balanced.
8. Augmentation is applied only to the training data. It includes horizontal flips, small Gaussian noise, scaling, rotation, temporal dropout, and temporal resampling.

After resampling, the normal runtime representation contains 60 frames × 42 landmarks × 3 coordinates, which is **7,560 features** per clip. Using a fixed-size input allows the classical models to work with the same feature shape for every sign.

### Why We Trained Multiple Models

There is no single model type that is always best for a small gesture dataset. We therefore trained a mix of classical machine-learning models and neural sequence models. This gave us a fair comparison between:

- simple linear decision boundaries;
- non-linear and neighbourhood-based classifiers;
- tree ensembles; and
- models designed to learn patterns over time.

The trained models were:

1. Logistic Regression
2. Linear SVM
3. RBF SVM
4. 3-nearest-neighbours (KNN-3)
5. Random Forest
6. Extra Trees Light
7. Histogram Gradient Boosting
8. Lightweight MLP
9. Lightweight LSTM
10. Sign2Sound-style BiLSTM
11. MSPT-style hand-only Transformer

### Recorded Model Metrics

The following table is the recorded validation and held-out test result for each model. Accuracy shows the percentage of correct predictions. Macro-F1 gives every word equal importance, which is useful when checking whether a model works well across all classes rather than only the easiest classes.

| Model | Validation accuracy | Validation macro-F1 | Test accuracy | Test macro-F1 |
|---|---:|---:|---:|---:|
| Logistic Regression | 80.0% | 71.3% | 86.7% | 81.3% |
| Linear SVM | 80.0% | 68.3% | 80.0% | 83.0% |
| RBF SVM | 86.7% | 80.0% | 80.0% | 70.0% |
| KNN-3 | 93.3% | 93.3% | 80.0% | 73.3% |
| Random Forest | 86.7% | 81.3% | 86.7% | 84.7% |
| Extra Trees Light | 93.3% | 86.7% | 86.7% | 84.7% |
| Histogram Gradient Boosting | 93.3% | 86.7% | 73.3% | 65.3% |
| Lightweight MLP | 86.7% | 80.0% | 80.0% | 69.3% |
| Lightweight LSTM | 86.7% | 81.3% | 40.0% | 35.1% |
| Sign2Sound-style BiLSTM | 100.0% | 100.0% | 66.7% | 61.1% |
| MSPT-style hand-only Transformer | 86.7% | 84.7% | 53.3% | 48.3% |

### How the Best Model Was Chosen

The training script ranks models by:

1. highest **test accuracy**; then
2. highest **test macro-F1** when test accuracy is the same.

On the recorded held-out test set, **Random Forest** and **Extra Trees Light** are tied for first place:

- test accuracy: **86.7%**;
- test macro-F1: **84.7%**.

Extra Trees Light is the strongest single recorded experimental candidate after that tie because it also has the highest separate whole-dataset sklearn score: **97.0% overall accuracy** and **97.0% overall macro-F1** over all 100 processed samples.

That whole-dataset figure is useful for comparison, but it is **not** an independent held-out test result. The held-out metrics in the table above are the correct numbers to use when comparing generalisation on this recorded experiment.

The production backend currently defaults to `knn_3.joblib` unless `KIOSK_MODEL_PATH` is changed. In other words, the application default is KNN-3, while Extra Trees Light is the strongest recorded experimental candidate. The model path can be configured when the kiosk is deployed.

### Training Workflow

The full model-development workflow is:

```text
Capture sign clips
        ↓
Extract and normalise MediaPipe hand landmarks
        ↓
Filter weak clips and resample each clip to 60 frames
        ↓
Create stratified train / validation / test splits
        ↓
Augment training data only
        ↓
Train 11 classical and neural models
        ↓
Measure validation and held-out test accuracy and macro-F1
        ↓
Rank models and save results, model files, and metadata
        ↓
Use selected model artifacts in live preview or kiosk inference
```

Training outputs are stored under `training/runs/custom_10_words/`. Important outputs include:

- `processed/hand_sequences.npz` — processed fixed-length landmark data;
- `processed/metadata.json` — labels and sequence information;
- `split_summary.json` — data split and sample information;
- `results.json` — validation/test reports and model ranking;
- `overall_accuracy.json` — separate whole-dataset scores for the sklearn models; and
- `models/` — saved `.joblib` and `.pt` model artifacts.

---

## 3. Dashboard and Interface

The user-facing part of the project is a restaurant ordering kiosk. It is a lightweight static interface built with HTML, CSS, and plain JavaScript, then served by the FastAPI backend from the same origin. It does not use a large frontend framework.

### User Journey

The normal order flow is:

```text
Choose a mode
        ↓
Browse the menu and add items
        ↓
Set preferences using voice or sign recognition
        ↓
Review the cart and total
        ↓
Confirm the order
        ↓
Store the order and show success
```

The interface keeps the menu, cart, preferences, checkout state, and final success state in the browser. The server validates the important actions and performs inference or storage when needed.

### Product Modes

#### Normal Mode

Normal mode is shown on the first screen. The customer uses the touch menu to choose food, then uses a live voice flow to set preferences for the selected item. The browser WebSocket carries session control, state, and Gemini audio output; the backend owns the configured ESP32 WAV stream and forwards validated PCM to Gemini Live.

Examples of normal-mode preferences include adding or removing ingredients after an item has been selected.

#### Deaf Mode

Deaf mode is also shown on the first screen. It keeps the camera-based sign preference flow:

1. The browser opens the camera.
2. It collects JPEG frames locally every 200 milliseconds for about two seconds.
3. The browser sends one complete clip to `POST /api/infer` rather than sending a request for every frame.
4. The backend extracts MediaPipe landmarks, normalises and resamples them in the same way as training, and runs the configured classifier.
5. A high-confidence word is turned into a simple preference message, such as `No cheese`.

The clip is processed as one short signing window. Video can remain visible after a completed result, while further inference pauses once the preference phrase is complete.

#### Blind Mode

Blind mode is intentionally not a first-screen button. It starts after the wake phrase **“Hey Kaizen”** is detected. It is a voice-led ordering experience:

- the browser WebSocket carries `start`/`stop`, state, wake/blind transition, and Gemini output playback;
- the backend reads `ESP_AUDIO_STREAM_URL` (default `http://172.16.162.9/stream`) as a server-only ESP32 WAV stream and forwards validated 16 kHz mono PCM16 to Gemini Live;
- the backend sends the conversation to Gemini Live and validates its requested actions against the kiosk menu;
- spoken audio replies are played in the browser; and
- the customer can browse categories, add items, customise an item, review the order, and explicitly confirm it by voice.

The backend controls the order state so that a voice model cannot submit arbitrary item data or bypass final confirmation.

#### Developer Live Top-3 Preview

There is also a developer-facing live camera preview for comparing model behaviour. It keeps a two-second hand-landmark buffer and runs these three saved sklearn models over the same buffer:

- Extra Trees Light;
- Random Forest; and
- KNN-3.

This mode is useful for observing model predictions in real time. It is separate from the restaurant kiosk's configured backend model.

### Main Backend Routes

| Route | Purpose |
|---|---|
| `GET /api/health` | Checks whether the service is running and whether model assets are available. |
| `POST /api/infer` | Receives a complete camera clip and returns a safe sign-recognition result. |
| `POST /api/orders` | Validates and persists a confirmed restaurant order. |
| `WS /ws/live` | Carries voice session control, Gemini output audio, optional `input_ready`, wake events, and validated order actions. Browser microphone audio is not sent. |

The interface is served by the same FastAPI application. This design keeps the browser API calls and WebSocket connection on the same origin.

---

## 4. End-to-End System Workflow

### Camera Sign Workflow

```text
Customer signs a preference
        ↓
Browser captures a short two-second clip
        ↓
POST /api/infer
        ↓
MediaPipe finds hand landmarks
        ↓
Backend applies the same normalisation and 60-frame resampling as training
        ↓
Configured sklearn model predicts a word and confidence
        ↓
Backend accepts only high-confidence predictions
        ↓
UI shows the completed preference and continues to checkout
```

The backend has safety checks such as ordered clip sequence numbers, a minimum number of detected hand frames, and a minimum probability threshold. Low-confidence or no-hand results do not create a false preference.

### Voice Ordering Workflow

```text
Customer selects Normal mode or triggers Blind mode
        ↓
Browser opens one controlled WebSocket session
        ↓
Backend reads the configured ESP32 WAV stream
        ↓
Backend validates PCM and relays it to Gemini Live
        ↓
Gemini requests a structured order action
        ↓
Backend validates the action against the real menu and current order state
        ↓
Browser updates the cart, preference, or checkout screen
        ↓
Customer explicitly confirms the order
        ↓
POST /api/orders stores the final order
```

### Order Persistence Workflow

When the customer submits an order, the browser sends an `Idempotency-Key`. The backend uses this key and a canonical payload hash so that retrying the same checkout request does not create duplicate orders. The UI shows success only after the server confirms that the order was persisted and returns an order ID.

---

## 5. Technology Stack

### Frontend and Browser Features

| Technology | How it is used |
|---|---|
| HTML, CSS, and vanilla JavaScript | Builds the dependency-free kiosk interface, menu, cart, preferences, checkout, and success screens. |
| Browser `getUserMedia` | Opens the camera for Deaf/sign preference capture only. |
| JPEG frame capture | Creates the camera frames sent together as one short sign-recognition clip. |
| Web Audio | Plays generated Gemini voice audio in the browser. |
| WebSocket | Maintains the real-time voice control/output connection to `/ws/live`; voice input comes from the backend ESP32 stream. |

Using plain JavaScript keeps the interface small and easy to serve from the Python backend. The frontend is still expected to be opened through the backend, not just as static files, because the live API, WebSocket, inference, and persistence routes are part of the same experience.

### Backend and API

| Technology | Version / role |
|---|---|
| Python | Main backend and training language. |
| FastAPI | `0.115.6`; defines the HTTP API, WebSocket route, and static UI mount. |
| Uvicorn | `0.34.0`; runs the FastAPI application locally. |
| HTTPX | `0.28.1`; used for server-side HTTP integrations, including persistence. |
| python-dotenv | `1.0.1`; loads local configuration without placing secrets in source code. |

The backend is responsible for more than routing. It validates request shape, controls the inference protocol, checks WebSocket origin rules, serialises live voice ownership, validates menu actions, and keeps sensitive provider credentials on the server.

### Machine Learning, Computer Vision, and Training

| Technology | Version / role |
|---|---|
| MediaPipe | `0.10.21`; detects hand landmarks for both training and live inference. |
| OpenCV | `4.10.0.84`; reads and processes video/image frames. |
| NumPy | `1.26.4`; stores and transforms landmark arrays. |
| scikit-learn | `1.7.2`; provides the classical classifiers and evaluation utilities. |
| joblib | `1.4.2`; saves and loads sklearn model artifacts. |
| PyTorch | Used to train the MLP, LSTM, BiLSTM, and Transformer variants. |

Using the same MediaPipe extraction, normalisation, and temporal resampling steps in training and runtime is important. If the kiosk processed landmarks differently from the training script, the model would receive data in a different format and results would be unreliable.

### Voice AI

| Technology | Role |
|---|---|
| Google Gen AI SDK (`google-genai`) | `1.56.0`; connects the backend to Gemini Live. |
| Gemini Live | Handles the real-time voice conversation for Normal and Blind mode. |
| Backend tool/action validation | Converts only allowed, structured actions into menu and cart updates. |

The browser never receives the Gemini API key. It connects only to the kiosk's own `/ws/live` endpoint. The backend supplies context, checks actions such as adding an item or reviewing the order, and requires an explicit confirmation before submission.

### Storage and Security

| Technology / control | Role |
|---|---|
| Supabase | Stores confirmed orders in `public.orders`. |
| Row Level Security (RLS) | Is enabled on the orders table; the service-role key stays on the server. |
| Idempotency key and payload hash | Prevent duplicate order records when a checkout request is retried. |
| Server-only environment variables | Protect `SUPABASE_*` and `GEMINI_API_KEY` values. |
| Same-origin WebSocket checks | Restrict who can open the live voice connection. |
| Confidence and frame-count gates | Prevent weak or incomplete camera input from being treated as a completed preference. |

### Dataset Capture

The custom sign clips can be captured with the project capture script using camera/video tools such as **V4L2**, **ffmpeg**, and **OpenCV**. These clips are then processed into the tracked landmark dataset used by the training pipeline.

### Testing and Runtime Validation

| Tool / script | Purpose |
|---|---|
| `pytest` `8.3.4` | Runs backend tests. |
| `python -m compileall app tests` | Checks that backend Python code compiles. |
| `scripts/verify_runtime.py` | Checks available real runtime assets and configuration. |
| `scripts/replay_video.py` | Replays a captured video through the real MediaPipe and KNN path. |
| `scripts/api_replay_clip.py` | Sends a clip through the API and checks the expected inference request behaviour. |

---

## 6. Important Notes and Limitations

- The reported test metrics come from only **15 held-out clips**. A larger, separate dataset with more signers, backgrounds, camera angles, and lighting conditions is needed before making strong production accuracy claims.
- The `overall_accuracy.json` values are measured over the full recorded dataset for seven sklearn models. They should not be treated as replacement test metrics.
- Extra Trees Light and Random Forest tie on the recorded held-out evaluation. The application's default KNN model is a deployment choice, not proof that KNN-3 won the comparison.
- Raw source videos are intentionally excluded from the repository; only processed data and metadata are tracked.
- Model confidence thresholds and runtime checks are deliberately conservative so uncertain predictions do not automatically change a customer's order.

## 7. Source-of-Truth Files

- `README.md`
- `training/README.md`
- `training/train_custom_signs.py`
- `training/runs/custom_10_words/split_summary.json`
- `training/runs/custom_10_words/results.json`
- `training/runs/custom_10_words/overall_accuracy.json`
- `backend/README.md`
- `backend/requirements.txt`
- `restaurant-order-ui/README.md`
