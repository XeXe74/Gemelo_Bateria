# Servicio del gemelo: decision. Contexto de construcción: raíz del repositorio.
FROM python:3.13.7-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY servicios/decision/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 1000 -m gemelo && mkdir /datos && chown gemelo /datos
COPY gemelo/ gemelo/
COPY servicios/decision/servicio.py .
USER gemelo
CMD ["python", "servicio.py"]
