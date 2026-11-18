# AegisFit - Flet GUI served as a web app.
#   docker build -t aegisfit .
#   docker run -d --name aegisfit -p 127.0.0.1:8550:8550 -v "${PWD}/data:/app/data" aegisfit
# then open http://localhost:8550 and add your Claude API key under Settings
# (or pass it with -e ANTHROPIC_API_KEY=...; a key saved in Settings takes priority).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLET_FORCE_WEB_SERVER=true \
    FLET_SERVER_IP=0.0.0.0 \
    FLET_SERVER_PORT=8550 \
    FITNESS_DATA_FILE=/app/data/fitness_data.json \
    FITNESS_LOG_FILE=/app/data/app.log \
    AEGISFIT_SETTINGS_FILE=/app/data/settings.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8550
VOLUME ["/app/data"]

CMD ["python", "main.py", "--web"]
