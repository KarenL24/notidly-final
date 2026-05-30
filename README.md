---
title: Melody Transcription
emoji: 🎵
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# Melody Transcription

Monophonic audio (vocals, single-instrument melodies) → sheet music, MIDI, and PDF.

The pipeline uses **CREPE** for pitch tracking, **music21** for notation, and a small **FastAPI** web app for upload, playback, editing, and export.

## Features

- Upload **MP3**, **WAV**, or **M4A**
- View **sheet music** in the browser (OpenSheetMusicDisplay)
- Playback: **transcription**, **original audio**, or **overlay**
- **Edit score** page: change notes, durations, BPM, key, and time signature
- Export **MusicXML**, **MIDI**, and **PDF** (PDF via LilyPond)

## Requirements

- **Python 3.10** (recommended)
- **ffmpeg** — for decoding MP3/M4A (often via `librosa`)
- **LilyPond** (optional) — for PDF export  
  Install from [lilypond.org](https://lilypond.org) and ensure `lilypond` is on your `PATH`.

## Setup

```bash
cd attempt2

# Create and activate a virtual environment (if you don't have venv/ yet)
python3.10 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

First run may take a while while **torch** and **crepe** download models.

## Run the web app

```bash
./start.sh
```

Then open **http://localhost:8765** in your browser.

The app uses port **8765** by default (not 8000), so it does not clash with other local services.

To use a different port:

```bash
PORT=9000 ./start.sh
```

Stop the server with **Ctrl+C**.

### Run from Cursor / VS Code (Run button)

This repo includes `.vscode/launch.json`. You do **not** need a separate terminal if you use:

1. Open the **`attempt2`** folder as your workspace root (so paths resolve correctly).
2. **Run and Debug** sidebar (play icon with bug) → choose **Start transcription server** → **Start Debugging** (F5).

Or: **Terminal → Run Task…** → **Start transcription server**.

The default green **Run** button on a random file (e.g. `README.md` or `index.html`) only runs that file. `index.html` is not the server — it is static UI served by `backend_main.py`, so you must launch the **uvicorn** configuration above.

### Using the UI

1. Upload an audio file on the home page.
2. Wait for transcription (CREPE can take ~30 seconds per file).
3. Review the score, use playback modes, and download exports.
4. Click **Edit score** to open the editor at `/editor`, change notes, then **Save & return**.

## Command-line transcription

You can run the pipeline without the web server:

```bash
source venv/bin/activate
python tfinal.py path/to/audio.mp3
```

This writes in the current directory:

- `output.mid`
- `output.musicxml`
- `output.pdf` (if LilyPond is installed)

## Project layout

| File | Role |
|------|------|
| `tfinal.py` | Transcription pipeline (CREPE, segmentation, quantization, export) |
| `backend_main.py` | FastAPI server and API routes |
| `index.html` | Main upload / results UI |
| `editor.html` | Score editor UI |
| `start.sh` | Start script (`uvicorn` on port 8765) |
| `requirements.txt` | Python dependencies |

## API (for reference)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Main frontend |
| `GET` | `/editor` | Editor frontend |
| `GET` | `/health` | Health check |
| `POST` | `/transcribe` | Upload audio → JSON with notes, MusicXML, MIDI, PDF |
| `POST` | `/parse-score` | MusicXML → editable note list |
| `POST` | `/rebuild-score` | Edited notes → updated MusicXML / MIDI / PDF |
| `POST` | `/export/pdf` | MusicXML → PDF |
| `POST` | `/export/midi` | MusicXML → MIDI |

Interactive API docs (when the server is running): **http://localhost:8765/docs**

## Troubleshooting

**Wrong page on port 8000**  
Another app may be using port 8000. Use **http://localhost:8765** for this project.

**PDF export fails**  
Install LilyPond and confirm it works:

```bash
lilypond --version
```

**Transcription errors on MP3**  
Install ffmpeg (e.g. `brew install ffmpeg` on macOS).

**Server not running**  
From `attempt2`:

```bash
source venv/bin/activate
./start.sh
```

## License

Private / practice project — add a license if you plan to distribute.
