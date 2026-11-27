# Activo simulado: móvil, cargador y calendario (SIM). Contexto de construcción: raíz del repositorio.
FROM python:3.13.7-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY servicios/simulador/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 1000 -m activo && mkdir /estado && chown activo /estado
COPY sim/ sim/
COPY servicios/simulador/servicio.py .
USER activo
CMD ["python", "servicio.py"]
