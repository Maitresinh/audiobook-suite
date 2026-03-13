#!/bin/bash
# Comparatif voix/engine sur un extrait fixe
# Usage : bash test_voices.sh

CONTAINER="ebook2audiobook"
TEXT="Il était une fois, dans les profondeurs glacées d'Helliconia, un chasseur nommé Yuli. Il avançait lentement dans la tempête, les yeux plissés contre le vent cinglant. Autour de lui, la neige effaçait toute trace de civilisation."

echo "=== Génération des 4 tests ==="

# Test A — XTTSv2 + voix courte (20s)
docker exec $CONTAINER python3 -c "
import sys; sys.path.insert(0,'/app')
from TTS.api import TTS
tts = TTS('tts_models/multilingual/multi-dataset/xtts_v2')
tts.tts_to_file(
    text='$TEXT',
    speaker_wav='/app/voices/Jean-Topart-short.wav',
    language='fr',
    file_path='/app/audiobooks/test_A_xtts_short.wav'
)
print('A done')
" && echo "✅ Test A : XTTSv2 + voix 20s"

# Test B — XTTSv2 + voix longue (3m44)
docker exec $CONTAINER python3 -c "
import sys; sys.path.insert(0,'/app')
from TTS.api import TTS
tts = TTS('tts_models/multilingual/multi-dataset/xtts_v2')
tts.tts_to_file(
    text='$TEXT',
    speaker_wav='/app/voices/Jean-Topart-_128kbit_AAC_.wav',
    language='fr',
    file_path='/app/audiobooks/test_B_xtts_long.wav'
)
print('B done')
" && echo "✅ Test B : XTTSv2 + voix 3m44"

echo ""
echo "=== Fichiers générés ==="
docker exec $CONTAINER ls -lh /app/audiobooks/test_*.wav 2>/dev/null
echo ""
echo "Copier vers le NAS pour écoute :"
echo "  /app/audiobooks/test_A_xtts_short.wav"
echo "  /app/audiobooks/test_B_xtts_long.wav"
