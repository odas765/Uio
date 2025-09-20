#!/usr/bin/env python3
"""
mixed_in_key_bot.py

A Telegram bot that accepts an audio file and returns:
- Detected musical key (and Camelot code)
- Tempo (BPM)
- Energy rating (1-10)
- Suggested cue points (timestamps)
- Waveform PNG with cue markers

Dependencies:
  - ffmpeg (system)
  - python-telegram-bot==13.15
  - librosa
  - numpy
  - matplotlib
  - soundfile
  - pydub
"""

import os
import io
import math
import logging
from pathlib import Path
from collections import defaultdict

import numpy as np
import librosa
import soundfile as sf
from pydub import AudioSegment
import matplotlib.pyplot as plt

from telegram import Update, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# -------------------------
# Configuration
# -------------------------
BOT_TOKEN = "REPLACE_WITH_YOUR_TELEGRAM_BOT_TOKEN"
TMP_DIR = Path("./tmp")
TMP_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Camelot mapping (note name -> Camelot)
# We'll use enharmonic simplified names (C, C#, D, ... , A#, B)
NOTE_TO_CAMELOT = {
    # Major keys (B = uppercase 'B', minor marked as m)
    "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B", "B": "1B",
    "F#": "2B", "C#": "3B", "G#": "4B", "D#": "5B", "A#": "6B", "F": "7B",
    # Minor
    "Am": "8A", "Em": "9A", "Bm": "10A", "F#m": "11A", "C#m": "12A", "G#m": "1A",
    "D#m": "2A", "A#m": "3A", "Gm": "4A", "D#m_alt": "5A", "A#m_alt": "6A", "Dm": "7A"
}

# We'll actually map by chroma index -> note name:
CHROMA_INDEX_TO_NOTE = [
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
]

# Utility: convert detected tonic and mode to readable name and Camelot
def tonic_mode_to_name_camelot(i_tonic, mode):  # i_tonic int 0-11; mode 'major'/'minor'
    tonic = CHROMA_INDEX_TO_NOTE[i_tonic]
    if mode == "major":
        note_name = tonic
        # direct mapping attempt
        camelot = NOTE_TO_CAMELOT.get(note_name, None)
    else:
        note_name = tonic + "m"
        camelot = NOTE_TO_CAMELOT.get(note_name, None)

    # fallback: try enharmonic conversions for some edge cases
    if camelot is None:
        # convert flats/sharps equivalently
        enharm = {
            "D#": "Eb", "A#": "Bb", "G#": "Ab", "C#": "Db", "F#": "Gb"
        }
        tn = tonic
        if tn in enharm:
            tn_name = enharm[tn] + ("m" if mode == "minor" else "")
            # try to find in mapping by replacing with flats (if mapping present)
            for k, v in NOTE_TO_CAMELOT.items():
                if k.lower() == tn_name.lower():
                    camelot = v
                    break

    return note_name, camelot or "Unknown"

# -------------------------
# Key detection: Krumhansl-Schmuckler template matching
# -------------------------
# Major and minor templates (Krumhansl)
MAJOR_PROFILE = np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
MINOR_PROFILE = np.array([6.33,2.68,3.52,5.38,2.6,3.53,2.54,4.75,3.98,2.69,3.34,3.17])

def detect_key(y, sr):
    # compute chroma
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_avg = np.mean(chroma, axis=1)  # 12-length vector
    chroma_norm = chroma_avg / (np.linalg.norm(chroma_avg) + 1e-9)

    best = {"mode": None, "tonic": None, "score": -1}
    # try all 12 transpositions for major/minor
    for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
        for shift in range(12):
            profile_shifted = np.roll(profile, shift)
            profile_norm = profile_shifted / (np.linalg.norm(profile_shifted) + 1e-9)
            score = np.dot(chroma_norm, profile_norm)
            if score > best["score"]:
                best = {"mode": mode, "tonic": shift, "score": float(score)}
    tonic = best["tonic"]
    mode = best["mode"]
    note_name, camelot = tonic_mode_to_name_camelot(tonic, mode)
    # convert camelot more robustly: we'll compute mapping programmatically below if Unknown
    camelot = camelot if camelot != "Unknown" else convert_tonic_mode_to_camelot(tonic, mode)
    return note_name, mode, camelot, best["score"]

# Programmatic Camelot mapping generator (stable)
CAMELOT_MAJOR_ORDER = ["1B","2B","3B","4B","5B","6B","7B","8B","9B","10B","11B","12B"]
CAMELOT_MINOR_ORDER = ["1A","2A","3A","4A","5A","6A","7A","8A","9A","10A","11A","12A"]
# map circle of fifths tonic order to chroma indices for majors:
# Camelot 1B corresponds to B major (tonic index for B = 11)
# We'll create a mapping by known sequence. Simpler: map chroma index to Camelot roughly using reference:
CHROMA_TO_CAMELOT_MAJOR = {
    11: "1B", 6: "2B", 1: "3B", 8: "4B", 3: "5B", 10: "6B",
    5: "7B", 0: "8B", 7: "9B", 2: "10B", 9: "11B", 4: "12B"
}
CHROMA_TO_CAMELOT_MINOR = {
    8: "1A", 3: "2A", 10: "3A", 5: "4A", 0: "5A", 7: "6A",
    2: "7A", 9: "8A", 4: "9A", 11: "10A", 6: "11A", 1: "12A"
}

def convert_tonic_mode_to_camelot(tonic_idx, mode):
    if mode == "major":
        return CHROMA_TO_CAMELOT_MAJOR.get(tonic_idx, "Unknown")
    else:
        return CHROMA_TO_CAMELOT_MINOR.get(tonic_idx, "Unknown")

# -------------------------
# Energy scoring and cue points
# -------------------------
def energy_score(y):
    # RMS energy -> map to 1-10 scale
    hop_length = 512
    frame_rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    mean_rms = float(np.mean(frame_rms))
    # normalize roughly using log scaling
    # prevent log(0)
    s = math.log1p(mean_rms * 1000)
    # scale to 1-10 (heuristic)
    val = max(1, min(10, int(round((s / 3.0) * 10))))  # tuned heuristically
    return val, mean_rms

def detect_cue_points(y, sr, max_points=8):
    # onset strength envelope
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    # get onset frames
    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, backtrack=False)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr)
    # if too few onsets, fallback to beat times
    if len(onset_times) < max_points:
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beats, sr=sr)
        times = beat_times
    else:
        times = onset_times

    # pick the most salient max_points by onset strength
    # compute strength per frame index (align)
    strengths = librosa.util.normalize(onset_env)
    # convert onset_frames -> strength
    frame_strengths = []
    for f in onset_frames:
        if f < len(strengths):
            frame_strengths.append((strengths[f], f))
    # sort by strength descending
    frame_strengths.sort(reverse=True)
    selected = []
    for st, f in frame_strengths[:max_points]:
        t = librosa.frames_to_time(f, sr=sr)
        selected.append(t)
    # ensure sorted by time
    selected = sorted(list(set(selected)))
    return selected

# -------------------------
# Waveform image generator
# -------------------------
def make_waveform_image(y, sr, cue_times, out_path):
    # create waveform plot, save to out_path
    duration = librosa.get_duration(y=y, sr=sr)
    times = np.linspace(0, duration, num=len(y))
    plt.figure(figsize=(12, 3))
    plt.plot(times, y)
    # mark cues
    for i, t in enumerate(cue_times):
        plt.axvline(x=t, linestyle='--', linewidth=1)
        plt.text(t, 0.9 * np.max(y), f"Cue {i+1}", rotation=90, verticalalignment='bottom', fontsize=8)
    plt.xlabel("Time (s)")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()

# -------------------------
# Telegram bot handlers
# -------------------------
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Send me an audio file (mp3, wav, flac, m4a). I'll analyze key, BPM, energy and suggest cue points."
    )

def analyze_audio_file(file_path: Path):
    # ensure we read a mono waveform (librosa loads as float32)
    y, sr = librosa.load(str(file_path), sr=22050, mono=True)
    # key detection
    key_name, mode, camelot, key_conf = detect_key(y, sr)
    bpm = float(librosa.beat.tempo(y=y, sr=sr, aggregate=np.mean))
    energy_val, mean_rms = energy_score(y)
    cue_times = detect_cue_points(y, sr, max_points=8)
    # generate waveform image
    waveform_path = file_path.with_suffix(".png")
    make_waveform_image(y, sr, cue_times, waveform_path)
    result = {
        "key_name": key_name,
        "mode": mode,
        "camelot": camelot,
        "key_confidence": key_conf,
        "bpm": round(bpm, 2),
        "energy": energy_val,
        "mean_rms": mean_rms,
        "cues": cue_times,
        "waveform_path": waveform_path
    }
    return result

def handle_audio(update: Update, context: CallbackContext):
    message = update.message
    # accept audio, voice, or document
    audio = None
    if message.audio:
        audio = message.audio
        file_id = audio.file_id
        fname = audio.file_name or f"{file_id}.mp3"
    elif message.voice:
        audio = message.voice
        file_id = audio.file_id
        fname = f"{file_id}.ogg"
    elif message.document:
        # user may send mp3/wav/flac as document
        audio = message.document
        file_id = audio.file_id
        fname = audio.file_name or f"{file_id}"
    else:
        message.reply_text("No audio found in message. Please send an audio file.")
        return

    # download file
    file = context.bot.get_file(file_id)
    tmp_in = TMP_DIR / f"in_{file_id}_{fname}"
    tmp_out_wav = TMP_DIR / f"processed_{file_id}.wav"
    file.download(custom_path=str(tmp_in))
    message.reply_text("File received. Converting and analyzing... (may take a few seconds)")

    try:
        # convert to wav (pydub supports many formats via ffmpeg)
        audio_seg = AudioSegment.from_file(str(tmp_in))
        audio_seg = audio_seg.set_channels(1)  # mono
        audio_seg.export(str(tmp_out_wav), format="wav")
        # analyze
        res = analyze_audio_file(tmp_out_wav)

        # prepare text reply
        text = []
        text.append(f"Key: {res['key_name']} ({res['mode']})")
        text.append(f"Camelot: {res['camelot']}")
        text.append(f"Tempo (BPM): {res['bpm']}")
        text.append(f"Energy (1-10): {res['energy']}")
        text.append(f"Key confidence: {res['key_confidence']:.3f}")
        text.append("Suggested cue points (seconds):")
        for i, t in enumerate(res['cues'], start=1):
            mins = int(t // 60)
            secs = t - mins*60
            text.append(f"  Cue {i}: {mins:d}:{secs:05.2f}")
        message.reply_text("\n".join(text))

        # send waveform image
        with open(res['waveform_path'], "rb") as f:
            message.reply_photo(photo=f, caption="Waveform with cue markers")

    except Exception as e:
        logger.exception("Error processing audio")
        message.reply_text("Error analyzing file: " + str(e))
    finally:
        # cleanup
        try:
            for f in TMP_DIR.glob(f"*{file_id}*"):
                f.unlink(missing_ok=True)
        except Exception:
            pass

def help_command(update: Update, context: CallbackContext):
    update.message.reply_text("Send an audio file and I'll analyze it. /start to begin.")

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(MessageHandler(Filters.audio | Filters.voice | Filters.document, handle_audio))

    # start
    updater.start_polling()
    logger.info("Bot started")
    updater.idle()


if __name__ == "__main__":
    if BOT_TOKEN == "REPLACE_WITH_YOUR_TELEGRAM_BOT_TOKEN":
        raise SystemExit("Please set BOT_TOKEN in the script before running.")
    main()
