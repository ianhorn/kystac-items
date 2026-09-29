import argparse
import datetime
import glob
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from pdal import Pipeline

# LAS "Adjusted Standard GPS Time" is GPS seconds since the GPS epoch, minus
# 1e9 (to keep values small for float precision). This is the actual
# instrument acquisition timestamp for each point, untouched by later
# reprocessing (classification, COPC conversion, etc.) that rewrites the LAS
# header's own "file creation date" field to whenever that tool ran.
GPS_EPOCH = datetime.datetime(1980, 1, 6)


def read_acquisition_datetime(path):
    pipeline = Pipeline(json.dumps([
        {"type": "readers.las", "filename": path},
        {"type": "filters.head", "count": 1},
    ]))
    pipeline.execute()
    meta = pipeline.metadata["metadata"]
    key = next(k for k in meta.keys() if k.startswith("readers"))
    global_encoding = meta[key]["global_encoding"]
    if not (global_encoding & 1):
        raise ValueError(
            f"{path} does not use Adjusted Standard GPS Time "
            f"(global_encoding={global_encoding}); can't decode GpsTime reliably."
        )
    gps_time = float(pipeline.arrays[0]["GpsTime"][0])
    gps_seconds = gps_time + 1_000_000_000
    return GPS_EPOCH + datetime.timedelta(seconds=gps_seconds)


def process_one(path, originals_dir):
    with open(path) as f:
        item = json.load(f)

    item_id = item["id"]
    original_name = item_id.replace(".copc", "") + ".laz"
    original_path = os.path.join(originals_dir, original_name)

    if not os.path.exists(original_path):
        return "missing", original_name

    try:
        dt = read_acquisition_datetime(original_path)
    except Exception as exc:
        return "error", (original_name, str(exc))

    new_datetime = dt.strftime("%Y-%m-%dT00:00:00Z")
    old_datetime = item["properties"].get("datetime")

    if new_datetime == old_datetime:
        return "unchanged", None

    item["properties"]["datetime"] = new_datetime
    with open(path, "w") as f:
        json.dump(item, f, indent=2)
    return "updated", None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("items_dir", nargs="?", default="items/laz-phase3")
    parser.add_argument(
        "originals_dir", nargs="?", default=r"H:\KYAPED_Lidar_2026\Classified_LAS"
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    paths = sorted(glob.glob(os.path.join(args.items_dir, "*.json")))
    total = len(paths)
    counts = {"updated": 0, "unchanged": 0, "missing": 0, "error": 0}
    missing = []
    errors = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_one, path, args.originals_dir): path for path in paths}
        for i, future in enumerate(as_completed(futures), 1):
            status, detail = future.result()
            counts[status] += 1
            if status == "missing":
                missing.append(detail)
            elif status == "error":
                errors.append(detail)
                print(f"[{i}/{total}] ERROR {detail[0]}: {detail[1]}")
            if i % 100 == 0 or i == total:
                print(f"[{i}/{total}] {counts}")

    print(f"\nDone. {counts}")
    if missing:
        print("\nMissing original files:")
        for name in missing:
            print(f"  {name}")
    if errors:
        print("\nErrors:")
        for name, error in errors:
            print(f"  {name}: {error}")


if __name__ == "__main__":
    main()
