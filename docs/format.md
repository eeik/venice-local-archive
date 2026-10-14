# Archive format notes

Each browser run contains `run-status.json`, an immutable `snapshot/`, `inventory.json`, `readable/`, and `Venice-readable.zip`. The snapshot has `snapshot-status.json` plus `snapshot-manifest.sha256`; the readable export has a manifest, verification result, one structured file per conversation, Markdown renderings, extracted media, and recovered Venice metadata where present.

`run-status.json` moves from `incomplete` to `complete` only after snapshot hashing, authenticated extraction, rendering, verification, ZIP CRC testing, and archive hashing succeed. Paths are provenance, not portable identifiers.

Lineage reports classify exact identity/content and conservative relationships without merging records. Combined archives label every source browser. Operator summaries contain operational metadata and counts, but no chat text or titles.

## Count vocabulary

| Count | Meaning |
|---|---|
| Snapshot files | Physical browser-storage files copied and hashed. They can be SQLite databases, LevelDB tables/logs, blobs, or browser File System files. They are not chats. |
| Conversations | Top-level classic Venice conversation records recovered from one browser capture. Each becomes one structured `chats/chat-<id>.json` file. |
| Messages | Child message records associated with recovered conversations and embedded in those chat JSON files. |
| Message IDs | A parallel Venice index/metadata store, not an additional conversation or message export. |
| Message images / attachments | Media records and embedded payloads linked to messages, counted separately. |
| RxDB documents | Supplemental memories, Minds, insights, folders, and internal metadata. These are not included in the classic conversation count. |
| Logical conversation | The same `(source browser, conversation ID)` identity across captures. |
| Immutable version | One logical conversation with one exact normalized content hash. Appends, edits, and branches remain separate versions. |
| Occurrence | One immutable version observed in one capture run. An unchanged recapture adds an occurrence without creating a new version. |

Physical-file counts depend heavily on the browser backend. A Chromium-family profile may use hundreds of LevelDB, blob, and File System files while Firefox may store comparable logical records in fewer SQLite and sidecar files. Neither physical count is an estimate of conversation count. Likewise, the newest live profile is not the whole archive: older immutable runs can contain conversations that no longer exist in the current profile.

These files are preservation evidence. Consumers should treat unknown fields as forward-compatible additions and should never infer that similar text proves two IDs are the same conversation.
