#!/usr/bin/env python3
"""Generate lightweight metrics and an HTML visual review page."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis"


def rel(path: Path, base: Path = ANALYSIS) -> str:
    return html.escape(Path(os.path.relpath(path, base)).as_posix())


def ensure_svg_renders(svg_dir: Path, out_dir: Path, size: int = 640) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    svgs = sorted(svg_dir.glob("*.svg"))
    missing = [svg for svg in svgs if not (out_dir / f"{svg.name}.png").exists()]
    if not missing:
        return
    qlmanage = shutil.which("qlmanage")
    if not qlmanage:
        raise RuntimeError("qlmanage is required to render SVG thumbnails on this machine")
    subprocess.run(
        [qlmanage, "-t", "-s", str(size), "-o", str(out_dir), *map(str, svgs)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def svg_stats(path: Path) -> Dict[str, int]:
    text = path.read_text(encoding="utf-8")
    d_values = re.findall(r'\sd="([^"]+)"', text)
    return {
        "bytes": path.stat().st_size,
        "paths": len(re.findall(r"<path\b", text)),
        "moves": sum(len(re.findall(r"[Mm]", d)) for d in d_values),
        "lines": sum(len(re.findall(r"[Ll]", d)) for d in d_values),
        "cubics": sum(len(re.findall(r"[Cc]", d)) for d in d_values),
    }


def black_mask(path: Path) -> Tuple[int, int, List[bool]]:
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    mask: List[bool] = []
    for r, g, b, a in img.getdata():
        lum = (299 * r + 587 * g + 114 * b) // 1000
        mask.append(a > 16 and lum < 128)
    return w, h, mask


def iou(a: Sequence[bool], b: Sequence[bool]) -> float:
    inter = 0
    union = 0
    for av, bv in zip(a, b):
        if av and bv:
            inter += 1
        if av or bv:
            union += 1
    return inter / union if union else 1.0


def distance_transform(mask: Sequence[bool], width: int, height: int) -> List[int]:
    inf = 10**8
    dist = [0 if mask[i] else inf for i in range(width * height)]
    for y in range(height):
        for x in range(width):
            i = y * width + x
            best = dist[i]
            if x:
                best = min(best, dist[i - 1] + 10)
            if y:
                best = min(best, dist[i - width] + 10)
                if x:
                    best = min(best, dist[i - width - 1] + 14)
                if x + 1 < width:
                    best = min(best, dist[i - width + 1] + 14)
            dist[i] = best
    for y in range(height - 1, -1, -1):
        for x in range(width - 1, -1, -1):
            i = y * width + x
            best = dist[i]
            if x + 1 < width:
                best = min(best, dist[i + 1] + 10)
            if y + 1 < height:
                best = min(best, dist[i + width] + 10)
                if x:
                    best = min(best, dist[i + width - 1] + 14)
                if x + 1 < width:
                    best = min(best, dist[i + width + 1] + 14)
            dist[i] = best
    return dist


def average_mask_distance(source: Sequence[bool], target_dist: Sequence[int]) -> float:
    values = [target_dist[i] / 10.0 for i, on in enumerate(source) if on]
    return sum(values) / len(values) if values else 0.0


def chamfer(a: Sequence[bool], b: Sequence[bool], width: int, height: int) -> float:
    dist_a = distance_transform(a, width, height)
    dist_b = distance_transform(b, width, height)
    return (average_mask_distance(a, dist_b) + average_mask_distance(b, dist_a)) / 2.0


def mean(items: Iterable[float]) -> float:
    items = list(items)
    return sum(items) / len(items) if items else 0.0


def generate_metrics() -> Dict[str, object]:
    smooth_rows = []
    for patched in sorted((ROOT / "outputs/smooth_patched").glob("*.svg")):
        baseline = ROOT / "outputs/smooth_baseline" / patched.name
        base_stats = svg_stats(baseline)
        patched_stats = svg_stats(patched)
        smooth_rows.append(
            {
                "name": patched.stem,
                "baseline": base_stats,
                "patched": patched_stats,
                "byte_delta": patched_stats["bytes"] - base_stats["bytes"],
                "cubic_delta": patched_stats["cubics"] - base_stats["cubics"],
            }
        )

    centerline_rows = []
    for output in sorted((ROOT / "outputs/centerline").glob("*.svg")):
        name = output.stem
        out_render = ANALYSIS / "centerline_renders" / f"{name}.svg.png"
        ref_render = ANALYSIS / "centerline_reference_renders" / f"{name}.svg.png"
        ow, oh, out_mask = black_mask(out_render)
        rw, rh, ref_mask = black_mask(ref_render)
        if (ow, oh) != (rw, rh):
            raise ValueError(f"Render size mismatch for {name}: {(ow, oh)} vs {(rw, rh)}")
        output_stats = svg_stats(output)
        reference_stats = svg_stats(ROOT / "samples/centerline/reference" / output.name)
        centerline_rows.append(
            {
                "name": name,
                "iou": iou(out_mask, ref_mask),
                "chamfer_px": chamfer(out_mask, ref_mask, ow, oh),
                "output": output_stats,
                "reference": reference_stats,
            }
        )

    return {
        "smooth": {
            "rows": smooth_rows,
            "avg_baseline_bytes": mean(row["baseline"]["bytes"] for row in smooth_rows),
            "avg_patched_bytes": mean(row["patched"]["bytes"] for row in smooth_rows),
            "avg_baseline_cubics": mean(row["baseline"]["cubics"] for row in smooth_rows),
            "avg_patched_cubics": mean(row["patched"]["cubics"] for row in smooth_rows),
        },
        "centerline": {
            "rows": centerline_rows,
            "avg_iou": mean(row["iou"] for row in centerline_rows),
            "avg_chamfer_px": mean(row["chamfer_px"] for row in centerline_rows),
            "avg_output_paths": mean(row["output"]["paths"] for row in centerline_rows),
            "avg_reference_paths": mean(row["reference"]["paths"] for row in centerline_rows),
        },
    }


def write_metrics_markdown(metrics: Dict[str, object]) -> None:
    smooth = metrics["smooth"]
    center = metrics["centerline"]
    lines = [
        "# Metrics",
        "",
        "These metrics are QA aids, not a replacement for visual inspection.",
        "",
        "## Experiment A: VTracer Patch",
        "",
        "| Image | Baseline bytes | Patched bytes | Baseline cubics | Patched cubics |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in smooth["rows"]:
        lines.append(
            f"| {row['name']} | {row['baseline']['bytes']} | {row['patched']['bytes']} | "
            f"{row['baseline']['cubics']} | {row['patched']['cubics']} |"
        )
    lines.extend(
        [
            "",
            "## Experiment B: Centerline vs Reference Render",
            "",
            "| Image | IoU | Symmetric Chamfer px | Output paths | Reference paths |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in center["rows"]:
        lines.append(
            f"| {row['name']} | {row['iou']:.3f} | {row['chamfer_px']:.2f} | "
            f"{row['output']['paths']} | {row['reference']['paths']} |"
        )
    lines.extend(
        [
            "",
            f"Average centerline IoU: **{center['avg_iou']:.3f}**",
            f"Average symmetric Chamfer distance: **{center['avg_chamfer_px']:.2f}px**",
            "",
        ]
    )
    (ANALYSIS / "metrics.md").write_text("\n".join(lines), encoding="utf-8")


def write_html(metrics: Dict[str, object]) -> None:
    smooth_rows = []
    for row in metrics["smooth"]["rows"]:
        name = row["name"]
        input_png = ROOT / "samples/smooth_png" / f"{name}.png"
        baseline = ANALYSIS / "baseline_renders" / f"{name}.svg.png"
        patched = ANALYSIS / "patched_renders" / f"{name}.svg.png"
        smooth_rows.append(
            f"""
            <tr>
              <td><strong>{html.escape(name)}</strong><br><small>{row['baseline']['bytes']} -> {row['patched']['bytes']} bytes</small></td>
              <td><img src="{rel(input_png)}" /></td>
              <td><img src="{rel(baseline)}" /></td>
              <td><img src="{rel(patched)}" /></td>
            </tr>
            """
        )

    center_rows = []
    for row in metrics["centerline"]["rows"]:
        name = row["name"]
        input_png = ROOT / "samples/centerline/input" / f"{name}.png"
        output = ANALYSIS / "centerline_renders" / f"{name}.svg.png"
        reference = ANALYSIS / "centerline_reference_renders" / f"{name}.svg.png"
        center_rows.append(
            f"""
            <tr>
              <td><strong>{html.escape(name)}</strong><br><small>IoU {row['iou']:.3f}, Chamfer {row['chamfer_px']:.2f}px</small></td>
              <td><img src="{rel(input_png)}" /></td>
              <td><img src="{rel(output)}" /></td>
              <td><img src="{rel(reference)}" /></td>
            </tr>
            """
        )

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Raster-to-Vector Research Review</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #171717; }}
    h1 {{ margin-bottom: 4px; }}
    h2 {{ margin-top: 36px; }}
    p {{ max-width: 900px; line-height: 1.5; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #ddd; padding: 12px; vertical-align: top; text-align: left; }}
    th {{ background: #f6f6f6; position: sticky; top: 0; }}
    img {{ max-width: 220px; max-height: 220px; object-fit: contain; background: white; }}
    small {{ color: #555; }}
    .metric {{ display: inline-block; margin-right: 18px; padding: 8px 10px; background: #f4f4f4; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>Raster-to-Vector Research Review</h1>
  <p>Compact visual QA page for the generated SVGs. Metrics are included to make the review reproducible, while final judgment remains visual because these are icon/vectorization tasks.</p>

  <h2>Experiment A: Smooth PNG to SVG</h2>
  <p>
    <span class="metric">Avg baseline bytes: {metrics['smooth']['avg_baseline_bytes']:.0f}</span>
    <span class="metric">Avg patched bytes: {metrics['smooth']['avg_patched_bytes']:.0f}</span>
    <span class="metric">Avg baseline cubics: {metrics['smooth']['avg_baseline_cubics']:.1f}</span>
    <span class="metric">Avg patched cubics: {metrics['smooth']['avg_patched_cubics']:.1f}</span>
  </p>
  <table>
    <thead><tr><th>Image</th><th>Input</th><th>Baseline VTracer</th><th>Patched VTracer</th></tr></thead>
    <tbody>{''.join(smooth_rows)}</tbody>
  </table>

  <h2>Experiment B: Centerline Extraction</h2>
  <p>
    <span class="metric">Average IoU: {metrics['centerline']['avg_iou']:.3f}</span>
    <span class="metric">Average Chamfer: {metrics['centerline']['avg_chamfer_px']:.2f}px</span>
    <span class="metric">Avg output paths: {metrics['centerline']['avg_output_paths']:.1f}</span>
    <span class="metric">Avg reference paths: {metrics['centerline']['avg_reference_paths']:.1f}</span>
  </p>
  <table>
    <thead><tr><th>Image</th><th>Input</th><th>Output</th><th>Reference</th></tr></thead>
    <tbody>{''.join(center_rows)}</tbody>
  </table>
</body>
</html>
"""
    (ANALYSIS / "report.html").write_text(html_text, encoding="utf-8")


def main() -> None:
    ensure_svg_renders(ROOT / "outputs/smooth_baseline", ANALYSIS / "baseline_renders")
    ensure_svg_renders(ROOT / "outputs/smooth_patched", ANALYSIS / "patched_renders")
    ensure_svg_renders(ROOT / "outputs/centerline", ANALYSIS / "centerline_renders")
    ensure_svg_renders(ROOT / "samples/centerline/reference", ANALYSIS / "centerline_reference_renders")

    metrics = generate_metrics()
    (ANALYSIS / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    write_metrics_markdown(metrics)
    write_html(metrics)
    print(ANALYSIS / "metrics.md")
    print(ANALYSIS / "report.html")


if __name__ == "__main__":
    main()
