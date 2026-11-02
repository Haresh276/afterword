# ---- build the React UI ----
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Python API serving the UI ----
FROM python:3.12-slim
# fonts + shaping libraries so the bilingual (Hindi/English) specimen certificate renders correctly
RUN apt-get update && apt-get install -y --no-install-recommends fonts-freefont-ttf libfribidi0 libharfbuzz0b libraqm0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=ui /ui/dist frontend/dist
WORKDIR /app/backend
ENV AFTERWORD_DB=/tmp/afterword.db
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
