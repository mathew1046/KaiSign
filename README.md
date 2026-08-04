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
