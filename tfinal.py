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

# Quantization grid
QUANTIZATION = 0.25  # quarter-beat

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

    beat_sec = 60 / bpm

    quantized = []

    for start, end, midi_pitch in notes:

        duration_sec = end - start

        beats = duration_sec / beat_sec

        quantized_beats = (
            round(beats / QUANTIZATION)
            * QUANTIZATION
        )

        quantized_beats = max(0.25, quantized_beats)

        quantized.append((
            round(midi_pitch),
            quantized_beats
        ))

    return quantized


# ============================================================
# CREATE SCORE
# ============================================================

def build_score(
    quantized_notes,
    bpm,
    detected_key,
    detected_time_signature
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

    for midi_pitch, dur in quantized_notes:
        n = note.Note()
        n.pitch.midi = int(midi_pitch)
        n.quarterLength = dur
        part.append(n)

    score.insert(0, part)
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
    Render PDF via LilyPond. Returns path to the PDF file.

    music21 passes `fp` to LilyPond as a basename and adds `.pdf`.
    If `fp` already ends in `.pdf`, LilyPond writes `name.pdf.pdf` and
    leaves `name.pdf` as the .ly source — so we must omit the extension.
    """
    if base_path.endswith(".pdf"):
        base_path = base_path[: -4]
    score.write("lily.pdf", fp=base_path)
    pdf_path = base_path + ".pdf"
    if not os.path.isfile(pdf_path):
        alt = base_path + ".pdf.pdf"
        if os.path.isfile(alt):
            return alt
        raise FileNotFoundError(f"LilyPond did not create {pdf_path}")
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
    score = build_score(quantized, bpm, detected_key, time_sig)

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