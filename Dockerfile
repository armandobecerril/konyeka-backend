FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema para cryptography/lxml en caso de que no haya wheel prebuilt
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium para el RPA de Opinión de Cumplimiento / Constancia de Situación
# Fiscal (app/sat_documentos/rpa.py). --with-deps instala también las
# librerías del sistema que Chromium headless necesita en Debian.
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

# Aplica migraciones y levanta la API. DATABASE_URL, JWT_SECRET_KEY y
# CORS_ORIGINS se inyectan como variables de entorno del Container App.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
