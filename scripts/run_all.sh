#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$ROOT/vendor/vtracer" ] || [ ! -d "$ROOT/vendor/visioncortex" ]; then
  echo "Cloning VTracer and VisionCortex..."
  mkdir -p "$ROOT/vendor"
  git clone --depth 1 https://github.com/visioncortex/vtracer.git "$ROOT/vendor/vtracer"
  git clone --depth 1 https://github.com/visioncortex/visioncortex.git "$ROOT/vendor/visioncortex"
  git -C "$ROOT/vendor/vtracer" apply "$ROOT/patches/vtracer.patch"
  git -C "$ROOT/vendor/visioncortex" apply "$ROOT/patches/visioncortex.patch"
fi

echo "Building patched VTracer..."
cargo build --release -p vtracer --manifest-path "$ROOT/vendor/vtracer/Cargo.toml"

echo "Generating smooth PNG -> SVG outputs..."
mkdir -p "$ROOT/outputs/smooth_patched"
for img in "$ROOT"/samples/smooth_png/*.png; do
  name="$(basename "$img" .png)"
  "$ROOT/vendor/vtracer/target/release/vtracer" \
    --input "$img" \
    --output "$ROOT/outputs/smooth_patched/${name}.svg" \
    --colormode bw \
    --mode spline \
    --filter_speckle 4 \
    --corner_threshold 60 \
    --segment_length 4 \
    --splice_threshold 45 \
    --path_precision 3
done

echo "Generating centerline SVG outputs..."
python3 "$ROOT/centerline/centerline.py" \
  "$ROOT/samples/centerline/input" \
  "$ROOT/outputs/centerline" \
  --stroke-width 45 \
  --precision 2 \
  --simplify-epsilon 2.2 \
  --smooth-rounds 2 \
  --line-epsilon 2.8

echo "Done. Outputs are in:"
echo "  $ROOT/outputs/smooth_patched"
echo "  $ROOT/outputs/centerline"
