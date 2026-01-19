import argparse
import csv
import hashlib
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Set

TARGET_EXTS = {
    ".jpg", ".jpeg", ".png",
    ".cr2", ".dng",
    ".mov", ".avi", ".3gp",
    ".gif", ".mp4",
}

EXIFTOOL_DATE_TAGS = [
    "DateTimeOriginal",
    "CreateDate",
    "MediaCreateDate",
    "TrackCreateDate",
    "ContentCreateDate",
    "CreationDate",
    "QuickTime:CreateDate",
    "QuickTime:MediaCreateDate",
    "QuickTime:TrackCreateDate",
    "EXIF:DateTimeOriginal",
    "EXIF:CreateDate",
    "ModifyDate",
    "FileModifyDate",
]

EXIF_DT_RE = re.compile(r"^(?P<y>\d{4}):(?P<m>\d{2}):(?P<d>\d{2})\s+(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})")
PROPER_NAME_RE = re.compile(r"^\d{8}_\d{6}(?:_\d{2})?\.[A-Za-z0-9]+$")
YEAR_NAME_RE = re.compile(r"^(?P<year>\d{4})\d{4}_\d{6}(?:_\d{2})?\.[A-Za-z0-9]+$")


def log(msg: str) -> None:
    print(msg, flush=True)


def is_target_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in TARGET_EXTS


def is_properly_named(filename: str) -> bool:
    return PROPER_NAME_RE.match(filename) is not None


def find_exiftool(user_value: str) -> Optional[str]:
    if user_value:
        resolved = shutil.which(user_value)
        if resolved:
            return resolved
        if Path(user_value).exists():
            return str(Path(user_value))
    for name in ("exiftool", "exiftool.exe"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    try:
        here = Path(__file__).resolve().parent
        local = here / "exiftool.exe"
        if local.exists():
            return str(local)
    except Exception:
        pass
    return None


def run_exiftool(exiftool_path: str, file_path: Path, timeout_s: int) -> dict:
    args = [exiftool_path, "-s", "-s", "-s", "-api", "QuickTimeUTC=1"]
    for tag in EXIFTOOL_DATE_TAGS:
        args.append(f"-{tag}")
    args.append(str(file_path))

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

    if proc.returncode != 0:
        return {}

    tag_map = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        tag, value = line.split(":", 1)
        tag = tag.strip()
        value = value.strip()
        if value:
            tag_map[tag] = value
    return tag_map


def parse_exif_datetime(value: str) -> Optional[datetime]:
    m = EXIF_DT_RE.match(value)
    if not m:
        return None
    try:
        return datetime(
            int(m.group("y")), int(m.group("m")), int(m.group("d")),
            int(m.group("h")), int(m.group("mi")), int(m.group("s")),
        )
    except ValueError:
        return None


def get_best_timestamp(exiftool_path: Optional[str], file_path: Path, prefer_oldest: bool, exif_timeout_s: int) -> Tuple[datetime, str]:
    candidates: List[Tuple[datetime, str]] = []
    if exiftool_path:
        tags = run_exiftool(exiftool_path, file_path, timeout_s=exif_timeout_s)
        for tag, value in tags.items():
            dt = parse_exif_datetime(value)
            if dt:
                candidates.append((dt, f"exif:{tag}"))

    if not candidates:
        stat = file_path.stat()
        return datetime.fromtimestamp(stat.st_mtime), "fs:mtime"

    candidates.sort(key=lambda x: x[0])
    chosen = candidates[0] if prefer_oldest else candidates[-1]
    return chosen[0], chosen[1]


def build_base_name(ts: datetime) -> str:
    return ts.strftime("%Y%m%d_%H%M%S")


def ensure_unique_name(dest_dir: Path, base_stem: str, ext: str) -> Path:
    ext = ext.lower()
    target = dest_dir / f"{base_stem}{ext}"
    if not target.exists():
        return target
    i = 1
    while True:
        candidate = dest_dir / f"{base_stem}_{i:02d}{ext}"
        if not candidate.exists():
            return candidate
        i += 1


def sha256_file(path: Path, chunk_size: int = 1024 * 1024, heartbeat_s: int = 10) -> str:
    h = hashlib.sha256()
    last_beat = time.time()
    total = 0

    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
            total += len(chunk)

            now = time.time()
            if now - last_beat >= heartbeat_s:
                mb = total / (1024 * 1024)
                log(f"  [HASH] {path.name} ... {mb:,.0f} MB read")
                last_beat = now

    return h.hexdigest()


def move_with_retries(src: Path, dest: Path, dry_run: bool, retries: int, sleep_s: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return
    last_err = None
    for _ in range(retries):
        try:
            src.replace(dest)
            return
        except FileNotFoundError as e:
            last_err = e
            break
        except (PermissionError, OSError) as e:
            last_err = e
        time.sleep(sleep_s)
    if last_err:
        raise last_err


def fmt_eta(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return "--:--:--"
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def print_progress(done: int, total: int, start: float, moved: int, renamed: int, dups: int, skipped: int) -> None:
    elapsed = max(0.001, time.time() - start)
    rate = done / elapsed
    remaining = max(0, total - done)
    eta = remaining / rate if rate > 0 else float("inf")
    pct = (done / total * 100.0) if total else 100.0
    line = (
        f"[PROC] {done:,}/{total:,} ({pct:5.1f}%) | "
        f"moved {moved:,} | renamed {renamed:,} | dups {dups:,} | skipped {skipped:,} | "
        f"{rate:,.1f} files/s | ETA {fmt_eta(eta)}"
    )
    print("\r" + line + " " * 5, end="", flush=True)


def normalize_exclude_paths(root: Path, excludes: List[str]) -> Set[Path]:
    out: Set[Path] = set()
    for ex in excludes:
        p = Path(ex)
        if not p.is_absolute():
            p = root / p
        try:
            out.add(p.resolve())
        except Exception:
            out.add(p)
    return out


def is_under_any(path: Path, bases: Set[Path]) -> bool:
    try:
        rp = path.resolve()
    except Exception:
        rp = path
    for base in bases:
        try:
            if base in rp.parents or rp == base:
                return True
        except Exception:
            continue
    return False


def organize_dest_by_year(dest_root: Path, dry_run: bool, progress_every: int = 200) -> None:
    log("\nStarting organize-by-year step...")
    log(f"Destination: {dest_root}")
    if dry_run:
        log("DRY RUN ENABLED — no files will be moved")
    log("")

    files = [p for p in dest_root.iterdir() if p.is_file() and p.suffix.lower() in TARGET_EXTS]
    total = len(files)
    log(f"Found {total:,} files in destination root to organize.\n")

    moved = 0
    skipped = 0
    done = 0
    start = time.time()

    for p in files:
        done += 1
        m = YEAR_NAME_RE.match(p.name)
        if not m:
            skipped += 1
            log(f"[SKIP-YEAR] {p.name} (not in YYYYMMDD_HHMMSS format)")
            continue

        year = m.group("year")
        year_dir = dest_root / year
        target = year_dir / p.name

        if target.exists():
            skipped += 1
            log(f"[SKIP-YEAR] {p.name} (already exists in {year}/)")
            continue

        if dry_run:
            log(f"[DRY][YEAR-MOVE] {p.name} -> {year}/")
        else:
            year_dir.mkdir(exist_ok=True)
            p.replace(target)

        moved += 1

        if done % progress_every == 0 or done == total:
            elapsed = max(0.001, time.time() - start)
            rate = done / elapsed
            pct = (done / total * 100.0) if total else 100.0
            print(f"\r[YEAR] {done:,}/{total:,} ({pct:5.1f}%) | moved {moved:,} | skipped {skipped:,} | {rate:,.1f} files/s", end="", flush=True)

    print("\n")
    log("Organize-by-year complete.")
    log(f"Year moved: {moved:,}")
    log(f"Year skipped: {skipped:,}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--dest-name", default="all_photos")
    ap.add_argument("--exiftool", default="exiftool")
    ap.add_argument("--no-exiftool", action="store_true")
    ap.add_argument("--exiftool-timeout", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--log-csv", default="")
    ap.add_argument("--prefer-newest", action="store_true")
    ap.add_argument("--dup-action", choices=["move", "skip", "delete"], default="move")
    ap.add_argument("--progress-every", type=int, default=50)
    ap.add_argument("--scan-print-every", type=int, default=50_000)
    ap.add_argument("--move-retries", type=int, default=3)
    ap.add_argument("--move-retry-sleep", type=float, default=0.25)
    ap.add_argument("--hash-max-mb", type=int, default=512)
    ap.add_argument("--hash-heartbeat", type=int, default=10)

    ap.add_argument("--exclude", action="append", default=[], help="Folder to exclude (repeatable). Name under root or full path.")
    ap.add_argument("--organize-by-year", action="store_true", help="Run year-folder organization after consolidation.")

    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Root path does not exist: {root}")

    dest_root = root / args.dest_name
    dup_root = dest_root / "_DUPLICATES"
    exclude_roots = normalize_exclude_paths(root, args.exclude)

    exiftool_path: Optional[str] = None
    if not args.no_exiftool:
        exiftool_path = find_exiftool(args.exiftool)

    log("Starting media consolidation...")
    log(f"Root: {root}")
    log(f"Destination: {dest_root}")
    log(f"Duplicates: {dup_root}")
    if exclude_roots:
        log("Excluding folders:")
        for ex in sorted(exclude_roots, key=lambda p: str(p)):
            log(f"  - {ex}")
    if args.dry_run:
        log("DRY RUN ENABLED — no changes will be made")
    log(f"ExifTool: {exiftool_path if exiftool_path else 'NOT FOUND (mtime fallback)'}")
    log("")

    csv_writer = None
    csv_file = None
    if args.log_csv:
        csv_file = open(args.log_csv, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["action", "old_path", "new_path", "timestamp", "source", "size_bytes", "dup_of", "note"])
        csv_file.flush()
        log(f"CSV log initialized: {args.log_csv}\n")

    log("Phase A: pre-scan (counting)...")
    total_found = 0
    entries = 0
    t0 = time.time()
    for p in root.rglob("*"):
        entries += 1
        if entries % args.scan_print_every == 0:
            elapsed = max(0.001, time.time() - t0)
            log(f"[SCAN] entries: {entries:,} | media found: {total_found:,} | {entries/elapsed:,.0f} entries/s")

        try:
            if dest_root in p.parents:
                continue
        except Exception:
            continue
        if exclude_roots and is_under_any(p, exclude_roots):
            continue
        if is_target_file(p):
            total_found += 1
    log(f"Phase A complete. Found {total_found:,} media files.\n")

    log("Phase B: snapshot list...")
    paths: List[Path] = []
    entries = 0
    t1 = time.time()
    for p in root.rglob("*"):
        entries += 1
        if entries % args.scan_print_every == 0:
            elapsed = max(0.001, time.time() - t1)
            log(f"[LIST] entries: {entries:,} | media queued: {len(paths):,} | {entries/elapsed:,.0f} entries/s")

        try:
            if dest_root in p.parents:
                continue
        except Exception:
            continue
        if exclude_roots and is_under_any(p, exclude_roots):
            continue
        if is_target_file(p):
            paths.append(p)

    total = len(paths)
    log(f"Phase B complete. Snapshot contains {total:,} files.\n")

    log("Phase C: processing (move/rename/dedupe)...")
    seen_candidates: Dict[Tuple[str, int], Path] = {}

    moved = renamed = duplicates = skipped = 0
    done = 0
    start = time.time()

    try:
        for p in paths:
            done += 1

            if not p.exists():
                skipped += 1
                log(f"[SKIP] {p} (missing)")
                if done % args.progress_every == 0 or done == total:
                    print_progress(done, total, start, moved, renamed, duplicates, skipped)
                continue

            try:
                size = p.stat().st_size
                ts, source = get_best_timestamp(exiftool_path, p, prefer_oldest=not args.prefer_newest, exif_timeout_s=args.exiftool_timeout)
                base = build_base_name(ts)
                key = (base, size)

                if is_properly_named(p.name):
                    target_path = dest_root / p.name
                    if target_path.exists():
                        stem = target_path.stem
                        ext = target_path.suffix
                        i = 1
                        while True:
                            cand = dest_root / f"{stem}_{i:02d}{ext}"
                            if not cand.exists():
                                target_path = cand
                                break
                            i += 1
                else:
                    target_path = ensure_unique_name(dest_root, base, p.suffix)

                if key in seen_candidates:
                    kept = seen_candidates[key]
                    max_bytes = args.hash_max_mb * 1024 * 1024
                    if kept.exists() and size <= max_bytes:
                        kept_hash = sha256_file(kept, heartbeat_s=args.hash_heartbeat)
                        p_hash = sha256_file(p, heartbeat_s=args.hash_heartbeat)
                        if p_hash == kept_hash:
                            duplicates += 1
                            if args.dup_action == "skip":
                                log(f"[DUP-SKIP] {p} (matches {kept.name})")
                                if csv_writer:
                                    csv_writer.writerow(["DUP_SKIP", str(p), "", ts.isoformat(sep=" "), source, size, str(kept), "hash_match"])
                                    csv_file.flush()
                                continue
                            if args.dup_action == "delete":
                                log(f"[DUP-DEL] {p} (matches {kept.name})")
                                if not args.dry_run:
                                    p.unlink()
                                if csv_writer:
                                    csv_writer.writerow(["DUP_DELETE", str(p), "", ts.isoformat(sep=" "), source, size, str(kept), "hash_match"])
                                    csv_file.flush()
                                continue

                            dup_target = dup_root / p.name
                            if dup_target.exists():
                                stem = dup_target.stem
                                ext = dup_target.suffix
                                i = 1
                                while True:
                                    cand = dup_root / f"{stem}_{i:02d}{ext}"
                                    if not cand.exists():
                                        dup_target = cand
                                        break
                                    i += 1
                            log(f"[DUP-MOVE] {p} -> {dup_target}")
                            if not args.dry_run:
                                move_with_retries(p, dup_target, dry_run=False, retries=args.move_retries, sleep_s=args.move_retry_sleep)
                            if csv_writer:
                                csv_writer.writerow(["DUP_MOVE", str(p), str(dup_target), ts.isoformat(sep=" "), source, size, str(kept), "hash_match"])
                                csv_file.flush()
                            continue

                action = "MOVE+RENAME" if target_path.name != p.name else "MOVE"
                log(f"[{action}] {p} -> {target_path}" if not args.dry_run else f"[DRY][{action}] {p} -> {target_path}")

                if not args.dry_run:
                    dest_root.mkdir(exist_ok=True)
                    move_with_retries(p, target_path, dry_run=False, retries=args.move_retries, sleep_s=args.move_retry_sleep)

                moved += 1
                if target_path.name != p.name:
                    renamed += 1

                seen_candidates[key] = target_path

                if csv_writer:
                    csv_writer.writerow([action, str(p), str(target_path), ts.isoformat(sep=" "), source, size, "", ""])
                    csv_file.flush()

            except Exception as e:
                skipped += 1
                log(f"[SKIP] {p} (reason: {e})")

            if done % args.progress_every == 0 or done == total:
                print_progress(done, total, start, moved, renamed, duplicates, skipped)

    finally:
        if csv_file:
            csv_file.close()

    print_progress(done, total, start, moved, renamed, duplicates, skipped)
    print("\n")
    log("Done.")
    log(f"Moved: {moved:,} | Renamed: {renamed:,} | Dups: {duplicates:,} | Skipped: {skipped:,}")
    log(f"Destination: {dest_root}")

    if args.organize_by_year:
        organize_dest_by_year(dest_root, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
