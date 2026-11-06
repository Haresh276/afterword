# Afterword: one container, the Python API serving the prebuilt React UI (frontend/dist is committed,
# so neither Render nor a judge needs Node). Rebuild the UI with:  cd frontend && npm ci && npm run build
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
 && pip install --no-cache-dir --no-deps paypal-agent-toolkit==1.11.0
COPY backend/ backend/
COPY evidence/ evidence/
COPY frontend/dist/ frontend/dist/
WORKDIR /app/backend
ENV AFTERWORD_DB=/tmp/afterword.db PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/api/status', timeout=4)"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
