#!/usr/bin/env bash
# Build SimpleEditor.app + SimpleEditor.dmg for macOS.
#
# Double-click this file in Finder. It will:
#   1. Verify Python 3 (guide install if missing).
#   2. Download ffmpeg + ffprobe for the host architecture (arm64 or x86_64).
#   3. Install PyInstaller.
#   4. Build dist/SimpleEditor.app.
#   5. Package dist/SimpleEditor.dmg (plain — .app + shortcut to /Applications).
set -euo pipefail
cd "$(dirname "$0")"

echo "=============================================================="
echo "  Building SimpleEditor.app + .dmg"
echo "=============================================================="

# ---- 1. Locate python3. ----
if ! command -v python3 >/dev/null 2>&1; then
  echo
  echo "ERROR: python3 is not installed."
  echo "Install it from https://www.python.org/downloads/ (or 'brew install python')"
  echo "and re-run this script."
  echo
  read -n 1 -s -r -p "Press any key to close..."
  exit 1
fi
echo "[1/5] python3: $(command -v python3)"

# ---- 2. Fetch ffmpeg for host arch if missing. ----
ARCH="$(uname -m)"            # arm64 or x86_64
if [[ "$ARCH" != "arm64" && "$ARCH" != "x86_64" ]]; then
  echo "ERROR: unknown Mac architecture: $ARCH"; exit 1
fi
echo "[2/5] Host arch: $ARCH"

need_download=0
[[ -f ffmpeg  ]] || need_download=1
[[ -f ffprobe ]] || need_download=1

if [[ $need_download -eq 1 ]]; then
  # evermeet.cx serves latest static builds per-arch as zips.
  # URLs are stable; "/getrelease/zip" redirects to the newest release.
  if [[ "$ARCH" == "arm64" ]]; then
    FF_URL="https://www.osxexperts.net/ffmpeg71arm.zip"
    FP_URL="https://www.osxexperts.net/ffprobe71arm.zip"
    FALLBACK_FF="https://evermeet.cx/ffmpeg/ffmpeg.zip"
    FALLBACK_FP="https://evermeet.cx/ffmpeg/ffprobe.zip"
  else
    FF_URL="https://evermeet.cx/ffmpeg/ffmpeg.zip"
    FP_URL="https://evermeet.cx/ffmpeg/ffprobe.zip"
    FALLBACK_FF=""
    FALLBACK_FP=""
  fi

  tmp="$(mktemp -d)"
  echo "     Downloading ffmpeg ($ARCH)..."
  if ! curl -fL --retry 3 -o "$tmp/ffmpeg.zip" "$FF_URL"; then
    if [[ -n "$FALLBACK_FF" ]]; then
      echo "     Primary URL failed, trying fallback..."
      curl -fL --retry 3 -o "$tmp/ffmpeg.zip" "$FALLBACK_FF"
    else
      echo "ERROR: ffmpeg download failed."; exit 1
    fi
  fi
  echo "     Downloading ffprobe ($ARCH)..."
  if ! curl -fL --retry 3 -o "$tmp/ffprobe.zip" "$FP_URL"; then
    if [[ -n "$FALLBACK_FP" ]]; then
      curl -fL --retry 3 -o "$tmp/ffprobe.zip" "$FALLBACK_FP"
    else
      echo "ERROR: ffprobe download failed."; exit 1
    fi
  fi

  (cd "$tmp" && unzip -oq ffmpeg.zip && unzip -oq ffprobe.zip)
  # The zips contain a single binary each, named ffmpeg / ffprobe.
  mv "$tmp/ffmpeg"  ./ffmpeg
  mv "$tmp/ffprobe" ./ffprobe
  chmod +x ffmpeg ffprobe
  rm -rf "$tmp"

  # macOS quarantines downloaded executables. Strip it so PyInstaller can
  # bundle them without Gatekeeper choking at runtime.
  xattr -dr com.apple.quarantine ffmpeg ffprobe 2>/dev/null || true
  echo "     ffmpeg + ffprobe ready."
else
  echo "     ffmpeg + ffprobe already present, skipping download."
fi

# Quick sanity check.
./ffmpeg  -version >/dev/null 2>&1 || { echo "ERROR: ffmpeg binary does not run on this Mac."; exit 1; }
./ffprobe -version >/dev/null 2>&1 || { echo "ERROR: ffprobe binary does not run on this Mac."; exit 1; }

# ---- 3. PyInstaller. ----
echo "[3/5] Installing PyInstaller + pywebview + faster-whisper..."
# pywebview pulls in its pyobjc Cocoa/WebKit deps on macOS. faster-whisper: "Remove
# fillers" (runs on CPU on Macs; the speech model downloads on first use).
python3 -m pip install --upgrade --quiet pip pyinstaller pywebview faster-whisper

# ---- 4. Build the .app. ----
echo "[4/5] Running PyInstaller..."
rm -rf build dist
python3 -m PyInstaller --noconfirm SimpleEditor.spec

if [[ ! -d "dist/SimpleEditor.app" ]]; then
  echo "ERROR: dist/SimpleEditor.app was not produced."; exit 1
fi

# Strip quarantine from everything inside the bundle (defensive — the
# downloaded ffmpeg was already de-quarantined, but PyInstaller sometimes
# re-adds it during copying).
xattr -cr "dist/SimpleEditor.app" 2>/dev/null || true

# Ad-hoc codesign. This doesn't give us notarization (which needs an Apple
# Developer account), but it satisfies Gatekeeper's "must be signed" hard
# block on newer macOS. Users will still see the "unidentified developer"
# right-click-to-Open warning on first launch — but NOT the stricter
# "Apple could not verify this app is free of malware" dead-end dialog.
# --force: overwrite any existing signature.
# --deep:  sign every nested binary (ffmpeg, ffprobe, Python dylibs, etc.).
# -s -:    ad-hoc signature (no identity required).
echo "     Ad-hoc signing bundle..."
codesign --force --deep --sign - "dist/SimpleEditor.app" || {
  echo "     WARNING: codesign failed — app will still work but may trigger"
  echo "              the stricter Gatekeeper block. Continuing anyway."
}
# Verify.
codesign --verify --deep --strict "dist/SimpleEditor.app" 2>&1 || true

# ---- 5. Package a plain DMG. ----
echo "[5/5] Creating DMG..."
DMG_STAGE="$(mktemp -d)"
cp -R "dist/SimpleEditor.app" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"

DMG_OUT="dist/SimpleEditor.dmg"
rm -f "$DMG_OUT"

# UDZO = compressed read-only dmg. -volname sets the mounted volume name.
hdiutil create \
  -volname "SimpleEditor" \
  -srcfolder "$DMG_STAGE" \
  -ov -format UDZO \
  "$DMG_OUT"

rm -rf "$DMG_STAGE"

echo
echo "=============================================================="
echo "  Build complete."
echo "  App: $(pwd)/dist/SimpleEditor.app"
echo "  DMG: $(pwd)/dist/SimpleEditor.dmg"
echo
echo "  Users: double-click the .dmg, drag SimpleEditor to Applications."
echo "  First run: right-click -> Open (unidentified developer warning)."
echo "=============================================================="
echo
read -n 1 -s -r -p "Press any key to close..."
