import re, uuid, io, os
from ebooklib import epub
from PIL import Image

src = "/app/ebooks/tagged_output_fixed.txt"
out = "/app/ebooks/tagged_output.epub"

text = open(src, encoding="utf-8").read()
print(f"Chars: {len(text)}")

# ── Normaliser les tags LLM mal formés ───────────────────────────────────────
text = re.sub(
    r'\[(?!voice:|/voice\b|break\b|pause\b)[^:\]]+:(/app/voices/[^\]]+)\]',
    r'[voice:\1]', text)
text = re.sub(
    r'\[(?!voice:|/voice\b|break\b|pause\b)[^:\]]+:(?!/app/)([^\]]+\.wav)\]',
    r'[voice:/app/voices/\1]', text)

# ── Extraire les blocs ───────────────────────────────────────────────────────
BLOCK_RE = re.compile(r'\[voice:([^\]]+)\](.*?)\[/voice\]', re.DOTALL)
raw_blocks = BLOCK_RE.findall(text)
print(f"Blocs extraits : {len(raw_blocks)}")

# ── Nettoyer le contenu de chaque bloc (supprimer SML imbriqués) ─────────────
def clean_content(content: str) -> str:
    # Supprimer toute balise SML résiduelle dans le contenu
    content = re.sub(r'\[voice:[^\]]+\]', '', content)
    content = re.sub(r'\[/voice\]', '', content)
    content = re.sub(r'\[/?(?:break|pause)[^\]]*\]', '', content)
    return content.strip()

all_blocks = [(p.strip(), clean_content(c)) for p, c in raw_blocks if clean_content(c)]
print(f"Blocs après nettoyage : {len(all_blocks)}")

# Diagnostic : combien avaient du contenu imbriqué ?
nested = sum(1 for _, c in raw_blocks if re.search(r'\[voice:|/voice\]', c))
print(f"Blocs avec SML imbriqués (nettoyés) : {nested}")

# ── Regrouper en chapitres (~50k chars) ──────────────────────────────────────
chapters, current, sz = [], [], 0
for path, content in all_blocks:
    block_len = len(content)
    if sz + block_len > 50000 and current:
        chapters.append(current[:])
        current, sz = [], 0
    current.append((path, content))
    sz += block_len
if current:
    chapters.append(current)
print(f"Chapitres : {len(chapters)}")

# ── Construire l'EPUB ────────────────────────────────────────────────────────
book = epub.EpubBook()
book.set_identifier(str(uuid.uuid4()))
book.set_title("Helliconia - Le Printemps")
book.set_language("fr")
book.add_author("Brian Aldiss")

img = Image.new("RGB", (600, 800), (30, 30, 50))
buf = io.BytesIO()
img.save(buf, "PNG")
book.set_cover("images/cover.png", buf.getvalue())

spine = ["nav"]
epub_chaps = []

for i, blocks in enumerate(chapters, 1):
    chap = epub.EpubHtml(
        title=f"Chapitre {i}",
        file_name=f"chapter_{i:03d}.xhtml",
        lang="fr"
    )
    body_parts = []
    for path, content in blocks:
        content_safe = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        path_safe = path.replace("&", "&amp;")
        body_parts.append(f'  <p>[voice:{path_safe}]{content_safe}[/voice]</p>')

    body = "\n".join(body_parts)
    chap.content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="fr">\n'
        f'<head><meta charset="utf-8"/><title>Chapitre {i}</title></head>\n'
        f'<body>\n{body}\n</body>\n'
        '</html>'
    ).encode("utf-8")

    book.add_item(chap)
    epub_chaps.append(chap)
    spine.append(chap)

book.toc = tuple(epub.Link(c.file_name, c.title, c.id) for c in epub_chaps)
book.add_item(epub.EpubNcx())
book.add_item(epub.EpubNav())
book.spine = spine

epub.write_epub(out, book)
print(f"EPUB : {out} ({os.path.getsize(out)//1024} KB)")

# ── Vérification ─────────────────────────────────────────────────────────────
import zipfile, collections
ok, bad = 0, 0
voices = collections.Counter()
with zipfile.ZipFile(out) as z:
    for name in z.namelist():
        if name.endswith('.xhtml'):
            c = z.read(name).decode('utf-8', errors='replace')
            opens  = len(re.findall(r'\[voice:[^\]]+\]', c))
            closes = len(re.findall(r'\[/voice\]', c))
            ok  += min(opens, closes)
            bad += abs(opens - closes)
            for p in re.findall(r'\[voice:([^\]]+)\]', c):
                voices[p.strip().split('/')[-1]] += 1

print(f"Blocs complets        : {ok}")
print(f"Balises déséquilibrées: {bad}")
for v, n in voices.most_common(5):
    print(f"  {v} -> {n}")
print("RESULTAT:", "OK" if bad == 0 else f"ATTENTION {bad} balises a corriger")
