# Short Research Report

## Goal

This repository contains two raster-to-vector experiments with an emphasis on explainable geometry rather than black-box image tooling:

- Experiment A: improve a real PNG-to-SVG vectorizer by modifying VTracer's core smoothing code.
- Experiment B: recover centerline SVG paths from filled black stroke shapes using a no-third-party-library Python implementation.

## Experiment A: Smooth PNG to SVG

I used VTracer as the base system and patched its `visioncortex` core:

- Fixed `PathF64::smooth` so iterative subdivision uses the updated path at each iteration.
- Added cubic Bezier handle clamping after curve fitting to reduce overshoot around corners and raster artifacts.
- Rejected a more aggressive straight-edge regularizer after visual testing because it bent small rectangular icons.

The final patch is intentionally conservative: it preserves VTracer's topology and path structure while changing the actual curve-fitting behavior.

## Experiment B: Centerline Extraction

The implemented pipeline is:

1. Direct PNG decode and binary mask extraction.
2. Zhang-Suen topology-preserving thinning.
3. Chamfer distance transform for radius estimation and spur pruning.
4. Skeleton graph construction using crossing-number and directional-arm junction detection.
5. Graph edge tracing, local smoothing, RDP simplification, and hybrid SVG path emission.

This produces editable stroked SVG paths with `fill="none"`, round caps, and round joins. Straight graph edges are emitted as `L` commands, while genuinely curved edges remain cubic.

## QA Results

Centerline output was raster-compared against the provided reference SVGs:

- Average IoU: **0.738**
- Average symmetric Chamfer distance: **0.73 px**
- Average output path count: **18.6**, with cleaner straight-line primitives on H/K/arrow shapes

The Chamfer score is the more relevant metric here because the centerline strokes are visually aligned even when path decomposition differs. The v2 change improves geometric alignment and emits simpler line primitives for straight edges.

Visual review page:

- `analysis/report.html`
- `analysis/metrics.md`

## Known Limitations

- Junctions can look slightly heavier because independent stroked paths overlap at round joins.
- The pure-Python thinning implementation is slower than OpenCV/scikit-image, but every step is transparent and reviewable.
- Experiment A would benefit from a larger benchmark set with a human-rated smoothness metric.

## Reproduce

```bash
./scripts/run_all.sh
python3 scripts/generate_review_assets.py
```
