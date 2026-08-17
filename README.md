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
