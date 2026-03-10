FROM pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime

# Avoid interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Europe/Paris

RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    gradio \
    pyannote.audio \
    demucs \
    requests

WORKDIR /app
COPY voice_studio.py /app/voice_studio.py

ENV VOICES_DIR=/voices
ENV MEDIA_DIR=/media
ENV WORK_DIR=/tmp/voice_studio
ENV VOICE_STUDIO_PORT=7862

EXPOSE 7862

CMD ["python3", "/app/voice_studio.py"]
