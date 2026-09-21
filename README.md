# Simple Video Editor

Editor for concatenating MP4s, cutting unwanted segments, muting, and exporting to MP4. Uses a tiny local Python server (stdlib only) + `ffmpeg`. Runs either in your browser (dev) or as a **standalone Windows app** in its own window.

## Install as a Windows app (recommended)

Double-click **`build-installer.bat`**. It downloads ffmpeg, builds a self-contained app, and produces:

- `dist\SimpleEditor.exe` — the standalone app (opens in its own window via Edge WebView2, no browser tab).
- `dist\SimpleEditor-Setup.exe` — a normal Windows installer.

Run `SimpleEditor-Setup.exe` to install. It adds Start-menu + desktop shortcuts, an app icon, and an entry in *Add or remove programs* (per-user, no admin prompt). Your editing data lives in `%LOCALAPPDATA%\SimpleEditor\`.

Prerequisites for building: Python 3, and [Inno Setup 6](https://jrsoftware.org/isdl.php) (`winget install JRSoftware.InnoSetup`). The batch file installs the Python build deps (PyInstaller, pywebview, Pillow) automatically.

## Run from source (dev)

### One-time setup

1. **Install ffmpeg** (the only external dependency):
   ```powershell
   winget install Gyan.FFmpeg
   ```
   Close and reopen your terminal so the new `PATH` takes effect.

2. Python 3 is already installed on this machine.

## Run

Double-click `run.bat`, or in a terminal:

```powershell
python server.py
```

Your browser opens to `http://127.0.0.1:8765/`.

## Use

1. **Drag MP4 files onto the drop zone** (or click "Choose files…"). They upload to the local server's `work/uploads/` folder and appear as clips. If you prefer, expand "…or paste full paths" to add files by absolute path without uploading.
2. For each clip:
   - Use the native video controls or click the timeline to scrub.
   - Play to a spot you want to **remove**, click **Mark cut START @ playhead**, scrub to the end of the unwanted part, click **Mark cut END**. The red band on the timeline shows what will be cut.
   - **Preview the result:** with **Skip cuts on play** ticked (default), pressing Space / playing jumps over the red cut regions, so you hear/see exactly what the export will look like. Click the timeline just before a cut and play to check the join. Untick it to watch the raw source instead.
   - **Hover the timeline** to see a thumbnail of the frame under your cursor (without moving the playhead) — handy for finding exact cut points fast.
   - **Zoom the timeline:** scroll the mouse wheel *up* while hovering the timeline to zoom in toward the cursor (for frame-precise cut placement), and *down* to zoom back out. A 🔍 readout shows the visible range and zoom level — click it to reset to the whole clip. While zoomed, playback auto-scrolls to keep the playhead on-screen.
   - **Undo / redo:** Ctrl+Z / Ctrl+Shift+Z (or the ↶/↷ buttons) covers cuts, deletes, clip reordering, and clip removal — not just the last action.
   - **Playback speed:** use the Speed dropdown, or press `]` / `[` to go faster / slower (0.5×–4×), and `\` to reset to 1×. Handy for skimming long footage.
   - **Adjust a cut:** click a red cut band to *select* it (white outline + edge handles). Drag either handle to resize the cut; the preview jumps to the edge you're dragging so you can place it precisely. Press Delete/Backspace (or the ✕ button) to remove the selected cut, Esc to deselect. Clicking anywhere else on the timeline just scrubs as normal, so you won't grab a cut by accident.
   - Delete individual cuts from the list below.

Your session **autosaves**: clips and cuts are remembered, so if you close or restart the app it offers to restore exactly where you left off. (Files that were moved or deleted in the meantime are skipped and reported.)
3. Reorder clips with ▲ / ▼, remove with ✕.
4. Set the **Output folder** and **Filename**.
5. Click **Export** to open the **export settings** dialog:
   - **Quality** — *High* (near-lossless, largest file), *Balanced* (default), or *Small* (most compression). Controls the encoder's CRF/CQ target.
   - **Resolution** — *Same as source* (default), or downscale to 1080p / 720p / 480p. Downscaling forces a re-encode (Accurate).
   - **Accurate cuts** (on by default) — cuts land exactly where you set them and audio stays perfectly in sync. Re-encodes the whole output in one pass (GPU-accelerated when available). Off = stream-copy fast mode (cuts snap to keyframes).
   - **Keep audio** (on by default) — includes the original audio (off = silent video).
   - Your choices are remembered for next time.
6. Click **Export ▸** in the dialog to start. Progress appears in the toast at the bottom.

## How the export works

For each clip, the UI turns your "cut ranges" into "keep ranges" (the complement).

- **Accurate mode (default):** a single ffmpeg `filter_complex` pass trims every kept range and concatenates them on one continuous timeline, re-encoding video (and audio, if kept) once with the best available H.264 encoder (NVENC / QSV / AMF / libx264). Because it's one process on one timeline, audio and video **cannot drift out of sync**, and cuts are frame-accurate. The output uses a fixed ~2-second keyframe interval so players (e.g. VLC) can seek instantly. The **Quality** setting maps to the encoder's rate-control target, and **Resolution** rescales in the same pass. Clips of different formats are normalized automatically; sources with no audio get generated silence so the audio track stays aligned.
- **Fast mode (Accurate off):** stream-copies each kept range and joins them with the concat demuxer (`-c copy`). Near-instant, no re-encode, but cuts snap to the nearest prior keyframe (so a cut may include up to ~2s before your requested in-point). Good for rough trims of a single source.

## Notes

- Only the current machine can reach the server (binds to `127.0.0.1`).
- Uploaded files live in `./work/uploads/` until you clean them up manually.
- Temporary segments go into `./work/job_*/` and are cleaned up after a successful export. If an export fails, leftovers there can help diagnose it.
- To stop the server: Ctrl+C in the terminal, or just close the window.
- ffmpeg lookup order: `./ffmpeg.exe` (drop-in), PATH, common install dirs, WinGet shim, then a deep scan under `%LOCALAPPDATA%\Microsoft\WinGet\Packages`. So winget installs work even before you restart your shell.
