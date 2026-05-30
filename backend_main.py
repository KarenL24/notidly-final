"""
FastAPI server for the tfinal.py transcription pipeline.
"""

import asyncio
import base64
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional

from tfinal import (
    export_score_pdf,
    rebuild_from_editor_notes,
    score_notes_from_musicxml,
    transcribe_file,
)

APP_DIR = Path(__file__).parent
FRONTEND_FILE = APP_DIR / "index.html"
EDITOR_FILE = APP_DIR / "editor.html"

app = FastAPI(title="Melody Transcription API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

executor = ThreadPoolExecutor(max_workers=2)

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"}
ALLOWED_CONTENT_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/m4a",
    "audio/mp4",
    "audio/ogg",
    "audio/flac",
    "audio/webm",
    "application/octet-stream",
}


class TimedNote(BaseModel):
    pitch: int
    note: str
    start: float
    end: float
    duration: float


class TranscriptionResponse(BaseModel):
    notes: List[TimedNote]
    note_count: int
    bpm: float
    time_signature: str
    key_signature: str
    duration: float
    processing_time: float
    musicxml: str
    midi_base64: str
    pdf_available: bool
    pdf_base64: Optional[str] = None
    pdf_error: Optional[str] = None


class ExportRequest(BaseModel):
    musicxml: str


class ScoreNote(BaseModel):
    pitch: int
    note: Optional[str] = None
    quarter_length: float


class RebuildRequest(BaseModel):
    score_notes: List[ScoreNote]
    bpm: float
    time_signature: str
    key_signature: str


class ParseScoreResponse(BaseModel):
    score_notes: List[ScoreNote]


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Melody Transcription API"}


@app.get("/")
async def serve_frontend():
    if FRONTEND_FILE.exists():
        return FileResponse(FRONTEND_FILE)
    return JSONResponse({"message": "Frontend not found. Place index.html next to backend_main.py."})


@app.get("/editor")
async def serve_editor():
    if EDITOR_FILE.exists():
        return FileResponse(EDITOR_FILE)
    raise HTTPException(status_code=404, detail="editor.html not found")


@app.post("/parse-score", response_model=ParseScoreResponse)
async def parse_score(body: ExportRequest):
    try:
        notes = score_notes_from_musicxml(body.musicxml)
        return ParseScoreResponse(
            score_notes=[ScoreNote(**n) for n in notes],
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/rebuild-score")
async def rebuild_score(body: RebuildRequest):
    try:
        payload = [
            {
                "pitch": n.pitch,
                "note": n.note,
                "quarter_length": n.quarter_length,
            }
            for n in body.score_notes
        ]
        if not payload:
            raise HTTPException(status_code=400, detail="Add at least one note.")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            rebuild_from_editor_notes,
            payload,
            body.bpm,
            body.time_signature,
            body.key_signature,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported format. Upload MP3, WAV, M4A, OGG, or FLAC.",
        )

    suffix = ext if ext else ".mp3"
    content = await file.read()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    start = datetime.now()
    loop = asyncio.get_event_loop()

    try:
        result = await loop.run_in_executor(executor, transcribe_file, tmp_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    processing_time = (datetime.now() - start).total_seconds()

    return TranscriptionResponse(
        processing_time=processing_time,
        **result,
    )


@app.post("/export/musicxml")
async def export_musicxml(body: ExportRequest):
    return Response(
        content=body.musicxml,
        media_type="application/vnd.recordare.musicxml+xml",
        headers={"Content-Disposition": 'attachment; filename="transcription.musicxml"'},
    )


@app.post("/export/pdf")
async def export_pdf(body: ExportRequest):
    import io
    from music21 import converter

    from tfinal import clean_musicxml

    score = converter.parse(clean_musicxml(body.musicxml), format="musicxml")
    pdf_bytes, pdf_error = export_score_pdf(score)

    if pdf_bytes is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "PDF export failed. Install LilyPond and ensure it is on your PATH. "
                f"Details: {pdf_error}"
            ),
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="transcription.pdf"'},
    )


@app.post("/export/midi")
async def export_midi(body: ExportRequest):
    import io
    from music21 import converter

    try:
        score = converter.parse(body.musicxml, format="musicxml")
        with tempfile.TemporaryDirectory() as tmp:
            midi_path = os.path.join(tmp, "score.mid")
            score.write("midi", fp=midi_path)
            with open(midi_path, "rb") as f:
                midi_bytes = f.read()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=midi_bytes,
        media_type="audio/midi",
        headers={"Content-Disposition": 'attachment; filename="transcription.mid"'},
    )


if __name__ == "__main__":
    import uvicorn

    # Port 8000 is often taken (e.g. Docker). Use 8765 for this app.
    PORT = int(os.environ.get("PORT", "8765"))
    uvicorn.run("backend_main:app", host="0.0.0.0", port=PORT, reload=True)
