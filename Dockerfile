# Frontline Assistant web app (version 2).
# Build and run together with Ollama via docker-compose.yml (see README, "Run on a server").
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# The four public documents ship in data/ (download them if they are not in the build folder).
RUN python scripts/download_documents.py || true

# Documents changed in the app, accounts, logs and restore points live on the /app/state volume.
ENV RAG_DATA_DIR=/app/state/library \
    RAG_STATE_DIR=/app/state \
    OLLAMA_URL=http://ollama:11434 \
    NETWORK_GUARD=block \
    ALLOWED_HOSTS=ollama \
    PYTHONUNBUFFERED=1
VOLUME /app/state
EXPOSE 8501

CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
