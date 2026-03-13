#!/bin/bash
CONTAINER="ebook2audiobook"
echo "=== PRE-REQUIS ==="
docker exec $CONTAINER pip show ebooklib Pillow 2>&1 | grep -E "^Name|^Version|WARNING"
echo ""
echo "=== TEST 1 : Generer EPUB ==="
docker exec $CONTAINER python3 -c "
import re, uuid, io, os
from ebooklib import epub
from PIL import Image
text = open('/app/ebooks/tagged_output_fixed.txt').read()
print('Chars:', len(text))
print('Blocs voice:', len(re.findall(r'\[voice:[^\]]+\].*?\[/voice\]', text, re.DOTALL)))
book = epub.EpubBook()
book.set_identifier(str(uuid.uuid4()))
book.set_title('Helliconia')
book.set_language('fr')
book.add_author('Brian Aldiss')
img = Image.new('RGB', (600, 800), (30, 30, 50))
buf = io.BytesIO()
img.save(buf, 'PNG')
book.set_cover('images/cover.png', buf.getvalue())
paragraphs = [p for p in re.split(r'\n{2,}', text.strip()) if p.strip()]
chapters_content, current, sz = [], [], 0
for p in paragraphs:
    if sz + len(p) > 50000 and current:
        chapters_content.append(current)
        current, sz = [], 0
    current.append(p)
    sz += len(p)
if current:
    chapters_content.append(current)
print('Chapitres:', len(chapters_content))
spine, epub_chaps = ['nav'], []
for i, paras in enumerate(chapters_content, 1):
    chap = epub.EpubHtml(title=f'Chapitre {i}', file_name=f'chapter_{i:03d}.xhtml', lang='fr')
    body = ''.join(f'<p>{p}</p>' for p in paras)
    chap.content = f'<?xml version=\"1.0\" encoding=\"utf-8\"?><html xmlns=\"http://www.w3.org/1999/xhtml\"><head><title>Ch{i}</title></head><body>{body}</body></html>'
    book.add_item(chap); epub_chaps.append(chap); spine.append(chap)
book.toc = tuple(epub.Link(c.file_name, c.title, c.id) for c in epub_chaps)
book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav())
book.spine = spine
epub.write_epub('/app/ebooks/tagged_output.epub', book)
print('EPUB OK:', os.path.getsize('/app/ebooks/tagged_output.epub')//1024, 'KB')
"
echo ""
echo "=== TEST 2 : Balises SML dans EPUB ? ==="
docker exec $CONTAINER python3 -c "
import zipfile, re, collections
v, chaps = 0, 0
voices = collections.Counter()
with zipfile.ZipFile('/app/ebooks/tagged_output.epub') as z:
    for name in z.namelist():
        if name.endswith('.xhtml'):
            c = z.read(name).decode('utf-8', errors='replace')
            found = re.findall(r'\[voice:([^\]]+)\].*?\[/voice\]', c, re.DOTALL)
            if found:
                chaps += 1; v += len(found)
                for p,_ in found: voices[p.strip().split('/')[-1]] += 1
print('Chapitres avec SML:', chaps)
print('Total blocs voice:', v)
for name, n in voices.most_common(5): print(f'  {name} -> {n}')
print('RESULTAT:', 'OK' if v > 0 else 'ECHEC')
"
echo ""
echo "=== TEST 3 : E2A lit l EPUB ? ==="
docker exec $CONTAINER python3 -c "
import sys; sys.path.insert(0, '/app')
from ebooklib import epub as el
import re
book = el.read_epub('/app/ebooks/tagged_output.epub', options={'ignore_ncx': True})
items = list(book.get_items_of_type(9))
total = sum(len(re.findall(r'\[voice:[^\]]+\]', item.get_content().decode('utf-8','replace'))) for item in items)
print('Items HTML:', len(items))
print('Balises voice detectees:', total)
print('RESULTAT:', 'PRET' if total > 0 else 'PROBLEME')
"
echo ""
echo "=== DONE ==="
