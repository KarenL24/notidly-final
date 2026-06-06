"""
High-Accuracy Monophonic MP3 -> Sheet Music Transcriber
-------------------------------------------------------

Designed for:
- Vocal melodies (with vibrato + vocal fry handling)
- Piano melodies
- Single-line melody transcription

Features:
- CREPE neural pitch tracking
- Vibrato suppression
- Vocal fry filtering
- Note segmentation with hysteresis
- Automatic key signature detection
- Automatic time signature estimation
- Pitch quantization
- MusicXML + MIDI export
- Sheet music PDF generation (via LilyPond)

INSTALL:
pip install numpy scipy librosa soundfile music21 matplotlib torch crepe

OPTIONAL (recommended):
- Install LilyPond and add to PATH for PDF rendering (https://lilypond.org)
- ffmpeg installed for mp3 decoding

USAGE:
python transcribe.py input.mp3

OUTPUTS:
- output.mid
- output.musicxml
- output.pdf (if LilyPond installed)
"""

import sys
import os

# Ensure Homebrew binaries (lilypond, ffmpeg) are on PATH when the server
# starts without a full shell environment (e.g. launched from an IDE).
for _p in ('/opt/homebrew/bin', '/usr/local/bin'):
    if os.path.isdir(_p) and _p not in os.environ.get('PATH', ''):
        os.environ['PATH'] = _p + ':' + os.environ.get('PATH', '')

import numpy as np
import librosa
import scipy.ndimage
import crepe

from collections import Counter

import re

from music21 import (
    stream,
    note,
    meter,
    tempo,
    key,
    metadata,
)

# ============================================================
# CONFIG
# ============================================================

SAMPLE_RATE = 16000
FRAME_HOP = 0.01  # 10ms
CONFIDENCE_THRESHOLD = 0.72

# Vibrato suppression
MEDIAN_FILTER_SIZE = 7

# Minimum note duration
MIN_NOTE_DURATION = 0.08  # seconds

# Pitch jump threshold for note splitting
MAX_SEMITONE_JUMP = 1.5

# Conventional note durations in quarter beats (no awkward values that cause ties)
CONVENTIONAL_DURATIONS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]

DEFAULT_BPM = 90

# ============================================================
# AUDIO LOADING
# ============================================================

def load_audio(path):

    y, sr = librosa.load(
        path,
        sr=SAMPLE_RATE,
        mono=True
    )

    # Normalize
    y = librosa.util.normalize(y)

    # Harmonic enhancement
    y_harmonic, _ = librosa.effects.hpss(y)

    return y_harmonic


# ============================================================
# TEMPO DETECTION
# ============================================================

def detect_tempo(audio):

    onset_env = librosa.onset.onset_strength(
        y=audio,
        sr=SAMPLE_RATE
    )

    tempo_est, _ = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=SAMPLE_RATE
    )

    if tempo_est <= 0:
        return DEFAULT_BPM

    return float(tempo_est)


# ============================================================
# TIME SIGNATURE ESTIMATION
# ============================================================

def detect_time_signature(audio, bpm):

    """
    Very lightweight heuristic.

    Most monophonic melodies will realistically be:
    - 4/4
    - 3/4
    - 6/8

    We estimate based on beat periodicity.
    """

    onset_env = librosa.onset.onset_strength(
        y=audio,
        sr=SAMPLE_RATE
    )

    tempo_frames = librosa.beat.tempo(
        onset_envelope=onset_env,
        sr=SAMPLE_RATE
    )

    # Simple heuristic:
    # stronger repeating accents every 3 beats -> 3/4
    # otherwise default 4/4

    beats = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=SAMPLE_RATE
    )[1]

    if len(beats) < 8:
        return "4/4"

    beat_strengths = onset_env[beats]

    score_3 = 0
    score_4 = 0

    for i in range(len(beat_strengths)):

        if i % 3 == 0:
            score_3 += beat_strengths[i]

        if i % 4 == 0:
            score_4 += beat_strengths[i]

    if score_3 > score_4 * 1.1:
        return "3/4"

    return "4/4"


# ============================================================
# CREPE PITCH TRACKING
# ============================================================

def extract_pitch(audio):

    time, frequency, confidence, _ = crepe.predict(
        audio,
        SAMPLE_RATE,
        step_size=int(FRAME_HOP * 1000),
        viterbi=True,
        verbose=0
    )

    frequency[confidence < CONFIDENCE_THRESHOLD] = 0

    return time, frequency, confidence


# ============================================================
# VIBRATO + VOCAL FRY HANDLING
# ============================================================

def smooth_pitch(freq):

    nonzero = freq > 0

    smoothed = np.copy(freq)

    if np.sum(nonzero) == 0:
        return smoothed

    midi = librosa.hz_to_midi(freq[nonzero])

    # Median filter removes vibrato oscillation
    midi_smooth = scipy.ndimage.median_filter(
        midi,
        size=MEDIAN_FILTER_SIZE
    )

    # Gentle gaussian smoothing
    midi_smooth = scipy.ndimage.gaussian_filter1d(
        midi_smooth,
        sigma=1
    )

    smoothed[nonzero] = librosa.midi_to_hz(midi_smooth)

    return smoothed


# ============================================================
# REMOVE VOCAL FRY / UNSTABLE REGIONS
# ============================================================

def remove_fry(freq, confidence):

    cleaned = np.copy(freq)

    for i in range(1, len(freq)-1):

        if freq[i] > 0 and freq[i-1] > 0:

            jump = abs(
                librosa.hz_to_midi(freq[i]) -
                librosa.hz_to_midi(freq[i-1])
            )

            # Fry / noisy unstable jumps
            if jump > 3 and confidence[i] < 0.85:
                cleaned[i] = 0

    return cleaned


# ============================================================
# NOTE SEGMENTATION
# ============================================================

def segment_notes(times, freq):

    notes = []

    active = False
    start_time = None
    pitches = []

    for i in range(len(freq)):

        f = freq[i]

        if f > 0:

            midi = librosa.hz_to_midi(f)

            if not active:

                active = True
                start_time = times[i]
                pitches = [midi]

            else:

                median_pitch = np.median(pitches)

                # Split if pitch jump too large
                if abs(midi - median_pitch) > MAX_SEMITONE_JUMP:

                    end_time = times[i]

                    duration = end_time - start_time

                    if duration >= MIN_NOTE_DURATION:

                        notes.append((
                            start_time,
                            end_time,
                            np.median(pitches)
                        ))

                    start_time = times[i]
                    pitches = [midi]

                else:
                    pitches.append(midi)

        else:

            if active:

                end_time = times[i]

                duration = end_time - start_time

                if duration >= MIN_NOTE_DURATION:

                    notes.append((
                        start_time,
                        end_time,
                        np.median(pitches)
                    ))

                active = False
                pitches = []

    return notes


# ============================================================
# LYRIC DETECTION + ALIGNMENT
# ============================================================

def detect_lyrics(audio_path: str) -> list:
    """Return [{word, start, end}] using openai-whisper medium model."""
    try:
        import whisper
        model = whisper.load_model("medium")
        result = model.transcribe(audio_path, word_timestamps=True)
        words = []
        for seg in result.get("segments", []):
            for w in seg.get("words", []):
                text = w.get("word", "").strip()
                if text:
                    words.append({"word": text, "start": w["start"], "end": w["end"]})
        return words
    except Exception as exc:
        print(f"Lyric detection skipped: {exc}")
        return []


def align_lyrics(words: list, segments: list) -> list:
    """
    Map each note segment to a word whose center falls inside it.
    Returns a list parallel to segments: word string or None per note.
    """
    used = set()
    lyrics = []
    for note_start, note_end, _ in segments:
        lyric = None
        for i, w in enumerate(words):
            if i in used:
                continue
            center = (w["start"] + w["end"]) / 2
            if note_start <= center < note_end:
                lyric = w["word"]
                used.add(i)
                break
        lyrics.append(lyric)
    return lyrics


# ============================================================
# KEY DETECTION
# ============================================================

def detect_key(notes):

    """
    Estimate musical key from pitch-class histogram.
    """

    if len(notes) == 0:
        return key.Key("C")

    pitch_classes = []

    for _, _, midi_pitch in notes:

        midi_rounded = int(round(midi_pitch))
        pc = midi_rounded % 12

        pitch_classes.append(pc)

    histogram = Counter(pitch_classes)

    major_profiles = {
        "C":  [0,2,4,5,7,9,11],
        "G":  [7,9,11,0,2,4,6],
        "D":  [2,4,6,7,9,11,1],
        "A":  [9,11,1,2,4,6,8],
        "E":  [4,6,8,9,11,1,3],
        "B":  [11,1,3,4,6,8,10],
        "F#": [6,8,10,11,1,3,5],
        "F":  [5,7,9,10,0,2,4],
        "Bb": [10,0,2,3,5,7,9],
        "Eb": [3,5,7,8,10,0,2],
        "Ab": [8,10,0,1,3,5,7]
    }

    best_key = "C"
    best_score = -1

    for k, scale_pcs in major_profiles.items():

        score = 0

        for pc in scale_pcs:
            score += histogram.get(pc, 0)

        if score > best_score:
            best_score = score
            best_key = k

    return key.Key(best_key)


# ============================================================
# RHYTHM QUANTIZATION
# ============================================================

def quantize_notes(notes, bpm):
    """
    Position-based quantization.

    Snap every note's start and end to the nearest 16th-note grid point,
    derive durations from those snapped positions, and insert rests for
    any resulting gaps.  Because all notes begin on clean beat subdivisions,
    music21 can notate them without unnecessary ties.
    """
    if not notes:
        return []

    beat_sec = 60.0 / bpm
    GRID     = 0.25        # 16th-note grid in quarter-beats
    MIN_REST = GRID / 2    # gaps smaller than this are articulation, not rests

    def snap(beats):
        return round(round(beats / GRID) * GRID, 6)

    events   = []
    prev_end = None   # quantized end of the previous note (in beats)

    for start_s, end_s, midi_pitch in notes:
        q_start = snap(start_s / beat_sec)
        q_end   = snap(end_s   / beat_sec)

        # Guarantee at least one grid step of duration
        if q_end <= q_start:
            q_end = q_start + GRID

        # If this note overlaps the previous one, push it right
        if prev_end is not None and q_start < prev_end:
            q_start = prev_end
            if q_end <= q_start:
                q_end = q_start + GRID

        # Insert a rest for any meaningful gap before this note
        if prev_end is not None:
            gap = round(q_start - prev_end, 6)
            if gap >= MIN_REST:
                rest_dur = min(CONVENTIONAL_DURATIONS, key=lambda d: abs(d - gap))
                events.append(('rest', None, rest_dur))

        # Snap the beat-duration to the nearest conventional value
        raw_dur  = round(q_end - q_start, 6)
        best_dur = min(CONVENTIONAL_DURATIONS, key=lambda d: abs(d - raw_dur))
        best_dur = max(best_dur, GRID)

        events.append(('note', round(midi_pitch), best_dur))
        prev_end = round(q_start + best_dur, 6)

    return events


# ============================================================
# NOTATION QUALITY
# ============================================================

def merge_same_pitch_notes(events):
    """
    Merge consecutive same-pitch note events (with no rest between them)
    into a single longer note, then re-snap to the nearest conventional duration.

    Only safe to call when there are no lyrics to preserve, since merging
    reduces the note count and would misalign lyric indices.
    """
    result = []
    i = 0
    while i < len(events):
        ev = events[i]
        if ev[0] != 'note':
            result.append(ev)
            i += 1
            continue

        _, pitch, dur = ev
        total = dur
        j = i + 1
        while j < len(events) and events[j][0] == 'note' and events[j][1] == pitch:
            total += events[j][2]
            j += 1

        if j > i + 1:
            best = min(CONVENTIONAL_DURATIONS, key=lambda d: abs(d - total))
            result.append(('note', pitch, best))
        else:
            result.append(ev)
        i = j
    return result


def simplify_notation(score):
    """
    Post-process a music21 Score to convert within-measure tied pairs into
    their dotted-note equivalents.

    Runs after makeNotation so ties at barlines are already correct;
    this pass only collapses ties that remain inside a single measure.
    """
    from music21 import note as m21note

    DOTTED_MAP = {
        (1.0,  0.5):  1.5,   # quarter + eighth   → dotted quarter
        (2.0,  1.0):  3.0,   # half    + quarter  → dotted half
        (0.5,  0.25): 0.75,  # eighth  + 16th     → dotted eighth
        (4.0,  2.0):  6.0,   # whole   + half     → dotted whole
        (0.25, 0.125):0.375, # 16th    + 32nd     → dotted 16th
    }

    for part in score.parts:
        for measure in part.getElementsByClass('Measure'):
            changed = True
            while changed:
                changed = False
                els = list(measure.getElementsByClass(['Note', 'Rest']))
                for i in range(len(els) - 1):
                    n1, n2 = els[i], els[i + 1]
                    if (isinstance(n1, m21note.Note) and
                            isinstance(n2, m21note.Note) and
                            n1.pitch.midi == n2.pitch.midi and
                            n1.tie is not None and n1.tie.type == 'start' and
                            n2.tie is not None and n2.tie.type == 'stop'):
                        key = (float(n1.quarterLength), float(n2.quarterLength))
                        if key in DOTTED_MAP:
                            n1.quarterLength = DOTTED_MAP[key]
                            n1.tie = None
                            measure.remove(n2)
                            changed = True
                            break
    return score


# ============================================================
# CREATE SCORE
# ============================================================

def build_score(
    quantized_notes,
    bpm,
    detected_key,
    detected_time_signature,
    lyrics=None,
):

    score = stream.Score()
    score.metadata = metadata.Metadata()
    score.metadata.title = ""
    score.metadata.composer = ""

    part = stream.Part()
    part.partName = "Melody"
    part.insert(0, tempo.MetronomeMark(number=round(bpm)))
    part.insert(0, meter.TimeSignature(detected_time_signature))
    part.insert(0, detected_key)

    lyric_idx = 0
    for event in quantized_notes:
        # Support old 2-tuple format from rebuild_from_editor_notes
        if len(event) == 2:
            evt_type, pitch, dur = 'note', event[0], event[1]
        else:
            evt_type, pitch, dur = event

        if evt_type == 'rest':
            r = note.Rest()
            r.quarterLength = dur
            part.append(r)
            continue

        n = note.Note()
        n.pitch.midi = int(pitch)
        n.quarterLength = dur
        if lyrics and lyric_idx < len(lyrics) and lyrics[lyric_idx]:
            n.addLyric(lyrics[lyric_idx])
        lyric_idx += 1
        part.append(n)

    score.insert(0, part)

    # Let music21 handle measure creation, barline ties, beaming, and
    # accidentals, then fold any remaining within-measure ties into dotted notes.
    score.makeNotation(inPlace=True)
    simplify_notation(score)

    return score


def clean_musicxml(xml: str) -> str:
    """Remove Music21 boilerplate that clutters the sheet-music viewer."""
    xml = re.sub(
        r"\s*<movement-title>.*?</movement-title>",
        "",
        xml,
        flags=re.DOTALL,
    )
    xml = re.sub(r"\s*<movement-title\s*/>", "", xml)
    xml = re.sub(r"\s*<identification>.*?</identification>", "", xml, flags=re.DOTALL)
    xml = re.sub(r"\s*<work>.*?</work>", "", xml, flags=re.DOTALL)
    xml = re.sub(
        r"<score-instrument[^>]*>.*?</score-instrument>\s*",
        "",
        xml,
        flags=re.DOTALL,
    )
    xml = re.sub(
        r"<midi-instrument[^>]*>.*?</midi-instrument>\s*",
        "",
        xml,
        flags=re.DOTALL,
    )
    return xml


# ============================================================
# HELPERS (API + CLI)
# ============================================================

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def midi_to_note_name(midi_pitch: int) -> str:
    octave = (midi_pitch // 12) - 1
    return f"{NOTE_NAMES[int(midi_pitch) % 12]}{octave}"


def parse_key_signature(key_signature: str):
    """Parse strings like 'C major' or 'A minor' into a music21 Key."""
    name = key_signature.strip()
    lower = name.lower()
    if " minor" in lower:
        tonic = name.split()[0]
        return key.Key(tonic, "minor")
    if " major" in lower:
        tonic = name.split()[0]
        return key.Key(tonic)
    return key.Key(name)


def score_notes_from_musicxml(musicxml: str) -> list:
    """Extract editable notes (pitch + quarter length) from MusicXML."""
    from music21 import converter

    score = converter.parse(musicxml, format="musicxml")
    part = score.parts[0] if score.parts else score

    out = []
    for el in part.recurse().notes:
        if isinstance(el, note.Note):
            midi_pitch = int(el.pitch.midi)
            out.append({
                "pitch": midi_pitch,
                "note": midi_to_note_name(midi_pitch),
                "quarter_length": float(el.quarterLength),
            })
    return out


def export_score_artifacts(score) -> dict:
    """Serialize score to MusicXML, MIDI, and optional PDF."""
    import base64
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        midi_path = os.path.join(tmp, "output.mid")
        xml_path = os.path.join(tmp, "output.musicxml")
        score.write("midi", fp=midi_path)
        score.write("musicxml", fp=xml_path)
        with open(midi_path, "rb") as f:
            midi_bytes = f.read()
        with open(xml_path, "r", encoding="utf-8") as f:
            musicxml = clean_musicxml(f.read())

    pdf_bytes, pdf_error = export_score_pdf(score)

    return {
        "musicxml": musicxml,
        "midi_base64": base64.b64encode(midi_bytes).decode("ascii"),
        "pdf_available": pdf_bytes is not None,
        "pdf_base64": (
            base64.b64encode(pdf_bytes).decode("ascii") if pdf_bytes else None
        ),
        "pdf_error": pdf_error,
    }


def rebuild_from_editor_notes(
    score_notes: list,
    bpm: float,
    time_signature: str,
    key_signature: str,
) -> dict:
    """Rebuild score artifacts after manual editing."""
    quantized = [
        (int(n["pitch"]), float(n["quarter_length"]))
        for n in score_notes
    ]
    detected_key = parse_key_signature(key_signature)
    score = build_score(quantized, bpm, detected_key, time_signature)
    artifacts = export_score_artifacts(score)

    beat_sec = 60 / float(bpm)
    t = 0.0
    timed = []
    for sn in score_notes:
        dur_sec = float(sn["quarter_length"]) * beat_sec
        midi_pitch = int(sn["pitch"])
        timed.append({
            "pitch": midi_pitch,
            "note": sn.get("note") or midi_to_note_name(midi_pitch),
            "start": t,
            "end": t + dur_sec,
            "duration": dur_sec,
        })
        t += dur_sec

    return {
        **artifacts,
        "notes": timed,
        "note_count": len(score_notes),
        "bpm": float(bpm),
        "time_signature": time_signature,
        "key_signature": key_signature,
        "duration": t,
    }


def timed_notes_from_segments(segments):
    return [
        {
            "pitch": int(round(midi_pitch)),
            "note": midi_to_note_name(int(round(midi_pitch))),
            "start": float(start),
            "end": float(end),
            "duration": float(end - start),
        }
        for start, end, midi_pitch in segments
    ]


def render_pdf_lilypond(score, base_path: str) -> str:
    """
    Render PDF via LilyPond with improved page layout.

    1. Use music21 to produce both the .ly source and an initial PDF.
    2. Inject paper settings into the .ly to prevent the last system
       from being clipped (ragged-last-bottom fix).
    3. Re-run LilyPond on the patched .ly to produce the final PDF.
    """
    import subprocess

    if base_path.endswith(".pdf"):
        base_path = base_path[:-4]

    ly_path = base_path + ".ly"

    # Step 1: music21 generates both .ly and PDF side-by-side
    score.write("lily.pdf", fp=base_path)

    if not os.path.isfile(ly_path):
        # Fall back to whatever PDF music21 produced
        for candidate in (base_path + ".pdf", base_path + ".pdf.pdf"):
            if os.path.isfile(candidate):
                return candidate
        raise FileNotFoundError(f"LilyPond produced no output at {base_path}")

    # Step 2: patch the .ly file with proper page settings
    with open(ly_path, "r", encoding="utf-8") as f:
        ly = f.read()

    paper = (
        "\n\\paper {\n"
        "  #(set-paper-size \"letter\")\n"
        "  ragged-last-bottom = ##f\n"
        "  ragged-bottom = ##f\n"
        "  top-margin = 15\\mm\n"
        "  bottom-margin = 15\\mm\n"
        "  left-margin = 15\\mm\n"
        "  right-margin = 15\\mm\n"
        "}\n"
    )

    # Replace any existing \paper block, or insert before \score
    if "\\paper" in ly:
        ly = re.sub(r'\\paper\s*\{[^}]*\}', paper.strip(), ly, count=1, flags=re.DOTALL)
    elif "\\score" in ly:
        ly = ly.replace("\\score", paper + "\\score", 1)
    else:
        ly = paper + ly

    with open(ly_path, "w", encoding="utf-8") as f:
        f.write(ly)

    # Step 3: re-run LilyPond on the patched source
    result = subprocess.run(
        ["lilypond", "-o", base_path, ly_path],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(base_path) or ".",
    )

    pdf_path = base_path + ".pdf"
    if not os.path.isfile(pdf_path):
        stderr = (result.stderr or "")[-600:]
        raise RuntimeError(f"LilyPond re-run failed.\n{stderr}")

    return pdf_path


def export_score_pdf(score) -> tuple:
    """Return (pdf_bytes, error_message). One will be None."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        try:
            pdf_path = render_pdf_lilypond(
                score,
                os.path.join(tmp, "score"),
            )
            with open(pdf_path, "rb") as f:
                data = f.read()
            if not data.startswith(b"%PDF"):
                return None, "LilyPond did not produce a valid PDF file."
            return data, None
        except Exception as exc:
            return None, str(exc)


def run_pipeline(input_file: str):
    """Run transcription and return (score, metadata dict)."""
    audio = load_audio(input_file)
    bpm = detect_tempo(audio)
    time_sig = detect_time_signature(audio, bpm)

    times, freq, conf = extract_pitch(audio)
    freq = remove_fry(freq, conf)
    freq = smooth_pitch(freq)
    segments = segment_notes(times, freq)

    detected_key = detect_key(segments)
    quantized = quantize_notes(segments, bpm)
    words = detect_lyrics(input_file)
    lyrics = align_lyrics(words, segments)

    # Merge consecutive same-pitch notes only when there are no lyrics to
    # preserve — merging reduces the note count and would misalign lyric indices.
    if not words:
        quantized = merge_same_pitch_notes(quantized)

    score = build_score(quantized, bpm, detected_key, time_sig, lyrics=lyrics)

    duration = float(librosa.get_duration(path=input_file))

    return score, {
        "notes": timed_notes_from_segments(segments),
        "note_count": len(segments),
        "bpm": float(bpm),
        "time_signature": time_sig,
        "key_signature": detected_key.name,
        "duration": duration,
    }


def transcribe_file(input_file: str) -> dict:
    """Full pipeline for the web API: score artifacts + metadata."""
    import base64
    import tempfile

    score, meta = run_pipeline(input_file)

    with tempfile.TemporaryDirectory() as tmp:
        midi_path = os.path.join(tmp, "output.mid")
        xml_path = os.path.join(tmp, "output.musicxml")

        score.write("midi", fp=midi_path)
        score.write("musicxml", fp=xml_path)

        with open(midi_path, "rb") as f:
            midi_bytes = f.read()
        with open(xml_path, "r", encoding="utf-8") as f:
            musicxml = clean_musicxml(f.read())

    pdf_bytes, pdf_error = export_score_pdf(score)

    return {
        **meta,
        "musicxml": musicxml,
        "midi_base64": base64.b64encode(midi_bytes).decode("ascii"),
        "pdf_available": pdf_bytes is not None,
        "pdf_base64": (
            base64.b64encode(pdf_bytes).decode("ascii") if pdf_bytes else None
        ),
        "pdf_error": pdf_error,
    }


# ============================================================
# MAIN
# ============================================================

def main(input_file):

    print("Loading audio...")
    print("Detecting tempo...")
    print("Estimating time signature...")
    print("Extracting pitch with CREPE...")
    print("Removing vocal fry...")
    print("Suppressing vibrato...")
    print("Segmenting notes...")
    print("Detecting key signature...")
    print("Quantizing rhythm...")
    print("Building score...")

    result = transcribe_file(input_file)

    print(f"Detected {result['note_count']} notes")
    print(f"Estimated BPM: {round(result['bpm'])}")
    print(f"Estimated time signature: {result['time_signature']}")
    print(f"Estimated key: {result['key_signature']}")

    print("Writing MIDI...")
    with open("output.mid", "wb") as f:
        f.write(__import__("base64").b64decode(result["midi_base64"]))

    print("Writing MusicXML...")
    with open("output.musicxml", "w", encoding="utf-8") as f:
        f.write(result["musicxml"])

    if result["pdf_available"]:
        print("Rendering PDF (LilyPond)...")
        with open("output.pdf", "wb") as f:
            f.write(__import__("base64").b64decode(result["pdf_base64"]))
    else:
        print("Could not render PDF.")
        print("Install LilyPond and add it to PATH.")
        if result["pdf_error"]:
            print(result["pdf_error"])

    print("Done!")


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    if len(sys.argv) < 2:

        print("Usage:")
        print("python transcribe.py input.mp3")

        sys.exit(1)

    main(sys.argv[1])