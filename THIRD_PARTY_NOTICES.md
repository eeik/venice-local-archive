# Third-party notices

Runtime dependencies are pinned in `requirements-lock.txt` and retain their own licenses:

- cffi — MIT
- cramjam — MIT
- dfindexeddb — Apache-2.0
- pycparser — BSD-3-Clause
- PyNaCl — Apache-2.0; it uses libsodium (ISC)
- python-snappy — BSD-3-Clause
- zstd — BSD-3-Clause

The wheelhouse release redistributes the corresponding unmodified wheels and includes their embedded package metadata and license files. Review those files as the authoritative notices for the exact artifacts.

The lock is intentionally complete and setup installs its exact versions with `--no-deps`. This avoids an overly strict historical `python-snappy` metadata pin in `dfindexeddb`; the newer pinned wheel is covered by synthetic decoding tests and the two-browser acceptance capture.
