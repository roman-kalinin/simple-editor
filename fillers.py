"""Filler-word detection with faster-whisper + an acoustic hesitation detector.

The UI calls /api/fillers, which runs detect_fillers() in a background job.
It returns the full word-level transcript plus a list of filler items, each
with a cut range. The front end adds those as ordinary cuts and shows the
transcript in a side panel where every filler can be reviewed and toggled.

Two detectors, because Whisper alone misses most hesitations:

1. Lexical — words Whisper did write down that are pure hesitations
   ("um", "uh", "э", "ммм", ...), in English / Russian / Ukrainian.
2. Acoustic — Whisper is trained on cleaned-up transcripts, so it usually
   drops "eeee" / "mmm" and silently stretches the neighbouring word's
   timestamps over it. A filled pause is a *held*, steady voiced sound
   (spectrum barely changes for 300 ms+), unlike normal speech whose spectrum
   changes every ~50-100 ms. We look for those steady stretches. Language
   independent. These are marked kind="sound" so the user can review them.

faster-whisper is optional: if it isn't installed, the endpoint reports a clear
error and the rest of the editor is unaffected.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import wave
from pathlib import Path
from typing import Callable

import numpy as np

# Pure hesitations only. Real words ("like", "там", "ну", "типа") are left
# alone — removing them automatically breaks sentences.
FILLER_WORDS = {
    # English
    "um", "umm", "uh", "uhh", "uhm", "er", "erm", "err", "ah", "ahh", "eh",
    "hmm", "hm", "mm", "mmm", "mhm",
    # Russian / Ukrainian (Cyrillic)
    "э", "ээ", "эээ", "эм", "эмм", "е", "ее", "еее", "ем", "емм",
    "м", "мм", "ммм", "хм", "хмм", "гм", "аа", "ааа", "ам", "ыы",
}
_STRETCHED = re.compile(
    r"(u+h+|u+m+|h+m+|m{2,}|e+r+m*|a+h+|э+м*|е{2,}м*|е+м+|м{2,}|х+м+|г+м+|а{2,}|ы{2,})"
)

PROMPTS = {
    "en": "Umm, so, uh, I was thinking, like, hmm... Er, yeah. Uh, okay, so um, "
          "we, uh, we did the thing. Mm, erm, right.",
    "ru": "Ээ, ну, я, ммм, хотел сказать... Эм, ну, то есть, э, да. Хм, ну вот.",
    "uk": "Ее, ну, я, ммм, хотів сказати... Ем, ну, тобто, е, так. Хм, ну от.",
}

# Lexical fillers: padding around the word, clamped so we never eat into
# neighbouring words.
PAD_BEFORE = 0.04
PAD_AFTER = 0.06
WORD_GUARD = 0.02

# Acoustic detector tuning.
SR = 16000
HOP = 160            # 10 ms frames
MIN_HOLD = 0.30      # a steady sound this long always counts
SHORT_HOLD = 0.15    # ...this long counts when it sits next to a pause
PAUSE_REACH = 0.14   # how far from the sound we look for that pause
MIN_PAUSE = 0.04     # shortest silence that counts as a pause
STABILITY = 1.17     # threshold = this × 10th percentile of voiced spectral change
EDGE_KEEP = 0.05     # keep this much of the held sound at each side (natural joins)

_model = None
_model_desc = ""
_model_lock = threading.Lock()


class Cancelled(Exception):
    pass


def available() -> tuple[bool, str]:
    try:
        import faster_whisper  # noqa: F401
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, (
            "faster-whisper is not installed. Run `pip install faster-whisper` "
            f"and restart the app. ({e})"
        )


def _cuda_ok() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def _load_model():
    """Load (once) the best model for this machine.

    GPU: large-v3 in float16. CPU: small in int8 (large is far too slow).
    """
    global _model, _model_desc
    with _model_lock:
        if _model is not None:
            return _model, _model_desc
        from faster_whisper import WhisperModel
        attempts = []
        # SIMPLEEDITOR_WHISPER_DEVICE=cpu forces the CPU path (what Macs run;
        # CTranslate2 has no Metal backend) — handy for testing on a GPU box.
        force_cpu = os.environ.get("SIMPLEEDITOR_WHISPER_DEVICE", "").lower() == "cpu"
        if _cuda_ok() and not force_cpu:
            attempts.append(("large-v3", "cuda", "float16"))
        attempts.append(("small", "cpu", "int8"))
        last_err = None
        for size, device, ctype in attempts:
            try:
                _model = WhisperModel(size, device=device, compute_type=ctype)
                _model_desc = f"{size} on {device.upper()}"
                return _model, _model_desc
            except Exception as e:  # noqa: BLE001
                last_err = e
        raise RuntimeError(f"Could not load a Whisper model: {last_err}")


def _norm(word: str) -> str:
    w = word.strip().lower()
    w = "".join(ch for ch in w if ch.isalpha())
    return w


def is_filler(word: str) -> bool:
    w = _norm(word)
    if not w:
        return False
    return w in FILLER_WORDS or bool(_STRETCHED.fullmatch(w))


def _extract_audio(ffmpeg: str, src: Path, dst: Path, flags: int) -> None:
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src), "-vn", "-ac", "1", "-ar", str(SR),
        "-c:a", "pcm_s16le", str(dst),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
    if r.returncode != 0 or not dst.exists():
        err = r.stderr or ""
        if "does not contain any stream" in err or "matches no streams" in err:
            raise RuntimeError("This clip has no audio track.")
        lines = err.strip().splitlines()
        raise RuntimeError(f"Could not extract audio: {lines[-1] if lines else 'unknown error'}")


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as wf:
        data = wf.readframes(wf.getnframes())
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def held_sounds(audio: np.ndarray, need_both_pauses: bool = False) -> list[dict]:
    """Find held, steady voiced sounds (filled pauses: "eee", "emm", "mmm").

    Frames are 25 ms every 10 ms. For each frame we take a log spectrum in 40
    bands (80 Hz - 4 kHz) and measure how much it differs from 50 ms earlier.
    Normal speech changes constantly; "eeee"/"mmmm" barely changes. The
    threshold adapts to the recording (a multiple of the 10th percentile of
    change over voiced frames), so mic/level differences don't matter.

    A steady core of MIN_HOLD+ always counts. A shorter core (SHORT_HOLD+)
    counts when it sits next to a pause — a short "emm" before a sentence —
    which separates it from an ordinary stressed vowel inside a word.

    Each hit: s/e = the whole sound (grown from the steady core out to its
    onset/decay), cs/ce = range to cut. Next to a pause the cut reaches a bit
    into the silence so the join is clean; otherwise it keeps EDGE_KEEP of the
    sound on that side (it's glued to a word).
    """
    win = 400
    n = (len(audio) - win) // HOP
    if n < 50:
        return []
    out_spec = np.empty((n, 40), dtype=np.float32)
    energy = np.empty(n, dtype=np.float32)
    window = np.hanning(win).astype(np.float32)
    edges = np.geomspace(5, 128, 41).astype(int)
    chunk = 4096  # frames per block, keeps memory bounded on long files
    for c0 in range(0, n, chunk):
        c1 = min(n, c0 + chunk)
        idx = np.arange(win)[None, :] + HOP * np.arange(c0, c1)[:, None]
        fr = audio[idx] * window
        energy[c0:c1] = 10 * np.log10((fr ** 2).mean(1) + 1e-10)
        spec = np.abs(np.fft.rfft(fr, 512, axis=1))
        for b in range(40):
            hi = max(edges[b] + 1, edges[b + 1])
            out_spec[c0:c1, b] = spec[:, edges[b]:hi].mean(1)
    lb = np.log(out_spec + 1e-6)
    loud = float(np.percentile(energy, 90))
    voiced = energy > loud - 18
    silent = energy < loud - 20
    if voiced.sum() < 100:
        return []
    d = np.zeros(n, dtype=np.float32)
    d[5:] = np.sqrt(((lb[5:] - lb[:-5]) ** 2).mean(1))
    ds = np.convolve(d, np.ones(5) / 5, "same")
    thr = STABILITY * float(np.percentile(ds[voiced], 10))
    stable = voiced & (ds < thr)
    loose = voiced & (ds < 2.0 * thr)

    def pause_from(k: int, step: int) -> tuple[int | None, int]:
        """Look up to PAUSE_REACH from frame k going `step` (+1/-1) for a pause:
        MIN_PAUSE+ of silence, allowing 1-frame blips above the threshold
        (room tone wobbles right at the edge). Returns (first silent frame or
        None, pause length in frames)."""
        need = int(round(MIN_PAUSE * 100))
        j = k
        for _ in range(int(round(PAUSE_REACH * 100))):
            j += step
            if j < 0 or j >= n:
                return None, 0
            if not silent[j]:
                continue
            m, length, blips = j, 0, 0
            while 0 <= m < n:
                if silent[m]:
                    blips = 0
                elif blips == 0 and 0 <= m + step < n and silent[m + step]:
                    blips = 1
                else:
                    break
                length += 1
                m += step
            if length >= need:
                return j, length
        return None, 0

    hits = []
    i = 0
    while i < n:
        if not stable[i]:
            i += 1
            continue
        j = i
        while j < n and stable[j]:
            j += 1
        core = (j - i) * 0.01
        a, b = i, j  # grow over onset/decay while still voiced and fairly steady
        while a > 0 and loose[a - 1]:
            a -= 1
        while b < n and loose[b]:
            b += 1
        lp, llen = pause_from(a, -1)
        rp, rlen = pause_from(b - 1, +1)
        near_pause = (lp is not None and rp is not None) if need_both_pauses \
            else (lp is not None or rp is not None)
        if core >= MIN_HOLD or (core >= SHORT_HOLD and near_pause):
            t = lambda k: k * 0.01 + 0.0125  # noqa: E731  frame -> centre time
            s0 = t(lp + 1) if lp is not None else t(a)
            e0 = t(rp) if rp is not None else t(b)
            cs = (s0 - min(0.10, llen * 0.01 / 2)) if lp is not None else t(a) + EDGE_KEEP
            ce = (e0 + min(0.10, rlen * 0.01 / 2)) if rp is not None else t(b) - EDGE_KEEP
            if ce - cs >= 0.12:
                hits.append({"s": s0, "e": e0, "cs": cs, "ce": ce, "core": core})
        i = j
    return hits


def detect_fillers(
    ffmpeg: str,
    src: Path,
    work_dir: Path,
    duration: float,
    report: Callable[[str, float], None],
    is_cancelled: Callable[[], bool],
    subprocess_flags: int = 0,
) -> dict:
    """Return {"words": [[s, e, text], ...], "fillers": [...], "language", "model"}.

    Each filler: {"s", "e"} = where the filler is, {"cs", "ce"} = range to cut,
    "label" = what to show, "kind" = "word" (Whisper wrote it) or "sound"
    (acoustic held-sound detector), "wi" = index into words for kind="word".
    """
    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    def check():
        if is_cancelled():
            raise Cancelled()

    report("Loading speech model (first run takes a moment)...", 1.0)
    model, desc = _load_model()
    check()

    wav = work_dir / f"fillers_{threading.get_ident()}.wav"
    try:
        report("Extracting audio...", 3.0)
        _extract_audio(ffmpeg, src, wav, subprocess_flags)
        audio = _read_wav(wav)
    finally:
        try:
            wav.unlink()
        except OSError:
            pass
    check()
    total = len(audio) / SR

    report("Detecting language...", 5.0)
    try:
        lang, _p, _all = model.detect_language(audio=audio[: SR * 30])
    except Exception:  # noqa: BLE001
        lang = None
    check()

    report(f"Transcribing ({desc})...", 7.0)
    segments, info = model.transcribe(
        audio,
        language=lang,
        word_timestamps=True,
        initial_prompt=PROMPTS.get(lang or "", PROMPTS["en"]),
        condition_on_previous_text=False,
        vad_filter=False,  # VAD tends to trim the very sounds we want
        beam_size=5,
    )
    lang = lang or getattr(info, "language", None)
    words: list[list] = []
    for seg in segments:
        check()
        for w in seg.words or []:
            words.append([round(float(w.start), 3), round(float(w.end), 3), w.word.strip()])
        if total > 0:
            report(
                f"Transcribing ({desc})... {int(seg.end)}s / {int(total)}s",
                7.0 + 83.0 * min(1.0, seg.end / total),
            )

    fillers: list[dict] = []

    # 1) Words Whisper wrote down that are pure hesitations.
    for i, (s, e, text) in enumerate(words):
        if e <= s or not is_filler(text):
            continue
        lo, hi = s - PAD_BEFORE, e + PAD_AFTER
        if i > 0 and not is_filler(words[i - 1][2]):
            lo = max(lo, words[i - 1][1] + WORD_GUARD)
        if i + 1 < len(words) and not is_filler(words[i + 1][2]):
            hi = min(hi, words[i + 1][0] - WORD_GUARD)
        lo, hi = max(0.0, lo), min(total, hi)
        if hi - lo < 0.05:
            continue
        fillers.append({
            "s": s, "e": e, "cs": round(lo, 3), "ce": round(hi, 3),
            "label": text.strip(" ,.!?…"), "kind": "word", "wi": i,
        })

    # 2) Held sounds Whisper didn't write down.
    report("Listening for held sounds (eeee, mmmm)...", 92.0)
    check()
    word_fill = [(f["cs"], f["ce"]) for f in fillers]
    for h in held_sounds(audio):
        s, e, cs, ce = h["s"], h["e"], max(0.0, h["cs"]), min(total, h["ce"])
        if any(cs < we and ce > ws for ws, we in word_fill):
            continue  # already covered by a lexical filler
        # Which word did Whisper stretch over it? (for display context)
        host = next((k for k, (ws, we, _) in enumerate(words) if ws <= s < we), None)
        fillers.append({
            "s": round(s, 3), "e": round(e, 3), "cs": round(cs, 3), "ce": round(ce, 3),
            "label": "held sound", "kind": "sound", "host": host,
        })

    fillers.sort(key=lambda f: f["s"])
    report("Done", 100.0)
    return {
        "words": words,
        "fillers": fillers,
        "language": lang,
        "model": desc,
    }
