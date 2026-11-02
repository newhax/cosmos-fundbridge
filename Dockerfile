# Single-container deploy: build the React UI, then FastAPI serves it from frontend/dist on one port.
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=web /web/dist ./frontend/dist
# Build geography + CSR dataset into the image so startup is fast.
RUN python scripts/geo.py load && python scripts/ingest.py
# Seeding is idempotent; run on every start because free hosts wipe the disk on restart.
CMD python scripts/seed_demo.py && python scripts/seed_platform.py && \
    uvicorn fundbridge.api:app --host 0.0.0.0 --port ${PORT:-8000}
