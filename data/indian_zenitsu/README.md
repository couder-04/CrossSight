---
dataset_info:
  features:
  - name: image
    dtype: image
  - name: plate_text
    dtype: string
  - name: source
    dtype: string
  - name: state
    dtype: string
  - name: orig_filename
    dtype: string
  - name: xmin
    dtype: string
  - name: ymin
    dtype: string
  - name: xmax
    dtype: string
  - name: ymax
    dtype: string
  splits:
  - name: train
    num_bytes: 9021914.973
    num_examples: 1709
  download_size: 5924512
  dataset_size: 9021914.973
configs:
- config_name: default
  data_files:
  - split: train
    path: data/train-*
---
