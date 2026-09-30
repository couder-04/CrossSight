---
license: cc-by-4.0
task_categories:
  - image-to-text
language:
  - en
tags:
  - anpr
  - synthetic-data
  - ocr
  - indian-rto-codes
  - data-augmentation
size_categories:
  - 10K<n<100K
---

# Synthetic Indian License Plate Character Generator Corpus

Part of the **Edge-AI Traffic & Vehicle Analytics System** repository by `thundarstrom`.

## Dataset Summary
**18,000 synthetically generated Indian plate crops** covering all 36 Indian states and Union Territories with diverse fonts, spacing, distortion, and noise.
Ideal for pretraining sequence recognition models on rare RTO state codes before real data fine-tuning.

---

## How to Access and Download

### Using Automated Project Downloader (Extracts automatically)
```bash
# Clone / pull and auto-extract dataset
python scripts/download_hf_datasets.py --dataset anpr_synthetic --org thundarstrom
```

### Using `huggingface_hub` Python SDK
```python
from huggingface_hub import snapshot_download

# Download into local dataset directory
local_path = snapshot_download(
    repo_id="thundarstrom/synthetic-indian-anpr-ocr",
    repo_type="dataset",
    local_dir="data/datasets/kaggle_synthetic"
)
print(f"Dataset downloaded to: {local_path}")
```

---

## Recommended Training / Evaluation Recipe

```bash
# Used for PARSeq vocabulary pre-training
```

---

## Citation & Maintainer
* **Maintained by**: `thundarstrom`
* **Project**: Edge-AI Real-Time Traffic Violation Detection & ANPR
* **License**: CC-BY-4.0
