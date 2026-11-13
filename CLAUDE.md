# Simple Video Editor — notes for Claude Code

Local video cut/concat/export tool. `server.py` = Python stdlib HTTP backend on
127.0.0.1:8765 that shells out to ffmpeg/ffprobe. `index.html`/`app.js`/`style.css`
= vanilla JS UI. `desktop.py` = pywebview window around the server (the packaged
entry point). Developed on Windows; macOS support is written but **not yet
verified on a real Mac** — if you're on a Mac, that's likely your job.

## Making it work on macOS

1. **Prereqs:** Python 3 (`brew install python` or python.org) and Xcode CLT
   (`xcode-select --install`, for `codesign`/`hdiutil`).
2. **Run from source first** (fastest feedback loop):
   ```bash
   brew install ffmpeg            # or drop ffmpeg/ffprobe binaries in the repo root
   python3 -m pip install pywebview
   python3 desktop.py             # native window; or `python3 server.py` for a browser tab
   ```
   `_find_tool()` in server.py looks in the repo root, then PATH, then
   /opt/homebrew/bin and /usr/local/bin.
3. **Build the app:** `./build-mac.command` → downloads ffmpeg for the host arch
   into the repo root (gitignored), installs PyInstaller + pywebview, builds
   `dist/SimpleEditor.app`, ad-hoc signs it, and makes `dist/SimpleEditor.dmg`.
   The build is single-arch (arm64 or x86_64, whichever Mac builds it).
4. **First launch:** unsigned app → right-click → Open. Frozen user data lives in
   `~/Library/Application Support/SimpleEditor/`.

## Things to verify / likely to need fixes on Mac

- **Window backend:** `desktop.py` tries `gui="edgechromium"` (Windows-only) and
  falls back to `webview.start()` → Cocoa/WKWebView. Check the fallback actually
  works; it may be cleaner to only pass `edgechromium` when `sys.platform == "win32"`.
- **PyInstaller spec:** `SimpleEditor.spec` hiddenimports are Windows-specific
  (edgechromium, winforms, pythonnet). On Mac these should just warn; if the
  .app fails to open its window, add `webview.platforms.cocoa` and ensure
  `pyobjc` is installed.
- **Encoder:** accurate export uses `h264_videotoolbox` on macOS via
  `pick_h264_encoder()` (constant quality `-q:v`, Apple Silicon only — Intel
  Macs fall back to libx264). After an export, the finished-export message shows
  `encoder: ...` — confirm it's `h264_videotoolbox` on Apple Silicon.
- **Drag & drop / file picker:** "Choose files…" uses an AppleScript picker
  (`/api/pick-files` in server.py). Dropped files in WKWebView may not expose real
  paths — test both.
- **"Show in Finder"** after export uses `open -R`.
- **Missing:** no `.icns` app icon (`BUNDLE(icon=None)` in the spec). `make_icon.py`
  makes `app.ico`/`favicon.png`; convert with `sips`/`iconutil` if wanted.

- **Filler removal (`fillers.py`):** needs `faster-whisper`. On Mac it runs
  Whisper `small` on the CPU (CTranslate2 has no Metal backend); the model
  downloads from Hugging Face on first click. Verify: run "Remove fillers" on a
  talking clip from source, then in the built .app. The spec bundles
  faster_whisper / ctranslate2 / tokenizers / av / huggingface_hub via
  `collect_all` on macOS; if the .app fails with an import error, that list is
  the place to fix. Set `SIMPLEEDITOR_WHISPER_DEVICE=cpu` on a GPU machine to
  test the CPU path.

## Filler detection — how it works

`/api/fillers` → `run_fillers()` → `fillers.detect_fillers()` returns words +
fillers, each with `cs`/`ce` (the cut range). The front end (`applyFillerRanges`
and the transcript panel in app.js) adds them as ordinary cuts; a filler counts
as "removed" when any cut covers it, so undo and manual edits stay in sync.
Held-sound detector (`held_sounds`): steady voiced spectrum for 0.30 s+, or
0.15 s+ next to a pause; grown out over onset/decay; the cut reaches into
adjacent silence for a clean join. Tuning constants are at the top of
`fillers.py`.

## Testing

- Syntax: `node --check app.js`, `python3 -c "import ast;ast.parse(open('server.py').read())"`.
- Backend in-process: `PYTHONPATH=. python3 -c "import server; srv,url=server.start_server(); print(url)"`.
- `_demo_clip.mp4` is a small sample clip for manual testing.

## Rules

- Accurate export must stay a **single ffmpeg filter_complex pass**
  (`run_accurate_export`). Never concat separately-encoded segments with `-c copy`
  when audio is kept — it drifts A/V out of sync.
- Keep the fixed ~2s GOP in accurate export (fast seeking in players).
- "Smooth cuts" uses per-segment `afade` in/out at joins (no `acrossfade`:
  that overlaps audio and would shorten it relative to the video).
- Don't break the Windows path (`build-installer.bat`, `installer.iss`) while
  fixing Mac issues; guard platform-specific code with `sys.platform`.
