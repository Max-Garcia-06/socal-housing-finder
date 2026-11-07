"""Add a narrated voiceover to video/out/common-ground-demo.mp4 -> video/out/common-ground-demo-vo.mp4.

    python3 video/voiceover.py
Uses Microsoft Edge neural TTS (pip install edge-tts). Each line starts shortly after its scene starts.
"""
import asyncio
import os
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
VOICE, RATE, PITCH = "en-US-AvaNeural", "+8%", "+3Hz"
# (scene start in seconds, line). Scene lengths: title 4.0, overview 7.6, profile 6.0, site 6.2, deep dive 5.0
LINES = [
    (0.25, "Churches across Orange County are sitting on land that could become home."),
    (4.20, "Thanks to SB 4, they can build affordable homes by right. We checked every church parcel, and flagged the risky ones."),
    (11.90, "Just tell us about your nonprofit, and Common Ground finds the sites that fit you best."),
    (17.85, "Each match shows the rules it passes, who to call, what it would cost, and how to pay for it."),
    (24.05, "Want more? Dive deeper with Claude. Let's turn parking lots into homes!"),
]


async def tts(text, path):
    import certifi
    if os.path.exists(os.path.expanduser("~/.ccr/ca-bundle.crt")):  # sandbox proxy CA; must be set before edge_tts import
        certifi.where = lambda: os.path.expanduser("~/.ccr/ca-bundle.crt")
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH).save(str(path))


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    trim = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,areverse"
    dur = duration(OUT / "common-ground-demo.mp4")
    inputs, chains = [], []
    for i, (t, line) in enumerate(LINES):
        mp3, wav = OUT / f"vo{i}.mp3", OUT / f"vo{i}.wav"
        asyncio.run(tts(line, mp3))
        run(["ffmpeg", "-y", "-i", str(mp3), "-af", trim, "-ar", "48000", "-ac", "1", str(wav)])
        inputs += ["-i", str(wav)]
        chains.append(f"[{i}:a]adelay={int(t * 1000)}:all=1[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(LINES))) + f"amix=inputs={len(LINES)}:normalize=0:duration=longest,volume=1.25,alimiter=limit=0.89,apad=whole_dur={dur}"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chains) + ";" + mix, "-t", str(dur), str(OUT / "vo-mix.wav")])
    tmp = OUT / "demo-vo.tmp.mp4"
    run(["ffmpeg", "-y", "-i", str(OUT / "common-ground-demo.mp4"), "-i", str(OUT / "vo-mix.wav"), "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(tmp)])
    tmp.rename(OUT / "common-ground-demo-vo.mp4")
    print("wrote", OUT / "common-ground-demo-vo.mp4")


if __name__ == "__main__":
    main()
