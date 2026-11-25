FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt
COPY . .
# 2 processus x 8 fils : une conversion longue (jusqu'à ~90 s) ne bloque plus le reste du site
CMD gunicorn -b 0.0.0.0:${PORT:-10000} -t 150 -k gthread --workers 2 --threads 8 server:app
