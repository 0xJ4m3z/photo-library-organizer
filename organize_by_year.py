import argparse
import re
import time
from pathlib import Path

# Matches:
# 20160817_124851.jpg
# 20160817_124851_01.jpg
NAME_RE = re.compile(r"^(?P<year>\d{4})\d{4}_\d{6}(?:_\d{2})?\.[A-Za-z0-9]+$")

TARGET_EXTS = {
    ".jpg", ".jpeg", ".png",
    ".cr2", ".dng",
    ".mov", ".avi", ".3gp",
    ".gif", ".mp4",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def print_progress(done: int, total: int, start: float, moved: int, skipped: int) -> None:
    elapsed = max(0.001, time.time() - start)
    rate = done / elapsed
    remaining = max(0, total - done)
    eta = remaining / rate if rate > 0 else float("inf")

    h = int(eta // 3600)
    m = int((eta % 3600) // 60)
    s = int(eta % 60)

    pct = (done / total * 100) if total else 100
    line = (
        f"[YEAR] {done:,}/{total:,} ({pct:5.1f}%) | "
        f"moved {moved:,} | skipped {skipped:,} | "
        f"{rate:,.1f} files/s | ETA {h:02d}:{m:02d}:{s:02d}"
    )
    print("\r" + line + " " * 5, end="", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Organize all_photos into year folders based on filename.")
    ap.add_argument("all_photos", help="Path to all_photos folder")
    ap.add_argument("--dry-run", action="store_true", help="Show what would happen without moving files")
    ap.add_argument("--progress-every", type=int, default=50)
    args = ap.parse_args()

    root = Path(args.all_photos)
    if not root.exists():
        raise SystemExit(f"Folder does not exist: {root}")

    log(f"Organizing by year in: {root}")
    if args.dry_run:
        log("DRY RUN ENABLED — no files will be moved")
    log("")

    files = [
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in TARGET_EXTS
    ]

    total = len(files)
    log(f"Found {total:,} files to evaluate.\n")

    moved = 0
    skipped = 0
    done = 0
    start = time.time()

    for p in files:
        done += 1

        m = NAME_RE.match(p.name)
        if not m:
            skipped += 1
            log(f"[SKIP] {p.name} (does not match expected filename format)")
            continue

        year = m.group("year")
        year_dir = root / year
        target = year_dir / p.name

        if target.exists():
            skipped += 1
            log(f"[SKIP] {p.name} (already exists in {year})")
            continue

        log(f"[MOVE] {p.name} -> {year}/" if not args.dry_run else f"[DRY][MOVE] {p.name} -> {year}/")

        if not args.dry_run:
            year_dir.mkdir(exist_ok=True)
            p.replace(target)

        moved += 1

        if done % args.progress_every == 0 or done == total:
            print_progress(done, total, start, moved, skipped)

    print_progress(done, total, start, moved, skipped)
    print("\n")
    log("Done.")
    log(f"Moved: {moved:,}")
    log(f"Skipped: {skipped:,}")


if __name__ == "__main__":
    main()
