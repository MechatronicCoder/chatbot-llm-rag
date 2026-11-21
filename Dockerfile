# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Etapa 1: dependencias en un entorno virtual aislado
# ---------------------------------------------------------------------------
FROM python:3.15-slim AS dependencias

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Solo el archivo de dependencias: esta capa se reutiliza mientras no cambie.
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Etapa 2: imagen final, sin herramientas de compilación ni caché de pip
# ---------------------------------------------------------------------------
FROM python:3.15-slim AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Usuario sin privilegios: si alguien explota la app, no es root en el contenedor.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=dependencias /opt/venv /opt/venv
COPY app ./app
COPY datos ./datos
COPY frontend ./frontend
RUN mkdir -p var && chown app:app var

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=2)"]

# La IP real del cliente detrás del ingress de Azure la resuelve la app con
# PROXIES_CONFIABLES (ver app/api/dependencias.py), no uvicorn: así no se confía
# en entradas de X-Forwarded-For que el propio cliente puede inventar.
CMD ["uvicorn", "app.main:crear_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
