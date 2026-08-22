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

card intro 3.0
card today 2.2
card capture 2.2
card ask 2.2
card together 2.2
card tidy 2.4
card expand 2.2
card outro 3.2
for s in today capture ask together tidy expand; do scene "$s"; done

# One card, then the thing itself, six times over.
{
  for s in intro today capture ask together tidy expand; do
    echo "file '$SEG/$s.mp4'"
    case "$s" in
      intro) ;;
      *) echo "file '$SEG/s-$s.mp4'" ;;
    esac
  done
  echo "file '$SEG/outro.mp4'"
} > "$WORK/concat.txt"

ffmpeg -y -loglevel error -f concat -safe 0 -i "$WORK/concat.txt" \
  -c:v libx264 -preset slow -crf 26 -pix_fmt yuv420p -movflags +faststart \
  "$OUT/cortex-promo.mp4"

# Poster: the chat scene mid-answer.
ffmpeg -y -loglevel error -sseof -4 -i "$SEG/s-today.mp4" -frames:v 1 \
  "$OUT/cortex-poster.png"

# README gif: the chat scene, 720px, 9 fps, palette-optimized.
ffmpeg -y -loglevel error -i "$SEG/s-ask.mp4" \
  -vf "fps=9,scale=720:-1:flags=lanczos,palettegen" "$WORK/palette.png"
ffmpeg -y -loglevel error -i "$SEG/s-ask.mp4" -i "$WORK/palette.png" \
  -lavfi "fps=9,scale=720:-1:flags=lanczos[x];[x][1:v]paletteuse" \
  "$OUT/cortex-demo.gif"

echo "---"
du -h "$OUT/cortex-promo.mp4" "$OUT/cortex-demo.gif" "$OUT/cortex-poster.png"
ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT/cortex-promo.mp4" \
  | xargs printf "duration: %ss\n"
