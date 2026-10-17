#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python3 - <<'PY'
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path.cwd()
folder = root / "raspberry-pi-package"
archive = root / "cashflow-engine-raspberry-pi.zip"
if not (folder / "install.sh").is_file() or not (folder / "README.md").is_file():
    raise SystemExit("Installer bundle is incomplete: install.sh and README.md are required.")

with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            zf.write(path, path.relative_to(root))
print(f"Created {archive} ({archive.stat().st_size:,} bytes)")
PY
