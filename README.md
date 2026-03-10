# Audiobook Suite

Companion tools for [ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook) to produce professional multi-voice audiobooks with emotion markers.

## Components

### 🎙️ Audiobook Studio (port 7861)
Pre-processing pipeline that prepares an ebook for multi-voice TTS production.

**Features:**
- Automatic character detection via Ollama (gemma3 / mistral)
- Voice assignment per character
- Text tagging with `[voice:...]...[/voice]` markers
- Emotion markers for Bark engine: `[laughs]`, `[sighs]`, `[whispers]`...
- Direct launch of ebook2audiobook in headless mode

**Workflow:**
1. Upload EPUB or TXT
2. LLM detects all characters automatically
3. Assign a `.wav` voice file to each character
4. Preview tagging on a sample chunk
5. Generate fully tagged text
6. Launch ebook2audiobook with Bark engine

---

### 🎤 Voice Studio (port 7862)
Extract and clone a specific character's voice from video files.

**Features:**
- Audio extraction from local video files (ffmpeg)
- Voice/music separation via Demucs
- Speaker diarization via pyannote-audio
- Per-speaker preview and identification
- Export clean voice file ready for XTTSv2 cloning

**Workflow:**
1. Scan a folder of video files (AVI, MKV, MP4...)
2. Extract and merge audio
3. Demucs separates voice from music/sfx
4. pyannote-audio identifies who speaks when
5. Listen to each speaker and identify the target character
6. Export all segments of that speaker as a clean `.wav`

---

## Requirements

- [ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook) Docker container (`cu128` image)
- [Ollama](https://ollama.ai) with `gemma3:latest` or `mistral:7b`
- HuggingFace token (for pyannote-audio) — [get one here](https://huggingface.co/settings/tokens)
- Accept pyannote model terms:
  - https://huggingface.co/pyannote/speaker-diarization-3.1
  - https://huggingface.co/pyannote/segmentation-3.0

---

## Installation

### Audiobook Studio

Add to your ebook2audiobook `Dockerfile`:

```dockerfile
FROM athomasson2/ebook2audiobook:cu128

# Fix: CUDA always falls back to CPU in full_docker mode
RUN sed -i "s/if not devices\['CUDA'\]\['found'\]:/import torch\n                                if not torch.cuda.is_available():/" /app/lib/core.py

# Fix: output directly to /audiobooks/ instead of /audiobooks/gui/host/<uuid>/
RUN sed -i "s|audiobooks_gradio_dir = os.path.abspath(os.path.join('audiobooks','gui','gradio'))|audiobooks_gradio_dir = os.path.abspath('audiobooks')|" /app/lib/conf.py && \
    sed -i "s|audiobooks_host_dir = os.path.abspath(os.path.join('audiobooks','gui','host'))|audiobooks_host_dir = os.path.abspath('audiobooks')|" /app/lib/conf.py

COPY audiobook_studio.py /app/audiobook_studio.py
```

```bash
docker build -t ebook2audiobook:custom .

docker run -d \
  --name ebook2audiobook \
  --gpus all \
  -p 7860:7860 \
  -p 7861:7861 \
  -v /path/to/ebooks:/app/ebooks \
  -v /path/to/audiobooks:/app/audiobooks \
  -v /path/to/models:/app/models \
  -v /path/to/voices:/app/voices \
  ebook2audiobook:custom

docker exec -d ebook2audiobook bash -c "nohup python3 /app/audiobook_studio.py > /tmp/studio.log 2>&1 &"
```

### Voice Studio

```bash
# Create .env file
echo "HF_TOKEN=your_token_here" > voice-studio/.env

docker build -t voice-studio ./voice-studio

docker run -d \
  --name voice-studio \
  --gpus all \
  -p 7862:7862 \
  --env-file voice-studio/.env \
  -v /path/to/media:/media \
  -v /path/to/voices:/voices \
  voice-studio
```

---

## Known issues / Notes

- Emotion markers (`[laughs]`, `[sighs]`...) only work with the **Bark** TTS engine
- XTTSv2 produces better voice quality but does not support emotion markers
- The CUDA fix in `Dockerfile.e2a` will become unnecessary once the upstream bug is fixed
- Tracked upstream issue: https://github.com/DrewThomasson/ebook2audiobook/issues

---

## Roadmap

- [ ] Voice mixer (blend multiple voice files)
- [ ] F5-TTS integration (better quality + expressiveness)
- [ ] Automatic MAJ script
- [ ] Per-engine tagging mode (XTTSv2 vs Bark)
