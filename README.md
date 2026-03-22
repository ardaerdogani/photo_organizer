# Photo Organizer

A Python tool for detecting and managing duplicate images in large photo collections. Combines byte-level hashing with perceptual hashing to catch both exact copies and visually similar images (resized, recompressed, cropped, etc.).

**Release date:** March 1, 2026

## Features

- **Exact duplicate detection** — Groups identical files using SHA-256 hashing, with a file-size pre-filter to skip unnecessary hash computations
- **Visual similarity detection** — Uses perceptual hashing (pHash) to find images that look the same but differ at the byte level (different resolution, compression, slight edits)
- **Non-destructive workflow** — Never deletes files automatically; moves suspected duplicates to a review folder (`duplicates_review/`) for manual inspection
- **Detailed reports** — Generates both human-readable (`.txt`) and machine-readable (`.json`) reports with similarity scores, group breakdowns, and move logs
- **Performance** — Parallel perceptual hash computation via multiprocessing; Union-Find grouping for efficient clustering

## How It Works

1. **Scan** — Collects all image files (`.jpg`, `.jpeg`, `.png`, `.heic`, `.webp`, `.bmp`, `.tiff`) from the target directory
2. **Phase 1: Exact duplicates** — Files with the same size are hashed with SHA-256; identical hashes are grouped together
3. **Phase 2: Visual similarity** — Every image gets a 256-bit perceptual hash (pHash); images within a Hamming distance of 8 are grouped as visually similar
4. **Review** — Non-original duplicates are moved to `duplicates_review/exact/` and `duplicates_review/similar/` subfolders. The largest file in each group is kept as the original
5. **Report** — A full report is written to `duplicates_report.txt` and `duplicates_report.json`

## Requirements

- Python 3.10+
- [Pillow](https://pypi.org/project/Pillow/)
- [ImageHash](https://pypi.org/project/ImageHash/)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install Pillow imagehash
```

## Usage

Edit `PHOTOS_DIR` in `find_duplicates.py` to point to your photos folder, then run:

```bash
python find_duplicates.py
```

### Output

```
duplicates_report.txt   — Human-readable report with groups and similarity scores
duplicates_report.json  — Machine-readable report for further processing
duplicates_review/
  exact/                — Byte-identical duplicates (safe to delete)
  similar/              — Visually similar images (review before deleting)
```

## Configuration

Key constants in `find_duplicates.py`:

| Variable | Default | Description |
|---|---|---|
| `PHOTOS_DIR` | `/path/to/photos` | Directory to scan |
| `PHASH_THRESHOLD` | `8` | Max Hamming distance for visual similarity (lower = stricter) |
| `PHASH_SIZE` | `16` | Perceptual hash resolution (16 = 256-bit hash) |

## Contributor

- **Arda Erdogan** ([@erdoganxarda](https://github.com/erdoganxarda))

## License

MIT
