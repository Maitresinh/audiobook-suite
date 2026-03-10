#!/usr/bin/env python3
"""
voice_studio.py
Voice preparation pipeline for ebook2audiobook.
- Extracts audio from local video files
- Separates voice from music/sfx via Demucs
- Diarizes speakers via pyannote-audio
- Lets user identify and export a specific speaker's voice

Usage: python3 voice_studio.py
"""

import os, json, shutil, threading, tempfile, subprocess
from pathlib import Path
import gradio as gr

# ─────────────────────────────────────────
# Config
# ─────────────────────────────────────────
HF_TOKEN     = os.environ.get("HF_TOKEN", "")
MEDIA_DIR    = os.environ.get("MEDIA_DIR",  "/media")
VOICES_DIR   = os.environ.get("VOICES_DIR", "/voices")
WORK_DIR     = os.environ.get("WORK_DIR",   "/tmp/voice_studio")
PORT         = int(os.environ.get("VOICE_STUDIO_PORT", "7862"))

Path(WORK_DIR).mkdir(parents=True, exist_ok=True)
Path(VOICES_DIR).mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────
# State
# ─────────────────────────────────────────
state = {
    "source_files":    [],
    "extracted_wav":   "",   # mono 16kHz wav after ffmpeg
    "vocals_wav":      "",   # after Demucs
    "diarization":     [],   # list of {speaker, start, end}
    "speakers":        [],   # unique speaker ids
    "selected_speaker": "",
    "job": {"running": False, "log": "", "progress": 0.0, "done": False, "error": ""}
}

# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────
def log(msg: str):
    print(msg)
    state["job"]["log"] = msg

def run_cmd(cmd: list, desc: str = "") -> bool:
    if desc: log(desc)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        state["job"]["error"] = r.stderr[-500:]
        return False
    return True

def list_video_files(folder: str) -> list:
    exts = {".avi", ".mkv", ".mp4", ".mov", ".flv", ".wmv", ".m4v"}
    p = Path(folder)
    if not p.exists():
        return []
    return sorted([str(f) for f in p.rglob("*") if f.suffix.lower() in exts])

# ─────────────────────────────────────────
# Tab 1 — Extraction
# ─────────────────────────────────────────
def do_scan_folder(folder: str):
    files = list_video_files(folder)
    state["source_files"] = files
    if not files:
        return gr.update(choices=[], value=None), f"⚠️ No video files found in {folder}"
    names = [Path(f).name for f in files]
    return gr.update(choices=names, value=names[0]), f"✅ Found {len(files)} files."

def do_extract(selected_names: list, progress=gr.Progress()):
    if not selected_names:
        return "⚠️ Select at least one file.", ""
    
    name_to_path = {Path(f).name: f for f in state["source_files"]}
    selected_paths = [name_to_path[n] for n in selected_names if n in name_to_path]
    
    out_parts = []
    for i, path in enumerate(selected_paths):
        progress((i+1)/len(selected_paths), desc=f"Extracting {i+1}/{len(selected_paths)}...")
        out = os.path.join(WORK_DIR, f"extract_{i:03d}.wav")
        ok = run_cmd([
            "ffmpeg", "-y", "-i", path,
            "-vn", "-ac", "1", "-ar", "16000",
            "-acodec", "pcm_s16le", out
        ], f"Extracting audio from {Path(path).name}...")
        if not ok:
            return f"❌ ffmpeg failed on {Path(path).name}: {state['job']['error']}", ""
        out_parts.append(out)
    
    if len(out_parts) == 1:
        state["extracted_wav"] = out_parts[0]
    else:
        # Concatenate all parts
        concat_list = os.path.join(WORK_DIR, "concat.txt")
        with open(concat_list, "w") as f:
            for p in out_parts:
                f.write(f"file '{p}'\n")
        merged = os.path.join(WORK_DIR, "extracted_merged.wav")
        ok = run_cmd([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list, "-c", "copy", merged
        ], "Merging audio files...")
        if not ok:
            return f"❌ Merge failed: {state['job']['error']}", ""
        state["extracted_wav"] = merged

    # Get duration
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", state["extracted_wav"]],
        capture_output=True, text=True
    )
    duration = float(r.stdout.strip()) if r.stdout.strip() else 0
    mins = int(duration // 60)
    return f"✅ Extracted {len(out_parts)} file(s) → {mins} min total.", state["extracted_wav"]

# ─────────────────────────────────────────
# Tab 2 — Demucs separation
# ─────────────────────────────────────────
def do_separate(progress=gr.Progress()):
    if not state["extracted_wav"] or not Path(state["extracted_wav"]).exists():
        return "⚠️ Extract audio first (Tab 1).", ""

    progress(0.1, desc="Loading Demucs model...")
    log("Running Demucs voice separation...")

    out_dir = os.path.join(WORK_DIR, "demucs_out")
    Path(out_dir).mkdir(exist_ok=True)

    # Use htdemucs_ft model, vocals only
    ok = run_cmd([
        "python3", "-m", "demucs",
        "--two-stems", "vocals",
        "-n", "htdemucs_ft",
        "--out", out_dir,
        "--mp3",
        state["extracted_wav"]
    ], "Separating voice from music/sfx (this may take a while)...")

    if not ok:
        # Fallback to basic model
        log("Falling back to htdemucs model...")
        ok = run_cmd([
            "python3", "-m", "demucs",
            "--two-stems", "vocals",
            "-n", "htdemucs",
            "--out", out_dir,
            state["extracted_wav"]
        ])
        if not ok:
            return f"❌ Demucs failed: {state['job']['error']}", ""

    # Find vocals output
    vocals = list(Path(out_dir).rglob("vocals*"))
    if not vocals:
        return "❌ Demucs ran but vocals file not found.", ""

    vocals_path = str(vocals[0])
    
    # Convert to mono 16kHz wav for pyannote
    vocals_wav = os.path.join(WORK_DIR, "vocals.wav")
    run_cmd([
        "ffmpeg", "-y", "-i", vocals_path,
        "-ac", "1", "-ar", "16000",
        "-acodec", "pcm_s16le", vocals_wav
    ])
    
    state["vocals_wav"] = vocals_wav
    progress(1.0, desc="Done!")
    return "✅ Voice separated successfully.", vocals_wav

# ─────────────────────────────────────────
# Tab 3 — Diarization
# ─────────────────────────────────────────
def do_diarize(progress=gr.Progress()):
    if not state["vocals_wav"] or not Path(state["vocals_wav"]).exists():
        return "⚠️ Separate voices first (Tab 2).", gr.update(choices=[], value=None), ""
    if not HF_TOKEN:
        return "⚠️ HF_TOKEN not set.", gr.update(choices=[], value=None), ""

    progress(0.1, desc="Loading pyannote model...")
    log("Loading diarization pipeline...")

    try:
        from pyannote.audio import Pipeline
        import torch

        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=HF_TOKEN
        )
        if torch.cuda.is_available():
            pipeline = pipeline.to(torch.device("cuda"))
            log("Using GPU for diarization.")
        
        progress(0.3, desc="Running diarization...")
        log("Diarizing... (this takes a few minutes)")
        
        diarization = pipeline(state["vocals_wav"])
        
        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append({
                "speaker": speaker,
                "start": round(turn.start, 2),
                "end":   round(turn.end, 2),
                "duration": round(turn.end - turn.start, 2)
            })
        
        state["diarization"] = segments
        
        # Count speaker stats
        from collections import defaultdict
        speaker_time = defaultdict(float)
        for s in segments:
            speaker_time[s["speaker"]] += s["duration"]
        
        speakers = sorted(speaker_time.keys(), key=lambda x: -speaker_time[x])
        state["speakers"] = speakers
        
        # Format summary
        summary = "**Speaker summary:**\n\n"
        for sp in speakers:
            mins = int(speaker_time[sp] // 60)
            secs = int(speaker_time[sp] % 60)
            summary += f"- **{sp}**: {mins}m{secs:02d}s ({len([s for s in segments if s['speaker']==sp])} segments)\n"
        
        # Save diarization JSON
        json_path = os.path.join(WORK_DIR, "diarization.json")
        with open(json_path, "w") as f:
            json.dump(segments, f, indent=2)
        
        progress(1.0)
        return (
            f"✅ Found {len(speakers)} speakers, {len(segments)} segments.",
            gr.update(choices=speakers, value=speakers[0] if speakers else None),
            summary
        )
    except Exception as e:
        return f"❌ Diarization failed: {e}", gr.update(choices=[]), ""

def do_preview_speaker(speaker: str, segment_idx: int):
    """Extract a short sample of the selected speaker for listening."""
    if not speaker or not state["diarization"]:
        return None, "No diarization data."
    
    segs = [s for s in state["diarization"] if s["speaker"] == speaker]
    if not segs:
        return None, f"No segments for {speaker}."
    
    idx = max(0, min(int(segment_idx), len(segs)-1))
    seg = segs[idx]
    
    preview_path = os.path.join(WORK_DIR, f"preview_{speaker}_{idx}.wav")
    run_cmd([
        "ffmpeg", "-y",
        "-i", state["vocals_wav"],
        "-ss", str(seg["start"]),
        "-to", str(seg["end"]),
        "-acodec", "pcm_s16le", preview_path
    ])
    
    total = len(segs)
    return preview_path, f"Segment {idx+1}/{total} — {seg['start']}s → {seg['end']}s ({seg['duration']}s)"

# ─────────────────────────────────────────
# Tab 4 — Export
# ─────────────────────────────────────────
def do_export(speaker: str, voice_name: str, min_duration: float, progress=gr.Progress()):
    if not speaker:
        return "⚠️ Select a speaker first.", ""
    if not voice_name.strip():
        return "⚠️ Give a name to the voice file.", ""
    if not state["diarization"]:
        return "⚠️ Run diarization first (Tab 3).", ""

    segs = [s for s in state["diarization"]
            if s["speaker"] == speaker and s["duration"] >= min_duration]
    
    if not segs:
        return f"⚠️ No segments ≥ {min_duration}s for {speaker}.", ""

    # Extract each segment
    parts = []
    for i, seg in enumerate(segs):
        progress((i+1)/len(segs), desc=f"Extracting segment {i+1}/{len(segs)}...")
        part = os.path.join(WORK_DIR, f"seg_{speaker}_{i:04d}.wav")
        ok = run_cmd([
            "ffmpeg", "-y",
            "-i", state["vocals_wav"],
            "-ss", str(seg["start"]),
            "-to", str(seg["end"]),
            "-acodec", "pcm_s16le", part
        ])
        if ok:
            parts.append(part)

    if not parts:
        return "❌ No segments extracted.", ""

    # Concatenate
    concat_list = os.path.join(WORK_DIR, "export_concat.txt")
    with open(concat_list, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")

    safe_name = voice_name.strip().replace(" ", "_").replace("/", "_")
    out_path = os.path.join(VOICES_DIR, f"{safe_name}.wav")

    ok = run_cmd([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list,
        "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
        out_path
    ], f"Assembling {len(parts)} segments...")

    if not ok:
        return f"❌ Export failed: {state['job']['error']}", ""

    # Duration
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", out_path],
        capture_output=True, text=True
    )
    duration = float(r.stdout.strip()) if r.stdout.strip() else 0
    mins = int(duration // 60)
    secs = int(duration % 60)

    return (
        f"✅ Exported {len(parts)} segments → {mins}m{secs:02d}s\nSaved to: {out_path}",
        out_path
    )

# ─────────────────────────────────────────
# Build UI
# ─────────────────────────────────────────
with gr.Blocks(title="🎤 Voice Studio", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🎤 Voice Studio\nExtract and clone a specific character's voice from video files.")

    with gr.Tabs():

        # ── Tab 1 — Extraction ──────────────────────────────────────
        with gr.Tab("🎬 1. Extraction"):
            gr.Markdown("Scan a folder and extract audio from selected video files.")
            with gr.Row():
                folder_in = gr.Textbox(
                    value="/media/Capitaine Flam/Season 1",
                    label="Source folder"
                )
                scan_btn = gr.Button("🔍 Scan", variant="secondary")
            scan_status = gr.Textbox(label="", interactive=False)
            file_select = gr.CheckboxGroup(label="Select episodes", choices=[])
            extract_btn = gr.Button("⚡ Extract audio", variant="primary")
            extract_status = gr.Textbox(label="Status", interactive=False)
            extracted_path = gr.Textbox(label="Extracted file", interactive=False)

            scan_btn.click(do_scan_folder, [folder_in], [file_select, scan_status])
            extract_btn.click(do_extract, [file_select], [extract_status, extracted_path])

        # ── Tab 2 — Séparation ──────────────────────────────────────
        with gr.Tab("🎵 2. Séparation"):
            gr.Markdown("Separate voice from music and sound effects using **Demucs**.")
            gr.Markdown("> ⚠️ This step can take 10–30 min depending on total audio length.")
            sep_btn    = gr.Button("🎙️ Separate voices", variant="primary")
            sep_status = gr.Textbox(label="Status", interactive=False)
            vocals_out = gr.Audio(label="Isolated vocals preview", interactive=False)

            sep_btn.click(do_separate, [], [sep_status, vocals_out])

        # ── Tab 3 — Diarisation ─────────────────────────────────────
        with gr.Tab("👥 3. Diarisation"):
            gr.Markdown("Identify **who speaks when** using **pyannote-audio**.")
            gr.Markdown("> ⚠️ First run will download the model (~1GB). Requires HF_TOKEN.")
            diar_btn     = gr.Button("🔍 Diarize", variant="primary")
            diar_status  = gr.Textbox(label="Status", interactive=False)
            speaker_summary = gr.Markdown()

            gr.Markdown("---\n### 🔊 Preview a speaker")
            with gr.Row():
                speaker_dd  = gr.Dropdown(label="Speaker", choices=[], interactive=True)
                seg_slider  = gr.Slider(0, 50, 0, step=1, label="Segment index")
            preview_btn  = gr.Button("▶️ Listen", variant="secondary")
            with gr.Row():
                preview_audio = gr.Audio(label="Sample", interactive=False)
                preview_info  = gr.Textbox(label="", interactive=False)

            diar_btn.click(do_diarize, [], [diar_status, speaker_dd, speaker_summary])
            preview_btn.click(do_preview_speaker, [speaker_dd, seg_slider], [preview_audio, preview_info])

        # ── Tab 4 — Export ──────────────────────────────────────────
        with gr.Tab("💾 4. Export"):
            gr.Markdown("Select the speaker to keep and export a clean voice file ready for XTTSv2 cloning.")
            with gr.Row():
                export_speaker = gr.Dropdown(label="Speaker to export", choices=[], interactive=True)
                voice_name_in  = gr.Textbox(label="Voice name (filename)", value="capitaine_flam")
            min_dur_in = gr.Slider(0.5, 10.0, value=2.0, step=0.5,
                                   label="Minimum segment duration (seconds) — filters out short fragments")
            export_btn    = gr.Button("💾 Export voice", variant="primary", size="lg")
            export_status = gr.Textbox(label="Status", lines=3, interactive=False)
            export_path   = gr.Textbox(label="Output file", interactive=False)

            # Sync speaker dropdown from Tab 3
            diar_btn.click(
                lambda choices, val: (gr.update(choices=choices, value=val),
                                      gr.update(choices=choices, value=val)),
                [speaker_dd, speaker_dd],
                [speaker_dd, export_speaker]
            )
            export_btn.click(
                do_export,
                [export_speaker, voice_name_in, min_dur_in],
                [export_status, export_path]
            )

if __name__ == "__main__":
    print(f"🎤 Voice Studio on port {PORT}")
    print(f"   Media  : {MEDIA_DIR}")
    print(f"   Voices : {VOICES_DIR}")
    print(f"   HF_TOKEN: {'set' if HF_TOKEN else 'NOT SET ⚠️'}")
    demo.launch(server_name="0.0.0.0", server_port=PORT, show_error=True)
