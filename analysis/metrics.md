# Metrics

These metrics are QA aids, not a replacement for visual inspection.

## Experiment A: VTracer Patch

| Image | Baseline bytes | Patched bytes | Baseline cubics | Patched cubics |
|---|---:|---:|---:|---:|
| smooth_01 | 15972 | 16004 | 349 | 349 |
| smooth_02 | 8236 | 8238 | 171 | 171 |
| smooth_03 | 14788 | 14789 | 316 | 316 |
| smooth_04 | 12639 | 12633 | 273 | 273 |
| smooth_05 | 9092 | 9135 | 205 | 205 |
| smooth_06 | 9758 | 9756 | 206 | 206 |

## Experiment B: Centerline vs Reference Render

| Image | IoU | Symmetric Chamfer px | Output paths | Reference paths |
|---|---:|---:|---:|---:|
| ampersand | 0.834 | 0.25 | 41 | 18 |
| arrow-pointer | 0.789 | 0.46 | 43 | 31 |
| arrow-turn-down-left | 0.906 | 0.63 | 8 | 10 |
| letter_H | 0.580 | 1.44 | 9 | 11 |
| letter_K | 0.558 | 1.43 | 8 | 13 |
| number_3 | 0.792 | 0.29 | 12 | 5 |
| number_6 | 0.704 | 0.62 | 9 | 5 |

Average centerline IoU: **0.738**
Average symmetric Chamfer distance: **0.73px**
