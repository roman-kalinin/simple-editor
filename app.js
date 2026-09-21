/* Simple Video Editor — DaVinci-style layout */

const $ = (id) => document.getElementById(id);
const escHtml = (s) => String(s).replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
/* Set a button's label to a Feather icon + text (preserves the inline SVG that
 * a plain textContent assignment would wipe out). */
const setBtnLabel = (btn, iconId, text) => {
  btn.innerHTML = `<svg class="icon"><use href="#${iconId}"/></svg>${escHtml(text)}`;
};

const preview = $('preview');
const previewEmpty = $('previewEmpty');
const previewTitle = $('previewTitle');

const tlTrack = $('tlTrack');
const tlRuler = $('tlRuler');
const tlProgress = $('tlProgress');
const tlPlayhead = $('tlPlayhead');
const tlPlayheadTime = $('tlPlayheadTime');
const tlCuts = $('tlCuts');
const tlNow = $('tlNow');
const tlDur = $('tlDur');
const skipCuts = $('skipCuts');
const tlHover = $('tlHover');
const tlThumb = $('tlThumb');
const tlThumbImg = $('tlThumbImg');
const tlThumbTime = $('tlThumbTime');
const speedSelect = $('speedSelect');
const tlZoomInfo = $('tlZoomInfo');
const tlZoomHint = $('tlZoomHint');
const helpBtn = $('helpBtn');
const helpModal = $('helpModal');
const helpModalClose = $('helpModalClose');
const helpModalOk = $('helpModalOk');

const btnMarkStart = $('btnMarkStart');
const btnMarkEnd = $('btnMarkEnd');
const btnCancelMark = $('btnCancelMark');
const btnUndoCut = $('btnUndoCut');
const btnDeleteCut = $('btnDeleteCut');
const cutStats = $('cutStats');

const clipStripEl = $('clipStrip');
const stripTotals = $('stripTotals');
const uploadQueueEl = $('uploadQueue');
const pickFilesBtn = $('pickFilesBtn');
const pickFilesBtnInline = $('pickFilesBtnInline');
const pathInput = $('pathInput');
const addBtn = $('addBtn');

const outputDir = $('outputDir');
const outputName = $('outputName');
const pickFolderBtn = $('pickFolderBtn');
const exportBtn = $('exportBtn');
const accurateMode = $('accurateMode');
const keepAudio = $('keepAudio');
const qualitySelect = $('qualitySelect');
const resolutionSelect = $('resolutionSelect');
const exportModal = $('exportModal');
const exportModalClose = $('exportModalClose');
const exportCancelBtn = $('exportCancelBtn');
const exportConfirmBtn = $('exportConfirmBtn');
const exportModalNote = $('exportModalNote');
const exportDest = $('exportDest');
const jobStatus = $('jobStatus');
const jobMsg = $('jobMsg');
const jobBar = $('jobBar');
const jobMeta = $('jobMeta');
const jobCancelBtn = $('jobCancelBtn');
const jobDismissBtn = $('jobDismissBtn');
const ffmpegStatus = $('ffmpegStatus');

const dropOverlay = $('dropOverlay');
const tileTpl = $('clipTileTemplate');

/* ---------- state ---------- */
const clips = []; // {id, name, path, duration, cuts, pendingStart, tile}
let activeClip = null;
let previewSeekable = false;

/* ---------- undo/redo history + autosave ----------
 *
 * The editable state is: the order of clips, each clip's cut ranges, and which
 * clip is active. We snapshot that after every mutation into an undo stack.
 * Undo/redo restore a snapshot by re-applying it to the live clip objects
 * (matched by id) and re-ordering the tile DOM. Snapshots deliberately do NOT
 * capture pendingStart (a transient half-made cut) or DOM nodes.
 *
 * Autosave writes the same shape (plus path/name/duration so a session can be
 * rebuilt from scratch on next launch) to localStorage, debounced.
 */
const UNDO_LIMIT = 200;
const AUTOSAVE_KEY = 'simple-editor-session-v1';
let undoStack = [];
let redoStack = [];
let restoringHistory = false; // guard: don't record snapshots while applying one

function snapshotState() {
  return {
    order: clips.map((c) => c.id),
    cuts: Object.fromEntries(
      clips.map((c) => [c.id, c.cuts.map(([s, e]) => [s, e])]),
    ),
    activeId: activeClip ? activeClip.id : null,
  };
}

/* Apply a snapshot to the live clips (used by undo/redo). Clips that no longer
 * exist are ignored; clips missing from the snapshot keep their current cuts. */
function applyStateSnapshot(snap) {
  restoringHistory = true;
  try {
    // Restore cut ranges.
    for (const c of clips) {
      if (snap.cuts[c.id]) {
        c.cuts = snap.cuts[c.id].map(([s, e]) => [s, e]);
        c.pendingStart = null;
      }
    }
    // Restore order.
    const byId = new Map(clips.map((c) => [c.id, c]));
    const ordered = snap.order.map((id) => byId.get(id)).filter(Boolean);
    for (const c of clips) if (!ordered.includes(c)) ordered.push(c);
    clips.length = 0;
    clips.push(...ordered);
    for (const c of clips) clipStripEl.appendChild(c.tile);
    // Refresh every clip's mini-timeline.
    for (const c of clips) updateTileMini(c);
    // Restore active selection.
    const active = snap.activeId ? byId.get(snap.activeId) : null;
    setActive(active || clips[0] || null);
    renderSourceTimeline();
    updateDeleteCutButton();
    updateCutButtons();
    updateTotals();
  } finally {
    restoringHistory = false;
  }
}

/* Record the current state as a new undo point. Call at the END of any
 * function that mutates cuts or clip order/membership. */
function pushHistory() {
  if (restoringHistory) return;
  undoStack.push(snapshotState());
  if (undoStack.length > UNDO_LIMIT) undoStack.shift();
  redoStack = [];
  scheduleAutosave();
  updateHistoryButtons();
}

function doUndo() {
  if (undoStack.length < 2) return; // keep the baseline snapshot
  const current = undoStack.pop();
  redoStack.push(current);
  applyStateSnapshot(undoStack[undoStack.length - 1]);
  scheduleAutosave();
  updateHistoryButtons();
}

function doRedo() {
  if (!redoStack.length) return;
  const snap = redoStack.pop();
  undoStack.push(snap);
  applyStateSnapshot(snap);
  scheduleAutosave();
  updateHistoryButtons();
}

function updateHistoryButtons() {
  if (btnUndoCut) btnUndoCut.disabled = undoStack.length < 2;
  const redoBtn = $('btnRedo');
  if (redoBtn) redoBtn.disabled = !redoStack.length;
}

/* ---------- autosave / restore ---------- */
let autosaveTimer = null;
function scheduleAutosave() {
  if (autosaveTimer) clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(saveSession, 400);
}
function saveSession() {
  try {
    const data = {
      version: 1,
      savedAt: Date.now(),
      clips: clips.map((c) => ({
        id: c.id,
        name: c.name,
        path: c.path,
        duration: c.duration,
        cuts: c.cuts.map(([s, e]) => [s, e]),
      })),
      activeId: activeClip ? activeClip.id : null,
    };
    localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(data));
  } catch (_) { /* storage full / unavailable — ignore */ }
}
function loadSavedSession() {
  try {
    const raw = localStorage.getItem(AUTOSAVE_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data || !Array.isArray(data.clips) || !data.clips.length) return null;
    return data;
  } catch (_) { return null; }
}
function clearSavedSession() {
  try { localStorage.removeItem(AUTOSAVE_KEY); } catch (_) {}
}

/* ---------- utilities ---------- */
function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${m}:${sec.toFixed(2).padStart(5, '0')}`;
}
function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
async function postJson(url, obj) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(obj || {}),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.error) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

/* ---------- ffmpeg status ---------- */
async function checkFfmpeg() {
  try {
    const r = await fetch('/api/ffmpeg-status');
    const j = await r.json();
    if (j.found) {
      ffmpegStatus.innerHTML = `<svg class="icon sm"><use href="#i-check"/></svg>ffmpeg ready`;
      ffmpegStatus.className = 'ffmpeg-status ok';
      ffmpegStatus.title = 'ffmpeg is available';
    } else {
      ffmpegStatus.innerHTML = `<svg class="icon sm"><use href="#i-alert"/></svg>ffmpeg missing`;
      ffmpegStatus.className = 'ffmpeg-status bad';
      ffmpegStatus.title = 'ffmpeg missing — run: winget install Gyan.FFmpeg';
    }
  } catch (e) {
    ffmpegStatus.innerHTML = `<svg class="icon sm"><use href="#i-alert"/></svg>server unreachable`;
    ffmpegStatus.className = 'ffmpeg-status bad';
    ffmpegStatus.title = 'Cannot reach the local server';
  }
}

/* ---------- cut ranges ---------- */
function buildKeepRanges(clip) {
  const dur = clip.duration;
  if (!dur) return [];
  const cuts = clip.cuts.slice().sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const [s, e] of cuts) {
    if (merged.length && s <= merged[merged.length - 1][1]) {
      merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], e);
    } else {
      merged.push([s, e]);
    }
  }
  const keep = [];
  let cursor = 0;
  for (const [s, e] of merged) {
    if (s > cursor) keep.push([cursor, Math.min(s, dur)]);
    cursor = Math.max(cursor, e);
  }
  if (cursor < dur) keep.push([cursor, dur]);
  return keep.filter(([s, e]) => e - s > 0.02);
}

function clipKept(clip) {
  return buildKeepRanges(clip).reduce((a, [s, e]) => a + (e - s), 0);
}

/* ---------- totals ---------- */
function updateTotals() {
  if (!clips.length) { stripTotals.textContent = ''; return; }
  let src = 0, kept = 0;
  for (const c of clips) { src += c.duration || 0; kept += clipKept(c); }
  stripTotals.textContent =
    `${clips.length} clip${clips.length === 1 ? '' : 's'} · source ${fmtTime(src)} · kept ${fmtTime(kept)}`;
}

/* ---------- preview / active clip ---------- */
function setActive(clip) {
  if (activeClip === clip) return;
  activeClip = clip;
  // Reset hover-thumbnail state so we don't show the previous clip's frame.
  if (typeof thumbReqTime !== 'undefined') thumbReqTime = -1;
  if (tlThumb) { tlThumb.hidden = true; tlThumbImg.removeAttribute('src'); }
  // A different clip means no cut is selected any more.
  selectedCut = null;
  previewSeekable = false;
  resetZoom(); // new clip -> show the whole timeline
  for (const c of clips) c.tile.classList.toggle('active', c === clip);

  if (!clip) {
    preview.removeAttribute('src');
    preview.load();
    preview.classList.add('hidden');
    previewEmpty.classList.remove('hidden');
    previewTitle.textContent = '';
    renderSourceTimeline();
    updateDeleteCutButton();
    updateCutButtons();
    return;
  }
  preview.classList.remove('hidden');
  previewEmpty.classList.add('hidden');
  previewTitle.textContent = clip.name;
  // Load the video. Since preview is muted=false by default for audible
  // scrubbing — but user wants output muted anyway, default muted=true.
  preview.muted = true;
  preview.src = `/video/${clip.id}`;
  preview.load();
  renderSourceTimeline();
  updateDeleteCutButton();
  updateCutButtons();
}

function updateCutButtons() {
  const has = !!activeClip;
  btnMarkStart.disabled = !has || !previewSeekable;
  btnMarkEnd.disabled = !has || activeClip?.pendingStart == null;
  btnCancelMark.disabled = !has || activeClip?.pendingStart == null;
  // Undo/redo buttons are driven by the history stack, not the current clip.
  updateHistoryButtons();
}

/* The currently selected cut (the [s,e] array itself, by identity). Selecting
 * a cut reveals edge handles for resizing and enables Delete/Backspace. */
let selectedCut = null;
/* A cut that should play a one-shot "just created" flash on the next render. */
let _flashCut = null;

/* ---------- timeline zoom (scroll wheel) ----------
 * The track shows a *view window* [viewStart, viewEnd] of the clip, not
 * necessarily the whole thing. Scrolling up on the timeline zooms in toward
 * the cursor's time; scrolling down zooms back out. All px<->time conversions
 * go through the helpers below so every interaction (scrub, hover, cut render,
 * playhead) respects the current zoom.  When not zoomed the window is the full
 * clip, so behaviour is identical to before. */
let viewStart = 0;
let viewEnd = 0; // 0 means "uninitialized / whole clip"
const MIN_VIEW_SPAN = 0.25; // never zoom tighter than a quarter-second

function fullDur() { return activeClip?.duration || 0; }

function viewSpan() {
  const d = fullDur();
  if (!d) return 0;
  if (viewEnd <= viewStart) return d; // uninitialized -> whole clip
  return viewEnd - viewStart;
}
function viewLo() { return (viewEnd <= viewStart) ? 0 : viewStart; }

function resetZoom() {
  viewStart = 0;
  viewEnd = 0;
}

/* Convert an absolute clip time to a percentage across the current view.
 * May be <0 or >100 when the time is outside the visible window. */
function timeToPct(t) {
  const span = viewSpan();
  if (!span) return 0;
  return ((t - viewLo()) / span) * 100;
}
/* Convert a pointer clientX to an absolute clip time within the view. */
function clientXToTime(clientX) {
  const rect = tlTrack.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  return viewLo() + ratio * viewSpan();
}

/* Keep an absolute time visible: if it's outside the window, recenter the
 * window on it (used so the playhead doesn't run off-screen while zoomed). */
function ensureVisible(t) {
  const d = fullDur();
  if (!d || viewEnd <= viewStart) return; // not zoomed
  const span = viewEnd - viewStart;
  const margin = span * 0.1;
  if (t < viewStart + margin || t > viewEnd - margin) {
    let ns = t - span / 2;
    ns = Math.max(0, Math.min(ns, d - span));
    viewStart = ns;
    viewEnd = ns + span;
    renderSourceTimeline();
  }
}

/* Wheel handler: zoom toward the time under the cursor. */
function onTimelineWheel(e) {
  const d = fullDur();
  if (!d) return;
  e.preventDefault();
  const anchor = clientXToTime(e.clientX); // keep this time under the cursor
  const curSpan = viewSpan();
  // Wheel up (deltaY<0) zooms in; down zooms out.
  const factor = e.deltaY < 0 ? 0.8 : 1.25;
  let newSpan = curSpan * factor;
  newSpan = Math.max(MIN_VIEW_SPAN, Math.min(newSpan, d));
  if (newSpan >= d) { resetZoom(); renderSourceTimeline(); syncPlayheadToView(); return; }
  // Preserve the cursor's fractional position within the view.
  const rect = tlTrack.getBoundingClientRect();
  const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  let ns = anchor - frac * newSpan;
  ns = Math.max(0, Math.min(ns, d - newSpan));
  viewStart = ns;
  viewEnd = ns + newSpan;
  renderSourceTimeline();
  syncPlayheadToView();
}

/* Repaint just the playhead/progress at the current time for the current view
 * (called after a zoom change so they land in the right place immediately). */
function syncPlayheadToView() {
  if (!activeClip || !activeClip.duration) return;
  const t = scrubbing ? scrubTargetTime : (preview.currentTime || 0);
  const pct = timeToPct(t);
  tlPlayhead.style.left = `${pct}%`;
  tlProgress.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  if (tlPlayheadTime) tlPlayheadTime.textContent = fmtTime(t);
}

/* Pick a "nice" tick interval (seconds) so a view span shows ~6-9 ticks. */
function niceStep(span) {
  const target = span / 7;               // aim for ~7 ticks
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  for (const s of steps) if (s >= target) return s;
  return 600;
}

/* Draw the time ruler (ticks + labels) across the visible window. */
function renderRuler() {
  if (!tlRuler) return;
  tlRuler.innerHTML = '';
  const c = activeClip;
  if (!c || !c.duration) return;
  const lo = viewLo(), span = viewSpan();
  const hi = lo + span;
  const step = niceStep(span);
  // First tick at or after lo, aligned to the step grid.
  let t = Math.ceil(lo / step) * step;
  for (; t <= hi + 1e-6; t += step) {
    const pct = timeToPct(t);
    if (pct < 0 || pct > 100) continue;
    const tick = document.createElement('div');
    tick.className = 'tl-tick';
    tick.style.left = `${pct}%`;
    const lab = document.createElement('span');
    lab.className = 'tl-tick-label';
    lab.textContent = fmtTick(t, step);
    tick.appendChild(lab);
    tlRuler.appendChild(tick);
  }
}

/* Compact tick label: show sub-second precision only when the step is <1s. */
function fmtTick(t, step) {
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  if (step < 1) return `${m}:${s.toFixed(1).padStart(4, '0')}`;
  return `${m}:${String(Math.round(s)).padStart(2, '0')}`;
}

function renderSourceTimeline() {
  tlCuts.innerHTML = '';
  const c = activeClip;
  if (!c || !c.duration) {
    tlProgress.style.width = '0%';
    tlPlayhead.style.left = '0%';
    tlNow.textContent = '0:00.00';
    tlDur.textContent = '0:00.00';
    cutStats.textContent = '';
    document.body.classList.remove('mark-pending');
    if (tlZoomHint) tlZoomHint.hidden = true;
    return;
  }
  // Drop a stale selection that no longer belongs to this clip.
  if (selectedCut && !c.cuts.includes(selectedCut)) selectedCut = null;
  tlDur.textContent = fmtTime(c.duration);
  tlNow.textContent = fmtTime(preview.currentTime || 0);
  // Keep progress + playhead consistent with the current view on every render.
  syncPlayheadToView();
  renderRuler();
  const lo = viewLo(), span = viewSpan();
  const hi = lo + span;
  for (const cut of c.cuts) {
    const [s, e] = cut;
    // Skip cuts entirely outside the visible window.
    if (e <= lo || s >= hi) continue;
    const band = document.createElement('div');
    band.className = 'cut-band' + (cut === selectedCut ? ' selected' : '');
    const leftPct = timeToPct(s);
    const rightPct = timeToPct(e);
    const clampedLeft = Math.max(0, leftPct);
    const clampedRight = Math.min(100, rightPct);
    const widthPct = Math.max(0, clampedRight - clampedLeft);
    band.style.left = `${clampedLeft}%`;
    band.style.width = `${widthPct}%`;
    band.title = `Cut ${fmtTime(s)}–${fmtTime(e)}  (${fmtTime(e - s)})`;
    // Only show a resize handle for an edge that's actually visible.
    band._edgeLeftVisible = leftPct >= 0;
    band._edgeRightVisible = rightPct <= 100;
    band._cut = cut;
    // A one-shot "just created" flash (set by markEnd via _flashCut).
    if (cut === _flashCut) band.classList.add('just-added');
    // Duration label when the band is wide enough to fit it.
    if (widthPct > 8) {
      const lbl = document.createElement('div');
      lbl.className = 'cut-band-label';
      lbl.textContent = fmtTime(e - s);
      band.appendChild(lbl);
    }
    // Click selects the cut (and must NOT scrub the playhead).
    band.addEventListener('pointerdown', (ev) => {
      // If a handle was hit, let its own handler run (it stops propagation).
      if (ev.target.classList.contains('cut-handle')) return;
      ev.stopPropagation();
      selectCut(cut);
    });
    if (cut === selectedCut) {
      for (const side of ['left', 'right']) {
        if (side === 'left' && !band._edgeLeftVisible) continue;
        if (side === 'right' && !band._edgeRightVisible) continue;
        const h = document.createElement('div');
        h.className = `cut-handle ${side}`;
        h.addEventListener('pointerdown', (ev) => startResize(ev, cut, side));
        band.appendChild(h);
      }
    }
    tlCuts.appendChild(band);
  }
  if (c.pendingStart != null) {
    const p = timeToPct(c.pendingStart);
    if (p >= 0 && p <= 100) {
      const m = document.createElement('div');
      m.className = 'pending-mark';
      m.style.left = `${p}%`;
      tlCuts.appendChild(m);
    }
  }
  // Reflect a pending cut-start so the Mark OUT button pulses (CSS).
  document.body.classList.toggle('mark-pending', c.pendingStart != null);
  // Reflect zoom state on the track (for styling + a small readout). When
  // zoomed, swap the "scroll to zoom" hint for the live range readout.
  const zoomed = viewEnd > viewStart;
  tlTrack.classList.toggle('zoomed', zoomed);
  if (tlZoomInfo) {
    if (zoomed) {
      tlZoomInfo.hidden = false;
      tlZoomInfo.innerHTML =
        `<svg class="icon sm"><use href="#i-search"/></svg>` +
        escHtml(`${fmtTime(viewLo())}–${fmtTime(viewLo() + viewSpan())} (${(c.duration / viewSpan()).toFixed(1)}×)`);
    } else {
      tlZoomInfo.hidden = true;
    }
  }
  if (tlZoomHint) tlZoomHint.hidden = zoomed;
  // Cut-focused readout (the aggregate "kept" totals live in the strip header).
  const removed = c.duration - clipKept(c);
  const nCuts = c.cuts.length;
  cutStats.textContent = nCuts
    ? `${nCuts} cut${nCuts === 1 ? '' : 's'} · ${fmtTime(removed)} removed`
    : 'no cuts yet';
  // Flash the readout when the cut count changes (create/delete feedback).
  if (cutStats._n !== undefined && cutStats._n !== nCuts) {
    cutStats.classList.remove('flash');
    void cutStats.offsetWidth; // restart the animation
    cutStats.classList.add('flash');
  }
  cutStats._n = nCuts;
}

/* ---------- cut selection + resize ---------- */
function selectCut(cut) {
  if (selectedCut === cut) return;
  selectedCut = cut;
  renderSourceTimeline();
  updateDeleteCutButton();
}

function deleteSelectedCut() {
  if (!selectedCut || !activeClip) return;
  activeClip.cuts = activeClip.cuts.filter((x) => x !== selectedCut);
  selectedCut = null;
  renderSourceTimeline();
  updateTileMini(activeClip);
  updateCutButtons();
  updateDeleteCutButton();
  updateTotals();
  pushHistory();
}

/* Drag a cut's left/right edge. The visual band follows the cursor live; the
 * cut array is updated on each move (clamped so edges can't cross and stay
 * inside [0, duration]); history is recorded once on release. */
let resizing = null; // {cut, side, pointerId}
function startResize(ev, cut, side) {
  if (!activeClip || !activeClip.duration) return;
  ev.preventDefault();
  ev.stopPropagation();
  resizing = { cut, side, pointerId: ev.pointerId };
  try { tlTrack.setPointerCapture(ev.pointerId); } catch (_) {}
  preview.pause();
}
function moveResize(ev) {
  if (!resizing || !activeClip) return;
  const dur = activeClip.duration || 0;
  let t = clientXToTime(ev.clientX);
  const cut = resizing.cut;
  const MIN = 0.05; // keep at least a sliver so the cut stays grabbable
  if (resizing.side === 'left') {
    cut[0] = Math.max(0, Math.min(t, cut[1] - MIN));
  } else {
    cut[1] = Math.min(dur, Math.max(t, cut[0] + MIN));
  }
  renderSourceTimeline();
  // Show the moving edge time where the hover thumb sits, as feedback.
  tlThumbTime.textContent = fmtTime(resizing.side === 'left' ? cut[0] : cut[1]);
  // Live-seek the preview to the edge being dragged so you see the frame.
  try { preview.currentTime = (resizing.side === 'left' ? cut[0] : cut[1]); } catch (_) {}
}
function endResize(ev) {
  if (!resizing) return;
  try { tlTrack.releasePointerCapture(resizing.pointerId); } catch (_) {}
  resizing = null;
  updateTileMini(activeClip);
  updateCutButtons();
  updateDeleteCutButton();
  updateTotals();
  pushHistory();
}

/* The cut that the playhead is currently inside, if any. Drives the
 * "✕ Delete cut" button visibility — there's no persistent cut list any
 * more; just scrub onto the cut band and click delete. */
function cutUnderPlayhead() {
  if (!activeClip) return null;
  const t = preview.currentTime;
  for (const cut of activeClip.cuts) {
    if (t >= cut[0] && t <= cut[1]) return cut;
  }
  return null;
}
function updateDeleteCutButton() {
  // Prefer an explicitly selected cut; otherwise fall back to the cut under
  // the playhead (the pre-selection behavior still works).
  const cut = selectedCut || cutUnderPlayhead();
  if (cut) {
    btnDeleteCut.hidden = false;
    const label = selectedCut ? 'Delete selected cut' : 'Delete cut';
    setBtnLabel(btnDeleteCut, 'i-trash', `${label} (${fmtTime(cut[1] - cut[0])})`);
    btnDeleteCut._target = cut;
  } else {
    btnDeleteCut.hidden = true;
    btnDeleteCut._target = null;
  }
}
btnDeleteCut.onclick = () => {
  const cut = btnDeleteCut._target;
  if (!cut || !activeClip) return;
  activeClip.cuts = activeClip.cuts.filter((x) => x !== cut);
  if (selectedCut === cut) selectedCut = null;
  renderSourceTimeline();
  updateTileMini(activeClip);
  updateCutButtons();
  updateDeleteCutButton();
  updateTotals();
  pushHistory();
};

/* ---------- skip-cuts-on-play ----------
 * When enabled, playback jumps over cut ranges so the preview matches the
 * exported result. `timeupdate` only fires ~4x/s (too coarse to catch a cut
 * boundary cleanly), so while the video is playing we poll on rAF and seek
 * past any cut the playhead has entered. A tiny epsilon past the cut end
 * avoids re-triggering on the same boundary. */
const SKIP_EPS = 0.02;

function cutContaining(t) {
  if (!activeClip) return null;
  for (const cut of activeClip.cuts) {
    // Half-open at the end so landing exactly on a cut's end doesn't re-skip.
    if (t >= cut[0] && t < cut[1]) return cut;
  }
  return null;
}

/* If `t` sits inside a cut, return where playback should resume (end of the
 * cut, merged across adjacent cuts); otherwise null. */
function skipTargetFor(t) {
  let cut = cutContaining(t);
  if (!cut) return null;
  let end = cut[1];
  // Chain through touching/overlapping cuts so we clear them all in one seek.
  let advanced = true;
  while (advanced) {
    advanced = false;
    for (const c of activeClip.cuts) {
      if (c[0] <= end + SKIP_EPS && c[1] > end) { end = c[1]; advanced = true; }
    }
  }
  return end;
}

function applySkip() {
  if (!skipCuts || !skipCuts.checked) return false;
  if (!activeClip || scrubbing) return false;
  const target = skipTargetFor(preview.currentTime);
  if (target == null) return false;
  const dur = activeClip.duration || 0;
  if (target >= dur - SKIP_EPS) {
    // The rest of the clip is cut — stop at the end of the kept content.
    preview.pause();
    try { preview.currentTime = Math.max(0, dur - 0.001); } catch (_) {}
  } else {
    try { preview.currentTime = target + SKIP_EPS; } catch (_) {}
  }
  return true;
}

let skipRaf = 0;
function skipLoop() {
  skipRaf = 0;
  if (preview.paused || preview.ended) return;
  applySkip();
  skipRaf = requestAnimationFrame(skipLoop);
}
preview.addEventListener('play', () => {
  // If the user pressed play while parked inside a cut, jump out immediately.
  applySkip();
  if (!skipRaf) skipRaf = requestAnimationFrame(skipLoop);
});
preview.addEventListener('pause', () => {
  if (skipRaf) { cancelAnimationFrame(skipRaf); skipRaf = 0; }
});

/* ---------- preview wiring ---------- */
preview.addEventListener('loadedmetadata', () => {
  // Now we can seek.
  previewSeekable = true;
  if (activeClip) {
    // If the active clip didn't know its duration yet (path-added without a
    // preload pass), capture it now.
    if (!activeClip.duration && isFinite(preview.duration)) {
      activeClip.duration = preview.duration;
      updateTileMini(activeClip);
      updateTotals();
    }
    renderSourceTimeline();
    updateCutButtons();
  }
});
preview.addEventListener('error', () => {
  previewSeekable = false;
  previewTitle.textContent =
    `${activeClip ? activeClip.name + ' — ' : ''}⚠ cannot play this file in the browser (codec unsupported?)`;
});
preview.addEventListener('timeupdate', () => {
  if (!activeClip || !activeClip.duration) return;
  // While playing zoomed-in, keep the playhead on-screen by scrolling the view.
  if (!preview.paused && !scrubbing) ensureVisible(preview.currentTime);
  const pct = timeToPct(preview.currentTime);
  tlProgress.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  tlPlayhead.style.left = `${pct}%`;
  tlNow.textContent = fmtTime(preview.currentTime);
  updateDeleteCutButton();
});
preview.addEventListener('seeked', updateDeleteCutButton);

/* ---------- playback speed ----------
 * A skimming preference that persists across clip switches. The <video>
 * element resets playbackRate to 1 whenever a new src loads, so we re-apply
 * it on loadedmetadata. Browsers correct audio pitch up to ~4x. */
const SPEEDS = [0.5, 1, 1.5, 2, 4];
let playbackSpeed = 1;

function applySpeed() {
  try { preview.playbackRate = playbackSpeed; } catch (_) {}
}
function setSpeed(v) {
  playbackSpeed = v;
  applySpeed();
  if (speedSelect) speedSelect.value = String(v);
}
if (speedSelect) {
  speedSelect.addEventListener('change', () => setSpeed(parseFloat(speedSelect.value)));
}
// Re-assert the chosen rate once the new clip is ready to play.
preview.addEventListener('loadedmetadata', applySpeed);
preview.addEventListener('ratechange', () => {
  // Keep the dropdown in sync if some other code (e.g. native controls) nudges
  // the rate to one of our presets.
  if (speedSelect && SPEEDS.includes(preview.playbackRate)) {
    playbackSpeed = preview.playbackRate;
    speedSelect.value = String(preview.playbackRate);
  }
});

/* Cycle to the next/previous preset speed (keyboard: ] / [ ). */
function stepSpeed(dir) {
  let i = SPEEDS.indexOf(playbackSpeed);
  if (i < 0) i = SPEEDS.indexOf(1);
  i = Math.max(0, Math.min(SPEEDS.length - 1, i + dir));
  setSpeed(SPEEDS[i]);
}

/* Drag-scrub on the timeline track.
 *
 * UX goal: the visual playhead MUST track the cursor at 60fps so drags feel
 * instant. The <video> element's currentTime can only seek at 5–20 Hz for
 * large H.264 files (every seek forces a decode). So we:
 *   - update the playhead position visually on every pointermove (rAF-coalesced)
 *   - set preview.currentTime at a throttled rate (~12/s) while dragging
 *   - do a final seek on pointerup so the frame lands exactly where the
 *     cursor stopped
 */
let scrubbing = false;
let scrubTargetTime = 0;
let scrubRafPending = false;
let lastSeekAt = 0;
const SEEK_THROTTLE_MS = 80; // ~12 Hz during drag — responsive without stutter

function scrubCoordsToTime(e) {
  return clientXToTime(e.clientX);
}

function paintScrub() {
  scrubRafPending = false;
  if (!activeClip || !activeClip.duration) return;
  const pct = timeToPct(scrubTargetTime);
  tlProgress.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  tlPlayhead.style.left = `${pct}%`;
  tlNow.textContent = fmtTime(scrubTargetTime);

  // Evaluate "cut under playhead" against the visual scrub target, not the
  // lagging video.currentTime — matches what the user sees.
  const cut = activeClip.cuts.find(([s, e]) => scrubTargetTime >= s && scrubTargetTime <= e);
  if (cut) {
    btnDeleteCut.hidden = false;
    setBtnLabel(btnDeleteCut, 'i-trash', `Delete cut (${fmtTime(cut[1] - cut[0])})`);
    btnDeleteCut._target = cut;
  } else {
    btnDeleteCut.hidden = true;
    btnDeleteCut._target = null;
  }

  const now = performance.now();
  if (now - lastSeekAt >= SEEK_THROTTLE_MS) {
    lastSeekAt = now;
    try { preview.currentTime = scrubTargetTime; } catch (_) {}
  }
}

function scheduleScrubPaint() {
  if (scrubRafPending) return;
  scrubRafPending = true;
  requestAnimationFrame(paintScrub);
}

tlTrack.addEventListener('pointerdown', (e) => {
  if (!activeClip) return;
  // A pointerdown that reaches the track itself (not stopped by a cut band)
  // means the user clicked empty timeline: deselect any selected cut and scrub.
  if (selectedCut) selectCut(null);
  scrubbing = true;
  tlTrack.setPointerCapture(e.pointerId);
  preview.pause(); // avoid audio/video fighting the scrub seeks
  scrubTargetTime = scrubCoordsToTime(e);
  scheduleScrubPaint();
});
tlTrack.addEventListener('pointermove', (e) => {
  if (resizing) { moveResize(e); return; }
  if (!scrubbing) return;
  scrubTargetTime = scrubCoordsToTime(e);
  scheduleScrubPaint();
});
function endScrub(e) {
  if (resizing) { endResize(e); return; }
  if (!scrubbing) return;
  scrubbing = false;
  try { tlTrack.releasePointerCapture(e.pointerId); } catch (_) {}
  // Final authoritative seek to the exact drop position.
  try { preview.currentTime = scrubTargetTime; } catch (_) {}
}
tlTrack.addEventListener('pointerup', endScrub);
tlTrack.addEventListener('pointercancel', endScrub);
tlTrack.addEventListener('lostpointercapture', () => { scrubbing = false; });
// Scroll wheel over the timeline zooms toward the cursor.
tlTrack.addEventListener('wheel', onTimelineWheel, { passive: false });
// Clicking the zoom readout resets to the whole timeline.
if (tlZoomInfo) tlZoomInfo.addEventListener('click', () => {
  resetZoom();
  renderSourceTimeline();
  syncPlayheadToView();
});

/* ---------- timeline hover thumbnails ----------
 * As the cursor moves over the track, show a small frame preview above it.
 * Thumbnails are generated on demand by the server (/api/thumb) and cached
 * there, so we only debounce the request slightly and let repeats be instant.
 * We hide the preview while actively scrubbing (the main preview already shows
 * the frame then). */
let thumbTimer = null;
let thumbReqTime = -1;

function timelineHoverTime(e) {
  const rect = tlTrack.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  return { ratio, time: clientXToTime(e.clientX) };
}

function requestThumb(time) {
  if (!activeClip) return;
  const rounded = Math.round(time * 4) / 4; // match server's 0.25s buckets
  if (rounded === thumbReqTime) return;
  thumbReqTime = rounded;
  const url = `/api/thumb?id=${encodeURIComponent(activeClip.id)}&t=${rounded}`;
  // Only swap the visible image once the new one has decoded, to avoid flicker.
  const img = new Image();
  img.onload = () => {
    if (thumbReqTime === rounded) tlThumbImg.src = url;
  };
  img.src = url;
}

tlTrack.addEventListener('pointermove', (e) => {
  if (!activeClip || !activeClip.duration) return;
  if (scrubbing || resizing) { tlThumb.hidden = true; return; }
  const { ratio, time } = timelineHoverTime(e);
  // Position the hover line and the preview.
  tlHover.style.left = `${ratio * 100}%`;
  const trackRect = tlTrack.getBoundingClientRect();
  const parentRect = tlThumb.parentElement.getBoundingClientRect();
  const x = (trackRect.left - parentRect.left) + ratio * trackRect.width;
  tlThumb.style.left = `${x}px`;
  tlThumbTime.textContent = fmtTime(time);
  tlThumb.hidden = false;
  // Debounce the actual fetch a touch so a fast sweep doesn't queue dozens.
  if (thumbTimer) clearTimeout(thumbTimer);
  thumbTimer = setTimeout(() => requestThumb(time), 40);
});
tlTrack.addEventListener('pointerleave', () => {
  tlThumb.hidden = true;
  if (thumbTimer) { clearTimeout(thumbTimer); thumbTimer = null; }
});

/* ---------- cut actions ---------- */
function markStart() {
  if (!activeClip || !previewSeekable) return;
  activeClip.pendingStart = preview.currentTime;
  renderSourceTimeline();
  updateCutButtons();
}
function markEnd() {
  if (!activeClip || activeClip.pendingStart == null) return;
  let s = activeClip.pendingStart, e = preview.currentTime;
  if (e < s) [s, e] = [e, s];
  let added = false;
  if (e - s > 0.01) {
    const cut = [s, e];
    activeClip.cuts.push(cut);
    _flashCut = cut; // one-shot create flash on next render
    added = true;
  }
  activeClip.pendingStart = null;
  renderSourceTimeline();
  _flashCut = null;
  updateDeleteCutButton();
  updateTileMini(activeClip);
  updateCutButtons();
  updateTotals();
  if (added) pushHistory();
}
function cancelMark() {
  if (!activeClip) return;
  activeClip.pendingStart = null;
  renderSourceTimeline();
  updateCutButtons();
}
btnMarkStart.onclick = markStart;
btnMarkEnd.onclick = markEnd;
btnCancelMark.onclick = cancelMark;
btnUndoCut.onclick = doUndo;
const btnRedoEl = $('btnRedo');
if (btnRedoEl) btnRedoEl.onclick = doRedo;

/* ---------- keyboard ----------
 *
 * Cut shortcuts are intentionally one-key-toggle so they're usable
 * one-handed while the other hand scrubs:
 *   C         — first press marks IN, next press marks OUT (toggle)
 *   Esc       — cancel pending mark
 *   Ctrl/⌘+Z  — undo last completed cut
 *   I / O     — explicit mark-in / mark-out (still work)
 *   Space     — play/pause
 *   ← / →     — ±0.1s  (Shift = ±1s)
 */
function toggleMark() {
  if (!activeClip) return;
  if (activeClip.pendingStart == null) markStart();
  else markEnd();
}

document.addEventListener('keydown', (e) => {
  // Help dialog: Esc / Enter / ? all close it.
  if (helpModal && !helpModal.hidden) {
    if (e.key === 'Escape' || e.key === 'Enter' || e.key === '?') {
      e.preventDefault(); closeHelpModal();
    }
    return;
  }
  // Export dialog: Esc closes it, Enter confirms — regardless of focus.
  if (exportModal && !exportModal.hidden) {
    if (e.key === 'Escape') { e.preventDefault(); closeExportModal(); }
    else if (e.key === 'Enter') { e.preventDefault(); confirmExport(); }
    return;
  }

  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

  // "?" opens the shortcuts panel from anywhere (outside inputs).
  if (e.key === '?') { e.preventDefault(); openHelpModal(); return; }

  // Undo/redo work even without an active clip selection focus.
  //   Ctrl/Cmd+Z         — undo
  //   Ctrl/Cmd+Shift+Z   — redo
  //   Ctrl/Cmd+Y         — redo (Windows convention)
  if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
    e.preventDefault();
    if (e.shiftKey) doRedo(); else doUndo();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || e.key === 'Y')) {
    e.preventDefault();
    doRedo();
    return;
  }

  if (!activeClip) return;
  const step = e.shiftKey ? 1 : 0.1;
  switch (e.key) {
    case ' ':
      e.preventDefault();
      if (preview.paused) preview.play(); else preview.pause();
      break;
    case 'ArrowLeft':
      e.preventDefault();
      preview.currentTime = Math.max(0, preview.currentTime - step);
      break;
    case 'ArrowRight':
      e.preventDefault();
      preview.currentTime = Math.min(
        activeClip.duration || preview.currentTime + step,
        preview.currentTime + step,
      );
      break;
    case 'c': case 'C':
      e.preventDefault(); toggleMark(); break;
    case 'i': case 'I':
      e.preventDefault(); markStart(); break;
    case 'o': case 'O':
      e.preventDefault(); markEnd(); break;
    case '[':
      e.preventDefault(); stepSpeed(-1); break;
    case ']':
      e.preventDefault(); stepSpeed(+1); break;
    case '\\':
      e.preventDefault(); setSpeed(1); break;
    case 'Delete': case 'Backspace':
      if (selectedCut) { e.preventDefault(); deleteSelectedCut(); }
      break;
    case 'Escape':
      if (selectedCut) selectCut(null);
      else cancelMark();
      break;
  }
});

/* ---------- clip tiles ---------- */
function updateTileMini(clip) {
  const mini = clip.tile.querySelector('.tile-mini-cuts');
  mini.innerHTML = '';
  if (!clip.duration) return;
  for (const [s, e] of clip.cuts) {
    const band = document.createElement('div');
    band.className = 'band';
    band.style.left = `${(s / clip.duration) * 100}%`;
    band.style.width = `${((e - s) / clip.duration) * 100}%`;
    mini.appendChild(band);
  }
  clip.tile.querySelector('.tile-dur').textContent = fmtTime(clip.duration);
}

function addClip(info, restore) {
  const tile = tileTpl.content.firstElementChild.cloneNode(true);
  const clip = {
    id: info.id,
    name: info.name,
    path: info.path,
    duration: restore && restore.duration ? restore.duration : 0,
    cuts: restore && Array.isArray(restore.cuts) ? restore.cuts.map(([s, e]) => [s, e]) : [],
    pendingStart: null,
    tile,
  };
  clips.push(clip);

  tile.querySelector('.tile-name').textContent = info.name;
  tile.title = info.path;
  const video = tile.querySelector('.tile-video');
  video.src = `/video/${info.id}`;

  // Capture duration + snap a thumbnail frame (seek slightly past the start
  // to avoid black keyframes).
  video.addEventListener('loadedmetadata', () => {
    clip.duration = video.duration;
    updateTileMini(clip);
    updateTotals();
    if (activeClip === clip) renderSourceTimeline();
    try { video.currentTime = Math.min(1.0, video.duration * 0.1); }
    catch (_) { /* ignore */ }
  });
  video.addEventListener('error', () => {
    tile.querySelector('.tile-dur').textContent = '!';
  });

  // Click tile to make active.
  tile.addEventListener('click', (e) => {
    if (e.target.closest('.tile-actions')) return;
    setActive(clip);
  });
  tile.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setActive(clip);
    }
  });

  // Reorder actions.
  tile.querySelector('.tile-left').onclick = () => moveClip(clip, -1);
  tile.querySelector('.tile-right').onclick = () => moveClip(clip, +1);
  tile.querySelector('.tile-remove').onclick = () => {
    if (!confirm(`Remove ${clip.name}?`)) return;
    removeClip(clip);
  };

  // Drag reorder — uses a dedicated placeholder node that reserves space
  // at the insertion point (instead of coloring the tile edge). Much more
  // obvious where the tile will land.
  tile.addEventListener('dragstart', (e) => {
    tile.classList.add('dragging');
    e.dataTransfer.setData('text/x-clip-id', clip.id);
    e.dataTransfer.effectAllowed = 'move';
  });
  tile.addEventListener('dragend', () => {
    tile.classList.remove('dragging');
    clearDropPlaceholder();
  });

  // Mount.
  const emptyMsg = clipStripEl.querySelector('.clip-strip-empty');
  if (emptyMsg) emptyMsg.remove();
  clipStripEl.appendChild(tile);

  // Auto-activate the first clip so user sees the preview immediately.
  if (!activeClip) setActive(clip);
  updateTotals();
  updateDefaultOutputName();
  // Restored clips already carry their cuts; make sure the mini-timeline shows
  // them even before the <video> metadata loads.
  if (restore) updateTileMini(clip);
  // A new clip (or a restored one) is a history-worthy state change, but during
  // a bulk restore we snapshot once at the end instead of per-clip.
  if (!restoringHistory) pushHistory();
  return clip;
}

/* ---------- drop placeholder (reorder) ---------- */
let dropPlaceholder = null;

function ensurePlaceholder() {
  if (dropPlaceholder) return dropPlaceholder;
  dropPlaceholder = document.createElement('div');
  dropPlaceholder.className = 'drop-placeholder';
  return dropPlaceholder;
}
function clearDropPlaceholder() {
  if (dropPlaceholder && dropPlaceholder.parentNode) {
    dropPlaceholder.parentNode.removeChild(dropPlaceholder);
  }
}
function tilesInOrder() {
  return [...clipStripEl.querySelectorAll('.clip-tile')];
}
function positionPlaceholderAt(clientX) {
  const ph = ensurePlaceholder();
  const tiles = tilesInOrder().filter((t) => !t.classList.contains('dragging'));
  if (!tiles.length) {
    if (dropPlaceholder.parentNode !== clipStripEl) clipStripEl.appendChild(ph);
    return;
  }
  let insertBefore = null;
  for (const t of tiles) {
    const r = t.getBoundingClientRect();
    if (clientX < r.left + r.width / 2) { insertBefore = t; break; }
  }
  if (insertBefore) {
    if (ph.nextElementSibling !== insertBefore || ph.parentNode !== clipStripEl) {
      clipStripEl.insertBefore(ph, insertBefore);
    }
  } else {
    if (ph.parentNode !== clipStripEl || ph !== clipStripEl.lastElementChild) {
      clipStripEl.appendChild(ph);
    }
  }
}

clipStripEl.addEventListener('dragover', (e) => {
  if (!e.dataTransfer.types.includes('text/x-clip-id')) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  positionPlaceholderAt(e.clientX);
});
clipStripEl.addEventListener('drop', (e) => {
  const srcId = e.dataTransfer.getData('text/x-clip-id');
  if (!srcId) return;
  e.preventDefault();
  const src = clips.find((c) => c.id === srcId);
  if (!src) { clearDropPlaceholder(); return; }

  // Compute target index from placeholder position.
  const ph = dropPlaceholder;
  let dstIdx = clips.length;
  if (ph && ph.parentNode === clipStripEl) {
    const siblings = [...clipStripEl.children].filter((n) => n !== ph);
    const phIdx = [...clipStripEl.children].indexOf(ph);
    // Count how many non-placeholder nodes precede the placeholder.
    let count = 0;
    for (let i = 0; i < phIdx; i++) {
      if (clipStripEl.children[i] !== ph) count++;
    }
    dstIdx = count;
  }
  clearDropPlaceholder();
  reorderClip(src, dstIdx);
});
clipStripEl.addEventListener('dragleave', (e) => {
  // Only clear when the cursor truly leaves the strip container.
  if (!clipStripEl.contains(e.relatedTarget)) clearDropPlaceholder();
});

/* ---------- wheel → horizontal scroll on clip strip ---------- */
clipStripEl.addEventListener('wheel', (e) => {
  // Convert any vertical wheel delta into a horizontal scroll. Holding
  // Shift (which browsers already translate to horizontal) is kept as-is.
  if (e.deltaY === 0 && e.deltaX === 0) return;
  const delta = Math.abs(e.deltaY) > Math.abs(e.deltaX) ? e.deltaY : e.deltaX;
  if (delta === 0) return;
  // Only consume the event if there's actually horizontal room to scroll.
  const maxScroll = clipStripEl.scrollWidth - clipStripEl.clientWidth;
  if (maxScroll <= 0) return;
  e.preventDefault();
  clipStripEl.scrollLeft += delta;
}, { passive: false });

function moveClip(clip, delta) {
  const i = clips.indexOf(clip);
  const j = i + delta;
  if (j < 0 || j >= clips.length) return;
  [clips[i], clips[j]] = [clips[j], clips[i]];
  // Re-append in new order.
  for (const c of clips) clipStripEl.appendChild(c.tile);
  pushHistory();
}

function reorderClip(clip, dstIdx) {
  const i = clips.indexOf(clip);
  if (i < 0) return;
  clips.splice(i, 1);
  const adj = dstIdx > i ? dstIdx - 1 : dstIdx;
  const clamped = Math.max(0, Math.min(clips.length, adj));
  clips.splice(clamped, 0, clip);
  for (const c of clips) clipStripEl.appendChild(c.tile);
  pushHistory();
}

function removeClip(clip) {
  const i = clips.indexOf(clip);
  if (i < 0) return;
  clips.splice(i, 1);
  clip.tile.remove();
  if (activeClip === clip) setActive(clips[Math.min(i, clips.length - 1)] || null);
  if (!clips.length) {
    const msg = document.createElement('div');
    msg.className = 'clip-strip-empty';
    msg.innerHTML = 'No clips yet — drop files anywhere, or click <em>+ Add files</em>';
    clipStripEl.appendChild(msg);
  }
  updateTotals();
  updateDefaultOutputName();
  pushHistory();
}

/* ---------- paste-path add ---------- */
addBtn.onclick = async () => {
  const lines = pathInput.value
    .split(/\r?\n/).map((l) => l.trim().replace(/^"|"$/g, '')).filter(Boolean);
  if (!lines.length) return;
  addBtn.disabled = true;
  const failures = [];
  for (const p of lines) {
    try {
      const info = await postJson('/api/register', { path: p });
      addClip(info);
    } catch (e) { failures.push(`${p} — ${e.message}`); }
  }
  pathInput.value = '';
  addBtn.disabled = false;
  if (failures.length) alert('Some files could not be added:\n' + failures.join('\n'));
};

/* ---------- chunked upload ---------- */
const CHUNK_SIZE = 4 * 1024 * 1024;
const MAX_RETRIES = 3;

function newUploadItem(file) {
  const row = document.createElement('div');
  row.className = 'upload-item';
  row.innerHTML = `
    <span class="name"></span>
    <span class="status">queued</span>
    <button class="cancel">cancel</button>
    <div class="bar"><div></div></div>
  `;
  row.querySelector('.name').textContent = `${file.name} (${fmtBytes(file.size)})`;
  uploadQueueEl.appendChild(row);
  return {
    row,
    statusEl: row.querySelector('.status'),
    cancelBtn: row.querySelector('.cancel'),
    barEl: row.querySelector('.bar > div'),
    setProgress(loaded, total) {
      const pct = total ? (loaded / total) * 100 : 0;
      this.barEl.style.width = `${pct.toFixed(1)}%`;
      this.statusEl.textContent = `${pct.toFixed(0)}% — ${fmtBytes(loaded)}/${fmtBytes(total)}`;
    },
    setStatus(text) { this.statusEl.textContent = text; },
    setDone() {
      this.row.classList.add('done');
      this.cancelBtn.remove();
      this.statusEl.textContent = 'done';
      this.barEl.style.width = '100%';
      setTimeout(() => this.row.remove(), 3500);
    },
    setError(msg) {
      this.row.classList.add('err');
      this.cancelBtn.textContent = '×';
      this.cancelBtn.title = 'dismiss';
      this.cancelBtn.onclick = () => this.row.remove();
      this.statusEl.textContent = `error: ${msg}`;
      // Auto-dismiss after 8s unless the user is reading it (hover pauses).
      let t = setTimeout(() => this.row.remove(), 8000);
      this.row.addEventListener('mouseenter', () => { clearTimeout(t); t = null; });
      this.row.addEventListener('mouseleave', () => {
        if (t == null) t = setTimeout(() => this.row.remove(), 3000);
      });
    },
  };
}

function sendChunk(uid, seq, blob) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/upload/chunk?uid=${uid}&seq=${seq}`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else {
        let msg = `HTTP ${xhr.status}`;
        try { msg = JSON.parse(xhr.responseText).error || msg; } catch (_) {}
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error('network'));
    xhr.ontimeout = () => reject(new Error('timeout'));
    xhr.send(blob);
  });
}

async function uploadOne(file, ui) {
  ui.setStatus('starting...');
  const { uid } = await postJson('/api/upload/begin', { name: file.name });
  let cancelled = false;
  ui.cancelBtn.onclick = async () => {
    cancelled = true;
    ui.cancelBtn.disabled = true;
    try { await postJson(`/api/upload/abort?uid=${uid}`); } catch (_) {}
    ui.setError('cancelled');
    setTimeout(() => ui.row.remove(), 1500);
  };
  const total = file.size;
  let uploaded = 0, seq = 0;
  for (let off = 0; off < total; off += CHUNK_SIZE) {
    if (cancelled) return null;
    const end = Math.min(off + CHUNK_SIZE, total);
    const blob = file.slice(off, end);
    let attempt = 0;
    while (true) {
      try { await sendChunk(uid, seq, blob); break; }
      catch (e) {
        attempt++;
        if (attempt > MAX_RETRIES) throw new Error(`chunk ${seq} failed: ${e.message}`);
        ui.setStatus(`retrying chunk ${seq} (${attempt}/${MAX_RETRIES})...`);
        await new Promise((r) => setTimeout(r, 500 * attempt));
      }
    }
    seq++; uploaded = end;
    ui.setProgress(uploaded, total);
  }
  if (cancelled) return null;
  ui.setStatus('finalizing...');
  const info = await postJson(`/api/upload/finish?uid=${uid}`);
  ui.setDone();
  return info;
}

async function addFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  for (const file of files) {
    const ui = newUploadItem(file);
    try {
      const info = await uploadOne(file, ui);
      if (info) addClip(info);
    } catch (e) { ui.setError(e.message); }
  }
}

async function openNativePicker(btn) {
  btn.disabled = true;
  try {
    const r = await fetch('/api/pick-files', { method: 'POST' });
    const j = await r.json();
    if (j.error) { alert('Picker failed: ' + j.error); return; }
    const paths = j.files || [];
    if (!paths.length) return;
    const failures = [];
    for (const p of paths) {
      try {
        const info = await postJson('/api/register', { path: p });
        addClip(info);
      } catch (e) { failures.push(`${p} — ${e.message}`); }
    }
    if (failures.length) alert('Some files could not be added:\n' + failures.join('\n'));
  } catch (e) {
    alert('Picker failed: ' + e.message);
  } finally {
    btn.disabled = false;
  }
}
if (pickFilesBtn) pickFilesBtn.onclick = () => openNativePicker(pickFilesBtn);
if (pickFilesBtnInline) pickFilesBtnInline.onclick = () => openNativePicker(pickFilesBtnInline);

/* ---------- drag & drop anywhere ---------- */
let dragDepth = 0;
window.addEventListener('dragenter', (e) => {
  if (!e.dataTransfer) return;
  // File drops only; ignore tile reorder drags (which use text/x-clip-id).
  const types = e.dataTransfer.types || [];
  if (!types.includes('Files')) return;
  dragDepth++;
  dropOverlay.classList.add('show');
  e.preventDefault();
});
window.addEventListener('dragover', (e) => {
  const types = e.dataTransfer?.types || [];
  if (types.includes('Files')) e.preventDefault();
});
window.addEventListener('dragleave', (e) => {
  if (!e.dataTransfer) return;
  const types = e.dataTransfer.types || [];
  if (!types.includes('Files')) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) dropOverlay.classList.remove('show');
});
window.addEventListener('drop', async (e) => {
  const types = e.dataTransfer?.types || [];
  if (!types.includes('Files')) return;
  e.preventDefault();
  dragDepth = 0;
  dropOverlay.classList.remove('show');
  const files = e.dataTransfer.files ? [...e.dataTransfer.files] : [];
  if (!files.length) return;

  // Try to register each dropped file by its on-disk path first — that
  // skips the upload entirely. Browsers don't expose paths for security,
  // BUT Windows Explorer drops populate `text/plain` with the full path(s)
  // for us; and Chromium also exposes `file.path` in some contexts. If
  // neither works, fall back to uploading.
  const textPath = e.dataTransfer.getData('text/plain') || '';
  const pathLines = textPath.split(/\r?\n/).map(s => s.trim()).filter(Boolean);

  const viaPath = [];
  const viaUpload = [];
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    // Candidate paths, in order of preference.
    const candidate =
      (f.path && typeof f.path === 'string') ? f.path :
      (pathLines[i] && /[\\/]/.test(pathLines[i]) ? pathLines[i] : null);
    if (candidate) {
      try {
        const info = await postJson('/api/register', { path: candidate });
        addClip(info);
        viaPath.push(f.name);
        continue;
      } catch (_) { /* fall through to upload */ }
    }
    viaUpload.push(f);
  }
  if (viaUpload.length) addFiles(viaUpload);
});

/* ---------- output path ---------- */
async function initDefaultOutputDir() {
  try {
    const j = await (await fetch('/api/default-output-dir')).json();
    if (j.folder && !outputDir.value) outputDir.value = j.folder;
  } catch (_) { /* ignore */ }
}
function updateDefaultOutputName() {
  if (outputName.value && outputName.dataset.userSet === '1') return;
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}_${pad(d.getHours())}-${pad(d.getMinutes())}`;
  outputName.value = `edit_${stamp}.mp4`;
}
outputName.addEventListener('input', () => { outputName.dataset.userSet = '1'; });

pickFolderBtn.onclick = async () => {
  pickFolderBtn.disabled = true;
  try {
    const j = await (await fetch('/api/pick-folder', { method: 'POST' })).json();
    if (j.folder) outputDir.value = j.folder;
  } catch (e) {
    alert('Folder picker failed: ' + e.message);
  } finally {
    pickFolderBtn.disabled = false;
  }
};

/* ---------- export ---------- */
function showToast(cls) {
  jobStatus.className = `job-toast show ${cls || ''}`;
}
function hideToast() {
  jobStatus.className = 'job-toast';
}
const jobRevealBtn = $('jobRevealBtn');
const jobBarWrap = jobBar.parentElement;
function setJobUi({ msg, pct, meta, phase }) {
  if (msg != null) jobMsg.innerHTML = msg;
  if (pct != null) jobBar.style.width = `${Math.max(0, Math.min(100, pct)).toFixed(1)}%`;
  if (meta != null) jobMeta.textContent = meta;
  if (phase === 'running') {
    jobBarWrap.style.display = '';
    jobCancelBtn.hidden = false;
    jobDismissBtn.hidden = true;
    jobRevealBtn.hidden = true;
  } else if (phase === 'done' || phase === 'error') {
    // The progress bar is only meaningful while running.
    jobBarWrap.style.display = 'none';
    jobCancelBtn.hidden = true;
    jobDismissBtn.hidden = false;
    jobRevealBtn.hidden = (phase !== 'done');
  }
}
jobDismissBtn.onclick = hideToast;

/* "Open folder" on a finished export reveals the file in the OS browser. */
let lastOutputPath = null;
jobRevealBtn.onclick = async () => {
  if (!lastOutputPath) return;
  try { await postJson('/api/reveal', { path: lastOutputPath }); }
  catch (e) { alert('Could not open folder: ' + e.message); }
};

let currentJobId = null;
jobCancelBtn.onclick = async () => {
  if (!currentJobId) return;
  jobCancelBtn.disabled = true;
  try { await postJson(`/api/job/${currentJobId}/cancel`); } catch (_) {}
};

/* Clicking Export validates inputs, then opens the settings dialog. The actual
 * job is fired by runExport() when the user confirms in the modal. */
exportBtn.onclick = () => {
  if (!clips.length) { alert('Add at least one clip first.'); return; }
  const gone = clips.filter((c) => c.missing);
  if (gone.length) {
    alert(
      `${gone.length} clip${gone.length === 1 ? '' : 's'} cannot be found on disk:\n` +
      gone.map((c) => `• ${c.name}`).join('\n') +
      `\n\nRemove or restore them before exporting.`,
    );
    return;
  }
  const payload = clips.map((c) => ({ path: c.path, keep: buildKeepRanges(c) }));
  if (payload.every((c) => c.keep.length === 0)) {
    alert('All clips are fully cut out — nothing to export.');
    return;
  }
  openExportModal();
};

/* ---------- export settings dialog ---------- */
const moreOptions = $('moreOptions');
function openExportModal() {
  updateExportNote();
  // Reveal "More options" up front if any advanced setting is non-default, so
  // a changed resolution / disabled toggle is never hidden from the user.
  if (moreOptions) {
    const advanced = resolutionSelect.value !== 'source'
      || !accurateMode.checked || !keepAudio.checked;
    moreOptions.open = advanced;
  }
  exportModal.hidden = false;
  // Focus the folder field if empty (the one thing that must be filled), else
  // the confirm button.
  if (!outputDir.value.trim()) outputDir.focus();
  else exportConfirmBtn.focus();
}
function closeExportModal() { exportModal.hidden = true; }

/* Build the final output path from the folder + filename fields (or null if
 * folder is empty). Also normalizes a missing/incorrect .mp4 extension. */
function buildOutputPath() {
  const dir = outputDir.value.trim().replace(/^"|"$/g, '');
  if (!dir) return null;
  let name = outputName.value.trim().replace(/^"|"$/g, '');
  if (!name) name = `edit_${Date.now()}.mp4`;
  if (!/\.mp4$/i.test(name)) name += '.mp4';
  return dir.replace(/[\\/]+$/, '') + '\\' + name;
}

function updateExportNote() {
  // Summarize what will happen so the choice is legible.
  const accurate = accurateMode.checked;
  const res = resolutionSelect.value;
  const bits = [];
  let warn = false;
  if (accurate) {
    bits.push('Re-encodes for exact cuts — slower, but frame-accurate and perfectly in sync.');
  } else {
    bits.push('Fast: near-instant (no re-encode), but cuts snap to keyframes.');
  }
  if (res !== 'source' && !accurate) {
    bits.push('Resolution change requires re-encoding — Accurate will be used.');
    warn = true;
  }
  exportModalNote.textContent = bits.join(' ');
  exportModalNote.classList.toggle('warn', warn);
  // Live destination summary so the user sees where the file lands.
  const out = buildOutputPath();
  exportDest.textContent = out ? `→ ${out}` : 'Choose a folder to save to.';
}
accurateMode.addEventListener('change', updateExportNote);
resolutionSelect.addEventListener('change', updateExportNote);
outputDir.addEventListener('input', updateExportNote);
outputName.addEventListener('input', updateExportNote);
exportModalClose.onclick = closeExportModal;
exportCancelBtn.onclick = closeExportModal;
exportModal.addEventListener('click', (e) => {
  if (e.target === exportModal) closeExportModal();
});
function confirmExport() {
  if (!buildOutputPath()) { outputDir.focus(); updateExportNote(); return; }
  closeExportModal();
  runExport();
}
exportConfirmBtn.onclick = confirmExport;

/* ---------- keyboard shortcuts dialog ---------- */
function openHelpModal() { helpModal.hidden = false; helpModalOk.focus(); }
function closeHelpModal() { helpModal.hidden = true; }
if (helpBtn) helpBtn.onclick = openHelpModal;
if (helpModalClose) helpModalClose.onclick = closeHelpModal;
if (helpModalOk) helpModalOk.onclick = closeHelpModal;
if (helpModal) helpModal.addEventListener('click', (e) => {
  if (e.target === helpModal) closeHelpModal();
});

/* Remember export settings across sessions. */
const EXPORT_SETTINGS_KEY = 'simple-editor-export-settings-v1';
function saveExportSettings() {
  try {
    localStorage.setItem(EXPORT_SETTINGS_KEY, JSON.stringify({
      quality: qualitySelect.value,
      resolution: resolutionSelect.value,
      accurate: accurateMode.checked,
      keepAudio: keepAudio.checked,
    }));
  } catch (_) {}
}
function loadExportSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(EXPORT_SETTINGS_KEY) || 'null');
    if (!s) return;
    if (s.quality) qualitySelect.value = s.quality;
    if (s.resolution) resolutionSelect.value = s.resolution;
    if (typeof s.accurate === 'boolean') accurateMode.checked = s.accurate;
    if (typeof s.keepAudio === 'boolean') keepAudio.checked = s.keepAudio;
  } catch (_) {}
}
for (const el of [qualitySelect, resolutionSelect, accurateMode, keepAudio]) {
  el.addEventListener('change', saveExportSettings);
}
loadExportSettings();

async function runExport() {
  const out = buildOutputPath();
  if (!out) { alert('Choose an output folder.'); return; }
  lastOutputPath = out;

  const payload = clips.map((c) => ({ path: c.path, keep: buildKeepRanges(c) }));
  if (payload.every((c) => c.keep.length === 0)) {
    alert('All clips are fully cut out — nothing to export.');
    return;
  }

  exportBtn.disabled = true;
  jobCancelBtn.disabled = false;
  setJobUi({ msg: 'Submitting export...', pct: 0, meta: '', phase: 'running' });
  showToast('run');

  try {
    const { id } = await postJson('/api/export', {
      clips: payload,
      output: out,
      accurate: !!accurateMode.checked,
      keep_audio: !!keepAudio.checked,
      quality: qualitySelect.value,
      resolution: resolutionSelect.value,
    });
    currentJobId = id;
    // The final concat-copy phase can write ~18 GB to disk and briefly starve
    // the single-threaded HTTP server, so /api/job polls can time out or throw
    // "Failed to fetch". Tolerate transient failures: keep the UI on the last
    // known state, back off, and only give up after a long stretch of silence.
    let consecutiveFailures = 0;
    const MAX_CONSECUTIVE_FAILURES = 60; // ~2+ min of total silence before we bail
    let lastMsg = 'Working...';
    while (true) {
      const waitMs = consecutiveFailures > 0
        ? Math.min(5000, 1000 + consecutiveFailures * 500)
        : 1000;
      await new Promise((r) => setTimeout(r, waitMs));
      let s;
      try {
        const resp = await fetch('/api/job/' + id);
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        s = await resp.json();
        consecutiveFailures = 0;
      } catch (err) {
        consecutiveFailures++;
        if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
          throw new Error('Lost contact with server: ' + err.message);
        }
        setJobUi({
          msg: escHtml(lastMsg + ' (server busy, retrying...)'),
          phase: 'running',
        });
        continue;
      }
      if (s.status === 'done') {
        const full = s.output || lastOutputPath || '';
        const fname = full.split(/[\\/]/).pop() || full;
        const folder = full.slice(0, full.length - fname.length).replace(/[\\/]+$/, '');
        setJobUi({
          msg: `<b>Export complete</b><span class="job-file" title="${escHtml(full)}">${escHtml(fname)}</span>`,
          pct: 100,
          meta: [folder, s.encoder ? `encoder: ${s.encoder}` : '']
            .filter(Boolean).join('  ·  '),
          phase: 'done',
        });
        showToast('ok');
        break;
      }
      if (s.status === 'error') {
        setJobUi({
          msg: escHtml('Export error: ' + (s.message || 'unknown')),
          pct: 0,
          meta: '',
          phase: 'error',
        });
        showToast('err');
        break;
      }
      lastMsg = s.message || 'Working...';
      setJobUi({
        msg: escHtml(lastMsg),
        pct: typeof s.progress === 'number' ? s.progress : undefined,
        meta: s.encoder ? `encoder: ${s.encoder}` : '',
        phase: 'running',
      });
      showToast('run');
    }
  } catch (e) {
    setJobUi({ msg: escHtml('Error: ' + e.message), pct: 0, meta: '', phase: 'error' });
    showToast('err');
  } finally {
    currentJobId = null;
    exportBtn.disabled = false;
  }
}

/* ---------- missing-file watcher ----------
 * Every 3s, ask the server which registered ids still resolve to real files
 * and flag any tile whose source was deleted or moved. Flagged clips are
 * excluded from export (their `missing` flag skips them in buildKeepRanges
 * consumers). */
async function pollMissingFiles() {
  if (!clips.length) return;
  try {
    const r = await fetch('/api/check-paths');
    const map = await r.json();
    let anyChanged = false;
    for (const c of clips) {
      const info = map[c.id];
      const missing = info ? !info.exists : true;
      if (!!c.missing !== missing) {
        c.missing = missing;
        c.tile.classList.toggle('missing', missing);
        let badge = c.tile.querySelector('.tile-missing-badge');
        if (missing) {
          if (!badge) {
            badge = document.createElement('div');
            badge.className = 'tile-missing-badge';
            badge.textContent = '⚠ file missing';
            c.tile.querySelector('.tile-thumb').appendChild(badge);
          }
        } else if (badge) {
          badge.remove();
        }
        anyChanged = true;
      }
    }
    if (anyChanged) updateTotals();
  } catch (_) { /* ignore transient errors */ }
}
setInterval(pollMissingFiles, 3000);

/* ---------- restore previous session ----------
 * Clip ids are assigned fresh by the server each run, so on restore we
 * re-register each saved file by its on-disk path to get a new id, then
 * re-attach the saved cuts. Files that no longer exist are skipped (and
 * reported). Runs before the baseline history snapshot so the restored state
 * becomes the undo baseline. */
async function restoreSession() {
  const saved = loadSavedSession();
  if (!saved) return false;
  const when = new Date(saved.savedAt || Date.now());
  const ok = confirm(
    `Restore your previous session?\n\n` +
    `${saved.clips.length} clip${saved.clips.length === 1 ? '' : 's'} from ` +
    `${when.toLocaleString()}.\n\n` +
    `OK = restore · Cancel = start fresh (your saved session is kept until you edit).`,
  );
  if (!ok) return false;

  restoringHistory = true;
  const failures = [];
  let activeTarget = null;
  try {
    for (const sc of saved.clips) {
      try {
        const info = await postJson('/api/register', { path: sc.path });
        const clip = addClip(info, { cuts: sc.cuts, duration: sc.duration });
        if (saved.activeId && sc.id === saved.activeId) activeTarget = clip;
      } catch (e) {
        failures.push(`${sc.name || sc.path} — ${e.message}`);
      }
    }
  } finally {
    restoringHistory = false;
  }
  if (activeTarget) setActive(activeTarget);
  updateTotals();
  // Establish the restored state as the undo baseline.
  undoStack = [snapshotState()];
  redoStack = [];
  updateHistoryButtons();
  saveSession();
  if (failures.length) {
    alert(
      `Some clips could not be restored (moved or deleted):\n` +
      failures.join('\n'),
    );
  }
  return true;
}

/* ---------- init ---------- */
checkFfmpeg();
initDefaultOutputDir();
updateDefaultOutputName();
setActive(null);
// Seed the history baseline, then attempt to restore a saved session.
undoStack = [snapshotState()];
updateHistoryButtons();
restoreSession();
