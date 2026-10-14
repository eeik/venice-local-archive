"""Offline Venice.ai recovery from closed Firefox or Chromium profile copies.

Requires dfindexeddb, python-snappy, PyNaCl. Never points at a live profile,
prints secrets, contacts Venice, or modifies a browser profile.
"""

import argparse
import base64
import collections
import contextlib
import dataclasses
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import re
import sqlite3
import sys

import snappy
from nacl.secret import SecretBox
from nacl.exceptions import CryptoError
from dfindexeddb.indexeddb.firefox import definitions as firefox_definitions, gecko
from dfindexeddb.indexeddb.chromium import record as chrome, v8
from dfindexeddb.indexeddb.chromium import blink, definitions as chromium_definitions
from dfindexeddb.leveldb import record as leveldb
from venice_copy_site_filesystem import copy as copy_site_filesystem

# Current Chromium uses V8 wire format 16. The forensic parser still caps at 15.
v8.ValueDeserializer.LATEST_VERSION = 16

SAFE_RECORD_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


def safe_record_id(value):
    """Return a Venice ID only when it is safe as one filename component."""
    if not isinstance(value, str) or SAFE_RECORD_ID.fullmatch(value) is None:
        raise ValueError("Unsafe conversation ID")
    return value


class FirefoxStructuredCloneDecoder(gecko.JSStructuredCloneDecoder):
    """Decode Firefox Files with the alignment used by Mozilla IndexedDB.

    dfindexeddb 20260210 reads Blob/File strings but does not realign the
    structured-clone stream before the following 64-bit field. Firefox pads
    JS_ReadBytes operations to the next 64-bit word. A Character with an
    uploaded context document therefore otherwise fails at the file name.
    """

    def _align(self):
        padding = (-self.decoder.stream.tell()) % 8
        if padding:
            self.decoder.stream.seek(padding, io.SEEK_CUR)

    def _DecodeBlobOrFile(self, tag, index):
        _, size = self.decoder.DecodeUint64()
        mime_type = self._ReadBlobString()
        if tag == firefox_definitions.StructuredCloneTags.BLOB:
            return gecko.Blob(index=index, size=size, type=mime_type)
        self._align()
        if tag == firefox_definitions.StructuredCloneTags.FILE:
            _, last_modified = self.decoder.DecodeUint64()
        elif tag == firefox_definitions.StructuredCloneTags.FILE_WITHOUT_LASTMODIFIEDDATE:
            last_modified = 0x7FFFFFFFFFFFFFFF
        else:
            raise ValueError(f"Unexpected Firefox File tag: {tag}")
        name = self._ReadBlobString()
        return gecko.File(index=index, size=size, type=mime_type, name=name, last_modified=last_modified)


class DepthLimitError(ValueError):
    """A record exceeded the allowed nested encryption depth."""


class KeyRing:
    """Current key first, then available historical Chromium localStorage keys."""

    def __init__(self, candidates):
        self.boxes = []
        seen = set()
        for key in candidates:
            if key in seen:
                continue
            seen.add(key)
            self.boxes.append(SecretBox(key))
        if not self.boxes:
            raise ValueError("No usable Venice encryption key in copied localStorage")
        self.key_check_proofs = 0
        self.record_proofs = 0
        self.historical_record_proofs = 0

    def decrypt(self, value, *, key_check=False):
        data = raw_bytes(value)
        for index, box in enumerate(self.boxes):
            try:
                plaintext = box.decrypt(data)
            except CryptoError:
                continue
            if key_check:
                self.key_check_proofs += 1
            else:
                self.record_proofs += 1
                if index:
                    self.historical_record_proofs += 1
            return plaintext
        raise CryptoError("No available local key authenticated this ciphertext")

    def evidence(self):
        return {
            "localKeyCandidates": len(self.boxes),
            "keyCheckProofs": self.key_check_proofs,
            "recordLayerProofs": self.record_proofs,
            "historicalKeyLayerProofs": self.historical_record_proofs,
            "authenticated": self.key_check_proofs + self.record_proofs > 0,
        }


def parse_key(value):
    parts = value.decode("utf-8").split(",")
    if len(parts) != SecretBox.KEY_SIZE:
        raise ValueError("Venice local key must contain 32 bytes")
    return bytes(int(x) for x in parts)


def read_varint(data, offset):
    value = 0
    shift = 0
    for _ in range(10):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("Varint header exceeds 10 bytes")


def wire_versions(raw):
    """Read only Blink/V8 header versions; this does not validate the value."""
    if raw[:3] == bytes((0xFF, chromium_definitions.REQUIRES_PROCESSING_SSV_PSEUDO_VERSION, chromium_definitions.COMPRESSED_WITH_SNAPPY)):
        raw = snappy.decompress(raw[3:])
    if len(raw) < 3 or raw[0] != 0xFF:
        return "unknown"
    blink_version, offset = read_varint(raw, 1)
    if blink_version < 16:
        offset = 0
    elif blink_version >= 21:
        if raw[offset] != chromium_definitions.BlinkSerializationTag.TRAILER_OFFSET:
            return f"blink-{blink_version}-unknown-v8"
        offset += 13
    if offset >= len(raw) or raw[offset] != 0xFF:
        return f"blink-{blink_version}-unknown-v8"
    v8_version, _ = read_varint(raw, offset + 1)
    return f"blink-{blink_version}-v8-{v8_version}"


def safe_copy(profile, browser):
    profile = pathlib.Path(profile).resolve()
    if not profile.is_dir():
        raise ValueError("Profile copy directory missing")
    if browser == "firefox":
        live = pathlib.Path(os.environ["APPDATA"]) / "Mozilla/Firefox/Profiles"
    elif browser == "brave":
        live = pathlib.Path(os.environ["LOCALAPPDATA"]) / "BraveSoftware/Brave-Browser/User Data"
    else:
        raise ValueError(f"Unsupported browser: {browser}")
    if profile.is_relative_to(live.resolve()):
        raise ValueError("Refusing to read a live browser profile; make a closed-browser copy first")
    return profile


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def serial(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"$binaryBase64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, v8.ArrayBufferView):
        return {"$binaryBase64": base64.b64encode(raw_bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    if dataclasses.is_dataclass(value):
        if type(value).__name__ == "JSArray":
            return serial(value.values) if not value.properties else {"$array": serial(value.values), "$properties": serial(value.properties)}
        if type(value).__name__ in {"Null", "Undefined"}:
            return None
        return {"$type": type(value).__name__, **serial(dataclasses.asdict(value))}
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def bind_firefox_external_files(value, file_ids, files_root):
    """Replace decoded File/Blob references with verified, portable objects."""
    ids = [token for token in str(file_ids or "").strip(".").split() if token]
    used = set()
    metadata = []

    def bind(item):
        if isinstance(item, (gecko.File, gecko.Blob)):
            index = int(item.index)
            if index < 0 or index >= len(ids):
                raise ValueError("Firefox external File index is outside file_ids")
            used.add(index)
            file_id = str(abs(int(ids[index])))
            path = files_root / file_id
            if not path.is_file():
                raise FileNotFoundError(f"Firefox external File is missing: {file_id}")
            raw = path.read_bytes()
            if len(raw) != int(item.size):
                raise ValueError("Firefox external File size differs from structured-clone metadata")
            entry = {
                "$type": type(item).__name__,
                "index": index,
                "size": int(item.size),
                "type": str(item.type),
                "externalFileId": file_id,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "$binaryBase64": base64.b64encode(raw).decode("ascii"),
            }
            if isinstance(item, gecko.File):
                entry.update({"name": str(item.name), "lastModified": item.last_modified})
            metadata.append({key: entry[key] for key in entry if key not in {"$binaryBase64"}})
            return entry
        if isinstance(item, dict):
            return {key: bind(child) for key, child in item.items()}
        if isinstance(item, list):
            return [bind(child) for child in item]
        if isinstance(item, tuple):
            return tuple(bind(child) for child in item)
        if dataclasses.is_dataclass(item):
            return dataclasses.replace(item, **{
                field.name: bind(getattr(item, field.name)) for field in dataclasses.fields(item)
            })
        return item

    bound = bind(value)
    if used != set(range(len(ids))):
        raise ValueError("Firefox file_ids and decoded File references differ")
    return bound, metadata


def raw_bytes(value):
    if isinstance(value, bytes):
        return value
    if isinstance(value, v8.ArrayBufferView):
        return value.buffer[value.offset:value.offset + value.length]
    if isinstance(value, dict) and isinstance(value.get("encoded"), str):
        return base64.b64decode(value["encoded"], validate=True)
    raise TypeError(f"Expected binary encryption field, found {type(value).__name__}")


def has_nested_encryption(value, depth=0):
    if depth > 30:
        return False
    if isinstance(value, dict):
        return "__encryptedData" in value or any(has_nested_encryption(x, depth + 1) for x in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_nested_encryption(x, depth + 1) for x in value)
    return False


def issue(phase, store, ordinal, obj, exc):
    ident = obj.get("id", obj.get("messageId")) if isinstance(obj, dict) else None
    return {
        "phase": phase,
        "store": store,
        "recordOrdinal": ordinal,
        "recordId": str(ident) if ident is not None else None,
        "errorType": type(exc).__name__,
    }


def observe_encrypted_record_size(sizes, store, value):
    """Record only outer ciphertext byte length; never retain plaintext or IDs."""
    if isinstance(value, dict) and "__encryptedData" in value:
        sizes[store].append(len(raw_bytes(value["__encryptedData"])))


def size_summary(values):
    """Return deterministic, content-free size statistics."""
    ordered = sorted(values)
    if not ordered:
        return {"records": 0, "min": 0, "median": 0, "p95": 0, "max": 0, "total": 0}
    percentile = lambda fraction: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]
    return {"records": len(ordered), "min": ordered[0], "median": percentile(0.5), "p95": percentile(0.95), "max": ordered[-1], "total": sum(ordered)}


def decrypt_layers(keys, record, *, max_layers=512):
    depth = 0
    ident = record.get("id", record.get("messageId"))
    while "__encryptedData" in record:
        if depth >= max_layers:
            raise DepthLimitError(f"Record exceeded {max_layers} encryption layers")
        record = json.loads(keys.decrypt(record["__encryptedData"]))
        if not isinstance(record, dict):
            raise ValueError("Decrypted record is not an object")
        depth += 1
    if ident is not None and record.get("id", record.get("messageId")) != ident:
        raise ValueError("Record ID changed across encryption layers")
    return serial(record), depth


def firefox_local_key(profile):
    origin = profile / "storage/default/https+++venice.ai"
    with sqlite3.connect((origin / "ls/data.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        row = conn.execute("SELECT value, compression_type FROM data WHERE key='encryptionKey'").fetchone()
    if not row:
        raise ValueError("Firefox Venice encryptionKey missing")
    key = snappy.decompress(row[0]) if row[1] == 1 else row[0]
    return parse_key(key)


def firefox_source(profile, historical_profiles=()):
    origin = profile / "storage/default/https+++venice.ai"
    dbs = list((origin / "idb").glob("*vdeentipcyer-cdnbe-.sqlite"))
    if len(dbs) != 1:
        raise ValueError(f"Expected one Firefox Venice classic database; found {len(dbs)}")
    db = dbs[0]
    keys = KeyRing([firefox_local_key(profile), *(firefox_local_key(p) for p in historical_profiles)])
    extra = {"externalFileReferences": 0, "externalFileBytes": 0, "externalFiles": []}
    def rows():
        with sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
            if conn.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise ValueError("Firefox IndexedDB SQLite quick_check failed")
            stores = dict(conn.execute("SELECT id, name FROM object_store"))
            for store_id, name in stores.items():
                for payload, file_ids in conn.execute("SELECT data, file_ids FROM object_data WHERE object_store_id=?", (store_id,)):
                    try:
                        value = FirefoxStructuredCloneDecoder.FromBytes(payload)
                        value, external = bind_firefox_external_files(value, file_ids, db.with_suffix(".files"))
                        for item in external:
                            extra["externalFiles"].append({"store": name, **item})
                            extra["externalFileReferences"] += 1
                            extra["externalFileBytes"] += item["bytes"]
                        yield name, value
                    except Exception as exc:
                        yield name, exc
    return keys, rows, extra


def chromium_local_keys(profile):
    ls = profile / "Local Storage/leveldb"
    if not ls.is_dir():
        return []
    key_versions = []
    with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
        for item in leveldb.FolderReader(ls).GetRecords(use_manifest=True, use_sequence_number=True):
            if b"venice.ai" in item.record.key and b"encryptionKey" in item.record.key:
                key_versions.append(item.record)
    key_versions.sort(key=lambda item: item.sequence_number or 0, reverse=True)
    if not key_versions or key_versions[0].record_type != 1:
        return []
    candidates = []
    for item in key_versions:
        if item.record_type != 1:
            continue
        try:
            candidates.append(parse_key(item.value[1:]))
        except (UnicodeError, ValueError):
            # A stale malformed value is not a usable historical key.
            continue
    return candidates


def chromium_source(profile, historical_profiles=()):
    keys = KeyRing([*chromium_local_keys(profile), *(key for old in historical_profiles for key in chromium_local_keys(old))])
    folder = profile / "IndexedDB/https_venice.ai_0.indexeddb.leveldb"
    if not folder.is_dir():
        alternatives = sorted(p.name for p in (profile / "IndexedDB").glob("https_venice.ai_0.indexeddb*"))
        raise NotImplementedError(f"Venice Chromium IndexedDB LevelDB is absent; preserved alternative files: {alternatives}. Extraction for this storage backend needs validation before use")
    latest = {}
    with open(os.devnull, "w") as sink, contextlib.redirect_stderr(sink):
        for item in chrome.FolderReader(folder).GetRecords(use_manifest=True, use_sequence_number=True, parse_value=False, include_raw_data=True, load_blobs=False):
            if isinstance(item.key, (chrome.DatabaseNameKey, chrome.ObjectStoreNamesKey, chrome.ObjectStoreDataKey, chrome.BlobEntryKey)):
                old = latest.get(item.raw_key)
                if old is None or (item.sequence_number or 0) > (old.sequence_number or 0):
                    latest[item.raw_key] = item
    dbnames = {}
    stores = {}
    data = {}
    blobs = {}
    for item in latest.values():
        if item.type != 1 or item.raw_value is None:
            continue
        if isinstance(item.key, chrome.DatabaseNameKey):
            dbnames[item.key.ParseValue(item.raw_value)] = item.key.database_name
        elif isinstance(item.key, chrome.ObjectStoreNamesKey):
            stores[(item.database_id, item.key.ParseValue(item.raw_value))] = item.key.object_store_name
        elif isinstance(item.key, chrome.ObjectStoreDataKey):
            data[(item.database_id, item.object_store_id, item.key.encoded_user_key.value)] = item
        elif isinstance(item.key, chrome.BlobEntryKey):
            blobs[(item.database_id, item.object_store_id, item.key.user_key.value)] = item
    classic_ids = [dbid for dbid, name in dbnames.items() if name == "venice-db-encrypted"]
    if len(classic_ids) != 1:
        raise ValueError(f"Expected one Brave Venice classic database; found {len(classic_ids)}")
    classic = classic_ids[0]
    blob_folder = profile / "IndexedDB/https_venice.ai_0.indexeddb.blob"
    blob_reader = chrome.BlobFolderReader(blob_folder) if blob_folder.is_dir() else None
    rx_ids = [dbid for dbid, name in dbnames.items() if name == "venice-rx-db-encrypted"]
    compatibility = {"unsignedStoreIdFallbackUses": 0, "wireVersions": collections.Counter()}
    def decoded_rows(dbids):
        for (dbid, storeid, userkey), item in data.items():
            if dbid not in dbids:
                continue
            name = stores.get((dbid, storeid))
            if name is None and storeid >= 128:
                # dfindexeddb decodes store-name metadata IDs as signed int8,
                # while data-key IDs are unsigned. Match their byte-equivalent.
                name = stores.get((dbid, storeid - 256))
                if name is not None:
                    compatibility["unsignedStoreIdFallbackUses"] += 1
            try:
                if not name:
                    raise ValueError(f"Unmapped Chromium store {storeid}")
                parsed = item.key.ParseValue(item.raw_value)
                if parsed.blob_size is None:
                    _, offset = read_varint(item.raw_value, 0)
                    compatibility["wireVersions"][wire_versions(item.raw_value[offset:])] += 1
                    yield name, parsed.value
                else:
                    linked = blobs.get((dbid, storeid, userkey))
                    if linked is None:
                        raise ValueError("Wrapped Chromium record lacks a blob entry")
                    entries = linked.key.ParseValue(linked.raw_value).entries
                    if len(entries) != 1 or entries[0].blob_number is None:
                        raise ValueError("Unexpected Chromium external blob mapping")
                    if blob_reader is None:
                        raise FileNotFoundError("Chromium external blob directory is absent")
                    _, raw = blob_reader.ReadBlob(dbid, entries[0].blob_number)
                    if len(raw) != parsed.blob_size:
                        raise ValueError("Chromium wrapped blob length mismatch")
                    compatibility["wireVersions"][wire_versions(raw)] += 1
                    yield name, blink.V8ScriptValueDecoder.FromBytes(raw)
            except Exception as exc:
                yield name or f"unknown-store-{storeid}", exc
    venice_ids = {classic, *rx_ids}
    return keys, lambda: decoded_rows({classic}), lambda: decoded_rows(set(rx_ids)), {
        "databases": {dbid: dbnames[dbid] for dbid in venice_ids},
        "rxDatabaseFound": bool(rx_ids),
        "rxActiveStoreNames": sorted({stores.get((dbid, storeid)) or stores.get((dbid, storeid - 256)) or f"unknown-store-{storeid}" for dbid, storeid, _ in data if dbid in rx_ids}),
        "activeDataRecords": sum(dbid in venice_ids for dbid, _, _ in data),
        "activeBlobRecords": sum(dbid in venice_ids for dbid, _, _ in blobs),
        "v8VersionCapOverride": 16,
        "compatibility": compatibility,
    }


def fire_rx(profile, keys, errors, depths, warnings, source_counts, firefox_extra, encrypted_sizes):
    origin = profile / "storage/default/https+++venice.ai/idb"
    dbs = list(origin.glob("*vdeentipcyer-crnxe--d.sqlite"))
    if len(dbs) != 1:
        return {}
    db = dbs[0]
    result = collections.defaultdict(list)
    with sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        stores = dict(conn.execute("SELECT id, name FROM object_store"))
        for storeid, name in stores.items():
            for ordinal, (payload, file_ids) in enumerate(conn.execute("SELECT data, file_ids FROM object_data WHERE object_store_id=?", (storeid,)), 1):
                source_counts[name] += 1
                obj = None
                try:
                    obj = FirefoxStructuredCloneDecoder.FromBytes(payload)
                    obj, external = bind_firefox_external_files(obj, file_ids, db.with_suffix(".files"))
                    for item in external:
                        firefox_extra["externalFiles"].append({"store": name, **item})
                        firefox_extra["externalFileReferences"] += 1
                        firefox_extra["externalFileBytes"] += item["bytes"]
                    doc = obj.get("d", obj) if isinstance(obj, dict) else obj
                    if isinstance(doc, dict) and "__encryptedData" in doc:
                        observe_encrypted_record_size(encrypted_sizes, name, doc)
                        doc, depth = decrypt_layers(keys, doc)
                        depths[name][depth] += 1
                    elif has_nested_encryption(doc):
                        warnings.append({"store": name, "recordOrdinal": ordinal, "warning": "Nested encrypted field preserved without interpretation"})
                    result[name].append(serial(doc))
                except Exception as exc:
                    errors.append(issue("rx-decode", name, ordinal, obj, exc))
    return dict(result)


def extract(browser, profile, output, historical_profiles=()):
    profile = safe_copy(profile, browser)
    historical_profiles = [safe_copy(p, browser) for p in historical_profiles]
    output = pathlib.Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    if browser == "firefox":
        keys, rows, extra = firefox_source(profile, historical_profiles)
    else:
        keys, rows, rx_rows, extra = chromium_source(profile, historical_profiles)
    grouped = collections.defaultdict(list)
    depths = collections.defaultdict(collections.Counter)
    encrypted_sizes = collections.defaultdict(list)
    errors = []
    warnings = []
    source_counts = collections.Counter()
    rx_source_counts = collections.Counter()
    for ordinal, (name, obj) in enumerate(rows(), 1):
        source_counts[name] += 1
        try:
            if isinstance(obj, Exception):
                raise obj
            if not isinstance(obj, dict):
                grouped[name].append(serial(obj))
                continue
            if name == "_encryptionSettings":
                keys.decrypt(obj["keyChangeDetection"], key_check=True)
                grouped[name].append({"keyChangeDetectionAuthenticated": True})
                continue
            if "__encryptedData" in obj:
                observe_encrypted_record_size(encrypted_sizes, name, obj)
                value, depth = decrypt_layers(keys, obj)
                depths[name][depth] += 1
            else:
                value = serial(obj)
            grouped[name].append(value)
        except Exception as exc:
            errors.append(issue("classic-decode", name, ordinal, obj, exc))
    required = ["conversations", "messages"]
    if any(x not in grouped for x in required):
        raise ValueError(f"Missing classic stores: {[x for x in required if x not in grouped]}")
    conversations = grouped.pop("conversations")
    messages = grouped.pop("messages")
    images = grouped.pop("messageImages", [])
    conv_ids = [x["id"] for x in conversations]
    message_ids = [x["id"] for x in messages]
    if len(set(conv_ids)) != len(conv_ids) or len(set(message_ids)) != len(message_ids):
        raise ValueError("Duplicate conversation or message IDs")
    by_conv = collections.defaultdict(list)
    for item in messages:
        by_conv[item.get("conversationId")].append(item)
    by_message = collections.defaultdict(list)
    for item in images:
        by_message[item.get("messageId")].append(item)
    orphan_messages = [m["id"] for m in messages if m.get("conversationId") not in conv_ids]
    orphan_images = [im.get("messageId") for im in images if im.get("messageId") not in message_ids]
    orphans = {"messages": len(orphan_messages), "images": len(orphan_images)}
    if browser == "firefox":
        rx = fire_rx(profile, keys, errors, depths, warnings, rx_source_counts, extra, encrypted_sizes)
    else:
        rx = collections.defaultdict(list)
        for ordinal, (name, obj) in enumerate(rx_rows(), 1):
            rx_source_counts[name] += 1
            try:
                if isinstance(obj, Exception):
                    raise obj
                doc = obj.get("d", obj) if isinstance(obj, dict) else obj
                if isinstance(doc, dict) and "__encryptedData" in doc:
                    observe_encrypted_record_size(encrypted_sizes, name, doc)
                    doc, depth = decrypt_layers(keys, doc)
                    depths[name][depth] += 1
                elif has_nested_encryption(doc):
                    warnings.append({"store": name, "recordOrdinal": ordinal, "warning": "Nested encrypted field preserved without interpretation"})
                rx[name].append(serial(doc))
            except Exception as exc:
                errors.append(issue("rx-decode", name, ordinal, obj, exc))
        rx = dict(rx)
        if not extra["rxDatabaseFound"]:
            warnings.append({"store": "venice-rx-db-encrypted", "warning": "RxDB not found; Mind and memory stores may be absent"})
    if not keys.evidence()["authenticated"]:
        raise ValueError("No authenticated Venice key-check or decrypted record in the snapshot")
    manifest = {
        "schemaVersion": 1,
        "browser": browser,
        "profileCopy": str(profile),
        "exportedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "counts": {"conversations": len(conversations), "messages": len(messages), "messageImages": len(images), **{k: len(v) for k, v in grouped.items()}},
        "encryptedLayers": {k: dict(v) for k, v in depths.items()},
        "encryptedRecordBytes": {k: size_summary(v) for k, v in sorted(encrypted_sizes.items())},
        "orphans": orphans,
        "orphanIds": {"messages": orphan_messages, "messageImagesByMessageId": orphan_images},
        "sourceStoreCounts": dict(sorted(source_counts.items())),
        "rxSourceStoreCounts": dict(sorted(rx_source_counts.items())),
        "extractionErrors": errors,
        "warnings": warnings,
        "keyAuthentication": keys.evidence(),
        "rxCounts": {k: len(v) for k, v in rx.items()},
        "firefoxMetadata": extra if browser == "firefox" else None,
        "chromiumMetadata": extra if browser != "firefox" else None,
    }
    output.mkdir(parents=True)
    (output / "chats").mkdir()
    (output / "supplemental").mkdir()
    for conv in conversations:
        ident = safe_record_id(conv["id"])
        own = sorted(by_conv[ident], key=lambda x: (x.get("createdAtUnixTimestamp") or 0, str(x["id"])))
        save(output / "chats" / f"chat-{ident}.json", {"sourceBrowser": browser, "conversation": conv, "messages": own, "messageImages": [im for m in own for im in by_message[m["id"]]]})
    if orphans["messages"] or orphans["images"]:
        save(output / "supplemental/orphans.json", {"messages": [m for m in messages if m.get("conversationId") not in conv_ids], "messageImages": [im for im in images if im.get("messageId") not in message_ids]})
    for name, items in grouped.items():
        if name == "_encryptionSettings":
            continue
        save(output / "supplemental" / f"classic-{name}.json", items)
    for name, items in rx.items():
        save(output / "supplemental" / f"rx-{name}.json", items)
    manifest["siteFilesystem"] = copy_site_filesystem(browser, profile, output)
    save(output / "manifest.json", manifest)
    print(json.dumps({"browser": browser, "counts": manifest["counts"], "rxCounts": manifest["rxCounts"], "orphans": orphans, "extractionErrors": len(errors), "warnings": len(warnings), "keyAuthentication": manifest["keyAuthentication"], "output": str(output)}, indent=2))
    return len(errors)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("browser", choices=["firefox", "brave"])
    p.add_argument("profile_copy", type=pathlib.Path)
    p.add_argument("output", type=pathlib.Path)
    p.add_argument("--key-source", action="append", type=pathlib.Path, default=[], help="Older copied profile whose Venice key may decrypt rotated records")
    a = p.parse_args()
    if extract(a.browser, a.profile_copy, a.output, a.key_source):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
