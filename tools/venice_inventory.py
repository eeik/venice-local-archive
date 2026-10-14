"""Read-only Venice store inventory from a copied browser profile.

Useful after a site-data reset, even when the new local key or database is
absent. Reports record counts and the detected storage backend, not values.
"""
import argparse
import collections
import contextlib
import json
import os
import pathlib
import sqlite3

from dfindexeddb.indexeddb.chromium import record as chrome
from venice_recovery import safe_copy


def firefox(profile):
    origin = profile / "storage/default/https+++venice.ai/idb"
    dbs = list(origin.glob("*vdeentipcyer-cdnbe-.sqlite")) if origin.is_dir() else []
    rx_dbs = list(origin.glob("*vdeentipcyer-crnxe--d.sqlite")) if origin.is_dir() else []
    if not dbs:
        return {"backend": "absent", "classicStoreCounts": {}, "rxStoreCounts": {}}
    if len(dbs) != 1:
        raise ValueError("Multiple Firefox Venice classic databases")
    if len(rx_dbs) > 1:
        raise ValueError("Multiple Firefox Venice RxDB databases")
    def counts_for(db):
        with sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            stores = dict(conn.execute("SELECT id,name FROM object_store"))
            counts = dict(conn.execute("SELECT object_store_id,COUNT(*) FROM object_data GROUP BY object_store_id"))
            return conn.execute("PRAGMA quick_check(1)").fetchone()[0], {name: counts.get(ident, 0) for ident, name in stores.items()}
    quick, classic = counts_for(dbs[0])
    rx_quick, rx = counts_for(rx_dbs[0]) if rx_dbs else (None, {})
    return {"backend": "firefox-sqlite", "quickCheck": quick, "rxQuickCheck": rx_quick, "classicStoreCounts": classic, "rxStoreCounts": rx}


def chromium(profile):
    root = profile / "IndexedDB"
    folder = root / "https_venice.ai_0.indexeddb.leveldb"
    if not folder.is_dir():
        alternatives = sorted(x.name for x in root.glob("https_venice.ai_0.indexeddb*")) if root.is_dir() else []
        return {"backend": "unknown-new-format" if alternatives else "absent", "alternativeFiles": alternatives, "classicStoreCounts": {}}
    latest = {}
    with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
        for item in chrome.FolderReader(folder).GetRecords(use_manifest=True, use_sequence_number=True, parse_value=False, include_raw_data=True, load_blobs=False):
            if isinstance(item.key, (chrome.DatabaseNameKey, chrome.ObjectStoreNamesKey, chrome.ObjectStoreDataKey)):
                old = latest.get(item.raw_key)
                if old is None or (item.sequence_number or 0) > (old.sequence_number or 0):
                    latest[item.raw_key] = item
    dbnames = {}
    stores = {}
    counts = collections.Counter()
    for item in latest.values():
        if item.type != 1 or item.raw_value is None:
            continue
        if isinstance(item.key, chrome.DatabaseNameKey):
            dbnames[item.key.ParseValue(item.raw_value)] = item.key.database_name
        elif isinstance(item.key, chrome.ObjectStoreNamesKey):
            stores[(item.database_id, item.key.ParseValue(item.raw_value))] = item.key.object_store_name
        elif isinstance(item.key, chrome.ObjectStoreDataKey):
            counts[(item.database_id, item.object_store_id)] += 1
    venice = {ident: name for ident, name in dbnames.items() if name in {"venice-db-encrypted", "venice-rx-db-encrypted"}}
    store_counts = {}
    for (dbid, storeid), count in counts.items():
        if dbid not in venice:
            continue
        name = stores.get((dbid, storeid)) or stores.get((dbid, storeid - 256)) or f"unknown-store-{storeid}"
        store_counts[f"{venice[dbid]}/{name}"] = count
    return {"backend": "chromium-leveldb", "veniceDatabases": list(venice.values()), "storeCounts": dict(sorted(store_counts.items()))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("browser", choices=["firefox", "brave"])
    p.add_argument("snapshot", type=pathlib.Path)
    p.add_argument("--output", type=pathlib.Path)
    a = p.parse_args()
    profile = safe_copy(a.snapshot, a.browser)
    result = {"browser": a.browser, "snapshot": str(profile), **(firefox(profile) if a.browser == "firefox" else chromium(profile))}
    data = json.dumps(result, indent=2) + "\n"
    if a.output:
        if a.output.exists():
            raise FileExistsError(f"Refusing to overwrite {a.output}")
        a.output.write_text(data, encoding="utf-8")
    print(data, end="")


if __name__ == "__main__":
    main()
