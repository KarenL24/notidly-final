const DEV_PORTS = new Set(['3000','3001','5500','5501','8080','4000']);
const API = (window.location.protocol === 'file:' || DEV_PORTS.has(window.location.port))
  ? 'http://localhost:8765'
  : window.location.origin;

// ── State ───────────────────────────────────────────────────────────────────
let osmd = null;
let combinedXML = null;
let parts = [];
let bpm = 120;
let speedMultiplier = 1;
let metronomeLoop = null;
let metronomeSynth = null;
let isMetronomeOn = false;
let isPlaying = false;
let playbackSynth = null;
let playheadRAF = null;
let totalDurationSec = 0;
const mutedParts = new Set();

let inputMode = 'record';   // 'record' | 'upload'
let mediaRecorder = null;
let recordedChunks = [];
let recordingTimer = null;
let countdownInterval = null;

// ── Add Part panel ──────────────────────────────────────────────────────────
function openAddPartPanel() {
  switchMode('edit');
  document.getElementById('addPartBtn').style.display = 'none';
  document.getElementById('tracksList').style.flex = '0 0 auto';
  document.getElementById('addPartPanel').classList.add('visible');
  document.getElementById('partName').value = '';
  document.getElementById('partKey').value = 'auto';
  document.getElementById('partTimeSig').value = '';
  document.getElementById('partClef').value = 'Treble';
  document.getElementById('recordingState').classList.remove('visible');
  document.getElementById('createBtn').disabled = false;
  selectInputMode('record');
}

function closeAddPartPanel() {
  switchMode('rehearsal');
  document.getElementById('addPartPanel').classList.remove('visible');
  document.getElementById('addPartBtn').style.display = '';
  document.getElementById('tracksList').style.flex = '';
  stopRecording(true);
}

function selectInputMode(mode) {
  inputMode = mode;
  document.getElementById('optRecord').classList.toggle('selected', mode === 'record');
  document.getElementById('optUpload').classList.toggle('selected', mode === 'upload');
}

// ── Create Part ─────────────────────────────────────────────────────────────
function createPart() {
  if (inputMode === 'upload') {
    document.getElementById('fileInput').click();
  } else {
    beginRecording();
  }
}

// ── File upload ─────────────────────────────────────────────────────────────
function handleFileChosen(e) {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = '';
  const name = document.getElementById('partName').value.trim()
    || file.name.replace(/\.(mp3|wav|m4a|ogg|flac|webm)$/i, '');
  transcribeAndAdd(file, name);
}

// ── Live recording ──────────────────────────────────────────────────────────
async function beginRecording() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    alert('Microphone access denied. Please allow microphone access and try again.');
    return;
  }

  recordedChunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = e => { if (e.data.size > 0) recordedChunks.push(e.data); };
  mediaRecorder.onstop = () => {
    stream.getTracks().forEach(t => t.stop());
    clearInterval(countdownInterval);
    clearTimeout(recordingTimer);
    document.getElementById('recordingState').classList.remove('visible');
    document.getElementById('createBtn').disabled = false;
    const blob = new Blob(recordedChunks, { type: 'audio/webm' });
    const name = document.getElementById('partName').value.trim() || 'Recording';
    transcribeAndAdd(blob, name);
  };

  mediaRecorder.start();

  let secs = 30;
  document.getElementById('recordCountdown').textContent = secs;
  document.getElementById('recordingState').classList.add('visible');
  document.getElementById('createBtn').disabled = true;

  countdownInterval = setInterval(() => {
    secs--;
    document.getElementById('recordCountdown').textContent = secs;
    if (secs <= 0) stopRecording();
  }, 1000);

  recordingTimer = setTimeout(stopRecording, 30000);
}

function stopRecording(abort = false) {
  clearInterval(countdownInterval);
  clearTimeout(recordingTimer);
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    if (abort) {
      mediaRecorder.ondataavailable = null;
      mediaRecorder.onstop = null;
    }
    mediaRecorder.stop();
  }
  if (abort) {
    document.getElementById('recordingState').classList.remove('visible');
    document.getElementById('createBtn').disabled = false;
  }
}

// ── MusicXML overrides ──────────────────────────────────────────────────────
const KEY_FIFTHS = {
  'C':0,'G':1,'D':2,'A':3,'E':4,'B':5,'F#':6,
  'F':-1,'Bb':-2,'Eb':-3,'Ab':-4,
  'Am':0,'Em':1,'Bm':2,'F#m':3,'Dm':-1,'Gm':-2,'Cm':-3,
};

function overrideKey(xml, keyVal) {
  const isMinor = keyVal.endsWith('m');
  const fifths = KEY_FIFTHS[keyVal] ?? 0;
  return xml
    .replace(/<fifths>-?\d+<\/fifths>/g, `<fifths>${fifths}</fifths>`)
    .replace(/<mode>\w+<\/mode>/g, `<mode>${isMinor ? 'minor' : 'major'}</mode>`);
}

const CLEF_MAP = {
  'Treble': { sign: 'G', line: '2' },
  'Bass':   { sign: 'F', line: '4' },
  'Alto':   { sign: 'C', line: '3' },
  'Tenor':  { sign: 'C', line: '4' },
};

function overrideClef(xml, clefName) {
  const clef = CLEF_MAP[clefName];
  if (!clef) return xml;
  return xml.replace(/<clef[^>]*>[\s\S]*?<\/clef>/g, match =>
    match
      .replace(/<sign>[A-Za-z]+<\/sign>/, `<sign>${clef.sign}</sign>`)
      .replace(/<line>\d<\/line>/, `<line>${clef.line}</line>`)
  );
}

function overrideTimeSig(xml, timeSig) {
  const parts = timeSig.split('/');
  if (parts.length !== 2) return xml;
  const [beats, beatType] = parts;
  return xml
    .replace(/<beats>\d+<\/beats>/g, `<beats>${beats}</beats>`)
    .replace(/<beat-type>\d+<\/beat-type>/g, `<beat-type>${beatType}</beat-type>`);
}

// ── Transcribe + merge ──────────────────────────────────────────────────────
async function transcribeAndAdd(fileOrBlob, partName) {
  const timeSig = document.getElementById('partTimeSig')?.value || '';
  closeAddPartPanel();
  setLoading(true, `Transcribing "${partName}"…`);

  const form = new FormData();
  const fileName = fileOrBlob instanceof File
    ? fileOrBlob.name
    : `${partName.replace(/\s+/g, '_')}.webm`;
  form.append('file', fileOrBlob, fileName);
  if (timeSig) form.append('time_signature', timeSig);

  try {
    const res = await fetch(`${API}/transcribe`, { method: 'POST', body: form });
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();

    let xml = result.musicxml;
    if (timeSig) xml = overrideTimeSig(xml, timeSig);

    const keyVal = document.getElementById('partKey')?.value || 'auto';
    if (keyVal !== 'auto') xml = overrideKey(xml, keyVal);

    const clefVal = document.getElementById('partClef')?.value || 'Treble';
    xml = overrideClef(xml, clefVal);

    parts.push({
      name: partName,
      xml,
      bpm: result.bpm,
      noteCount: result.note_count,
      duration: result.duration,
    });

    if (parts.length === 1) {
      combinedXML = xml;
      bpm = Math.round(result.bpm);
      document.getElementById('bpmInput').value = bpm;
      document.getElementById('titleInput').value = partName;
    } else {
      combinedXML = mergePart(combinedXML, xml, partName, parts.length);
    }

    await renderScore(combinedXML);
    renderTracks();
    document.getElementById('playBtn').disabled = false;

  } catch (err) {
    alert(`Transcription failed: ${err.message}\n\nMake sure the backend is running at ${API}`);
  } finally {
    setLoading(false);
  }
}

// ── MusicXML merging ────────────────────────────────────────────────────────
function mergePart(baseXML, newXML, partName, partNumber) {
  const parser = new DOMParser();
  const baseDoc = parser.parseFromString(baseXML, 'text/xml');
  const newDoc  = parser.parseFromString(newXML, 'text/xml');
  const newId   = `P${partNumber}`;

  const newScorePart = newDoc.querySelector('score-part').cloneNode(true);
  const newPart      = newDoc.querySelector('part').cloneNode(true);
  newScorePart.setAttribute('id', newId);
  newPart.setAttribute('id', newId);
  const nameEl = newScorePart.querySelector('part-name');
  if (nameEl) nameEl.textContent = partName;

  baseDoc.querySelector('part-list').appendChild(baseDoc.importNode(newScorePart, true));
  baseDoc.querySelector('score-partwise').appendChild(baseDoc.importNode(newPart, true));
  return new XMLSerializer().serializeToString(baseDoc);
}

// ── Key display ─────────────────────────────────────────────────────────────
function getKeyFromXML(xml) {
  const doc = new DOMParser().parseFromString(xml, 'text/xml');
  const fifths = parseInt(doc.querySelector('fifths')?.textContent || '0');
  const mode = doc.querySelector('mode')?.textContent || 'major';
  const names = { 0:'C',1:'G',2:'D',3:'A',4:'E',5:'B',6:'F#','-1':'F','-2':'B♭','-3':'E♭','-4':'A♭','-5':'D♭','-6':'G♭' };
  const tonic = names[fifths] || 'C';
  return `${tonic} ${mode === 'minor' ? 'Minor' : 'Major'}`;
}

// ── OSMD ────────────────────────────────────────────────────────────────────
async function renderScore(xml) {
  document.getElementById('uploadPrompt').style.display = 'none';
  document.getElementById('keyDisplay').textContent = getKeyFromXML(xml);
  if (!osmd) {
    osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay('osmd-container', {
      autoResize: true,
      drawTitle: false,
      drawSubtitle: false,
      drawComposer: false,
      backend: 'svg',
    });
  }
  await osmd.load(xml);
  osmd.render();
  requestAnimationFrame(() => styleOSMDCursor());
}

// ── Tracks panel ─────────────────────────────────────────────────────────────
function renderTracks() {
  const list = document.getElementById('tracksList');
  list.innerHTML = '';
  parts.forEach((part, i) => {
    const el = document.createElement('div');
    el.className = 'track-item' + (i === parts.length - 1 ? ' active' : '');
    el.innerHTML = `
      <div class="track-icon">🎙️</div>
      <div class="track-info">
        <div class="track-name">${part.name}</div>
        <div class="track-meta">Recorded · ${fmtDur(part.duration)} · ${part.noteCount} notes</div>
      </div>
      <div class="track-btns">
        <button class="track-btn" id="mute-${i}" title="Mute" onclick="toggleMute(event,${i})">🔇</button>
        <button class="track-btn" title="Solo" onclick="event.stopPropagation()">🎧</button>
      </div>`;
    el.addEventListener('click', () => {
      list.querySelectorAll('.track-item').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
    });
    list.appendChild(el);
  });
}

function fmtDur(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}

function toggleMute(e, i) {
  e.stopPropagation();
  const btn = document.getElementById(`mute-${i}`);
  mutedParts.has(i) ? (mutedParts.delete(i), btn.classList.remove('on')) : (mutedParts.add(i), btn.classList.add('on'));
}

// ── Loading ──────────────────────────────────────────────────────────────────
function setLoading(on, title = 'Transcribing audio…') {
  document.getElementById('loadingTitle').textContent = title;
  document.getElementById('loadingOverlay').classList.toggle('visible', on);
}

// ── BPM / Metronome ──────────────────────────────────────────────────────────
function updateBPM(val) {
  bpm = Math.max(30, Math.min(300, parseInt(val) || 120));
  document.getElementById('bpmInput').value = bpm;
  Tone.getTransport().bpm.value = bpm * speedMultiplier;
}

function updateSpeed(val) {
  speedMultiplier = parseFloat(val);
  Tone.getTransport().bpm.value = bpm * speedMultiplier;
}

function toggleMetronomeBtn() {
  isMetronomeOn = !isMetronomeOn;
  const pill = document.getElementById('metronomePill');
  pill.textContent = isMetronomeOn ? 'ON' : 'OFF';
  pill.classList.toggle('on', isMetronomeOn);
  toggleMetronome(isMetronomeOn);
}

function toggleMetronome(on) {
  if (on) {
    Tone.start();
    Tone.getTransport().bpm.value = bpm * speedMultiplier;
    metronomeSynth = new Tone.Synth({
      oscillator: { type: 'triangle' },
      envelope: { attack: 0.001, decay: 0.08, sustain: 0, release: 0.05 },
      volume: -6,
    }).toDestination();
    metronomeLoop = new Tone.Loop(t => metronomeSynth.triggerAttackRelease('C5', '32n', t), '4n').start(0);
    if (!isPlaying) Tone.getTransport().start();
  } else {
    metronomeLoop?.stop().dispose(); metronomeLoop = null;
    metronomeSynth?.dispose(); metronomeSynth = null;
    if (!isPlaying) Tone.getTransport().stop();
  }
}

// ── OSMD cursor playhead ─────────────────────────────────────────────────────
let cursorSchedule = [];
let cursorIdx = 0;

function styleOSMDCursor() {
  if (!osmd?.cursor?.cursorElement) return;
  const el = osmd.cursor.cursorElement;
  el.style.backgroundColor = 'rgba(245, 137, 58, 0.15)';
  el.style.borderLeft = '3px solid #F5893A';
  el.style.zIndex = '5';
  if (el.tagName === 'IMG') {
    el.style.opacity = '0.4';
    el.style.filter = 'sepia(1) saturate(10) hue-rotate(-20deg)';
  }
}

function buildCursorSchedule() {
  // Build from ALL note positions in the MusicXML — including tie-stop segments —
  // because OSMD's internal cursor has one position per note element regardless of ties.
  const doc = new DOMParser().parseFromString(combinedXML, 'text/xml');
  const beats = new Set();
  doc.querySelectorAll('part').forEach((part, pi) => {
    if (mutedParts.has(pi)) return;
    let divisions = 1, beat = 0, lastDur = 0;
    part.querySelectorAll('measure').forEach(m => {
      const d = m.querySelector('divisions');
      if (d) divisions = parseInt(d.textContent);
      m.querySelectorAll('note').forEach(n => {
        const isChord = !!n.querySelector('chord');
        const dur = parseInt(n.querySelector('duration')?.textContent || divisions);
        if (isChord) beat -= lastDur;
        if (!n.querySelector('rest')) beats.add(parseFloat(beat.toFixed(4)));
        lastDur = dur / divisions;
        if (!isChord) beat += dur / divisions;
      });
    });
  });
  cursorSchedule = [...beats].sort((a, b) => a - b);
}

function startCursor(beatSec) {
  if (!osmd?.cursor) return;
  buildCursorSchedule();
  osmd.cursor.reset();
  osmd.cursor.show();
  styleOSMDCursor();
  cursorIdx = 0;

  function frame() {
    const currentBeat = Tone.getTransport().seconds / beatSec;
    while (cursorIdx < cursorSchedule.length - 1 && cursorSchedule[cursorIdx + 1] <= currentBeat + 0.04) {
      osmd.cursor.next();
      cursorIdx++;
    }
    if (isPlaying) playheadRAF = requestAnimationFrame(frame);
  }
  playheadRAF = requestAnimationFrame(frame);
}

function stopCursor() {
  cancelAnimationFrame(playheadRAF);
  if (osmd?.cursor) {
    osmd.cursor.reset();
    osmd.cursor.hide();
  }
}

// ── Playback ─────────────────────────────────────────────────────────────────
function parseNotes(xml) {
  const doc = new DOMParser().parseFromString(xml, 'text/xml');
  const notes = [];
  doc.querySelectorAll('part').forEach((part, pi) => {
    if (mutedParts.has(pi)) return;
    let divisions = 1, beat = 0, lastDur = 0;
    part.querySelectorAll('measure').forEach(m => {
      const d = m.querySelector('divisions');
      if (d) divisions = parseInt(d.textContent);
      m.querySelectorAll('note').forEach(n => {
        const isRest  = !!n.querySelector('rest');
        const isChord = !!n.querySelector('chord');
        const dur = parseInt(n.querySelector('duration')?.textContent || divisions);
        if (isChord) beat -= lastDur;

        if (!isRest) {
          const step   = n.querySelector('step')?.textContent;
          const octave = n.querySelector('octave')?.textContent;
          const alter  = parseInt(n.querySelector('alter')?.textContent || 0);

          // A tie-stop continues a previous note — don't re-attack, just extend duration.
          const tieTypes = [...n.querySelectorAll('tie')].map(t => t.getAttribute('type'));
          const isTieStop = tieTypes.includes('stop') && !tieTypes.includes('start');

          if (step && octave) {
            const acc = alter === 1 ? '#' : alter === -1 ? 'b' : '';
            const noteName = `${step}${acc}${octave}`;
            const durBeats = dur / divisions;

            if (isTieStop && notes.length > 0 && notes[notes.length - 1].n === noteName) {
              notes[notes.length - 1].d += durBeats;
            } else if (!isTieStop) {
              notes.push({ t: beat, n: noteName, d: Math.max(0.05, durBeats - 0.05) });
            }
          }
        }
        lastDur = dur / divisions;
        if (!isChord) beat += dur / divisions;
      });
    });
  });
  return notes;
}

async function togglePlayback() {
  if (!combinedXML) return;
  await Tone.start();
  if (isPlaying) {
    Tone.getTransport().pause();
    document.getElementById('playBtn').textContent = '▶';
    isPlaying = false;
    stopCursor();
    return;
  }
  Tone.getTransport().stop();
  Tone.getTransport().cancel();
  Tone.getTransport().bpm.value = bpm * speedMultiplier;
  playbackSynth?.dispose();
  playbackSynth = new Tone.PolySynth(Tone.Synth, {
    oscillator: { type: 'triangle' },
    envelope: { attack: 0.02, decay: 0.1, sustain: 0.5, release: 0.6 },
    volume: -10,
  }).toDestination();
  const notes = parseNotes(combinedXML);
  const beatSec = 60 / (bpm * speedMultiplier);
  totalDurationSec = notes.length ? Math.max(...notes.map(n => n.t + n.d)) * beatSec : 4;
  notes.forEach(({ t, n, d }) => {
    Tone.getTransport().schedule(time => {
      try { playbackSynth.triggerAttackRelease(n, d * beatSec, time); } catch (_) {}
    }, t * beatSec);
  });
  if (isMetronomeOn) metronomeLoop?.start(0);
  Tone.getTransport().schedule(() => {
    isPlaying = false;
    document.getElementById('playBtn').textContent = '▶';
    playbackSynth?.dispose();
    stopCursor();
    if (!isMetronomeOn) Tone.getTransport().stop();
  }, totalDurationSec + 0.5);
  Tone.getTransport().start();
  document.getElementById('playBtn').textContent = '⏸';
  isPlaying = true;
  startCursor(beatSec);
}

// ── Export modal ─────────────────────────────────────────────────────────────
let selectedExportFormat = 'pdf';
let selectedPartIdx = -1;

function openExportModal() {
  if (!combinedXML) { alert('No score loaded.'); return; }
  populatePartsDropdown();
  document.getElementById('exportModal').classList.add('open');
}

function closeExportModal() {
  document.getElementById('exportModal').classList.remove('open');
  document.getElementById('partsDropdown').classList.remove('open');
  document.getElementById('partsSelectBtn').classList.remove('open');
}

function selectExportFormat(fmt) {
  selectedExportFormat = fmt;
  document.querySelectorAll('.format-card').forEach(c => c.classList.toggle('selected', c.dataset.fmt === fmt));
}

function populatePartsDropdown() {
  const dd = document.getElementById('partsDropdown');
  dd.innerHTML = '';
  dd.appendChild(makeOption('All Parts', -1, selectedPartIdx === -1));
  parts.forEach((p, i) => dd.appendChild(makeOption(p.name, i, selectedPartIdx === i)));
}

function makeOption(label, idx, selected) {
  const el = document.createElement('div');
  el.className = 'custom-select-option' + (selected ? ' selected' : '');
  el.textContent = label;
  el.onclick = () => {
    selectedPartIdx = idx;
    document.getElementById('partsSelectLabel').textContent = label;
    document.querySelectorAll('#partsDropdown .custom-select-option').forEach(o => {
      o.classList.toggle('selected', o === el);
    });
    document.getElementById('partsDropdown').classList.remove('open');
    document.getElementById('partsSelectBtn').classList.remove('open');
  };
  return el;
}

function togglePartsDropdown() {
  document.getElementById('partsDropdown').classList.toggle('open');
  document.getElementById('partsSelectBtn').classList.toggle('open');
}

async function downloadExport() {
  const xml = selectedPartIdx >= 0 && parts[selectedPartIdx]
    ? parts[selectedPartIdx].xml
    : combinedXML;
  const fmt = selectedExportFormat;
  const base = selectedPartIdx >= 0 ? parts[selectedPartIdx].name : 'score';

  if (fmt === 'musicxml') {
    dl(new Blob([xml], { type: 'application/xml' }), `${base}.musicxml`);
    closeExportModal();
    return;
  }

  if (fmt === 'audio') {
    alert('Audio export coming soon.');
    return;
  }

  try {
    const res = await fetch(`${API}/export/${fmt}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ musicxml: xml }),
    });
    if (!res.ok) throw new Error(await res.text());
    const ext = fmt === 'midi' ? 'mid' : 'pdf';
    dl(await res.blob(), `${base}.${ext}`);
    closeExportModal();
  } catch (err) {
    alert(`Export failed: ${err.message}`);
  }
}

function dl(blob, name) {
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: name });
  a.click(); URL.revokeObjectURL(a.href);
}

// ── Mode / sidebar ───────────────────────────────────────────────────────────
function switchMode(mode) {
  document.getElementById('editBtn').classList.toggle('active', mode === 'edit');
  document.getElementById('rehearsalBtn').classList.toggle('active', mode === 'rehearsal');
  document.getElementById('rehearsalToolbar').style.display = mode === 'rehearsal' ? 'flex' : 'none';
  document.getElementById('editToolbar').classList.toggle('visible', mode === 'edit');
  if (mode === 'edit' && combinedXML) {
    document.getElementById('transposeBtnLabel').textContent = getKeyFromXML(combinedXML);
    currentKey = getKeyFromXML(combinedXML).split(' ')[0];
  }
}

function toggleSidebar() {
  const s = document.getElementById('sidebar');
  s.classList.toggle('collapsed');
  document.getElementById('collapseBtn').textContent = s.classList.contains('collapsed') ? '▸' : '▾';
}

// ── Edit toolbar dropdowns ───────────────────────────────────────────────────
function toggleEditDropdown(menuId, btn) {
  const menu = document.getElementById(menuId);
  const isOpen = menu.classList.contains('open');
  document.querySelectorAll('.edit-dropdown-menu').forEach(m => m.classList.remove('open'));
  document.querySelectorAll('.edit-dropdown-btn').forEach(b => b.classList.remove('open'));
  if (!isOpen) { menu.classList.add('open'); btn.classList.add('open'); }
}

document.addEventListener('click', e => {
  if (!e.target.closest('.edit-dropdown')) {
    document.querySelectorAll('.edit-dropdown-menu').forEach(m => m.classList.remove('open'));
    document.querySelectorAll('.edit-dropdown-btn').forEach(b => b.classList.remove('open'));
  }
});

// ── Transpose ────────────────────────────────────────────────────────────────
let currentKey = 'C';

const KEY_SEMITONE = {
  'C':0,'G':7,'D':2,'A':9,'E':4,'B':11,'F#':6,
  'F':5,'Bb':10,'Eb':3,'Ab':8,'Db':1,'Gb':6,
  'Am':9,'Em':4,'Bm':11,'F#m':6,'Dm':2,'Gm':7,'Cm':0,
};
const FLAT_KEY_SET = new Set(['F','Bb','Eb','Ab','Db','Gb','Dm','Gm','Cm']);
const STEP_TO_SEMI = { C:0, D:2, E:4, F:5, G:7, A:9, B:11 };
const SHARP_NAMES  = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const FLAT_NAMES   = ['C','Db','D','Eb','E','F','Gb','G','Ab','A','Bb','B'];

const KEY_DISPLAY = {
  'C':'C Major','G':'G Major','D':'D Major','A':'A Major','E':'E Major',
  'B':'B Major','F#':'F♯ Major','F':'F Major','Bb':'B♭ Major','Eb':'E♭ Major','Ab':'A♭ Major',
  'Am':'A Minor','Em':'E Minor','Bm':'B Minor','F#m':'F♯ Minor',
  'Dm':'D Minor','Gm':'G Minor','Cm':'C Minor',
};

function applyTranspose(toKey) {
  document.getElementById('transposeMenu').classList.remove('open');
  document.getElementById('transposeBtn').classList.remove('open');
  if (!combinedXML) return;

  const fromSemi = KEY_SEMITONE[currentKey] ?? 0;
  const toSemi   = KEY_SEMITONE[toKey]      ?? 0;
  let interval = toSemi - fromSemi;
  if (interval > 6)  interval -= 12;
  if (interval < -6) interval += 12;
  if (interval === 0) return;

  const useFlats = FLAT_KEY_SET.has(toKey);
  const names    = useFlats ? FLAT_NAMES : SHARP_NAMES;

  combinedXML = combinedXML.replace(/<pitch>([\s\S]*?)<\/pitch>/g, (_, inner) => {
    const step  = (inner.match(/<step>([A-G])<\/step>/)   || [])[1];
    const alter = parseInt((inner.match(/<alter>(-?\d+)<\/alter>/) || ['','0'])[1]);
    const oct   = parseInt((inner.match(/<octave>(\d+)<\/octave>/) || [])[1]);
    if (!step || isNaN(oct)) return `<pitch>${inner}</pitch>`;

    const midi    = (oct + 1) * 12 + STEP_TO_SEMI[step] + alter;
    const newMidi = midi + interval;
    const newOct  = Math.floor(newMidi / 12) - 1;
    const pc      = ((newMidi % 12) + 12) % 12;
    const name    = names[pc];
    const newStep = name[0];
    const newAlter = name.includes('#') ? 1 : name.includes('b') ? -1 : 0;

    let pitch = `<step>${newStep}</step>`;
    if (newAlter !== 0) pitch += `<alter>${newAlter}</alter>`;
    pitch += `<octave>${newOct}</octave>`;
    return `<pitch>${pitch}</pitch>`;
  });

  combinedXML = overrideKey(combinedXML, toKey);
  currentKey = toKey;

  const label = KEY_DISPLAY[toKey] || toKey;
  document.getElementById('transposeBtnLabel').textContent = label;
  document.getElementById('keyDisplay').textContent = label;
  document.querySelectorAll('#transposeMenu .edit-dropdown-item').forEach(el => {
    el.classList.toggle('active', el.textContent.trim().startsWith(label.replace('♯','#').replace('♭','b')));
  });

  renderScore(combinedXML);
  showToast(`Transposed to ${label}`);
}

// ── Markings ─────────────────────────────────────────────────────────────────
function applyMarking(marking) {
  document.getElementById('markingsMenu').classList.remove('open');
  if (!combinedXML) return;

  let xml = combinedXML;
  let label = marking;

  if (marking.startsWith('text:')) {
    const text = marking.slice(5);
    label = text;
    const dir = `<direction placement="above"><direction-type><words font-weight="bold">${text}</words></direction-type></direction>\n    `;
    xml = xml.replace(/(<measure [^>]*number="1"[^>]*>)/, `$1\n    ${dir}`);
  } else if (marking.startsWith('rehearsal:')) {
    const mark = marking.slice(10);
    label = mark;
    const dir = `<direction placement="above"><direction-type><rehearsal enclosure="box">${mark}</rehearsal></direction-type></direction>\n    `;
    xml = xml.replace(/(<measure [^>]*number="1"[^>]*>)/, `$1\n    ${dir}`);
  } else {
    const dir = `<direction placement="below"><direction-type><dynamics><${marking}/></dynamics></direction-type></direction>\n    `;
    xml = xml.replace(/(<measure [^>]*number="1"[^>]*>)/, `$1\n    ${dir}`);
  }

  combinedXML = xml;
  renderScore(combinedXML);
  showToast(`Added: ${label}`);
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => t.classList.remove('show'), 2500);
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.getElementById('titleInput').addEventListener('input', () => {
  document.getElementById('savedStatus').textContent = 'Unsaved';
  clearTimeout(window._t);
  window._t = setTimeout(() => { document.getElementById('savedStatus').textContent = 'Saved'; }, 1000);
});
