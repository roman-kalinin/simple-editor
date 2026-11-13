# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — builds SimpleEditor for the current platform.
#
# Usage (Windows):  pyinstaller --noconfirm SimpleEditor.spec
# Usage (macOS):    pyinstaller --noconfirm SimpleEditor.spec
#
# Before building, drop these next to server.py:
#   Windows: ffmpeg.exe, ffprobe.exe
#   macOS:   ffmpeg, ffprobe   (static builds from https://evermeet.cx/ffmpeg/)
import sys
from pathlib import Path

HERE = Path(SPECPATH).resolve()
is_windows = sys.platform.startswith("win")
is_macos = sys.platform == "darwin"

# Bundled resources: HTML/JS/CSS + ffmpeg binaries.
# Tuple form for --add-data: (src, dest_dir_inside_bundle). Dest "." = root.
datas = [
    (str(HERE / "index.html"), "."),
    (str(HERE / "app.js"), "."),
    (str(HERE / "style.css"), "."),
]
for extra in ("favicon.png", "app.ico"):
    p = HERE / extra
    if p.is_file():
        datas.append((str(p), "."))
binaries = []

# App icon (Windows). Falls back to no icon if the file is missing.
_icon = HERE / "app.ico"
ICON = str(_icon) if _icon.is_file() else None

if is_windows:
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        p = HERE / name
        if p.is_file():
            binaries.append((str(p), "."))
else:
    for name in ("ffmpeg", "ffprobe"):
        p = HERE / name
        if p.is_file():
            binaries.append((str(p), "."))

if not binaries:
    print("[spec] WARNING: no bundled ffmpeg/ffprobe found — "
          "the built app will rely on the user's PATH.")

# Filler-word removal (fillers.py) needs faster-whisper and its native deps.
# Bundled on macOS by default (build-mac.command installs it). On Windows it's
# opt-in via SIMPLEEDITOR_BUNDLE_WHISPER=1 to keep the installer small; without
# it the app still runs and the "Remove fillers" button explains what's missing.
import os
hiddenimports_whisper = []
if is_macos or os.environ.get("SIMPLEEDITOR_BUNDLE_WHISPER") == "1":
    try:
        from PyInstaller.utils.hooks import collect_all
        for pkg in ("faster_whisper", "ctranslate2", "tokenizers", "av", "huggingface_hub"):
            try:
                d, b, h = collect_all(pkg)
                datas += d
                binaries += b
                hiddenimports_whisper += h
            except Exception as e:  # noqa: BLE001
                print(f"[spec] WARNING: could not collect {pkg}: {e}")
        hiddenimports_whisper += ["fillers", "numpy"]
    except Exception as e:  # noqa: BLE001
        print(f"[spec] WARNING: whisper bundling skipped: {e}")

# Entry point: desktop.py opens a native window (pywebview/WebView2) and starts
# the bundled server in-process. It imports server.py, so the backend is
# included automatically.
a = Analysis(
    ["desktop.py"],
    pathex=[str(HERE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "webview",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
        "clr_loader",
        "pythonnet",
        *hiddenimports_whisper,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SimpleEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # --noconsole on Windows hides the terminal window. On macOS this flag
    # has no effect on `.app` bundles produced below.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

if is_macos:
    # Wrap the single-file exe in a .app bundle so it double-clicks.
    app = BUNDLE(
        exe,
        name="SimpleEditor.app",
        icon=None,
        bundle_identifier="com.local.simpleeditor",
        info_plist={
            "CFBundleName": "SimpleEditor",
            "CFBundleDisplayName": "Simple Editor",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1",
            # Background agent: no Dock icon / menu bar flash.
            "LSUIElement": False,
            "NSHighResolutionCapable": True,
        },
    )
