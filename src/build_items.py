import argparse
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
from pointcloud_fast import create_item_fast


def process_one(url, out_dir, overwrite):
    item_id = os.path.splitext(os.path.basename(url))[0]
    out_path = os.path.join(out_dir, f"{item_id}.json")
    if not overwrite and os.path.exists(out_path):
        return item_id, "skipped", None
    try:
        item = create_item_fast(url)
        with open(out_path, "w") as f:
            json.dump(item.to_dict(), f, indent=2)
        return item_id, "ok", None
    except Exception as exc:
        return item_id, "error", str(exc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", nargs="?", default="csv/laz-phase3.csv")
    parser.add_argument("out_dir", nargs="?", default="items/laz-phase3")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--overwrite", action="store_true",
                        help="Reprocess urls that already have an output file")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process the first N urls (for testing)")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with open(args.csv_path, newline="") as f:
        urls = [row["url"] for row in csv.DictReader(f)]
    if args.limit:
        urls = urls[: args.limit]

    total = len(urls)
    ok = skipped = failed = 0
    failures = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_one, url, args.out_dir, args.overwrite): url
            for url in urls
        }
        for i, future in enumerate(as_completed(futures), 1):
            url = futures[future]
            item_id, status, error = future.result()
            if status == "ok":
                ok += 1
            elif status == "skipped":
                skipped += 1
            else:
                failed += 1
                failures.append((url, error))
                print(f"[{i}/{total}] FAILED {item_id}: {error}")
            if i % 50 == 0 or i == total:
                print(f"[{i}/{total}] ok={ok} skipped={skipped} failed={failed}")

    print(f"\nDone. ok={ok} skipped={skipped} failed={failed}")
    if failures:
        print("\nFailed URLs:")
        for url, error in failures:
            print(f"  {url}: {error}")


if __name__ == "__main__":
    main()
