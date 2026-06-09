#!/usr/bin/env bash
# Compose final video from shot clips + voiceover + bgm + subs via ffmpeg.
#
# Usage:
#   ./compose.sh <feature_dir>
#
# Expects:
#   <feature_dir>/shots/01_clip.mp4 ... NN_clip.mp4
#   <feature_dir>/voice.wav
#   <feature_dir>/bgm.wav
#   <feature_dir>/subs.srt
# Output:
#   <feature_dir>/final.mp4

set -e

FEATURE_DIR="${1:?Usage: compose.sh <feature_dir>}"
cd "$FEATURE_DIR"

# 1. Concat shot clips into video.mp4
ls shots/*_clip.mp4 | sort | sed 's|^|file '"'"'|;s|$|'"'"'|' > shots/_concat.txt
ffmpeg -y -f concat -safe 0 -i shots/_concat.txt -c copy video_raw.mp4

# 2. Mix voice (foreground) + bgm (background, -18 dB)
ffmpeg -y -i voice.wav -i bgm.wav -filter_complex \
  "[1:a]volume=0.13[bg];[0:a][bg]amix=inputs=2:duration=longest:dropout_transition=2[a]" \
  -map "[a]" -c:a aac mixed.wav

# 3. Merge video + mixed audio + burn subs
ffmpeg -y -i video_raw.mp4 -i mixed.wav \
  -c:v libx264 -preset slow -crf 18 \
  -c:a aac -b:a 192k \
  -vf "subtitles=subs.srt:force_style='Fontname=Inter,Fontsize=18,OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=4'" \
  -shortest \
  final.mp4

# 4. Normalize audio to -14 LUFS (YouTube standard)
ffmpeg -y -i final.mp4 -af loudnorm=I=-14:LRA=11:TP=-1 -c:v copy final_normalized.mp4
mv final_normalized.mp4 final.mp4

# 5. Cleanup
rm video_raw.mp4 mixed.wav shots/_concat.txt

echo "Done: $FEATURE_DIR/final.mp4"
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration final.mp4
