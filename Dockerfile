# Frontline Assistant web app.
# Build and run together with Ollama via docker-compose.yml (see README, "Run as a web app on a server").
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Build the keyword search index into the image (semantic embeddings are added later with Ollama).
RUN python -m rag.ingest

ENV RAG_STATE_DIR=/app/state \
    OLLAMA_URL=http://ollama:11434 \
    PYTHONUNBUFFERED=1
VOLUME /app/state
EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
