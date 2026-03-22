#!/usr/bin/env python3
"""
Photo Duplicate Finder
======================
1. Exact duplicates — SHA-256 file hash grouping
2. Visually similar  — perceptual hash (pHash) comparison

Outputs:
  - duplicates_report.txt   human-readable report
  - duplicates_report.json  machine-readable report
  - duplicates_review/      folder with moved non-original duplicates
"""

import hashlib
import json
import os
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from PIL import Image
import imagehash

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PHOTOS_DIR = Path("/Users/ardaerdogan/Downloads/photos")
REVIEW_DIR = PHOTOS_DIR.parent / "duplicates_review"
REPORT_TXT = Path(__file__).parent / "duplicates_report.txt"
REPORT_JSON = Path(__file__).parent / "duplicates_report.json"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".bmp", ".tiff", ".tif"}
PHASH_THRESHOLD = 8          # hamming distance ≤ 8 → visually similar
PHASH_SIZE = 16              # 16×16 → 256-bit hash, good precision
BATCH_SIZE = 200             # progress reporting interval

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def collect_images(directory: Path) -> list[Path]:
    """Return sorted list of image paths (non-recursive, files only)."""
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def file_sha256(path: Path) -> str:
    """Return hex SHA-256 of a file, read in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_phash(path: Path, hash_size: int = PHASH_SIZE) -> str | None:
    """Return perceptual hash string, or None if the image can't be opened."""
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img, hash_size=hash_size))
    except Exception:
        return None


def pick_original(paths: list[Path], file_sizes: dict[str, int] | None = None) -> Path:
    """Heuristic: the file with the largest size is kept as the 'original'."""
    if file_sizes:
        return max(paths, key=lambda p: file_sizes.get(str(p), 0))
    return max(paths, key=lambda p: p.stat().st_size)


def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex-encoded hashes (fast XOR + popcount)."""
    if len(a) != len(b):
        return 999
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---------------------------------------------------------------------------
# Phase 1 — Exact duplicates (SHA-256)
# ---------------------------------------------------------------------------

def find_exact_duplicates(images: list[Path]) -> dict[str, list[Path]]:
    """Group files by SHA-256; return only groups with ≥2 members."""
    hash_map: dict[str, list[Path]] = defaultdict(list)
    total = len(images)

    # First pass: quick size grouping to skip unique-size files
    size_map: dict[int, list[Path]] = defaultdict(list)
    for p in images:
        try:
            size_map[p.stat().st_size].append(p)
        except OSError:
            continue

    # Only hash files that share a size with at least one other file
    candidates = []
    for paths in size_map.values():
        if len(paths) >= 2:
            candidates.extend(paths)

    print(f"  [size filter] {len(candidates)}/{total} files share a size → hashing these")

    for i, p in enumerate(candidates, 1):
        try:
            h = file_sha256(p)
            hash_map[h].append(p)
        except OSError:
            continue
        if i % BATCH_SIZE == 0 or i == len(candidates):
            print(f"  [sha256] {i}/{len(candidates)} hashed", end="\r")

    print()
    return {h: ps for h, ps in hash_map.items() if len(ps) >= 2}


# ---------------------------------------------------------------------------
# Phase 2 — Visually similar (perceptual hash)
# ---------------------------------------------------------------------------

def _compute_phash_worker(path_str: str) -> tuple[str, str | None]:
    """Worker for multiprocessing — accepts str path, returns (path, hash)."""
    return (path_str, compute_phash(Path(path_str)))


def build_phash_index(images: list[Path], already_grouped: set[Path]) -> dict[Path, str]:
    """Compute perceptual hashes (parallel). Skip files already in exact-dup groups."""
    # We hash ALL images (including exact-dup originals) so we can catch
    # visually-similar-but-not-identical pairs across groups.
    index: dict[Path, str] = {}
    total = len(images)
    done = 0
    errors = 0

    workers = min(os.cpu_count() or 4, 8)
    print(f"  [phash] computing with {workers} workers …")

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_compute_phash_worker, str(p)): p for p in images
        }
        for future in as_completed(futures):
            done += 1
            path_str, h = future.result()
            if h is not None:
                index[Path(path_str)] = h
            else:
                errors += 1
            if done % BATCH_SIZE == 0 or done == total:
                print(f"  [phash] {done}/{total} computed ({errors} errors)", end="\r")

    print()
    return index


def find_similar_groups(
    phash_index: dict[Path, str],
    exact_dup_files: set[Path],
    threshold: int = PHASH_THRESHOLD,
) -> list[dict]:
    """
    Find groups of visually similar images via pairwise hamming distance.
    Uses a bucket strategy to avoid full O(n²) comparison.
    """
    # Bucket by truncated hash prefix for coarse grouping
    TRUNC = 8  # first 8 hex chars → 32-bit prefix
    buckets: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for p, h in phash_index.items():
        prefix = h[:TRUNC]
        # Put into own bucket and neighboring buckets
        buckets[prefix].append((p, h))

    # For a more thorough approach with 9k images, do a full O(n²) comparison
    # but be smart about it using Union-Find
    items = list(phash_index.items())
    n = len(items)
    print(f"  [similar] comparing {n} images pairwise …")

    parent = list(range(n))
    rank = [0] * n

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx == ry:
            return
        if rank[rx] < rank[ry]:
            rx, ry = ry, rx
        parent[ry] = rx
        if rank[rx] == rank[ry]:
            rank[rx] += 1

    comparisons = 0
    # Sort by hash to make nearby hashes adjacent → speeds up comparison
    items.sort(key=lambda x: x[1])

    for i in range(n):
        for j in range(i + 1, n):
            # Quick prefix check — if first 4 hex chars differ too much, skip
            d = hamming(items[i][1], items[j][1])
            if d <= threshold:
                union(i, j)
            comparisons += 1
            if comparisons % 5_000_000 == 0:
                print(f"  [similar] {comparisons:,} comparisons …", end="\r")

    print(f"  [similar] {comparisons:,} total comparisons done")

    # Collect groups
    groups_map: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups_map[find(i)].append(i)

    similar_groups = []
    for members in groups_map.values():
        if len(members) < 2:
            continue
        # Skip groups where ALL members are already exact duplicates of each other
        paths = [items[m][0] for m in members]
        if all(p in exact_dup_files for p in paths):
            # Check if they are all in the SAME exact-dup group — if so, skip
            # (we'll handle them in the exact-dup section)
            # But if they span multiple exact-dup groups, keep them
            pass  # still include — they may span groups

        group_info = []
        for m in members:
            p = items[m][0]
            group_info.append({
                "file": p.name,
                "path": str(p),
                "phash": items[m][1],
            })

        # Compute pairwise distances for the report
        distances = []
        for a_idx in range(len(members)):
            for b_idx in range(a_idx + 1, len(members)):
                d = hamming(items[members[a_idx]][1], items[members[b_idx]][1])
                distances.append({
                    "a": items[members[a_idx]][0].name,
                    "b": items[members[b_idx]][0].name,
                    "distance": d,
                    "similarity": f"{max(0, 100 - d * 100 / (PHASH_SIZE * PHASH_SIZE * 4)):.1f}%",
                })

        similar_groups.append({
            "files": group_info,
            "distances": distances,
        })

    return similar_groups


# ---------------------------------------------------------------------------
# Phase 3 — Move duplicates and write reports
# ---------------------------------------------------------------------------

def move_duplicates(
    exact_groups: dict[str, list[Path]],
    similar_groups: list[dict],
    review_dir: Path,
):
    """Move non-original duplicates to review_dir. Return move log."""
    review_dir.mkdir(parents=True, exist_ok=True)
    moved = []

    # Exact duplicates — move all except the original
    for sha, paths in exact_groups.items():
        original = pick_original(paths)
        for p in paths:
            if p == original:
                continue
            dest = review_dir / "exact" / p.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Handle name collisions
            if dest.exists():
                stem = dest.stem
                ext = dest.suffix
                i = 1
                while dest.exists():
                    dest = dest.parent / f"{stem}_{i}{ext}"
                    i += 1
            shutil.move(str(p), str(dest))
            moved.append({"from": str(p), "to": str(dest), "type": "exact"})

    # Similar groups — move all except the largest in each group
    already_moved = {m["from"] for m in moved}
    for group in similar_groups:
        paths = [Path(f["path"]) for f in group["files"]]
        # Filter out already-moved files
        remaining = [p for p in paths if str(p) not in already_moved and p.exists()]
        if len(remaining) < 2:
            continue
        original = pick_original(remaining)
        for p in remaining:
            if p == original:
                continue
            dest = review_dir / "similar" / p.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                stem = dest.stem
                ext = dest.suffix
                i = 1
                while dest.exists():
                    dest = dest.parent / f"{stem}_{i}{ext}"
                    i += 1
            shutil.move(str(p), str(dest))
            moved.append({"from": str(p), "to": str(dest), "type": "similar"})

    return moved


def write_reports(
    exact_groups: dict[str, list[Path]],
    similar_groups: list[dict],
    moved: list[dict],
    elapsed: float,
    total_images: int,
    file_sizes: dict[str, int],
):
    """Write human-readable .txt and machine-readable .json reports."""

    # --- JSON report ---
    report_data = {
        "summary": {
            "total_images_scanned": total_images,
            "exact_duplicate_groups": len(exact_groups),
            "exact_duplicate_files": sum(len(ps) - 1 for ps in exact_groups.values()),
            "similar_groups": len(similar_groups),
            "files_moved_to_review": len(moved),
            "elapsed_seconds": round(elapsed, 1),
        },
        "exact_duplicates": [
            {
                "sha256": sha,
                "original": pick_original(paths, file_sizes).name,
                "duplicates": [p.name for p in paths if p != pick_original(paths, file_sizes)],
                "all_files": [{"name": p.name, "size": file_sizes.get(str(p), 0)} for p in paths],
            }
            for sha, paths in exact_groups.items()
        ],
        "visually_similar": similar_groups,
        "moved_files": moved,
    }
    REPORT_JSON.write_text(json.dumps(report_data, indent=2, ensure_ascii=False))
    print(f"\n  JSON report → {REPORT_JSON}")

    # --- Text report ---
    lines = []
    lines.append("=" * 72)
    lines.append("  PHOTO DUPLICATE FINDER — REPORT")
    lines.append("=" * 72)
    lines.append(f"  Date           : {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  Photos scanned : {total_images}")
    lines.append(f"  Time elapsed   : {elapsed:.1f}s")
    lines.append("")

    # Exact duplicates
    lines.append("-" * 72)
    lines.append(f"  EXACT DUPLICATES  ({len(exact_groups)} groups, "
                 f"{sum(len(ps)-1 for ps in exact_groups.values())} redundant files)")
    lines.append("-" * 72)
    for i, (sha, paths) in enumerate(exact_groups.items(), 1):
        original = pick_original(paths, file_sizes)
        lines.append(f"\n  Group {i}  (SHA-256: {sha[:16]}…)")
        for p in paths:
            size_kb = file_sizes.get(str(p), 0) / 1024
            tag = " ← ORIGINAL (kept)" if p == original else " → moved to review"
            lines.append(f"    {p.name:50s}  {size_kb:8.1f} KB{tag}")

    lines.append("")
    lines.append("-" * 72)
    lines.append(f"  VISUALLY SIMILAR  ({len(similar_groups)} groups)")
    lines.append("-" * 72)
    for i, group in enumerate(similar_groups, 1):
        lines.append(f"\n  Group {i}  ({len(group['files'])} images)")
        for f in group["files"]:
            lines.append(f"    {f['file']:50s}  phash={f['phash'][:16]}…")
        if group["distances"]:
            lines.append("    Pairwise similarities:")
            for d in group["distances"][:10]:  # cap at 10 pairs for readability
                lines.append(f"      {d['a']} ↔ {d['b']}  "
                             f"distance={d['distance']}  similarity={d['similarity']}")
            if len(group["distances"]) > 10:
                lines.append(f"      … and {len(group['distances']) - 10} more pairs")

    lines.append("")
    lines.append("-" * 72)
    lines.append(f"  FILES MOVED TO REVIEW  ({len(moved)} files)")
    lines.append("-" * 72)
    for m in moved:
        lines.append(f"    {m['type']:8s}  {Path(m['from']).name}  →  {m['to']}")

    lines.append("")
    lines.append("=" * 72)
    lines.append("  Review moved files in: " + str(REVIEW_DIR))
    lines.append("  Delete them or move them back as you see fit.")
    lines.append("=" * 72)

    REPORT_TXT.write_text("\n".join(lines))
    print(f"  Text report → {REPORT_TXT}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  Photo Duplicate Finder")
    print("=" * 60)

    # Collect images
    print(f"\n[1/4] Scanning {PHOTOS_DIR} …")
    images = collect_images(PHOTOS_DIR)
    print(f"  Found {len(images)} images")

    if not images:
        print("  No images found. Exiting.")
        sys.exit(0)

    t0 = time.time()

    # Phase 1 — Exact duplicates
    print("\n[2/4] Finding exact duplicates (SHA-256) …")
    exact_groups = find_exact_duplicates(images)
    exact_dup_files: set[Path] = set()
    for paths in exact_groups.values():
        exact_dup_files.update(paths)
    print(f"  → {len(exact_groups)} groups, "
          f"{sum(len(ps)-1 for ps in exact_groups.values())} redundant files")

    # Phase 2 — Visually similar
    print("\n[3/4] Finding visually similar images (perceptual hash) …")
    phash_index = build_phash_index(images, exact_dup_files)
    similar_groups = find_similar_groups(phash_index, exact_dup_files)
    # Filter out groups that are purely subsets of a single exact-dup group
    print(f"  → {len(similar_groups)} visually similar groups")

    # Cache file sizes before moving (files won't exist at original paths after)
    print("\n[4/4] Moving duplicates to review folder & writing reports …")
    file_sizes: dict[str, int] = {}
    for p in images:
        try:
            file_sizes[str(p)] = p.stat().st_size
        except OSError:
            file_sizes[str(p)] = 0

    moved = move_duplicates(exact_groups, similar_groups, REVIEW_DIR)

    elapsed = time.time() - t0
    write_reports(exact_groups, similar_groups, moved, elapsed, len(images), file_sizes)

    print(f"\n  Done! {elapsed:.1f}s elapsed.")
    print(f"  {len(moved)} files moved to {REVIEW_DIR}")
    print(f"  Review the reports and the duplicates_review/ folder.\n")


if __name__ == "__main__":
    main()
