"""
Score building, MusicXML/MIDI/PDF export, and score-editing helpers.

This module is import-free of the audio pipeline — it only depends on
music21 and standard library. pipeline.py imports from here; not the
other way around.
"""

import os
import re

from music21 import (
    converter,
    key,
    metadata,
    meter,
    note,
    stream,
    tempo,
)

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

CONVENTIONAL_DURATIONS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0]


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


def simplify_notation(score):
    """
    Post-process a music21 Score to convert within-measure tied pairs into
    their dotted-note equivalents.

    Runs after makeNotation so ties at barlines are already correct;
    this pass only collapses ties that remain inside a single measure.
    """
    from music21 import note as m21note

    DOTTED_MAP = {
        (1.0,  0.5):  1.5,
        (2.0,  1.0):  3.0,
        (0.5,  0.25): 0.75,
        (4.0,  2.0):  6.0,
        (0.25, 0.125):0.375,
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
                        k = (float(n1.quarterLength), float(n2.quarterLength))
                        if k in DOTTED_MAP:
                            n1.quarterLength = DOTTED_MAP[k]
                            n1.tie = None
                            measure.remove(n2)
                            changed = True
                            break
    return score


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
    score.makeNotation(inPlace=True)
    simplify_notation(score)

    return score


def clean_musicxml(xml: str) -> str:
    """Remove music21 boilerplate that clutters the sheet-music viewer."""
    xml = re.sub(r"\s*<movement-title>.*?</movement-title>", "", xml, flags=re.DOTALL)
    xml = re.sub(r"\s*<movement-title\s*/>", "", xml)
    xml = re.sub(r"\s*<identification>.*?</identification>", "", xml, flags=re.DOTALL)
    xml = re.sub(r"\s*<work>.*?</work>", "", xml, flags=re.DOTALL)
    xml = re.sub(r"<score-instrument[^>]*>.*?</score-instrument>\s*", "", xml, flags=re.DOTALL)
    xml = re.sub(r"<midi-instrument[^>]*>.*?</midi-instrument>\s*", "", xml, flags=re.DOTALL)
    return xml


def score_notes_from_musicxml(musicxml: str) -> list:
    """Extract editable notes (pitch + quarter length) from MusicXML."""
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


def render_pdf_lilypond(score, base_path: str) -> str:
    """
    Render PDF via LilyPond.

    1. Use music21 to produce both the .ly source and an initial PDF.
    2. Inject paper settings into the .ly to prevent the last system
       from being clipped (ragged-last-bottom fix).
    3. Re-run LilyPond on the patched .ly to produce the final PDF.
    """
    import subprocess

    if base_path.endswith(".pdf"):
        base_path = base_path[:-4]

    ly_path = base_path + ".ly"

    score.write("lily.pdf", fp=base_path)

    if not os.path.isfile(ly_path):
        for candidate in (base_path + ".pdf", base_path + ".pdf.pdf"):
            if os.path.isfile(candidate):
                return candidate
        raise FileNotFoundError(f"LilyPond produced no output at {base_path}")

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

    if "\\paper" in ly:
        ly = re.sub(r'\\paper\s*\{[^}]*\}', paper.strip(), ly, count=1, flags=re.DOTALL)
    elif "\\score" in ly:
        ly = ly.replace("\\score", paper + "\\score", 1)
    else:
        ly = paper + ly

    with open(ly_path, "w", encoding="utf-8") as f:
        f.write(ly)

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
