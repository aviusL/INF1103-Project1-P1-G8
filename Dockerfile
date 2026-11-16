FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV DISPLAY=host.docker.internal:0.0
ENV SDL_VIDEODRIVER=x11

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 libglib2.0-0 libsdl2-2.0-0 libsdl2-image-2.0-0 \
    libsdl2-mixer-2.0-0 libsdl2-ttf-2.0-0

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "kivy_app.py"]
