#!/usr/bin/env python3
"""
Centerline extraction for black filled stroke shapes.

The script intentionally uses only the Python standard library. It implements:
  - a small PNG reader for 8-bit grayscale/RGB/RGBA images,
  - binary mask extraction,
  - Zhang-Suen topology-preserving thinning,
  - chamfer distance transform for pruning/width estimation,
  - skeleton-to-graph tracing,
  - Ramer-Douglas-Peucker simplification and Catmull-Rom-to-Bezier SVG output.
"""

from __future__ import annotations

import argparse
import math
import statistics
import struct
import sys
import zlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


Point = Tuple[int, int]
PointF = Tuple[float, float]


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
NEIGHBORS_8: Tuple[Point, ...] = (
    (0, -1),
    (1, -1),
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
)


@dataclass(frozen=True)
class RGBAImage:
    width: int
    height: int
    pixels: bytes


@dataclass
class GraphPath:
    points: List[PointF]
    closed: bool = False


def read_png(path: Path) -> RGBAImage:
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError(f"{path} is not a PNG file")

    pos = len(PNG_SIGNATURE)
    width = height = color_type = bit_depth = None
    idat = bytearray()

    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        pos += 12 + length

        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if compression != 0 or filter_method != 0 or interlace != 0:
                raise ValueError("Unsupported PNG compression/filter/interlace mode")
            if bit_depth != 8:
                raise ValueError("Only 8-bit PNGs are supported")
            if color_type not in (0, 2, 4, 6):
                raise ValueError(f"Unsupported PNG color type: {color_type}")
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            break

    if width is None or height is None or color_type is None:
        raise ValueError("PNG is missing IHDR")

    channels_by_type = {0: 1, 2: 3, 4: 2, 6: 4}
    channels = channels_by_type[color_type]
    stride = width * channels
    raw = zlib.decompress(bytes(idat))
    expected = height * (stride + 1)
    if len(raw) != expected:
        raise ValueError(f"Unexpected PNG payload length: got {len(raw)}, expected {expected}")

    rows: List[bytearray] = []
    out = bytearray(width * height * 4)
    src = 0
    prev = bytearray(stride)
    dst = 0

    for _y in range(height):
        filter_type = raw[src]
        src += 1
        row = bytearray(raw[src : src + stride])
        src += stride
        recon = _unfilter_scanline(row, prev, channels, filter_type)
        rows.append(recon)
        prev = recon

        for x in range(width):
            base = x * channels
            if color_type == 0:
                g = recon[base]
                r = g
                b = g
                a = 255
            elif color_type == 2:
                r, g, b = recon[base : base + 3]
                a = 255
            elif color_type == 4:
                g, a = recon[base : base + 2]
                r = g
                b = g
            else:
                r, g, b, a = recon[base : base + 4]
            out[dst : dst + 4] = bytes((r, g, b, a))
            dst += 4

    return RGBAImage(width, height, bytes(out))


def _unfilter_scanline(row: bytearray, prev: bytearray, bpp: int, filter_type: int) -> bytearray:
    if filter_type == 0:
        return row
    recon = bytearray(row)

    if filter_type == 1:
        for i, val in enumerate(row):
            left = recon[i - bpp] if i >= bpp else 0
            recon[i] = (val + left) & 0xFF
    elif filter_type == 2:
        for i, val in enumerate(row):
            recon[i] = (val + prev[i]) & 0xFF
    elif filter_type == 3:
        for i, val in enumerate(row):
            left = recon[i - bpp] if i >= bpp else 0
            up = prev[i]
            recon[i] = (val + ((left + up) >> 1)) & 0xFF
    elif filter_type == 4:
        for i, val in enumerate(row):
            left = recon[i - bpp] if i >= bpp else 0
            up = prev[i]
            up_left = prev[i - bpp] if i >= bpp else 0
            recon[i] = (val + _paeth(left, up, up_left)) & 0xFF
    else:
        raise ValueError(f"Unsupported PNG filter type: {filter_type}")
    return recon


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def luminance(r: int, g: int, b: int) -> int:
    return (299 * r + 587 * g + 114 * b) // 1000


def otsu_threshold(img: RGBAImage) -> int:
    hist = [0] * 256
    for i in range(0, len(img.pixels), 4):
        r, g, b, a = img.pixels[i : i + 4]
        if a > 16:
            hist[luminance(r, g, b)] += 1

    total = sum(hist)
    if total == 0:
        return 128

    sum_total = sum(i * count for i, count in enumerate(hist))
    sum_back = 0
    weight_back = 0
    best_var = -1.0
    best_threshold = 128

    for t, count in enumerate(hist):
        weight_back += count
        if weight_back == 0:
            continue
        weight_fore = total - weight_back
        if weight_fore == 0:
            break
        sum_back += t * count
        mean_back = sum_back / weight_back
        mean_fore = (sum_total - sum_back) / weight_fore
        between = weight_back * weight_fore * (mean_back - mean_fore) ** 2
        if between > best_var:
            best_var = between
            best_threshold = t

    # Anti-aliased black-on-white strokes benefit from including light gray edge pixels.
    return max(best_threshold, 210)


def image_to_mask(img: RGBAImage, threshold: Optional[int] = None) -> List[bool]:
    threshold = otsu_threshold(img) if threshold is None else threshold
    mask = [False] * (img.width * img.height)
    for y in range(img.height):
        row = y * img.width
        for x in range(img.width):
            i = (row + x) * 4
            r, g, b, a = img.pixels[i : i + 4]
            if a > 16 and luminance(r, g, b) < threshold:
                mask[row + x] = True
    return mask


def mask_to_set(mask: Sequence[bool], width: int, height: int) -> Set[Point]:
    return {(x, y) for y in range(height) for x in range(width) if mask[y * width + x]}


def zhang_suen_thinning(mask: Sequence[bool], width: int, height: int) -> Set[Point]:
    pixels = mask_to_set(mask, width, height)
    if not pixels:
        return pixels

    changed = True
    while changed:
        changed = False
        for phase in (0, 1):
            to_delete: List[Point] = []
            for x, y in pixels:
                if x <= 0 or y <= 0 or x >= width - 1 or y >= height - 1:
                    continue
                n = [
                    (x, y - 1) in pixels,
                    (x + 1, y - 1) in pixels,
                    (x + 1, y) in pixels,
                    (x + 1, y + 1) in pixels,
                    (x, y + 1) in pixels,
                    (x - 1, y + 1) in pixels,
                    (x - 1, y) in pixels,
                    (x - 1, y - 1) in pixels,
                ]
                count = sum(n)
                if count < 2 or count > 6:
                    continue
                transitions = sum((not n[i] and n[(i + 1) % 8]) for i in range(8))
                if transitions != 1:
                    continue
                p2, _p3, p4, _p5, p6, _p7, p8, _p9 = n
                if phase == 0:
                    if p2 and p4 and p6:
                        continue
                    if p4 and p6 and p8:
                        continue
                else:
                    if p2 and p4 and p8:
                        continue
                    if p2 and p6 and p8:
                        continue
                to_delete.append((x, y))

            if to_delete:
                changed = True
                pixels.difference_update(to_delete)

    return pixels


def chamfer_distance(mask: Sequence[bool], width: int, height: int) -> List[int]:
    inf = 10**9
    dist = [inf if mask[i] else 0 for i in range(width * height)]

    for y in range(height):
        for x in range(width):
            idx = y * width + x
            if not mask[idx]:
                continue
            best = dist[idx]
            if x > 0:
                best = min(best, dist[idx - 1] + 10)
            if y > 0:
                best = min(best, dist[idx - width] + 10)
                if x > 0:
                    best = min(best, dist[idx - width - 1] + 14)
                if x + 1 < width:
                    best = min(best, dist[idx - width + 1] + 14)
            dist[idx] = best

    for y in range(height - 1, -1, -1):
        for x in range(width - 1, -1, -1):
            idx = y * width + x
            if not mask[idx]:
                continue
            best = dist[idx]
            if x + 1 < width:
                best = min(best, dist[idx + 1] + 10)
            if y + 1 < height:
                best = min(best, dist[idx + width] + 10)
                if x > 0:
                    best = min(best, dist[idx + width - 1] + 14)
                if x + 1 < width:
                    best = min(best, dist[idx + width + 1] + 14)
            dist[idx] = best

    return dist


def neighbor_points(p: Point, pixels: Set[Point]) -> List[Point]:
    x, y = p
    return [(x + dx, y + dy) for dx, dy in NEIGHBORS_8 if (x + dx, y + dy) in pixels]


def degree(p: Point, pixels: Set[Point]) -> int:
    return len(neighbor_points(p, pixels))


def neighbor_component_count(p: Point, pixels: Set[Point]) -> int:
    x, y = p
    ring = [(x + dx, y + dy) in pixels for dx, dy in NEIGHBORS_8]
    if not any(ring):
        return 0
    return sum((not ring[i] and ring[(i + 1) % 8]) for i in range(8))


def directional_arm_count(p: Point, pixels: Set[Point], min_radius: int = 2, max_radius: int = 6) -> int:
    x, y = p
    arms = 0
    for dx, dy in NEIGHBORS_8:
        if any((x + dx * r, y + dy * r) in pixels for r in range(min_radius, max_radius + 1)):
            arms += 1
    return arms


def is_graph_node(p: Point, pixels: Set[Point]) -> bool:
    neighbor_count = degree(p, pixels)
    if neighbor_count <= 1:
        return True
    return neighbor_component_count(p, pixels) >= 3 or directional_arm_count(p, pixels) >= 3


def prune_short_spurs(skel: Set[Point], dist: Sequence[int], width: int, max_passes: int = 8) -> Set[Point]:
    pixels = set(skel)
    if not pixels:
        return pixels

    radii = [dist[y * width + x] / 10.0 for x, y in pixels if dist[y * width + x] < 10**8]
    median_radius = statistics.median(radii) if radii else 20.0
    min_spur_len = max(8, int(0.85 * median_radius))

    for _ in range(max_passes):
        endpoints = [p for p in pixels if degree(p, pixels) == 1]
        removed_any = False
        for endpoint in endpoints:
            if endpoint not in pixels:
                continue
            chain = [endpoint]
            prev: Optional[Point] = None
            cur = endpoint
            while True:
                ns = [n for n in neighbor_points(cur, pixels) if n != prev]
                if len(ns) != 1:
                    break
                nxt = ns[0]
                if degree(nxt, pixels) != 2:
                    chain.append(nxt)
                    break
                chain.append(nxt)
                prev, cur = cur, nxt
                if len(chain) > min_spur_len:
                    break
            if len(chain) <= min_spur_len and len(chain) > 1:
                for p in chain[:-1]:
                    pixels.discard(p)
                removed_any = True
        if not removed_any:
            break

    return pixels


def cluster_nodes(skel: Set[Point], dist: Sequence[int], width: int) -> Tuple[Dict[Point, int], Dict[int, PointF]]:
    node_pixels = {p for p in skel if is_graph_node(p, skel)}
    point_to_node: Dict[Point, int] = {}
    node_centers: Dict[int, PointF] = {}
    visited: Set[Point] = set()
    node_id = 0

    for start in node_pixels:
        if start in visited:
            continue
        queue = deque([start])
        visited.add(start)
        cluster: List[Point] = []
        while queue:
            p = queue.popleft()
            cluster.append(p)
            for n in neighbor_points(p, node_pixels):
                if n not in visited:
                    visited.add(n)
                    queue.append(n)

        total_w = 0.0
        sx = 0.0
        sy = 0.0
        for x, y in cluster:
            w = max(1.0, dist[y * width + x] / 10.0)
            total_w += w
            sx += (x + 0.5) * w
            sy += (y + 0.5) * w
            point_to_node[(x, y)] = node_id
        node_centers[node_id] = (sx / total_w, sy / total_w)
        node_id += 1

    return point_to_node, node_centers


def trace_graph_paths(skel: Set[Point], dist: Sequence[int], width: int) -> List[GraphPath]:
    point_to_node, node_centers = cluster_nodes(skel, dist, width)
    visited_edges: Set[Tuple[Point, Point]] = set()
    paths: List[GraphPath] = []

    def edge_key(a: Point, b: Point) -> Tuple[Point, Point]:
        return (a, b) if a <= b else (b, a)

    def mark(a: Point, b: Point) -> None:
        visited_edges.add(edge_key(a, b))

    def is_marked(a: Point, b: Point) -> bool:
        return edge_key(a, b) in visited_edges

    for node_pixel, node_id in list(point_to_node.items()):
        for nxt in neighbor_points(node_pixel, skel):
            if point_to_node.get(nxt) == node_id or is_marked(node_pixel, nxt):
                continue
            raw: List[PointF] = [node_centers[node_id]]
            prev = node_pixel
            cur = nxt
            mark(prev, cur)

            while True:
                end_node = point_to_node.get(cur)
                if end_node is not None:
                    raw.append(node_centers[end_node])
                    break

                raw.append((cur[0] + 0.5, cur[1] + 0.5))
                candidates = [n for n in neighbor_points(cur, skel) if n != prev]
                if not candidates:
                    break
                if len(candidates) == 1:
                    nxt2 = candidates[0]
                else:
                    nxt2 = choose_straightest(prev, cur, candidates)
                if is_marked(cur, nxt2):
                    break
                mark(cur, nxt2)
                prev, cur = cur, nxt2

            if path_length(raw) >= 3:
                paths.append(GraphPath(raw, closed=False))

    remaining_edges = []
    for p in skel:
        for n in neighbor_points(p, skel):
            if p < n and not is_marked(p, n):
                remaining_edges.append((p, n))

    for start, nxt in remaining_edges:
        if is_marked(start, nxt):
            continue
        raw = [(start[0] + 0.5, start[1] + 0.5)]
        prev = start
        cur = nxt
        mark(prev, cur)
        closed = False
        while True:
            raw.append((cur[0] + 0.5, cur[1] + 0.5))
            candidates = [n for n in neighbor_points(cur, skel) if n != prev]
            if not candidates:
                break
            nxt2 = choose_straightest(prev, cur, candidates)
            if nxt2 == start:
                mark(cur, nxt2)
                closed = True
                break
            if is_marked(cur, nxt2):
                break
            mark(cur, nxt2)
            prev, cur = cur, nxt2
        if path_length(raw) >= 8:
            paths.append(GraphPath(raw, closed=closed))

    paths.sort(key=lambda p: path_length(p.points), reverse=True)
    return paths


def choose_straightest(prev: Point, cur: Point, candidates: Sequence[Point]) -> Point:
    vx = cur[0] - prev[0]
    vy = cur[1] - prev[1]
    best = candidates[0]
    best_score = -10**9
    for n in candidates:
        wx = n[0] - cur[0]
        wy = n[1] - cur[1]
        score = vx * wx + vy * wy
        if score > best_score:
            best = n
            best_score = score
    return best


def path_length(points: Sequence[PointF]) -> float:
    return sum(distance(points[i - 1], points[i]) for i in range(1, len(points)))


def distance(a: PointF, b: PointF) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def smooth_polyline(points: Sequence[PointF], closed: bool, rounds: int = 2) -> List[PointF]:
    pts = list(points)
    if len(pts) <= 3:
        return pts

    for _ in range(rounds):
        new_pts: List[PointF] = []
        n = len(pts)
        for i, p in enumerate(pts):
            if not closed and (i == 0 or i == n - 1):
                new_pts.append(p)
                continue
            prev = pts[(i - 1) % n]
            nxt = pts[(i + 1) % n]
            new_pts.append(((prev[0] + 2 * p[0] + nxt[0]) / 4.0, (prev[1] + 2 * p[1] + nxt[1]) / 4.0))
        pts = new_pts
    return pts


def rdp(points: Sequence[PointF], epsilon: float) -> List[PointF]:
    if len(points) <= 2:
        return list(points)
    start = points[0]
    end = points[-1]
    max_dist = -1.0
    index = 0
    for i in range(1, len(points) - 1):
        d = point_line_distance(points[i], start, end)
        if d > max_dist:
            index = i
            max_dist = d
    if max_dist > epsilon:
        left = rdp(points[: index + 1], epsilon)
        right = rdp(points[index:], epsilon)
        return left[:-1] + right
    return [start, end]


def simplify_closed(points: Sequence[PointF], epsilon: float) -> List[PointF]:
    if len(points) <= 4:
        return list(points)
    # Break a closed loop at the point farthest from the centroid to avoid a weak arbitrary chord.
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    start = max(range(len(points)), key=lambda i: (points[i][0] - cx) ** 2 + (points[i][1] - cy) ** 2)
    rotated = list(points[start:]) + list(points[:start]) + [points[start]]
    simplified = rdp(rotated, epsilon)
    if len(simplified) > 1 and distance(simplified[0], simplified[-1]) < 1e-6:
        simplified.pop()
    return simplified


def point_line_distance(p: PointF, a: PointF, b: PointF) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return distance(p, a)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    proj = (ax + t * dx, ay + t * dy)
    return distance(p, proj)


def catmull_rom_svg(points: Sequence[PointF], closed: bool, precision: int = 2, tension: float = 0.85) -> str:
    pts = list(points)
    if not pts:
        return ""
    if len(pts) == 1:
        return f"M{fmt(pts[0][0], precision)} {fmt(pts[0][1], precision)}"
    if len(pts) == 2:
        return (
            f"M{fmt(pts[0][0], precision)} {fmt(pts[0][1], precision)} "
            f"L{fmt(pts[1][0], precision)} {fmt(pts[1][1], precision)}"
        )

    out = [f"M{fmt(pts[0][0], precision)} {fmt(pts[0][1], precision)}"]
    n = len(pts)
    seg_count = n if closed else n - 1
    for i in range(seg_count):
        p0 = pts[(i - 1) % n] if closed or i > 0 else pts[i]
        p1 = pts[i % n]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if closed or i + 2 < n else pts[(i + 1) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) * tension / 6.0, p1[1] + (p2[1] - p0[1]) * tension / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) * tension / 6.0, p2[1] - (p3[1] - p1[1]) * tension / 6.0)
        out.append(
            "C"
            f"{fmt(c1[0], precision)} {fmt(c1[1], precision)} "
            f"{fmt(c2[0], precision)} {fmt(c2[1], precision)} "
            f"{fmt(p2[0], precision)} {fmt(p2[1], precision)}"
        )
    if closed:
        out.append("Z")
    return " ".join(out)


def line_fit_error(points: Sequence[PointF]) -> float:
    if len(points) <= 2:
        return 0.0
    start = points[0]
    end = points[-1]
    return max(point_line_distance(p, start, end) for p in points[1:-1])


def polyline_to_svg(
    raw_points: Sequence[PointF],
    fitted_points: Sequence[PointF],
    closed: bool,
    precision: int,
    line_epsilon: float,
) -> str:
    if not fitted_points:
        return ""
    if not closed and len(fitted_points) >= 2 and line_fit_error(raw_points) <= line_epsilon:
        a = fitted_points[0]
        b = fitted_points[-1]
        return (
            f"M{fmt(a[0], precision)} {fmt(a[1], precision)} "
            f"L{fmt(b[0], precision)} {fmt(b[1], precision)}"
        )
    return catmull_rom_svg(fitted_points, closed, precision=precision)


def fmt(value: float, precision: int) -> str:
    text = f"{value:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text else "0"


def estimate_stroke_width(skel: Set[Point], dist: Sequence[int], width: int, default: float = 45.0) -> float:
    samples = []
    for x, y in skel:
        d = dist[y * width + x]
        if d < 10**8:
            samples.append(2.0 * d / 10.0)
    if not samples:
        return default
    median_width = statistics.median(samples)
    return min(70.0, max(30.0, median_width))


def paths_to_svg(
    paths: Sequence[GraphPath],
    width: int,
    height: int,
    stroke_width: float,
    precision: int,
    simplify_epsilon: float,
    smooth_rounds: int,
    line_epsilon: float,
) -> str:
    body = []
    for i, path in enumerate(paths, 1):
        smoothed = smooth_polyline(path.points, path.closed, rounds=smooth_rounds)
        if path.closed:
            simplified = simplify_closed(smoothed, epsilon=simplify_epsilon)
        else:
            simplified = rdp(smoothed, epsilon=simplify_epsilon)
        if len(simplified) < 2:
            continue
        d = polyline_to_svg(path.points, simplified, path.closed, precision=precision, line_epsilon=line_epsilon)
        body.append(
            f'  <path id="path-{i}" d="{d}" stroke="#000000" '
            f'stroke-width="{fmt(stroke_width, 1)}" fill="none" '
            'stroke-linecap="round" stroke-linejoin="round" />'
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        '<g id="centerline-paths">\n'
        + "\n".join(body)
        + "\n</g>\n</svg>\n"
    )


def extract_centerline(
    png_path: Path,
    svg_path: Path,
    threshold: Optional[int],
    stroke_width: Optional[float],
    precision: int,
    simplify_epsilon: float,
    smooth_rounds: int,
    line_epsilon: float,
) -> None:
    img = read_png(png_path)
    mask = image_to_mask(img, threshold=threshold)
    dist = chamfer_distance(mask, img.width, img.height)
    skeleton = zhang_suen_thinning(mask, img.width, img.height)
    skeleton = prune_short_spurs(skeleton, dist, img.width)
    paths = trace_graph_paths(skeleton, dist, img.width)
    width = stroke_width if stroke_width is not None else estimate_stroke_width(skeleton, dist, img.width)
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(
        paths_to_svg(paths, img.width, img.height, width, precision, simplify_epsilon, smooth_rounds, line_epsilon),
        encoding="utf-8",
    )


def iter_pngs(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
    else:
        yield from sorted(path.glob("*.png"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract SVG centerlines from black PNG strokes.")
    parser.add_argument("input", type=Path, help="Input PNG file or directory")
    parser.add_argument("output", type=Path, help="Output SVG file or directory")
    parser.add_argument("--threshold", type=int, default=None, help="Foreground threshold; default uses Otsu with AA bias")
    parser.add_argument("--stroke-width", type=float, default=None, help="Override output stroke width")
    parser.add_argument("--precision", type=int, default=2, help="Decimal places in SVG path data")
    parser.add_argument("--simplify-epsilon", type=float, default=2.2, help="RDP simplification tolerance in pixels")
    parser.add_argument("--smooth-rounds", type=int, default=2, help="Number of local averaging passes before RDP")
    parser.add_argument("--line-epsilon", type=float, default=2.8, help="Max line-fit error before emitting a cubic path")
    args = parser.parse_args(argv)

    inputs = list(iter_pngs(args.input))
    if not inputs:
        print(f"No PNG files found in {args.input}", file=sys.stderr)
        return 2

    if len(inputs) == 1 and args.output.suffix.lower() == ".svg":
        output_paths = [args.output]
    else:
        args.output.mkdir(parents=True, exist_ok=True)
        output_paths = [args.output / (p.stem + ".svg") for p in inputs]

    for src, dst in zip(inputs, output_paths):
        extract_centerline(
            src,
            dst,
            args.threshold,
            args.stroke_width,
            args.precision,
            args.simplify_epsilon,
            args.smooth_rounds,
            args.line_epsilon,
        )
        print(f"{src.name} -> {dst}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
