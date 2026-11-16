"""Downloads the live blackjack dealer's voice into voices/ (they're too big to keep in git).

    python download_voices.py           the Kokoro model and voices (about 350 MB, the default dealer voice)
    python download_voices.py piper     also the smaller, faster, more robotic Piper voice (about 63 MB)

Files that are already there with the right size are skipped.
"""
import sys
import urllib.request
from pathlib import Path

VOICES = Path(__file__).resolve().parent / "voices"
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/"
FILES = {"kokoro": [(KOKORO + "kokoro-v1.0.onnx", "kokoro-v1.0.onnx"), (KOKORO + "voices-v1.0.bin", "voices-v1.0.bin")],
         "piper": [(PIPER + "en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx"),
                   (PIPER + "en_US-lessac-medium.onnx.json", "en_US-lessac-medium.onnx.json")]}


def download(url: str, name: str) -> None:
    target = VOICES / name
    with urllib.request.urlopen(url) as r:
        size = int(r.headers.get("Content-Length") or 0)
        if target.is_file() and size and target.stat().st_size == size:
            print(f"{name}: already there")
            return
        part = target.with_name(name + ".part")
        done = 0
        with open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if size:
                    print(f"\r{name}: {done * 100 // size}% of {size / 1e6:.0f} MB", end="", flush=True)
        part.replace(target)
        print(f"\r{name}: done ({done / 1e6:.0f} MB)          ")


if __name__ == "__main__":
    VOICES.mkdir(exist_ok=True)
    for kind in ["kokoro", *(a for a in sys.argv[1:] if a in FILES and a != "kokoro")]:
        for url, name in FILES[kind]:
            download(url, name)
