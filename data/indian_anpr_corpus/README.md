---
license: cc-by-4.0
task_categories:
  - image-to-text
language:
  - en
tags:
  - anpr
  - alpr
  - ocr
  - parseq
  - indian-license-plate
  - transformer
size_categories:
  - 10K<n<100K
---

# Indian License Plate Character Recognition (PARSeq & LMDB)

Part of the **Edge-AI Traffic & Vehicle Analytics System** repository by `thundarstrom`.

## Dataset Summary
A curated dataset of **18,537 normalized license plate image crops** aligned strictly with Indian Motor Vehicle Act alphanumeric formats (`^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$`).

### Formats Provided
1. **Raw Crops & Ground Truth**: `parseq_dataset/` with `gt.txt` (Tab-delimited: `filename \t text`).
2. **High-Throughput LMDB Store**: Ready for zero-bottleneck GPU memory-mapped I/O in PyTorch Lightning / PARSeq.

### Splits
* **Train**: 15,756 crops (85%)
* **Val**: 1,854 crops (10%)
* **Test**: 927 crops (5%)
* **Vocabulary**: 36 alphanumeric characters (0-9, A-Z)

---

## How to Access and Download

### Using Automated Project Downloader (Extracts automatically)
```bash
# Clone / pull and auto-extract dataset
python scripts/download_hf_datasets.py --dataset anpr_ocr --org thundarstrom
```

### Using `huggingface_hub` Python SDK
```python
from huggingface_hub import snapshot_download

# Download into local dataset directory
local_path = snapshot_download(
    repo_id="thundarstrom/indian-anpr-ocr-corpus",
    repo_type="dataset",
    local_dir="data/datasets/parseq_dataset"
)
print(f"Dataset downloaded to: {local_path}")
```

---

## Recommended Training / Evaluation Recipe

```bash
python scripts/train_parseq.py --data_dir data/datasets/parseq_lmdb --batch_size 256 --max_epochs 50 --lr 7e-4
```

---

## Citation & Maintainer
* **Maintained by**: `thundarstrom`
* **Project**: Edge-AI Real-Time Traffic Violation Detection & ANPR
* **License**: CC-BY-4.0
