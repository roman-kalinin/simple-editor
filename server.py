"""Simple video editor backend.

Serves index.html, streams local video files to the browser (with HTTP Range
support so scrubbing works), accepts chunked uploads, and runs ffmpeg to
produce the final output. No third-party Python deps — stdlib only.
"""
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def _resource_root() -> Path:
    """Read-only bundled resources (index.html, app.js, bundled ffmpeg).
    Under PyInstaller this is sys._MEIPASS (the extracted temp dir for
    --onefile, or the app folder for --onedir). Otherwise the script dir.
    """
    if _frozen():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).parent
    return Path(__file__).parent.resolve()


def _user_data_root() -> Path:
    """Writable user-data dir (work/, uploads/, debug.log).

    Frozen:
      - macOS: ~/Library/Application Support/SimpleEditor/  (the bundle
        itself lives in /Applications and is read-only for non-admins, so
        we can't use the exe's folder like we do on Windows).
      - Windows: next to the .exe — users typically drop it somewhere
        writable (Desktop, Downloads, user folder).
    Dev: script folder.
    """
    if _frozen():
        if sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "SimpleEditor"
            root.mkdir(parents=True, exist_ok=True)
            return root
        # Windows: when installed under Program Files the exe folder is not
        # writable for standard users, so keep user data in %LOCALAPPDATA%.
        # (Falls back to the exe folder if LOCALAPPDATA is somehow unset.)
        base = os.environ.get("LOCALAPPDATA")
        if base:
            root = Path(base) / "SimpleEditor"
            root.mkdir(parents=True, exist_ok=True)
            return root
        return Path(sys.executable).parent
    return Path(__file__).parent.resolve()


HERE = _resource_root()
DATA_DIR = _user_data_root()
WORK_DIR = DATA_DIR / "work"
WORK_DIR.mkdir(exist_ok=True)
UPLOAD_DIR = WORK_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
UPLOAD_TMP = WORK_DIR / "uploads_tmp"
UPLOAD_TMP.mkdir(exist_ok=True)
THUMB_DIR = WORK_DIR / "thumbs"
THUMB_DIR.mkdir(exist_ok=True)

# session_id -> absolute path of a registered source video
REGISTERED: dict[str, Path] = {}

# job_id -> {"status": "running"|"done"|"error", "message": str, "output": str}
JOBS: dict[str, dict] = {}

# upload_uid -> {"name": str, "tmp": Path, "fh": BinaryIO, "lock": Lock}
UPLOADS: dict[str, dict] = {}
UPLOADS_LOCK = threading.Lock()

DEBUG_LOG = WORK_DIR / "debug.log"
_DEBUG_LOCK = threading.Lock()

SERVER_BUILD = "2026-04-21-freeze-ready"

# Under PyInstaller --noconsole on Windows, every subprocess.Popen / .run
# spawns a visible cmd window unless we pass CREATE_NO_WINDOW. The flag
# only exists on Windows; we AND it in everywhere subprocess is called.
# 0 on other platforms is a no-op creationflags value.
SUBPROCESS_FLAGS = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)


def dlog(msg: str, job_id: str = "-") -> None:
    """Append a timestamped line to work/debug.log AND stderr. Thread-safe.
    sys.stderr can be None under PyInstaller --noconsole; guard the write.
    """
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] [{job_id[:8]}] {msg}"
    with _DEBUG_LOCK:
        try:
            with DEBUG_LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    if sys.stderr is not None:
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


def _find_tool(exe_name: str) -> str | None:
    # macOS / Linux binaries have no .exe suffix; check both names.
    names = [exe_name]
    if exe_name.endswith(".exe"):
        names.append(exe_name[:-4])
    # 1) Bundled next to the frozen executable or the script.
    for base in {HERE, DATA_DIR, Path(sys.executable).parent}:
        for n in names:
            p = base / n
            if p.is_file():
                return str(p)
    # 2) PATH.
    for n in names:
        exe = shutil.which(n)
        if exe:
            return exe
    candidates: list[str] = [
        rf"C:\ffmpeg\bin\{exe_name}",
        rf"C:\Program Files\ffmpeg\bin\{exe_name}",
        rf"C:\Program Files (x86)\ffmpeg\bin\{exe_name}",
        f"/opt/homebrew/bin/{exe_name.removesuffix('.exe')}",
        f"/usr/local/bin/{exe_name.removesuffix('.exe')}",
        f"/usr/bin/{exe_name.removesuffix('.exe')}",
    ]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(str(Path(local) / "Microsoft" / "WinGet" / "Links" / exe_name))
        root = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if root.exists():
            try:
                for p in root.rglob(exe_name):
                    candidates.append(str(p))
            except OSError:
                pass
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def find_ffmpeg() -> str | None:
    return _find_tool("ffmpeg.exe")


def find_ffprobe() -> str | None:
    return _find_tool("ffprobe.exe")


_KEYFRAMES_CACHE: dict[str, list[float]] = {}
_STREAM_PARAMS_CACHE: dict[str, dict] = {}

# Small in-memory + on-disk cache of timeline hover thumbnails, keyed by
# (resolved source path, rounded timestamp). Values are JPEG bytes. Thumbnails
# are tiny (~160px wide) so a few hundred fit comfortably in memory.
_THUMB_MEM_CACHE: dict[tuple, bytes] = {}
_THUMB_MEM_ORDER: list[tuple] = []
_THUMB_MEM_MAX = 400
THUMB_WIDTH = 200  # px; height follows the source aspect ratio


def make_thumbnail(src: Path, t: float) -> bytes | None:
    """Return JPEG bytes of a single frame at ~t seconds, or None on failure.

    Cached in memory and on disk (WORK_DIR/thumbs). Timestamps are rounded to
    0.25s so nearby hovers reuse the same frame instead of re-decoding.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    key = (str(src.resolve()), round(max(0.0, t) * 4) / 4)
    cached = _THUMB_MEM_CACHE.get(key)
    if cached is not None:
        return cached

    # On-disk cache: <sha of path>_<centiseconds>.jpg
    import hashlib
    tag = hashlib.sha1(key[0].encode("utf-8", "replace")).hexdigest()[:16]
    disk = THUMB_DIR / f"{tag}_{int(key[1] * 100):08d}.jpg"
    if disk.is_file():
        try:
            data = disk.read_bytes()
            _thumb_mem_put(key, data)
            return data
        except OSError:
            pass

    try:
        # -ss before -i = fast keyframe seek (accurate enough for a hover
        # preview and far quicker than decoding from 0). scale keeps aspect.
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-ss", f"{key[1]:.3f}", "-i", str(src),
             "-frames:v", "1",
             "-vf", f"scale={THUMB_WIDTH}:-2:flags=fast_bilinear",
             "-f", "image2", "-c:v", "mjpeg", "-q:v", "5", "-"],
            capture_output=True, timeout=15,
            creationflags=SUBPROCESS_FLAGS,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        data = proc.stdout
        _thumb_mem_put(key, data)
        try:
            disk.write_bytes(data)
        except OSError:
            pass
        return data
    except Exception:  # noqa: BLE001
        return None


def _thumb_mem_put(key: tuple, data: bytes) -> None:
    if key in _THUMB_MEM_CACHE:
        return
    _THUMB_MEM_CACHE[key] = data
    _THUMB_MEM_ORDER.append(key)
    while len(_THUMB_MEM_ORDER) > _THUMB_MEM_MAX:
        old = _THUMB_MEM_ORDER.pop(0)
        _THUMB_MEM_CACHE.pop(old, None)


def probe_stream_params(ffprobe: str, src: Path) -> dict:
    """Return a small dict of normalization-relevant params for a source.

    Keys: vcodec, width, height, fps, pix_fmt, sar, acodec, channels, sample_rate.
    Any field we fail to read is None. Cached per resolved path.
    """
    key = str(src.resolve())
    cached = _STREAM_PARAMS_CACHE.get(key)
    if cached is not None:
        return cached
    info: dict = {
        "vcodec": None, "width": None, "height": None, "fps": None,
        "pix_fmt": None, "sar": None,
        "acodec": None, "channels": None, "sample_rate": None,
    }
    if not ffprobe:
        _STREAM_PARAMS_CACHE[key] = info
        return info
    try:
        proc = subprocess.run(
            [ffprobe, "-hide_banner", "-loglevel", "error",
             "-show_streams", "-print_format", "json", str(src)],
            capture_output=True, text=True, timeout=30,
            creationflags=SUBPROCESS_FLAGS,
        )
        data = json.loads(proc.stdout or "{}")
    except Exception:  # noqa: BLE001
        _STREAM_PARAMS_CACHE[key] = info
        return info
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and info["vcodec"] is None:
            info["vcodec"] = st.get("codec_name")
            info["width"] = st.get("width")
            info["height"] = st.get("height")
            info["pix_fmt"] = st.get("pix_fmt")
            info["sar"] = st.get("sample_aspect_ratio") or "1:1"
            # avg_frame_rate looks like "60000/1001" or "30/1".
            afr = st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1"
            try:
                num_s, den_s = afr.split("/")
                num, den = int(num_s), int(den_s)
                info["fps"] = (num / den) if den else None
            except (ValueError, ZeroDivisionError):
                info["fps"] = None
        elif st.get("codec_type") == "audio" and info["acodec"] is None:
            info["acodec"] = st.get("codec_name")
            info["channels"] = st.get("channels")
            try:
                info["sample_rate"] = int(st.get("sample_rate") or 0) or None
            except ValueError:
                info["sample_rate"] = None
    _STREAM_PARAMS_CACHE[key] = info
    return info


def sources_are_heterogeneous(params_list: list[dict]) -> tuple[bool, str]:
    """Return (is_hetero, reason). Compares the first entry to the rest.
    Audio is only compared if present on the first clip (ignored if keep_audio
    is off, caller passes a filtered list).
    """
    if len(params_list) < 2:
        return False, ""
    base = params_list[0]
    # Tolerate tiny fps differences (e.g. 59.94 vs 60): must be within 0.05.
    for i, p in enumerate(params_list[1:], 1):
        for k in ("vcodec", "width", "height", "pix_fmt", "sar"):
            if base.get(k) != p.get(k):
                return True, f"clip {i}: {k} differs ({base.get(k)} vs {p.get(k)})"
        bf, pf = base.get("fps"), p.get("fps")
        if bf and pf and abs(bf - pf) > 0.05:
            return True, f"clip {i}: fps differs ({bf:.3f} vs {pf:.3f})"
    return False, ""


def source_keyframes(ffprobe: str, src: Path) -> list[float]:
    """Return sorted list of keyframe PTS (seconds) for the video stream.
    Cached per source path — ffprobe walks the whole packet index, which
    takes a second or two on long files.
    """
    key = str(src.resolve())
    cached = _KEYFRAMES_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        proc = subprocess.run(
            [ffprobe, "-hide_banner", "-loglevel", "error",
             "-select_streams", "v:0",
             "-skip_frame", "nokey",
             "-show_entries", "frame=pts_time",
             "-of", "csv=print_section=0",
             str(src)],
            capture_output=True, text=True, timeout=120,
            creationflags=SUBPROCESS_FLAGS,
        )
    except Exception:  # noqa: BLE001
        _KEYFRAMES_CACHE[key] = []
        return []
    kfs: list[float] = []
    for line in proc.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            kfs.append(float(line))
        except ValueError:
            pass
    kfs.sort()
    _KEYFRAMES_CACHE[key] = kfs
    return kfs


# Cache: encoder probe results so we don't shell out on every export.
_ENCODER_CACHE: dict[str, bool] = {}


def encoder_works(ffmpeg: str, name: str, args: tuple[str, ...] = ()) -> bool:
    """Does this ffmpeg build actually have a working encoder of that name?
    Probes with a tiny null output — handles cases where the encoder is
    compiled in but no matching hardware is available (e.g. h264_qsv on a
    machine without Intel iGPU). `args` are extra encoder options to probe
    with, for options only some hardware supports (VideoToolbox -q:v)."""
    key = " ".join((name, *args))
    if key in _ENCODER_CACHE:
        return _ENCODER_CACHE[key]
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.1",
             "-c:v", name, *args, "-pix_fmt", "yuv420p", "-f", "null", "-"],
            capture_output=True, timeout=15,
            creationflags=SUBPROCESS_FLAGS,
        )
        ok = proc.returncode == 0
    except Exception:  # noqa: BLE001
        ok = False
    _ENCODER_CACHE[key] = ok
    return ok


# Quality presets → per-encoder rate-control value. Lower number = higher
# quality / bigger file. "high" is near-visually-lossless; "small" trades some
# detail for a much smaller file. Keys must match the frontend's <select>.
# VideoToolbox ("vt") is the exception: its -q:v scale is 1–100, HIGHER = better.
QUALITY_LEVELS = {
    "high":     {"nvenc": 18, "qsv": 18, "amf": 18, "vt": 75, "x264": 18},
    "balanced": {"nvenc": 22, "qsv": 23, "amf": 23, "vt": 62, "x264": 22},
    "small":    {"nvenc": 27, "qsv": 28, "amf": 28, "vt": 50, "x264": 27},
}


def pick_h264_encoder(ffmpeg: str, quality: str = "balanced") -> tuple[str, list[str]]:
    """Return (encoder_name, extra_args) preferring hardware. libx264 is the
    universal fallback. `quality` selects the rate-control target (see
    QUALITY_LEVELS)."""
    q = QUALITY_LEVELS.get(quality, QUALITY_LEVELS["balanced"])
    # NVENC is by far the fastest; try first on machines that have it.
    if encoder_works(ffmpeg, "h264_nvenc"):
        # p1 + tune ll + bf 0 + no-scenecut: fastest NVENC settings.
        # Benchmarked ~565 fps single session at 1440p60 on RTX 4090,
        # up from ~470 fps with default B-frames.
        return "h264_nvenc", [
            "-preset", "p1", "-tune", "ll",
            "-rc", "vbr", "-cq", str(q["nvenc"]), "-b:v", "0",
            "-bf", "0", "-no-scenecut", "1",
        ]
    if encoder_works(ffmpeg, "h264_qsv"):
        return "h264_qsv", ["-preset", "veryfast", "-global_quality", str(q["qsv"])]
    if encoder_works(ffmpeg, "h264_amf"):
        return "h264_amf", ["-quality", "speed", "-rc", "cqp",
                            "-qp_p", str(q["amf"]), "-qp_i", str(q["amf"])]
    if sys.platform == "darwin":
        # Apple VideoToolbox (Media Engine on Apple Silicon). Constant-quality
        # -q:v is only supported on Apple Silicon, so probe with it — Intel
        # Macs fall through to libx264 rather than an unbounded bitrate.
        vt_args = ["-q:v", str(q["vt"]), "-allow_sw", "0", "-realtime", "0"]
        if encoder_works(ffmpeg, "h264_videotoolbox", tuple(vt_args)):
            return "h264_videotoolbox", vt_args
    return "libx264", ["-preset", "ultrafast", "-crf", str(q["x264"])]


def parse_progress_line(line: str, state: dict) -> None:
    """Parse one key=value line from `ffmpeg -progress pipe:1` and update
    the shared state dict with human-readable fields."""
    if "=" not in line:
        return
    k, v = line.strip().split("=", 1)
    if k == "out_time_us":
        try:
            state["out_time_s"] = int(v) / 1_000_000
        except ValueError:
            pass
    elif k == "out_time_ms":
        # Older ffmpeg: value is microseconds despite the name.
        try:
            state["out_time_s"] = int(v) / 1_000_000
        except ValueError:
            pass
    elif k == "frame":
        try:
            state["frame"] = int(v)
        except ValueError:
            pass
    elif k == "fps":
        try:
            state["fps"] = float(v)
        except ValueError:
            pass
    elif k == "speed":
        state["speed"] = v.strip()
    elif k == "progress":
        state["progress_flag"] = v.strip()  # "continue" or "end"


def run_parallel_segments(
    specs: list[dict],
    job_id: str,
    total_output_seconds: float,
    concurrency: int,
    stage_prefix: str,
) -> None:
    """Execute a list of segment-producing ffmpeg commands in parallel.

    Each spec is {"cmd": list[str], "cmd_fallback": list[str]|None,
    "duration": float, "index": int}. Runs up to `concurrency` processes at
    once. Aggregates progress across workers by summing each worker's
    produced seconds plus completed segments' durations.

    Raises on any hard failure (after best-effort fallback). Honors the
    shared cancel flag on JOBS[job_id].
    """
    import queue
    todo: "queue.Queue[dict]" = queue.Queue()
    for s in specs:
        todo.put(s)

    # Shared state: per-worker "current seconds produced" + cumulative done.
    state_lock = threading.Lock()
    worker_progress: dict[int, float] = {}  # worker_id -> seconds produced on current segment
    done_seconds: float = 0.0
    frames_total: int = 0
    fps_latest: float = 0.0
    speed_latest: str = "—"
    errors: list[BaseException] = []
    active_procs: list[subprocess.Popen] = []
    active_procs_lock = threading.Lock()
    # Store previous single-proc slot so cancel path keeps working alongside.
    JOBS[job_id]["procs"] = active_procs

    def _refresh_message():
        with state_lock:
            produced = done_seconds + sum(worker_progress.values())
            pct = 0.0
            if total_output_seconds > 0:
                pct = max(0.0, min(100.0, produced / total_output_seconds * 100))
            JOBS[job_id]["progress"] = pct
            JOBS[job_id]["message"] = (
                f"{stage_prefix}  {pct:5.1f}%  · speed {speed_latest}"
                f"  · {frames_total} frames @ {fps_latest:.0f} fps"
            )

    def _run_one(cmd: list[str], duration: float, worker_id: int) -> tuple[int, str]:
        nonlocal frames_total, fps_latest, speed_latest
        full = [cmd[0], "-hide_banner", "-nostats", "-progress", "pipe:1"] + cmd[1:]
        t_start = time.time()
        dlog(f"  W{worker_id} SPAWN  dur={duration:.2f}s  cmd: {' '.join(full)}", job_id)
        proc = subprocess.Popen(
            full, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=SUBPROCESS_FLAGS,
        )
        with active_procs_lock:
            active_procs.append(proc)
        stderr_tail: list[str] = []

        def drain_stderr():
            for line in proc.stderr:
                stderr_tail.append(line)
                if len(stderr_tail) > 200:
                    del stderr_tail[:100]
        t = threading.Thread(target=drain_stderr, daemon=True)
        t.start()

        local_state: dict = {"out_time_s": 0.0, "frame": 0, "fps": 0.0, "speed": "—"}
        assert proc.stdout is not None
        last_frame_contribution = 0
        for line in proc.stdout:
            if JOBS[job_id].get("cancel"):
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                break
            parse_progress_line(line, local_state)
            # Cap out_time by the segment duration — ffmpeg occasionally
            # reports slightly past-end on the last frame.
            produced = min(local_state["out_time_s"], duration) if duration > 0 else 0.0
            with state_lock:
                worker_progress[worker_id] = produced
                # Frame contribution: delta since last update from this worker.
                delta = local_state["frame"] - last_frame_contribution
                if delta > 0:
                    frames_total += delta
                    last_frame_contribution = local_state["frame"]
                fps_latest = local_state["fps"] or fps_latest
                if local_state["speed"].strip() not in ("", "—", "N/A"):
                    speed_latest = local_state["speed"]
            _refresh_message()

        rc = proc.wait()
        t.join(timeout=1)
        with active_procs_lock:
            try:
                active_procs.remove(proc)
            except ValueError:
                pass
        elapsed = time.time() - t_start
        fps = (local_state["frame"] / elapsed) if elapsed > 0 else 0
        dlog(
            f"  W{worker_id} DONE   rc={rc}  elapsed={elapsed:.2f}s  "
            f"frames={local_state['frame']}  avg_fps={fps:.0f}",
            job_id,
        )
        if rc != 0:
            tail = "".join(stderr_tail)[-800:]
            dlog(f"  W{worker_id} STDERR-TAIL: {tail}", job_id)
        return rc, "".join(stderr_tail)[-4000:]

    def _worker(worker_id: int):
        nonlocal done_seconds
        dlog(f"  worker W{worker_id} started", job_id)
        while True:
            if JOBS[job_id].get("cancel"):
                dlog(f"  worker W{worker_id} exiting (cancel)", job_id)
                return
            try:
                spec = todo.get_nowait()
            except queue.Empty:
                dlog(f"  worker W{worker_id} exiting (queue empty)", job_id)
                return
            try:
                rc, stderr = _run_one(spec["cmd"], spec["duration"], worker_id)
                if rc != 0 and spec.get("cmd_fallback") and not JOBS[job_id].get("cancel"):
                    rc, stderr = _run_one(spec["cmd_fallback"], spec["duration"], worker_id)
                if JOBS[job_id].get("cancel"):
                    return
                if rc != 0:
                    if "No space left on device" in stderr or "error 28" in stderr.lower():
                        raise RuntimeError(
                            f"Disk full while writing segment {spec['index'] + 1}. "
                            f"Free up space and retry."
                        )
                    raise RuntimeError(
                        f"ffmpeg failed on segment {spec['index'] + 1}:\n{stderr or '(no stderr)'}"
                    )
                with state_lock:
                    done_seconds += spec["duration"]
                    worker_progress[worker_id] = 0.0
                _refresh_message()
            except BaseException as e:  # noqa: BLE001
                with state_lock:
                    errors.append(e)
                JOBS[job_id]["cancel"] = True  # stop other workers
                return

    threads = [threading.Thread(target=_worker, args=(i,), daemon=True)
               for i in range(max(1, concurrency))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    JOBS[job_id].pop("procs", None)
    if errors:
        # If cancel came from a real error (not user), re-raise first.
        raise errors[0]


def run_ffmpeg_with_progress(
    cmd: list[str],
    job_id: str,
    total_seconds: float,
    stage_label: str,
) -> int:
    """Run ffmpeg with -progress pipe:1 and stream status back into JOBS[job_id].

    Returns the process exit code. Honors a cancel flag in JOBS[job_id].
    """
    # Insert -progress pipe:1 -nostats so we get machine-readable progress on
    # stdout. Keep stderr for errors. -hide_banner cleans logs.
    full = [
        cmd[0], "-hide_banner", "-nostats",
        "-progress", "pipe:1",
    ] + cmd[1:]

    proc = subprocess.Popen(
        full,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=SUBPROCESS_FLAGS,
    )
    JOBS[job_id]["proc"] = proc

    state: dict = {"out_time_s": 0.0, "frame": 0, "fps": 0.0, "speed": "—"}
    stderr_tail: list[str] = []

    # Consume stderr in a background thread so it can't deadlock the pipe.
    def drain_stderr():
        for line in proc.stderr:
            stderr_tail.append(line)
            if len(stderr_tail) > 200:
                del stderr_tail[:100]
    t = threading.Thread(target=drain_stderr, daemon=True)
    t.start()

    # Read stdout progress lines.
    assert proc.stdout is not None
    for line in proc.stdout:
        if JOBS[job_id].get("cancel"):
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        parse_progress_line(line, state)
        if total_seconds > 0:
            pct = max(0.0, min(100.0, state["out_time_s"] / total_seconds * 100))
        else:
            pct = 0.0
        JOBS[job_id]["progress"] = pct
        JOBS[job_id]["message"] = (
            f"{stage_label}  {pct:5.1f}%  · speed {state['speed']}"
            f"  · {state['frame']} frames @ {state['fps']:.0f} fps"
        )

    rc = proc.wait()
    t.join(timeout=1)
    JOBS[job_id]["proc"] = None
    if rc != 0 and not JOBS[job_id].get("cancel"):
        JOBS[job_id]["_stderr"] = "".join(stderr_tail)[-4000:]
    return rc


def _q(path: Path) -> str:
    """Quote a path for use as an ffmpeg filter argument value (rare, but the
    filtergraph itself never contains paths — this is unused for inputs)."""
    return str(path)


def run_accurate_export(
    job_id: str,
    ffmpeg: str,
    ranges: list[tuple[Path, float, float]],
    output: Path,
    keep_audio: bool,
    source_has_audio: dict[str, bool],
    target_w: int,
    target_h: int,
    target_fps: float,
    total_output_seconds: float,
    quality: str = "balanced",
) -> None:
    """Frame-accurate export in a SINGLE ffmpeg process.

    Every kept range is trimmed and concatenated inside one `filter_complex`,
    re-encoding video (and audio, if kept) exactly once. Because the whole
    output is produced on one continuous timeline by one process, audio and
    video stay perfectly in sync — no per-segment priming/keyframe drift like
    a concat-of-separately-muxed-segments approach suffers from.

    The video of every range is normalized (scale→pad→fps→setsar→yuv420p) to a
    common WxH/fps so clips of different formats concat cleanly. Audio is
    resampled to 48 kHz stereo; ranges from sources with no audio get generated
    silence so the audio track stays aligned with the video.
    """
    enc_name, enc_args = pick_h264_encoder(ffmpeg, quality)
    JOBS[job_id]["encoder"] = enc_name
    dlog(f"ACCURATE(single-pass)  encoder={enc_name}  quality={quality}  keep_audio={keep_audio}  "
         f"ranges={len(ranges)}  target={target_w}x{target_h}@{target_fps:.3f}", job_id)

    # Deduplicate sources into input slots; reuse a source across ranges.
    input_paths: list[Path] = []
    input_index: dict[str, int] = {}
    for src, _, _ in ranges:
        k = str(src.resolve())
        if k not in input_index:
            input_index[k] = len(input_paths)
            input_paths.append(src)

    cpu_count = os.cpu_count() or 8
    decode_threads = min(16, max(4, cpu_count))

    cmd: list[str] = [ffmpeg, "-y", "-threads", str(decode_threads)]
    for p in input_paths:
        cmd += ["-i", str(p)]
    # An extra lavfi input providing silence for ranges whose source has no
    # audio. Only added when we're keeping audio AND at least one source lacks it.
    need_silence = keep_audio and any(
        not source_has_audio.get(str(p.resolve()), False) for p in input_paths
    )
    silence_idx = None
    if need_silence:
        silence_idx = len(input_paths)
        cmd += ["-f", "lavfi", "-t", f"{total_output_seconds + 1:.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

    vf_norm = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,"
        f"setsar=1,fps={target_fps:.6f},format=yuv420p"
    )

    parts: list[str] = []   # filtergraph statements
    concat_ins: list[str] = []
    for i, (src, s, e) in enumerate(ranges):
        idx = input_index[str(src.resolve())]
        vlab = f"v{i}"
        parts.append(
            f"[{idx}:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS,{vf_norm}[{vlab}]"
        )
        if keep_audio:
            alab = f"a{i}"
            if source_has_audio.get(str(src.resolve()), False):
                parts.append(
                    f"[{idx}:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS,"
                    f"aresample=48000:async=1,aformat=sample_fmts=fltp:channel_layouts=stereo[{alab}]"
                )
            else:
                # Take silence of exactly this range's length.
                dur = e - s
                parts.append(
                    f"[{silence_idx}:a]atrim=start=0:end={dur:.3f},asetpts=PTS-STARTPTS,"
                    f"aresample=48000:async=1,aformat=sample_fmts=fltp:channel_layouts=stereo[{alab}]"
                )
            concat_ins.append(f"[{vlab}][{alab}]")
        else:
            concat_ins.append(f"[{vlab}]")

    n = len(ranges)
    if keep_audio:
        parts.append("".join(concat_ins) + f"concat=n={n}:v=1:a=1[vout][aout]")
    else:
        parts.append("".join(concat_ins) + f"concat=n={n}:v=1:a=0[vout]")

    filtergraph = ";".join(parts)
    cmd += ["-filter_complex", filtergraph, "-map", "[vout]"]
    if keep_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    # Force a regular keyframe interval (~1 keyframe every 2s) so players can
    # seek quickly. Without this, encoders (esp. NVENC with -no-scenecut) place
    # keyframes many seconds apart, and seeking in VLC has to decode a long run
    # from the previous keyframe — causing multi-second stalls when you click.
    gop = max(1, round(target_fps * 2))
    cmd += ["-g", str(gop), "-keyint_min", str(gop)]
    if enc_name == "libx264":
        # Pin the min interval and disable scenecut jitter so the GOP is steady.
        cmd += ["-x264-params", f"keyint={gop}:min-keyint={gop}:scenecut=0"]
    elif enc_name == "h264_nvenc":
        # We set -no-scenecut in enc_args; -g/-keyint_min above give a fixed GOP.
        cmd += ["-forced-idr", "1"]
    elif enc_name == "h264_videotoolbox":
        # VideoToolbox ignores -keyint_min; cap the keyframe distance in time too.
        cmd += ["-max_key_interval_duration", "2"]
    cmd += ["-c:v", enc_name, *enc_args, "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(output)]

    dlog(f"  filtergraph statements={len(parts)}  inputs={len(input_paths)}"
         f"{' +silence' if need_silence else ''}", job_id)
    # The filtergraph can be long; log a trimmed sample for diagnosis.
    dlog(f"  fg[:400]={filtergraph[:400]}", job_id)

    rc = run_ffmpeg_with_progress(
        cmd, job_id, total_output_seconds,
        f"Encoding ({enc_name}, single-pass, in sync)",
    )
    if JOBS[job_id].get("cancel"):
        raise RuntimeError("Cancelled by user.")
    if rc != 0:
        stderr = JOBS[job_id].get("_stderr", "")
        if "No space left on device" in stderr or "error 28" in stderr.lower():
            raise RuntimeError(
                f"Disk full while writing output to {output.parent}. "
                f"Free up space and retry."
            )
        raise RuntimeError(f"Accurate export failed:\n{stderr}")


def run_export(job_id: str, payload: dict) -> None:
    """Build the output mp4.

    Fast path: stream-copy each kept range into a segment, then concat-copy.
    No re-encoding — near-instant, bit-identical video. Cuts snap to the
    nearest prior keyframe, so a cut may include up to ~2s before your
    requested in-point. Fine for trimming long captures.

    Accurate path (payload['accurate'] == True): one ffmpeg process with a
    filter_complex trim+concat, re-encoding once with the best available
    H.264 encoder (NVENC / QSV / AMF / VideoToolbox / libx264). Frame-accurate, slower.
    """
    try:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg not found. Install it (e.g. `winget install Gyan.FFmpeg`) "
                "and restart this app."
            )

        clips = payload["clips"]
        output = Path(payload["output"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        accurate = bool(payload.get("accurate", False))
        keep_audio = bool(payload.get("keep_audio", False))
        quality = str(payload.get("quality", "balanced")).lower()
        if quality not in QUALITY_LEVELS:
            quality = "balanced"
        # Resolution override: "source" (default), or a target height like
        # "1080" / "720". Width is derived to preserve aspect (even number).
        resolution = str(payload.get("resolution", "source")).lower()

        dlog("=" * 60, job_id)
        dlog(f"EXPORT START  accurate={accurate}  keep_audio={keep_audio}  "
             f"quality={quality}  resolution={resolution}", job_id)
        dlog(f"  ffmpeg = {ffmpeg}", job_id)
        dlog(f"  output = {output}", job_id)
        dlog(f"  clips = {len(clips)}  ranges will be flattened next", job_id)

        tmp = WORK_DIR / f"job_{job_id}"
        tmp.mkdir(exist_ok=True)

        # Flatten kept ranges in output order.
        ranges: list[tuple[Path, float, float]] = []
        for clip in clips:
            src = Path(clip["path"]).resolve()
            if not src.is_file():
                raise RuntimeError(f"Missing source: {src}")
            for start, end in clip["keep"]:
                if end > start:
                    ranges.append((src, float(start), float(end)))
        if not ranges:
            raise RuntimeError("Nothing to export — no kept ranges.")

        total_output_seconds = sum(e - s for _, s, e in ranges)
        dlog(f"  {len(ranges)} ranges totaling {total_output_seconds:.1f}s", job_id)
        for i, (src, s, e) in enumerate(ranges[:5]):
            dlog(f"    [{i}] {src.name}  {s:.2f}→{e:.2f}  ({e - s:.2f}s)", job_id)
        if len(ranges) > 5:
            dlog(f"    ... {len(ranges) - 5} more ranges", job_id)

        # Probe each unique source so we can detect heterogeneous timelines.
        # If clips differ in codec / resolution / fps / pix_fmt / SAR, a final
        # `-c copy` concat will either fail or produce a broken file. In that
        # case we force a normalizing re-encode pass.
        ffprobe = find_ffprobe()
        unique_srcs: list[Path] = []
        seen: set[str] = set()
        for src, _, _ in ranges:
            k = str(src.resolve())
            if k not in seen:
                seen.add(k)
                unique_srcs.append(src)
        source_params = [probe_stream_params(ffprobe, s) for s in unique_srcs]
        # Only compare audio params when the user is keeping audio.
        heterogeneous, hetero_reason = sources_are_heterogeneous(source_params)
        if heterogeneous:
            dlog(f"  HETEROGENEOUS sources detected: {hetero_reason}", job_id)
        else:
            dlog(f"  sources homogeneous across {len(unique_srcs)} unique file(s)", job_id)
        # Canonical target for normalization: largest WxH among sources, fps
        # from the first source, yuv420p pix_fmt, 1:1 SAR.
        target_w = max((p["width"] for p in source_params if p["width"]), default=1920)
        target_h = max((p["height"] for p in source_params if p["height"]), default=1080)
        target_fps = next((p["fps"] for p in source_params if p["fps"]), 30.0) or 30.0

        # Apply a resolution downscale if requested. We only ever scale DOWN
        # (upscaling just wastes bitrate); keep aspect ratio and even dims.
        if resolution not in ("source", "", "none"):
            try:
                want_h = int(resolution)
            except ValueError:
                want_h = 0
            if want_h and target_h and want_h < target_h:
                scale = want_h / target_h
                new_w = int(round(target_w * scale)) & ~1  # force even
                new_h = want_h & ~1
                dlog(f"  resolution override: {target_w}x{target_h} -> {new_w}x{new_h}",
                     job_id)
                target_w, target_h = max(2, new_w), max(2, new_h)
                # Rescaling requires the filter pass; fast copy can't do it.
                accurate = True

        # Pre-flight free-space check. Temp segments go to WORK_DIR, final
        # to output.parent — possibly different drives. Each range's size
        # is bounded by min(source_size, ~500 MB/min for 1440p60 h264);
        # 1.5× that + 512 MB of slack covers copy (≈source bitrate) and
        # accurate NVENC (usually smaller) without being wildly pessimistic.
        estimated = 0
        for src, s, e in ranges:
            try:
                sz = src.stat().st_size
            except OSError:
                continue
            bitrate_cap = int((e - s) * 500 * 1024 * 1024 / 60)
            estimated += min(sz, bitrate_cap)
        required = int(estimated * 1.5) + 512 * 1024 * 1024
        try:
            work_free = shutil.disk_usage(str(WORK_DIR)).free
            out_free = shutil.disk_usage(str(output.parent)).free
        except OSError:
            work_free = out_free = required
        if work_free < required or out_free < required:
            def _fmt_gb(n: int) -> str:
                return f"{n / (1024 ** 3):.1f} GB"
            tight = WORK_DIR if work_free < out_free else output.parent
            raise RuntimeError(
                f"Not enough free disk space to export. Estimated need: "
                f"~{_fmt_gb(required)}, but only {_fmt_gb(min(work_free, out_free))} "
                f"free on the drive holding {tight}. Free up space (or change "
                f"the output folder to another drive) and retry."
            )

        # Fast mode (stream-copy) can't merge clips with different codecs or
        # video params — the final `-c copy` concat would fail or produce a
        # broken file. Auto-upgrade to accurate (which re-encodes + normalizes).
        if not accurate and heterogeneous:
            dlog(
                f"  auto-upgrade Fast→Accurate: {hetero_reason}",
                job_id,
            )
            JOBS[job_id]["message"] = (
                f"Clips differ in format ({hetero_reason}) — "
                f"switching to Accurate mode to normalize."
            )
            accurate = True

        if accurate:
            # Frame-accurate, perfectly in-sync: one filter_complex pass.
            # (The old smart-split/concat-copy scheme drifted audio out of
            #  sync; a single encode on one timeline cannot desync.)
            source_has_audio = {
                str(s.resolve()): bool(p.get("acodec"))
                for s, p in zip(unique_srcs, source_params)
            }
            run_accurate_export(
                job_id, ffmpeg, ranges, output, keep_audio,
                source_has_audio, target_w, target_h, target_fps,
                total_output_seconds, quality,
            )
        else:
            # ---------- FAST: stream-copy segments + concat-copy ----------
            # Per segment: -ss + -to with -c copy is near-instant. Use input
            # seeking (-ss before -i) for fast jump to the preceding keyframe.
            parts: list[Path] = []
            n = len(ranges)
            # For progress, fake a duration proportional to the final output.
            # Each segment's "progress" is its own kept span.
            cumulative = 0.0
            for i, (src, s, e) in enumerate(ranges):
                if JOBS[job_id].get("cancel"):
                    raise RuntimeError("Cancelled by user.")
                part = tmp / f"part_{i:04d}.mp4"
                seg_dur = e - s
                cmd = [
                    ffmpeg, "-y",
                    "-ss", f"{s:.3f}",
                    "-to", f"{e:.3f}",
                    "-i", str(src),
                ]
                if keep_audio:
                    cmd += ["-c", "copy"]
                else:
                    cmd += ["-an", "-c:v", "copy"]
                cmd += [
                    "-avoid_negative_ts", "make_zero",
                    "-movflags", "+faststart",
                    str(part),
                ]
                # Progress scaled per-segment, then folded into overall.
                stage = f"Copy segment {i + 1}/{n}"
                # We can't easily get cumulative progress from the copy path
                # through -progress alone, so reuse run_ffmpeg_with_progress
                # with this segment's own duration, then add to cumulative
                # after completion.
                rc = run_ffmpeg_with_progress(cmd, job_id, seg_dur, stage)
                if JOBS[job_id].get("cancel"):
                    raise RuntimeError("Cancelled by user.")
                if rc != 0:
                    stderr = JOBS[job_id].get("_stderr", "")
                    if "No space left on device" in stderr or "error 28" in stderr.lower():
                        raise RuntimeError(
                            f"Disk full while writing segment {i + 1} to {tmp}. "
                            f"Free up space on that drive (or move this editor "
                            f"to a drive with more room) and retry."
                        )
                    raise RuntimeError(
                        f"Stream-copy failed on {src.name} [{s:.2f}→{e:.2f}]. "
                        f"This can happen if the range starts at a non-keyframe "
                        f"boundary and the source can't be safely copied there. "
                        f"Retry with Accurate mode.\n{stderr}"
                    )
                parts.append(part)
                cumulative += seg_dur
                # Overall progress update.
                if total_output_seconds > 0:
                    JOBS[job_id]["progress"] = cumulative / total_output_seconds * 100

            # Concat all parts — copy, no re-encode.
            concat_file = tmp / "concat.txt"
            with concat_file.open("w", encoding="utf-8") as f:
                for p in parts:
                    safe = str(p).replace("\\", "/").replace("'", r"\'")
                    f.write(f"file '{safe}'\n")
            cmd = [
                ffmpeg, "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                "-movflags", "+faststart",
                str(output),
            ]
            rc = run_ffmpeg_with_progress(
                cmd, job_id, total_output_seconds, "Joining segments"
            )
            if JOBS[job_id].get("cancel"):
                raise RuntimeError("Cancelled by user.")
            if rc != 0:
                stderr = JOBS[job_id].get("_stderr", "")
                if "No space left on device" in stderr or "error 28" in stderr.lower():
                    raise RuntimeError(
                        f"Disk full while writing final output to {output.parent}. "
                        f"Free up space and retry."
                    )
                raise RuntimeError(f"Concat failed:\n{stderr}")

            # Cleanup temp parts.
            for p in parts:
                try: p.unlink()
                except OSError: pass
            try:
                concat_file.unlink()
                tmp.rmdir()
            except OSError:
                pass

        JOBS[job_id].update(
            status="done",
            message=f"Done — {output.name}",
            progress=100.0,
            output=str(output),
        )
        dlog(f"EXPORT DONE  output={output}", job_id)
    except Exception as e:  # noqa: BLE001
        JOBS[job_id].update(status="error", message=str(e))
        dlog(f"EXPORT ERROR: {e}", job_id)
        # Best-effort cleanup on failure.
        try:
            tmp = WORK_DIR / f"job_{job_id}"
            if tmp.is_dir():
                for p in tmp.iterdir():
                    try: p.unlink()
                    except OSError: pass
                try: tmp.rmdir()
                except OSError: pass
        except Exception:  # noqa: BLE001
            pass


def safe_basename(name: str) -> str:
    name = os.path.basename(name or "")
    # Replace characters that are illegal on Windows, keep unicode.
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "upload.mp4"
    return name


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 — no keep-alive, no pipelining. That dodges a 3.11 stdlib bug
    # where a partial write during a 206 range response can bleed leftover
    # bytes into the next pipelined request (causing `501 Unsupported method
    # '{}GET'` responses). Each request gets a fresh TCP connection.
    protocol_version = "HTTP/1.0"

    def log_message(self, format, *args):  # noqa: A002
        # stderr is None under PyInstaller --noconsole — silently drop.
        if sys.stderr is None:
            return
        try:
            sys.stderr.write(
                f"[{self.log_date_time_string()}] {format % args}\n"
            )
        except (OSError, ValueError):
            pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def handle_expect_100(self):
        # Always accept the body — our endpoints decide validity afterward.
        # Explicit override in case some client lib still sends this header
        # despite HTTP/1.0 on our side.
        try:
            self.send_response_only(100)
            self.end_headers()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return False
        return True

    # ---------- helpers ----------
    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    # ---------- routes ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/index.html":
            return self._serve_file(HERE / "index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._serve_file(HERE / "app.js", "application/javascript; charset=utf-8")
        if path == "/style.css":
            return self._serve_file(HERE / "style.css", "text/css; charset=utf-8")
        if path == "/favicon.png":
            return self._serve_file(HERE / "favicon.png", "image/png")
        if path == "/api/ffmpeg-status":
            return self._send_json({"found": bool(find_ffmpeg())})
        if path == "/api/version":
            return self._send_json({
                "build": SERVER_BUILD,
                "ffmpeg": find_ffmpeg(),
                "ffprobe": find_ffprobe(),
                "cpu_count": os.cpu_count(),
                "debug_log": str(DEBUG_LOG),
            })
        if path == "/api/check-paths":
            # Reports which registered ids still point at real files on disk.
            # Called periodically by the frontend to flag clips the user
            # deleted or moved.
            report = {
                sid: {"exists": src.is_file(), "path": str(src)}
                for sid, src in list(REGISTERED.items())
            }
            return self._send_json(report)
        if path == "/api/default-output-dir":
            home = Path.home()
            for candidate in (home / "Desktop", home / "OneDrive" / "Desktop", home):
                if candidate.is_dir():
                    return self._send_json({"folder": str(candidate)})
            return self._send_json({"folder": str(home)})
        if path.startswith("/video/"):
            sid = path[len("/video/"):]
            src = REGISTERED.get(sid)
            if not src or not src.is_file():
                self.send_error(404)
                return
            return self._serve_video(src)
        if path == "/api/thumb":
            q = self._query()
            sid = (q.get("id") or [""])[0]
            try:
                t = float((q.get("t") or ["0"])[0])
            except ValueError:
                t = 0.0
            src = REGISTERED.get(sid)
            if not src or not src.is_file():
                self.send_error(404)
                return
            data = make_thumbnail(src, t)
            if not data:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            # Thumbnails for a given (id,t) never change during a session.
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/api/job/"):
            jid = path[len("/api/job/"):]
            job = JOBS.get(jid)
            if not job:
                return self._send_json({"error": "unknown job"}, 404)
            # Strip non-serializable fields (Popen handle, stderr buffer).
            safe = {k: v for k, v in job.items() if k not in ("proc", "_stderr")}
            return self._send_json(safe)
        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/register":
                body = self._read_json()
                p = Path(body.get("path", "")).resolve()
                if not p.is_file():
                    return self._send_json({"error": f"Not a file: {p}"}, 400)
                sid = uuid.uuid4().hex
                REGISTERED[sid] = p
                return self._send_json({"id": sid, "name": p.name, "path": str(p)})
            if path == "/api/upload/begin":
                body = self._read_json()
                name = safe_basename(body.get("name", "upload.mp4"))
                uid = uuid.uuid4().hex
                tmp = UPLOAD_TMP / f"{uid}.part"
                # Open in binary append so subsequent chunks can be written.
                fh = tmp.open("wb")
                with UPLOADS_LOCK:
                    UPLOADS[uid] = {
                        "name": name,
                        "tmp": tmp,
                        "fh": fh,
                        "lock": threading.Lock(),
                        "next_seq": 0,
                    }
                return self._send_json({"uid": uid})
            if path == "/api/upload/chunk":
                q = self._query()
                uid = (q.get("uid") or [""])[0]
                seq = int((q.get("seq") or ["0"])[0])
                with UPLOADS_LOCK:
                    up = UPLOADS.get(uid)
                if not up:
                    return self._send_json({"error": "unknown upload"}, 404)
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    return self._send_json({"error": "empty chunk"}, 400)
                # Read the whole chunk, then write under the session lock.
                data = b""
                remaining = length
                chunk_sz = 512 * 1024
                while remaining > 0:
                    buf = self.rfile.read(min(chunk_sz, remaining))
                    if not buf:
                        break
                    data += buf
                    remaining -= len(buf)
                if len(data) != length:
                    return self._send_json({"error": "short read"}, 400)
                with up["lock"]:
                    if seq != up["next_seq"]:
                        return self._send_json(
                            {"error": f"out-of-order chunk: got {seq}, expected {up['next_seq']}"},
                            409,
                        )
                    up["fh"].write(data)
                    up["next_seq"] = seq + 1
                return self._send_json({"ok": True, "received": len(data)})
            if path == "/api/upload/finish":
                q = self._query()
                uid = (q.get("uid") or [""])[0]
                with UPLOADS_LOCK:
                    up = UPLOADS.pop(uid, None)
                if not up:
                    return self._send_json({"error": "unknown upload"}, 404)
                with up["lock"]:
                    try:
                        up["fh"].close()
                    except OSError:
                        pass
                tmp: Path = up["tmp"]
                name: str = up["name"]
                final = UPLOAD_DIR / f"{uid[:8]}_{name}"
                try:
                    tmp.replace(final)
                except OSError as e:
                    return self._send_json({"error": f"finalize failed: {e}"}, 500)
                sid = uuid.uuid4().hex
                REGISTERED[sid] = final.resolve()
                return self._send_json(
                    {"id": sid, "name": name, "path": str(final.resolve())}
                )
            if path == "/api/upload/abort":
                q = self._query()
                uid = (q.get("uid") or [""])[0]
                with UPLOADS_LOCK:
                    up = UPLOADS.pop(uid, None)
                if not up:
                    return self._send_json({"ok": True})
                try:
                    up["fh"].close()
                except OSError:
                    pass
                try:
                    up["tmp"].unlink()
                except OSError:
                    pass
                return self._send_json({"ok": True})
            if path == "/api/export":
                body = self._read_json()
                jid = uuid.uuid4().hex
                JOBS[jid] = {"status": "running", "message": "Starting...", "progress": 0.0}
                t = threading.Thread(
                    target=run_export, args=(jid, body), daemon=True
                )
                t.start()
                return self._send_json({"id": jid})
            if path.startswith("/api/job/") and path.endswith("/cancel"):
                jid = path[len("/api/job/"):-len("/cancel")]
                job = JOBS.get(jid)
                if not job:
                    return self._send_json({"error": "unknown job"}, 404)
                job["cancel"] = True
                proc = job.get("proc")
                if proc is not None:
                    try:
                        proc.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                for p in list(job.get("procs") or []):
                    try:
                        p.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                return self._send_json({"ok": True})
            if path == "/api/pick-files":
                if sys.platform == "darwin":
                    # AppleScript multi-select file picker. Returns POSIX paths,
                    # one per line.
                    script = (
                        'set chosen to choose file with prompt "Choose video files" '
                        'with multiple selections allowed of type '
                        '{"public.movie", "mp4", "mov", "m4v", "mkv", "webm", "avi"}\n'
                        'set out to ""\n'
                        'repeat with f in chosen\n'
                        '    set out to out & POSIX path of f & linefeed\n'
                        'end repeat\n'
                        'return out'
                    )
                    try:
                        proc = subprocess.run(
                            ["osascript", "-e", script],
                            capture_output=True, text=True, timeout=600,
                            creationflags=SUBPROCESS_FLAGS,
                        )
                        files = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
                        return self._send_json({"files": files})
                    except Exception as e:  # noqa: BLE001
                        return self._send_json({"error": str(e)}, 500)
                # Native Windows multi-select file picker. OpenFileDialog
                # under -STA can deadlock without a proper owner window and
                # message pump, so we give it a hidden topmost form as owner
                # and route output through a temp file (stdout capture under
                # -STA sometimes also stalls the dialog).
                import tempfile
                tmpf = Path(tempfile.gettempdir()) / f"pick_{uuid.uuid4().hex}.txt"
                # Owner form must be a real, on-screen, topmost window for the
                # dialog to inherit z-order/focus — off-screen owners leave the
                # dialog behind the browser on some Windows builds. We make it
                # 1×1, almost transparent, and call SetForegroundWindow on its
                # handle before spawning the dialog.
                ps = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "Add-Type -AssemblyName System.Drawing; "
                    "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; "
                    "public class W { [DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h); "
                    "[DllImport(\"user32.dll\")] public static extern bool BringWindowToTop(IntPtr h); }'; "
                    "$owner = New-Object System.Windows.Forms.Form; "
                    "$owner.FormBorderStyle = 'None'; "
                    "$owner.TopMost = $true; "
                    "$owner.ShowInTaskbar = $false; "
                    "$owner.Opacity = 0.01; "
                    "$owner.Size = New-Object System.Drawing.Size 1,1; "
                    "$owner.StartPosition = 'CenterScreen'; "
                    "$owner.Show(); "
                    "[W]::BringWindowToTop($owner.Handle) | Out-Null; "
                    "[W]::SetForegroundWindow($owner.Handle) | Out-Null; "
                    "[System.Windows.Forms.Application]::DoEvents(); "
                    "$f = New-Object System.Windows.Forms.OpenFileDialog; "
                    "$f.Title = 'Choose video files'; "
                    "$f.Multiselect = $true; "
                    "$f.Filter = 'Video files|*.mp4;*.mov;*.mkv;*.avi;*.webm;*.m4v|All files|*.*'; "
                    f"$result = $f.ShowDialog($owner); "
                    f"if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{ "
                    f"$f.FileNames | Set-Content -Path '{tmpf}' -Encoding UTF8 }} "
                    f"$owner.Close()"
                )
                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-STA", "-WindowStyle", "Hidden",
                         "-Command", ps],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=600,
                        creationflags=SUBPROCESS_FLAGS,
                    )
                    files: list[str] = []
                    if tmpf.is_file():
                        try:
                            raw = tmpf.read_text(encoding="utf-8-sig")
                            files = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                        finally:
                            try: tmpf.unlink()
                            except OSError: pass
                    return self._send_json({"files": files})
                except Exception as e:  # noqa: BLE001
                    try: tmpf.unlink()
                    except OSError: pass
                    return self._send_json({"error": str(e)}, 500)
            if path == "/api/pick-folder":
                if sys.platform == "darwin":
                    script = (
                        'set chosen to choose folder with prompt "Choose output folder"\n'
                        'return POSIX path of chosen'
                    )
                    try:
                        proc = subprocess.run(
                            ["osascript", "-e", script],
                            capture_output=True, text=True, timeout=300,
                            creationflags=SUBPROCESS_FLAGS,
                        )
                        folder = proc.stdout.strip()
                        return self._send_json({"folder": folder})
                    except Exception as e:  # noqa: BLE001
                        return self._send_json({"error": str(e)}, 500)
                # Open a native Windows folder picker (blocks this request
                # until the user chooses or cancels). Powered by PowerShell's
                # FolderBrowserDialog — no deps.
                ps = (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                    "$f.Description = 'Choose output folder'; "
                    "if ($f.ShowDialog() -eq 'OK') { [Console]::Out.WriteLine($f.SelectedPath) }"
                )
                try:
                    proc = subprocess.run(
                        ["powershell", "-NoProfile", "-STA", "-Command", ps],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=300,
                        creationflags=SUBPROCESS_FLAGS,
                    )
                    chosen = proc.stdout.strip().splitlines()
                    folder = chosen[-1] if chosen else ""
                    return self._send_json({"folder": folder})
                except Exception as e:  # noqa: BLE001
                    return self._send_json({"error": str(e)}, 500)
            if path == "/api/reveal":
                # Open the OS file browser with the given file selected.
                body = self._read_json()
                target = Path(body.get("path", "")).resolve()
                if not target.exists():
                    return self._send_json({"error": f"Not found: {target}"}, 404)
                try:
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", "-R", str(target)],
                                         creationflags=SUBPROCESS_FLAGS)
                    elif os.name == "nt":
                        # /select, highlights the file inside its folder.
                        subprocess.Popen(f'explorer /select,"{target}"',
                                         creationflags=SUBPROCESS_FLAGS)
                    else:
                        subprocess.Popen(["xdg-open", str(target.parent)],
                                         creationflags=SUBPROCESS_FLAGS)
                    return self._send_json({"ok": True})
                except Exception as e:  # noqa: BLE001
                    return self._send_json({"error": str(e)}, 500)
            self.send_error(404)
        except Exception as e:  # noqa: BLE001
            try:
                self._send_json({"error": str(e)}, 500)
            except Exception:
                pass

    # ---------- file serving ----------
    def _serve_file(self, path: Path, content_type: str):
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_video(self, path: Path):
        size = path.stat().st_size
        ctype, _ = mimetypes.guess_type(str(path))
        ctype = ctype or "video/mp4"
        range_header = self.headers.get("Range")
        try:
            if range_header:
                m = re.match(r"bytes=(\d*)-(\d*)", range_header)
                if not m:
                    self.send_error(416)
                    return
                start_s, end_s = m.group(1), m.group(2)
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else size - 1
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                with path.open("rb") as f:
                    f.seek(start)
                    remaining = length
                    chunk = 1024 * 256
                    while remaining > 0:
                        buf = f.read(min(chunk, remaining))
                        if not buf:
                            break
                        self.wfile.write(buf)
                        remaining -= len(buf)
            else:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                with path.open("rb") as f:
                    shutil.copyfileobj(f, self.wfile)
        except (BrokenPipeError, ConnectionResetError):
            return


def _safe_print(*args, **kwargs):
    # Under PyInstaller --noconsole, sys.stdout is None and print() would
    # raise. Just drop the message in that case.
    if sys.stdout is None:
        return
    try:
        print(*args, **kwargs)
    except (OSError, ValueError):
        pass


def start_server(port: int = 8765):
    """Create and bind the HTTP server. Returns (server, url).

    Does not call serve_forever — the caller decides whether to block
    (CLI/browser mode) or run it in a background thread (desktop window mode).
    If `port` is busy, tries the next few ports so a stale instance doesn't
    block launch.
    """
    last_err = None
    for p in range(port, port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            url = f"http://127.0.0.1:{p}/"
            _safe_print(f"Simple video editor running at {url}  (build {SERVER_BUILD})")
            _safe_print(f"Debug log: {DEBUG_LOG}")
            dlog(f"SERVER START  build={SERVER_BUILD}  ffmpeg={find_ffmpeg()}  ffprobe={find_ffprobe()}")
            dlog(f"  cpu_count={os.cpu_count()}  WORK_DIR={WORK_DIR}")
            try:
                free = shutil.disk_usage(str(WORK_DIR)).free
                dlog(f"  work drive free: {free / (1024 ** 3):.1f} GB")
            except OSError:
                pass
            return server, url
        except OSError as e:
            last_err = e
            continue
    raise SystemExit(f"Could not bind a local port ({port}-{port+9}): {last_err}")


def main(open_browser: bool = True):
    server, url = start_server()
    _safe_print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _safe_print("\nShutting down.")


if __name__ == "__main__":
    main()
