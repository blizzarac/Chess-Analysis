FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STOCKFISH_PATH=/usr/games/stockfish CHESS_DATA_DIR=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends stockfish \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY chess_analysis ./chess_analysis
COPY static ./static

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)" || exit 1

CMD ["python", "-m", "chess_analysis"]
