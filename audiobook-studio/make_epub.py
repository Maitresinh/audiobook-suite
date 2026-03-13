import re, uuid, io, os, sys
from ebooklib import epub
from PIL import Image

src = "/app/ebooks/tagged_output_fixed.txt"
out = "/app/ebooks/tagged_output.epub"

text = open(src, encoding="utf-8").read()
print(f"Chars: {len(text)}")
print(f"Blocs voice: {len(re.findall(chr(91)+'voice:[^'+chr(93)+']+'+chr(93), text))}")

book = epub.EpubBook()
book.set_identifier(str(uuid.uuid4()))
book.set_title("Helliconia - Le Printemps")
book.set_language("fr")
book.add_author("Brian Aldiss")

# Couverture
img = Image.new("RGB", (600, 800), (30, 30, 50))
buf = io.BytesIO()
img.save(buf, "PNG")
book.set_cover("images/cover.png", buf.getvalue())

# Découper en chapitres
paragraphs = [p.strip() for p in re.split(r'\n{2,}', text.strip()) if p.strip()]
chapters_content, current, sz = [], [], 0
for p in paragraphs:
    if sz + len(p) > 50000 and current:
        chapters_content.append(current[:])
        current, sz = [], 0
    current.append(p)
    sz += len(p)
if current:
    chapters_content.append(current)
print(f"Chapitres: {len(chapters_content)}")

spine = ["nav"]
epub_chaps = []

for i, paras in enumerate(chapters_content, 1):
    chap = epub.EpubHtml(
        title=f"Chapitre {i}",
        file_name=f"chapter_{i:03d}.xhtml",
        lang="fr"
    )
    # Chaque paragraphe dans un <p> — les balises [voice:...] sont du texte brut valide en XHTML
    body_parts = []
    for p in paras:
        # Échapper uniquement < > & qui casseraient le XML — PAS les crochets
        p_safe = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body_parts.append(f"  <p>{p_safe}</p>")
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
size = os.path.getsize(out) // 1024
print(f"EPUB écrit : {out} ({size} KB)")

# Vérification immédiate
import zipfile, collections
voice_blocks = 0
voices = collections.Counter()
with zipfile.ZipFile(out) as z:
    for name in z.namelist():
        if name.endswith('.xhtml'):
            content = z.read(name).decode('utf-8', errors='replace')
            # Les & ont été échappés donc on cherche le pattern décodé
            # [voice: devient &lt; ? Non — les crochets ne sont PAS échappés
            found = re.findall(r'\[voice:([^\]]+)\]', content)
            voice_blocks += len(found)
            for p in found:
                voices[p.strip().split('/')[-1]] += 1

print(f"Balises [voice:] dans EPUB : {voice_blocks}")
for v, n in voices.most_common(5):
    print(f"  {v} -> {n}")
print("RESULTAT:", "OK" if voice_blocks > 0 else "ECHEC")
