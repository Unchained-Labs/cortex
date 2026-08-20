#!/usr/bin/env bash
# Build the cortex promo film: seed a demo brain, run the real stack against
# the scripted model, record the scenes, assemble with ffmpeg.
#
#   bash docs/promo/build.sh
#
# Outputs: docs/assets/cortex-promo.mp4, cortex-poster.png, cortex-demo.gif.
# Needs: the repo venv (.venv with playwright + chrome), ffmpeg.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$REPO/.venv/bin/python"
WORK="${PROMO_WORK:-$(mktemp -d)}"
OUT="$REPO/docs/assets"
PORT=8646

for p in 8199 $PORT; do
  if curl -fsS -m 1 "http://127.0.0.1:$p/health" >/dev/null 2>&1 || \
     python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$p))==0 else 1)"; then
    echo "error: port $p is already in use — stop the other process first" >&2
    exit 1
  fi
done

echo "workdir: $WORK"
"$PY" "$REPO/docs/promo/seed.py" "$WORK"

"$PY" "$REPO/docs/promo/mock_model.py" &
MOCK=$!
trap 'kill $MOCK $SERVER 2>/dev/null || true' EXIT
sleep 1

"$PY" -m cortex.cli index --brain "$WORK/brain"
"$PY" -m cortex.cli serve --brain "$WORK/brain" --port $PORT &
SERVER=$!
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done

"$PY" "$REPO/docs/promo/shots.py" "$WORK"
kill $SERVER $MOCK 2>/dev/null || true

# ---- assembly -------------------------------------------------------------
SEG="$WORK/seg"
mkdir -p "$SEG" "$OUT"

card() { # card <name> <seconds>
  ffmpeg -y -loglevel error -loop 1 -i "$WORK/cards/$1.png" -t "$2" \
    -vf "fps=25,scale=1280:800,fade=t=in:st=0:d=0.35,fade=t=out:st=$(echo "$2 - 0.35" | bc):d=0.35" \
    -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p "$SEG/$1.mp4"
}

scene() { # scene <name>
  ffmpeg -y -loglevel error -i "$WORK/scenes/$1.webm" \
    -vf "fps=25,scale=1280:800" \
    -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p "$SEG/s-$1.mp4"
}

card intro 2.8
card chat 2.0
card vault 2.0
card channels 2.0
card extend 2.0
card outro 3.2
for s in chat vault channels extend; do scene "$s"; done

cat > "$WORK/concat.txt" <<EOF
file '$SEG/intro.mp4'
file '$SEG/chat.mp4'
file '$SEG/s-chat.mp4'
file '$SEG/vault.mp4'
file '$SEG/s-vault.mp4'
file '$SEG/channels.mp4'
file '$SEG/s-channels.mp4'
file '$SEG/extend.mp4'
file '$SEG/s-extend.mp4'
file '$SEG/outro.mp4'
EOF

ffmpeg -y -loglevel error -f concat -safe 0 -i "$WORK/concat.txt" \
  -c:v libx264 -preset slow -crf 26 -pix_fmt yuv420p -movflags +faststart \
  "$OUT/cortex-promo.mp4"

# Poster: the chat scene mid-answer.
ffmpeg -y -loglevel error -sseof -4 -i "$SEG/s-chat.mp4" -frames:v 1 \
  "$OUT/cortex-poster.png"

# README gif: the chat scene, 720px, 9 fps, palette-optimized.
ffmpeg -y -loglevel error -i "$SEG/s-chat.mp4" \
  -vf "fps=9,scale=720:-1:flags=lanczos,palettegen" "$WORK/palette.png"
ffmpeg -y -loglevel error -i "$SEG/s-chat.mp4" -i "$WORK/palette.png" \
  -lavfi "fps=9,scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse" \
  "$OUT/cortex-demo.gif"

echo "---"
du -h "$OUT/cortex-promo.mp4" "$OUT/cortex-demo.gif" "$OUT/cortex-poster.png"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/cortex-promo.mp4" \
  | xargs printf "duration: %ss\n"
