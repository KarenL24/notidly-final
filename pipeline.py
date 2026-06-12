"""
Audio transcription pipeline: pitch tracking, note segmentation, key/tempo
detection, rhythm quantization, and lyric alignment.

Depends on score.py for score building and export. Does not touch music21
directly except for key detection (which returns a music21 Key object).
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

from music21 import key

from score import (
    build_score,
    clean_musicxml,
    export_score_pdf,
    timed_notes_from_segments,
)

# ── Config ────────────────────────────────────────────────────────────────────

SAMPLE_RATE = 16000
FRAME_HOP = 0.01  # 10ms
CONFIDENCE_THRESHOLD = 0.72

MEDIAN_FILTER_SIZE = 7      # vibrato suppression
MIN_NOTE_DURATION = 0.08    # seconds
MAX_SEMITONE_JUMP = 1.5

CONVENTIONAL_DURATIONS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]

DEFAULT_BPM = 90

# ── Audio loading ─────────────────────────────────────────────────────────────

def load_audio(path):
    y, sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    y = librosa.util.normalize(y)
    y_harmonic, _ = librosa.effects.hpss(y)
    return y_harmonic


# ── Tempo detection ───────────────────────────────────────────────────────────

def detect_tempo(audio):
    onset_env = librosa.onset.onset_strength(y=audio, sr=SAMPLE_RATE)
    tempo_est, _ = librosa.beat.beat_track(onset_envelope=onset_env, sr=SAMPLE_RATE)
    return float(tempo_est) if tempo_est > 0 else DEFAULT_BPM


# ── Time signature estimation ─────────────────────────────────────────────────

def detect_time_signature(audio, bpm):
    """Lightweight heuristic — detects 3/4 vs 4/4 via beat accent periodicity."""
    onset_env = librosa.onset.onset_strength(y=audio, sr=SAMPLE_RATE)
    beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=SAMPLE_RATE)[1]

    if len(beats) < 8:
        return "4/4"

    beat_strengths = onset_env[beats]
    score_3 = sum(beat_strengths[i] for i in range(len(beat_strengths)) if i % 3 == 0)
    score_4 = sum(beat_strengths[i] for i in range(len(beat_strengths)) if i % 4 == 0)

    return "3/4" if score_3 > score_4 * 1.1 else "4/4"


# ── CREPE pitch tracking ──────────────────────────────────────────────────────

def extract_pitch(audio):
    time, frequency, confidence, _ = crepe.predict(
        audio,
        SAMPLE_RATE,
        step_size=int(FRAME_HOP * 1000),
        viterbi=True,
        verbose=0,
    )
    frequency[confidence < CONFIDENCE_THRESHOLD] = 0
    return time, frequency, confidence


# ── Vibrato suppression + vocal fry removal ───────────────────────────────────

def smooth_pitch(freq):
    nonzero = freq > 0
    smoothed = np.copy(freq)
    if np.sum(nonzero) == 0:
        return smoothed
    midi = librosa.hz_to_midi(freq[nonzero])
    midi = scipy.ndimage.median_filter(midi, size=MEDIAN_FILTER_SIZE)
    midi = scipy.ndimage.gaussian_filter1d(midi, sigma=1)
    smoothed[nonzero] = librosa.midi_to_hz(midi)
    return smoothed


def remove_fry(freq, confidence):
    cleaned = np.copy(freq)
    for i in range(1, len(freq) - 1):
        if freq[i] > 0 and freq[i - 1] > 0:
            jump = abs(librosa.hz_to_midi(freq[i]) - librosa.hz_to_midi(freq[i - 1]))
            if jump > 3 and confidence[i] < 0.85:
                cleaned[i] = 0
    return cleaned


# ── Note segmentation ─────────────────────────────────────────────────────────

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
                if abs(midi - median_pitch) > MAX_SEMITONE_JUMP:
                    duration = times[i] - start_time
                    if duration >= MIN_NOTE_DURATION:
                        notes.append((start_time, times[i], np.median(pitches)))
                    start_time = times[i]
                    pitches = [midi]
                else:
                    pitches.append(midi)
        else:
            if active:
                duration = times[i] - start_time
                if duration >= MIN_NOTE_DURATION:
                    notes.append((start_time, times[i], np.median(pitches)))
                active = False
                pitches = []

    return notes


# ── Lyric detection + alignment ───────────────────────────────────────────────

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


# ── Key detection ─────────────────────────────────────────────────────────────

def detect_key(notes):
    """Estimate musical key from pitch-class histogram."""
    if not notes:
        return key.Key("C")

    pitch_classes = [int(round(midi_pitch)) % 12 for _, _, midi_pitch in notes]
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
        "Ab": [8,10,0,1,3,5,7],
    }

    best_key = max(major_profiles, key=lambda k: sum(histogram.get(pc, 0) for pc in major_profiles[k]))
    return key.Key(best_key)


# ── Rhythm quantization ───────────────────────────────────────────────────────

def quantize_notes(notes, bpm):
    """
    Snap note start/end times to a 16th-note grid, derive durations from those
    snapped positions, and insert rests for any resulting gaps.
    """
    if not notes:
        return []

    beat_sec = 60.0 / bpm
    GRID = 0.25
    MIN_REST = GRID / 2

    def snap(beats):
        return round(round(beats / GRID) * GRID, 6)

    events = []
    prev_end = None

    for start_s, end_s, midi_pitch in notes:
        q_start = snap(start_s / beat_sec)
        q_end   = snap(end_s   / beat_sec)

        if q_end <= q_start:
            q_end = q_start + GRID

        if prev_end is not None and q_start < prev_end:
            q_start = prev_end
            if q_end <= q_start:
                q_end = q_start + GRID

        if prev_end is not None:
            gap = round(q_start - prev_end, 6)
            if gap >= MIN_REST:
                rest_dur = min(CONVENTIONAL_DURATIONS, key=lambda d: abs(d - gap))
                events.append(('rest', None, rest_dur))

        raw_dur  = round(q_end - q_start, 6)
        best_dur = min(CONVENTIONAL_DURATIONS, key=lambda d: abs(d - raw_dur))
        best_dur = max(best_dur, GRID)

        events.append(('note', round(midi_pitch), best_dur))
        prev_end = round(q_start + best_dur, 6)

    return events


# ── Notation quality ──────────────────────────────────────────────────────────

def merge_same_pitch_notes(events):
    """
    Merge consecutive same-pitch note events into one longer note.
    Only safe to call when there are no lyrics (merging misaligns lyric indices).
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


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(input_file: str, time_signature: str = None):
    """Run transcription and return (score, metadata dict)."""
    audio = load_audio(input_file)
    bpm = detect_tempo(audio)
    time_sig = time_signature if time_signature else detect_time_signature(audio, bpm)

    times, freq, conf = extract_pitch(audio)
    freq = remove_fry(freq, conf)
    freq = smooth_pitch(freq)
    segments = segment_notes(times, freq)

    detected_key = detect_key(segments)
    quantized = quantize_notes(segments, bpm)
    words = detect_lyrics(input_file)
    lyrics = align_lyrics(words, segments)

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


def transcribe_file(input_file: str, time_signature: str = None) -> dict:
    """Full pipeline for the web API: score artifacts + metadata."""
    import base64
    import tempfile

    score, meta = run_pipeline(input_file, time_signature=time_signature)

    with tempfile.TemporaryDirectory() as tmp:
        midi_path = os.path.join(tmp, "output.mid")
        xml_path  = os.path.join(tmp, "output.musicxml")
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


# ── CLI entry ─────────────────────────────────────────────────────────────────

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

    import base64

    print("Writing MIDI...")
    with open("output.mid", "wb") as f:
        f.write(base64.b64decode(result["midi_base64"]))

    print("Writing MusicXML...")
    with open("output.musicxml", "w", encoding="utf-8") as f:
        f.write(result["musicxml"])

    if result["pdf_available"]:
        print("Rendering PDF (LilyPond)...")
        with open("output.pdf", "wb") as f:
            f.write(base64.b64decode(result["pdf_base64"]))
    else:
        print("Could not render PDF. Install LilyPond and add it to PATH.")
        if result["pdf_error"]:
            print(result["pdf_error"])

    print("Done!")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py input.mp3")
        sys.exit(1)
    main(sys.argv[1])
