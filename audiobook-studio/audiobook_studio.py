#!/usr/bin/env python3
"""
audiobook_studio.py
Pre-processing tool for ebook2audiobook.
"""

import os, json, re, requests, shutil, subprocess, threading
import gradio as gr
from pathlib import Path

# ─────────────────────────────────────────
# Config
# ─────────────────────────────────────────
OLLAMA_URL   = os.environ.get("OLLAMA_URL",   "http://172.17.0.18:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:latest")
VOICES_DIR   = os.environ.get("VOICES_DIR",   "/app/voices")
EBOOKS_DIR   = os.environ.get("EBOOKS_DIR",   "/app/ebooks")
AUDIOBOOKS_DIR = os.environ.get("AUDIOBOOKS_DIR", "/app/audiobooks")
E2A_SCRIPT   = "/app/app.py"
PORT         = int(os.environ.get("STUDIO_PORT", "7861"))

CHUNK_SIZE      = 3000
ANALYSIS_SAMPLE = 8000

# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────
def list_voice_files() -> list:
    exts = {".wav", ".mp3", ".flac"}
    p = Path(VOICES_DIR)
    if not p.exists():
        return []
    return sorted([str(f) for f in p.rglob("*") if f.suffix.lower() in exts])

def extract_text(filepath: str) -> str:
    ext = Path(filepath).suffix.lower()
    if ext == ".epub":
        try:
            from ebooklib import epub, ITEM_DOCUMENT
            from bs4 import BeautifulSoup
            book = epub.read_epub(filepath, options={"ignore_ncx": True})
            parts = []
            for item in book.get_items_of_type(ITEM_DOCUMENT):
                soup = BeautifulSoup(item.get_content(), "html.parser")
                parts.append(soup.get_text(separator="\n"))
            return "\n\n".join(parts)
        except Exception as e:
            return f"ERROR: {e}"
    elif ext in (".txt", ".md"):
        return Path(filepath).read_text(encoding="utf-8", errors="ignore")
    return "ERROR: Format non supporté. Utilise .epub ou .txt"

# ─────────────────────────────────────────
# Ollama
# ─────────────────────────────────────────
def ollama_generate(prompt: str, system: str = "") -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 2048}
    }
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        return f"ERROR: {e}"

def detect_characters(text_sample: str) -> list:
    system = """Tu es un analyste littéraire. Identifie tous les personnages nommés dans cet extrait de roman.
Retourne UNIQUEMENT un tableau JSON valide, sans explication, sans markdown, sans backticks.
Chaque élément doit avoir : {"name": "Nom du personnage", "role": "narrator|protagonist|secondary|minor", "description": "description brève en français"}
Inclus toujours une entrée NARRATEUR pour la voix narrative."""
    response = ollama_generate(
        f"Analyse cet extrait et liste tous les personnages :\n\n{text_sample[:ANALYSIS_SAMPLE]}",
        system
    )
    try:
        return json.loads(re.sub(r"```[a-z]*", "", response).strip())
    except Exception:
        return [{"name": "NARRATEUR", "role": "narrator", "description": "Voix narrative"}]

def tag_chunk(chunk: str, characters: list, voice_map: dict) -> str:
    char_list   = "\n".join([f"- {c['name']} ({c['role']})" for c in characters])
    voice_paths = "\n".join([f"- {name}: {path}" for name, path in voice_map.items() if path])
    system = """Tu es un tagueur de texte pour la production d'audiobooks.
Règles :
- Entoure chaque réplique avec [voice:/chemin]...[/voice] en utilisant les chemins exacts fournis
- Entoure les passages narrateurs avec le chemin voix du NARRATEUR
- Ajoute des marqueurs d'émotion Bark DANS les balises voice si approprié : [laughs] [sighs] [whispers] [gasps] [clears throat]
- Conserve TOUT le texte original intact, ajoute UNIQUEMENT les balises
- Retourne UNIQUEMENT le texte balisé, sans explication"""
    prompt = f"Personnages :\n{char_list}\n\nVoix assignées :\n{voice_paths}\n\nTague ce texte :\n---\n{chunk}\n---"
    return ollama_generate(prompt, system)

# ─────────────────────────────────────────
# Global state
# ─────────────────────────────────────────
state = {
    "full_text":     "",
    "characters":    [],
    "voice_map":     {},
    "tagged_path":   "",
    "conversion":    {"running": False, "progress": 0.0, "log": "", "done": False, "error": ""}
}

# ─────────────────────────────────────────
# Tab 1 — Analyse
# ─────────────────────────────────────────
def do_analyze(file):
    if file is None:
        yield "⚠️ Aucun fichier uploadé.", "", [], []
        return
    text = extract_text(file.name)
    if text.startswith("ERROR"):
        yield text, "", [], []
        return
    state["full_text"] = text
    yield f"📖 {len(text.split()):,} mots extraits. Analyse des personnages...", "", [], []
    chars = detect_characters(text)
    state["characters"] = chars
    state["voice_map"]  = {c["name"]: "" for c in chars}
    names = [c["name"] for c in chars]
    yield (
        f"✅ {len(chars)} personnages détectés.",
        json.dumps(chars, indent=2, ensure_ascii=False),
        names,
        names
    )

def do_save_chars(char_json):
    try:
        chars = json.loads(char_json)
        state["characters"] = chars
        state["voice_map"]  = {c["name"]: state["voice_map"].get(c["name"], "") for c in chars}
        names = [c["name"] for c in chars]
        return f"✅ {len(chars)} personnages sauvegardés.", names, names
    except Exception as e:
        return f"❌ JSON invalide : {e}", [], []

# ─────────────────────────────────────────
# Tab 2 — Validation
# ─────────────────────────────────────────
def do_assign_voice(char_name, voice_path):
    if not char_name:
        return "⚠️ Sélectionne un personnage.", get_voice_summary()
    if not voice_path:
        return "⚠️ Sélectionne un fichier voix.", get_voice_summary()
    state["voice_map"][char_name] = voice_path
    assigned = sum(1 for v in state["voice_map"].values() if v)
    return f"✅ {char_name} → {voice_path}  ({assigned}/{len(state['voice_map'])} assignés)", get_voice_summary()

def get_voice_summary():
    if not state["voice_map"]:
        return "Aucun personnage chargé."
    lines = [f"**{n}**: {'✅ ' + Path(p).name if p else '❌ pas de voix'}"
             for n, p in state["voice_map"].items()]
    return "\n\n".join(lines)

def do_preview(chunk_idx):
    if not state["full_text"]: return "Pas de texte chargé.", ""
    chunks = [state["full_text"][i:i+CHUNK_SIZE] for i in range(0, len(state["full_text"]), CHUNK_SIZE)]
    idx    = max(0, min(int(chunk_idx), len(chunks)-1))
    tagged = tag_chunk(chunks[idx], state["characters"], state["voice_map"])
    return chunks[idx], tagged

def refresh_voices():
    voices = list_voice_files()
    return gr.update(choices=voices, value=voices[0] if voices else None)

# ─────────────────────────────────────────
# Tab 3 — Génération
# ─────────────────────────────────────────
def do_generate_tagged(progress=gr.Progress()):
    if not state["full_text"]:  return "⚠️ Pas de texte chargé.", ""
    if not state["characters"]: return "⚠️ Pas de personnages définis.", ""
    chunks = [state["full_text"][i:i+CHUNK_SIZE] for i in range(0, len(state["full_text"]), CHUNK_SIZE)]
    tagged_parts = []
    for i, chunk in enumerate(chunks):
        progress((i+1)/len(chunks), desc=f"Balisage {i+1}/{len(chunks)}...")
        result = tag_chunk(chunk, state["characters"], state["voice_map"])
        if result.startswith("ERROR"): return f"❌ Erreur LLM chunk {i+1}: {result}", ""
        tagged_parts.append(result)
    tagged_text = "\n".join(tagged_parts)
    out_path = os.path.join(EBOOKS_DIR, "tagged_output.txt")
    Path(out_path).write_text(tagged_text, encoding="utf-8")
    state["tagged_path"] = out_path
    return f"✅ Fichier balisé sauvegardé → {out_path}", tagged_text[:3000] + "\n\n[...tronqué...]"

def do_launch_e2a(language, default_voice_path, output_subdir,
                  bark_temp, bark_waveform_temp, output_format):
    if not state["tagged_path"] or not Path(state["tagged_path"]).exists():
        return "⚠️ Génère d'abord le texte balisé."
    if state["conversion"]["running"]:
        return "⚠️ Une conversion est déjà en cours."
    out_dir = os.path.join(AUDIOBOOKS_DIR, output_subdir.strip() or "studio_output")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    cmd = [
        "python3", E2A_SCRIPT,
        "--headless",
        "--ebook",          state["tagged_path"],
        "--tts_engine",     "bark",
        "--device",         "cuda",
        "--language",       language,
        "--output_format",  output_format,
        "--output_dir",     out_dir,
        "--bark_text_temp",     str(bark_temp),
        "--bark_waveform_temp", str(bark_waveform_temp),
    ]
    if default_voice_path:
        cmd += ["--voice", default_voice_path]
    state["conversion"] = {"running": True, "progress": 0.0, "log": "", "done": False, "error": ""}

    def _run():
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                line = line.rstrip()
                state["conversion"]["log"] = line
                m = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
                if m:
                    state["conversion"]["progress"] = float(m.group(1)) / 100.0
            proc.wait()
            state["conversion"]["running"] = False
            state["conversion"]["done"]    = True
            if proc.returncode != 0:
                state["conversion"]["error"] = f"Exit code {proc.returncode}"
        except Exception as e:
            state["conversion"]["running"] = False
            state["conversion"]["error"]   = str(e)

    threading.Thread(target=_run, daemon=True).start()
    return f"🚀 Conversion lancée → sortie : {out_dir}"

def poll_progress():
    c = state["conversion"]
    if c["done"] and not c["error"]: return 1.0, "✅ Conversion terminée !"
    if c["error"]:                   return 0.0, f"❌ Erreur : {c['error']}"
    if c["running"]:                 return c["progress"], f"⏳ {int(c['progress']*100)}%  —  {c['log']}"
    return 0.0, "En attente"

# ─────────────────────────────────────────
# Build UI
# ─────────────────────────────────────────
with gr.Blocks(title="🎙️ Audiobook Studio", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🎙️ Audiobook Studio\nPipeline de production d'audiobook multi-voix.")

    # Shared state between tabs
    char_names_state = gr.State([])

    with gr.Tabs():

        # ── Tab 1 ─────────────────────────────────────────────────
        with gr.Tab("📖 1. Analyse"):
            gr.Markdown("Upload ton ebook. Gemma3 détectera automatiquement tous les personnages.")
            upload  = gr.File(label="EPUB ou TXT", file_types=[".epub",".txt",".md"])
            ana_btn = gr.Button("🔍 Analyser", variant="primary")
            status1 = gr.Textbox(label="Statut", interactive=False)
            char_json = gr.Code(label="Personnages détectés (JSON éditable)", language="json", lines=18)
            with gr.Row():
                save_btn    = gr.Button("💾 Sauvegarder les personnages", variant="secondary")
                save_status = gr.Textbox(label="", interactive=False, scale=3)

            # char_names_state_2 used to sync Tab 2 dropdown
            char_names_state_2 = gr.State([])

            ana_btn.click(
                do_analyze, [upload],
                [status1, char_json, char_names_state, char_names_state_2]
            )
            save_btn.click(
                do_save_chars, [char_json],
                [save_status, char_names_state, char_names_state_2]
            )

        # ── Tab 2 ─────────────────────────────────────────────────
        with gr.Tab("✏️ 2. Validation"):
            with gr.Row():
                with gr.Column(scale=1):
                    char_dd    = gr.Dropdown(label="Personnage", choices=[], interactive=True)
                    voice_dd   = gr.Dropdown(label="Fichier voix", choices=list_voice_files(), interactive=True)
                    refresh_v  = gr.Button("🔄 Rafraîchir les voix")
                    assign_btn = gr.Button("🎤 Assigner la voix", variant="primary")
                    assign_st  = gr.Textbox(label="", interactive=False)
                with gr.Column(scale=2):
                    voice_sum  = gr.Markdown(value="Aucun personnage chargé.")

            gr.Markdown("---\n### 🔬 Prévisualiser le balisage")
            chunk_sl   = gr.Slider(0, 200, 0, step=1, label="Index du chunk")
            prev_btn   = gr.Button("👁️ Prévisualiser", variant="secondary")
            with gr.Row():
                orig_prev   = gr.Textbox(label="Texte original", lines=8, interactive=False)
                tagged_prev = gr.Textbox(label="Texte balisé",   lines=8, interactive=False)

            # Sync character dropdown from Tab 1
            char_names_state.change(
                lambda names: gr.update(choices=names, value=names[0] if names else None),
                [char_names_state], [char_dd]
            )
            refresh_v.click(refresh_voices, [], [voice_dd])
            assign_btn.click(do_assign_voice, [char_dd, voice_dd], [assign_st, voice_sum])
            prev_btn.click(do_preview, [chunk_sl], [orig_prev, tagged_prev])

        # ── Tab 3 ─────────────────────────────────────────────────
        with gr.Tab("🚀 3. Génération"):
            gr.Markdown("### Étape 1 — Générer le texte balisé")
            gen_btn    = gr.Button("⚡ Générer le fichier balisé", variant="primary")
            gen_status = gr.Textbox(label="Statut", interactive=False)
            gen_prev   = gr.Textbox(label="Aperçu", lines=12, interactive=False)
            gen_btn.click(do_generate_tagged, [], [gen_status, gen_prev])

            gr.Markdown("---\n### Étape 2 — Configurer et lancer ebook2audiobook")
            with gr.Row():
                with gr.Column():
                    lang_in    = gr.Textbox(value="fra", label="Langue (iso639-3)")
                    out_dir_in = gr.Textbox(value="studio_output", label="Sous-dossier de sortie dans /audiobooks/")
                    fmt_in     = gr.Dropdown(["m4b","mp3","flac","ogg"], value="m4b", label="Format de sortie")
                with gr.Column():
                    def_voice_dd   = gr.Dropdown(
                        label="Voix par défaut (narration)",
                        choices=list_voice_files(), interactive=True
                    )
                    refresh_v2     = gr.Button("🔄 Rafraîchir")
                    bark_temp_in   = gr.Slider(0.1, 1.0, value=0.7, step=0.05, label="Bark text temperature")
                    bark_wform_in  = gr.Slider(0.1, 1.0, value=0.7, step=0.05, label="Bark waveform temperature")

            launch_btn = gr.Button("🎬 Lancer la conversion", variant="primary", size="lg")
            launch_st  = gr.Textbox(label="", interactive=False)

            gr.Markdown("---\n### Progression")
            prog_bar   = gr.Slider(0, 1, value=0, label="Progression", interactive=False)
            prog_log   = gr.Textbox(label="Étape en cours", interactive=False)
            timer      = gr.Timer(value=3)

            refresh_v2.click(refresh_voices, [], [def_voice_dd])
            launch_btn.click(
                do_launch_e2a,
                [lang_in, def_voice_dd, out_dir_in, bark_temp_in, bark_wform_in, fmt_in],
                [launch_st]
            )
            timer.tick(poll_progress, [], [prog_bar, prog_log])

if __name__ == "__main__":
    print(f"🎙️  Audiobook Studio on port {PORT}")
    print(f"    Ollama  : {OLLAMA_URL}  model: {OLLAMA_MODEL}")
    demo.launch(server_name="0.0.0.0", server_port=PORT, show_error=True)
