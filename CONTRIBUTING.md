# Contributing

Use Python 3.12 on Windows. Changes must preserve append-only output, closed-browser enforcement, authenticated extraction, deterministic comparison, and offline capture. Add only synthetic fixtures.

Before submitting a change:

```powershell
python -m unittest discover -s tests -v
python scripts\privacy_scan.py .
python scripts\build_release.py --check-only
```

Never commit real profiles, snapshots, exports, database files, media, keys, conversation identifiers/titles, absolute personal paths, virtual environments, wheels, or generated release ZIPs.
