FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    lilypond \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# CPU-only torch is ~700 MB vs ~2 GB for CUDA; install it first so the
# later `pip install -r requirements.txt` sees it already satisfied.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -r requirements.txt

COPY backend_main.py tfinal.py index.html editor.html ./

ENV PORT=7860
EXPOSE 7860

CMD ["uvicorn", "backend_main:app", "--host", "0.0.0.0", "--port", "7860"]
