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
