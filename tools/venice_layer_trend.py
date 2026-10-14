"""Track encrypted-wrapper depth across recurring Venice export manifests."""
import argparse
import csv
import json
import pathlib


def quantile(histogram, numerator, denominator):
    total = sum(histogram.values())
    if not total:
        return 0
    threshold = (total * numerator + denominator - 1) // denominator
    seen = 0
    for depth, count in sorted(histogram.items()):
        seen += count
        if seen >= threshold:
            return depth
    raise AssertionError("Quantile threshold missing")


def rows(paths):
    output = []
    for path in paths:
        path = pathlib.Path(path).resolve()
        manifest_path = path / "manifest.json" if path.is_dir() else path
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for store, values in sorted(data.get("encryptedLayers", {}).items()):
            histogram = {int(k): int(v) for k, v in values.items()}
            if not histogram:
                continue
            count = sum(histogram.values())
            output.append({
                "browser": data["browser"],
                "snapshot": pathlib.Path(data["profileCopy"]).name,
                "exportedAtUtc": data["exportedAtUtc"],
                "store": store,
                "records": count,
                "maxLayers": max(histogram),
                "medianLayers": quantile(histogram, 1, 2),
                "p95Layers": quantile(histogram, 95, 100),
                "meanLayers": round(sum(depth * n for depth, n in histogram.items()) / count, 3),
            })
    return sorted(output, key=lambda x: (x["browser"], x["store"], x["exportedAtUtc"], x["snapshot"]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifests", nargs="+", type=pathlib.Path)
    p.add_argument("--output", type=pathlib.Path, help="New CSV path; refuses overwrite")
    a = p.parse_args()
    data = rows(a.manifests)
    if a.output:
        if a.output.exists():
            raise FileExistsError(f"Refusing to overwrite {a.output}")
        with a.output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["browser", "snapshot", "exportedAtUtc", "store", "records", "maxLayers", "medianLayers", "p95Layers", "meanLayers"])
            writer.writeheader()
            writer.writerows(data)
    latest = {}
    changes = []
    for item in data:
        key = (item["browser"], item["store"])
        previous = latest.get(key)
        if previous:
            changes.append({"browser": item["browser"], "store": item["store"], "previousMax": previous["maxLayers"], "currentMax": item["maxLayers"], "deltaMax": item["maxLayers"] - previous["maxLayers"], "snapshot": item["snapshot"]})
        latest[key] = item
    print(json.dumps({"runs": len(a.manifests), "latestByBrowserAndStore": list(latest.values()), "changes": changes}, indent=2))


if __name__ == "__main__":
    main()
