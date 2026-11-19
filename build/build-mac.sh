#!/bin/bash
# Build the macOS Adapter template (adapter-mac.zip) - MUST run on a Mac (PyInstaller
# cannot cross-compile). Output: dist-mac/adapter-mac.zip containing
#   TokenVault Adapter.app              (ad-hoc signed only, LSUIElement=true)
#   Install.command, Uninstall.command  (rendered from packaging/mac/*.command.tmpl)
# Copy the zip to the Manager's  <data dir>/templates/adapter-mac.zip  (or set
# TOKENVAULT_MAC_TEMPLATE) so "Create installer -> macOS" can use it.
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "build-mac.sh must be run on macOS." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="TokenVault Adapter.app"
BIN_NAME="TokenVault Adapter"
OUT="$ROOT/dist-mac"
WORK="$ROOT/build-mac-work"
BASE_PY="${PYTHON:-python3}"

rm -rf "$OUT" "$WORK"
mkdir -p "$OUT" "$WORK"

# Isolated venv: the build must not depend on whatever is installed system-wide.
# Runtime deps come from pyproject.toml; the build tools are pinned here.
VENV="$WORK/venv"
"$BASE_PY" -m venv "$VENV"
PY="$VENV/bin/python"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet "$ROOT" "pyinstaller==6.11.1" "pyobjc-framework-Cocoa>=10,<12"

"$PY" -m PyInstaller --noconfirm --clean --windowed \
  --name "$BIN_NAME" \
  --osx-bundle-identifier vn.ics.tokenvault.adapter \
  --paths "$ROOT/src" \
  --hidden-import pystray._darwin \
  --hidden-import watchdog.observers.fsevents \
  --collect-submodules tokenvault.adapter \
  --collect-submodules tokenvault.common \
  --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" \
  "$ROOT/packaging/entry_adapter.py"

APP="$WORK/dist/$APP_NAME"
[ -d "$APP" ] || { echo "PyInstaller did not produce $APP_NAME" >&2; exit 1; }

# Merge our Info.plist keys (LSUIElement=true: menu-bar agent, no Dock icon).
"$PY" - "$APP/Contents/Info.plist" "$ROOT/packaging/mac/Info.plist" <<'PYEOF'
import plistlib, sys
dst, src = sys.argv[1], sys.argv[2]
with open(dst, "rb") as f:
    info = plistlib.load(f)
with open(src, "rb") as f:
    info.update(plistlib.load(f))
with open(dst, "wb") as f:
    plistlib.dump(info, f)
PYEOF

# Ad-hoc signature (required for arm64 to run at all). NOT a Developer ID signature:
# the owner's Install.command removes the quarantine flag instead of notarising.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

STAGE="$WORK/stage"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
for name in Install Uninstall; do
  sed -e "s|@@APP_NAME@@|$APP_NAME|g" -e "s|@@BIN_NAME@@|$BIN_NAME|g" \
    "$ROOT/packaging/mac/$name.command.tmpl" > "$STAGE/$name.command"
  chmod 755 "$STAGE/$name.command"
done

# zip (not ditto) keeps unix modes and symlinks and adds no __MACOSX folder.
(cd "$STAGE" && zip -qry "$OUT/adapter-mac.zip" "$APP_NAME" Install.command Uninstall.command)

echo "Built $OUT/adapter-mac.zip"
echo "Copy it to the Manager data folder: templates/adapter-mac.zip"
