# Raspberry Pi 3 deployment

Deploy only the backend app, UI files, `backend/runtime_assets/logistic_sign_classifier.npz`, the Pi service/launcher helpers, and the external MediaPipe hand landmarker. Do not deploy training data, `training/`, sklearn/joblib models, tests, scripts, alternate model artifacts, `.env` files, or secrets.

The deployment target is ARM64 with system Python 3.13.5, but Pi runtime must not use that interpreter: `mediapipe` has no aarch64 cp313 wheel. Provision uv-managed Python 3.12. Never attempt a MediaPipe source build on a 1GB Pi.

The Pi has enough root disk, but `/tmp` tmpfs is only about 450 MB. A normal `mediapipe==0.10.18` dependency resolution pulls heavy unused packages and can fail during extraction. Use the provided two-phase installer: it directs temporary extraction to `$PROJECT_ROOT/.pip-tmp`, installs base runtime packages plus lightweight direct dependencies normally from binary wheels, installs MediaPipe separately with `--no-deps`, then cleans the temp directory.

```bash
cd /home/kaizen/kaizen/custom_dataset_camera/backend
../deploy/pi/install-runtime.sh
```

The installer runs this smoke test before exiting:

```python
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision
```

Set secrets only in local environment files or systemd drop-ins, never in this directory. `GEMINI_LIVE_MAX_SESSIONS=1` is required for a Pi 3 1GB.

The expected Pi layout is:

- repo: `/home/kaizen/kaizen/custom_dataset_camera`
- venv Python: `/home/kaizen/kaizen/custom_dataset_camera/backend/.venv/bin/python` from uv-managed Python 3.12
- hand landmarker: `/home/kaizen/kaizen/wlasl_signs_model/hand_landmarker.task`

Use `runtime-copy-manifest.txt` as the authoritative copy list. Example final copy/check steps from the development machine:

```bash
ssh kaizen@PI_HOST 'mkdir -p /home/kaizen/kaizen/custom_dataset_camera/backend /home/kaizen/kaizen/custom_dataset_camera/deploy /home/kaizen/kaizen/wlasl_signs_model'
scp -r backend/app kaizen@PI_HOST:/home/kaizen/kaizen/custom_dataset_camera/backend/app
scp -r backend/runtime_assets kaizen@PI_HOST:/home/kaizen/kaizen/custom_dataset_camera/backend/runtime_assets
scp backend/requirements-runtime.txt backend/requirements-pi-base.txt backend/requirements-pi-mediapipe.txt backend/README.md kaizen@PI_HOST:/home/kaizen/kaizen/custom_dataset_camera/backend/
scp -r restaurant-order-ui kaizen@PI_HOST:/home/kaizen/kaizen/custom_dataset_camera/restaurant-order-ui
scp -r deploy/pi kaizen@PI_HOST:/home/kaizen/kaizen/custom_dataset_camera/deploy/pi
ssh kaizen@PI_HOST 'mkdir -p /home/kaizen/kaizen/wlasl_signs_model && test -f /home/kaizen/kaizen/wlasl_signs_model/hand_landmarker.task'
```

Copy the external hand landmarker separately if the check fails; source is `../wlasl_signs_model/hand_landmarker.task`, destination is `/home/kaizen/kaizen/wlasl_signs_model/hand_landmarker.task`.

Copy `backend.service` to `/etc/systemd/system/`, then run `sudo systemctl enable --now backend.service`.

Start Chromium with `chromium-kiosk.sh`. The launcher keeps sandbox and GPU behavior intact while disabling unnecessary background features.

Use `measure-memory.sh` during soak tests to inspect system, backend, and Chromium RSS.
