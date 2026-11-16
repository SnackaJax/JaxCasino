"""The live blackjack dealer's voice, made on the bot's own machine with an offline neural text-to-speech.

Android's Discord app shows Activities in a WebView without the browser's speech synthesis, so the page can't speak
there by itself. Instead the page asks the server (POST /api/voice) to say a line, and plays the WAV it gets back.
Nothing is sent to any outside service.

Two engines, picked by the model file in DEALER_VOICE_MODEL (.env):
  - Kokoro (the default, voices/kokoro-v1.0.onnx with voices-v1.0.bin next to it; package kokoro-onnx): the most
    natural voice, about half a second per short sentence. DEALER_VOICE_NAME picks the speaker (default af_heart).
  - Piper (any other .onnx, like voices/en_US-lessac-medium.onnx; package piper-tts): more robotic, but very fast.
Without the package or the model file the server voice is off, and the pages fall back to the device's own voices.
"""
from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger("gamblebot.activity.voice")

MAX_TEXT = 240               # characters in one line
CACHE_LINES = 400            # lines kept ready (the fixed ones, like "No more bets, thank you", are asked for a lot)
RATE_WINDOW = 60             # seconds
RATE_LINES = 60              # lines one player may ask for in that window (the pages ask one sentence at a time)
KOKORO_STYLES = "voices-v1.0.bin"
# made right after the model loads, so the lines every round needs are there at once
COMMON_LINES = ["Place your bets, please.", "No more bets, thank you.", "Dealer busts!", "Dealer has blackjack.",
                *[f"Dealer has {n}." for n in range(17, 22)], "It's a push.", "No winners this round.",
                "The house wins this round.", "Better luck next time!", "Hello!", "I will be your dealer.",
                "That's 21 for the dealer, thanks for playing."]


def _clean(text: str) -> str:
    return " ".join(str(text).split())[:MAX_TEXT]


class _Piper:
    def __init__(self, path: Path, speaker: str):
        try:
            from piper import PiperVoice
        except ImportError:
            raise RuntimeError("piper-tts is not installed (pip install piper-tts)") from None
        self.voice = PiperVoice.load(str(path))
        self.name = path.name

    def wav(self, text: str) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            self.voice.synthesize_wav(text, wf)
        return buf.getvalue()


class _Kokoro:
    def __init__(self, path: Path, speaker: str):
        styles = path.with_name(KOKORO_STYLES)
        if not styles.is_file():
            raise RuntimeError(f"{styles} not found (it goes next to the model, see the README)")
        try:
            import numpy as np
            from kokoro_onnx import Kokoro
        except ImportError:
            raise RuntimeError("kokoro-onnx is not installed (pip install kokoro-onnx)") from None
        self.np = np
        # its phonemizer warns about every line with numbers or punctuation it splits differently; harmless
        logging.getLogger("phonemizer").setLevel(logging.ERROR)
        self.kokoro = Kokoro(str(path), str(styles))
        self.speaker = speaker or "af_heart"
        if self.speaker not in self.kokoro.get_voices():
            raise RuntimeError(f"there is no Kokoro voice {self.speaker!r} (DEALER_VOICE_NAME)")
        self.lang = "en-gb" if self.speaker[:1] == "b" else "en-us"   # bf_/bm_ voices are British
        self.name = f"{path.name}, {self.speaker}"

    def wav(self, text: str) -> bytes:
        audio, rate = self.kokoro.create(text, voice=self.speaker, speed=1.0, lang=self.lang)
        pcm = (self.np.clip(audio, -1, 1) * 32767).astype(self.np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()


class DealerVoice:
    def __init__(self, model_path: str, speaker: str = ""):
        path = Path(model_path) if model_path else None
        if path is not None and not path.is_absolute():   # relative to the bot's folder, wherever it's started from
            path = Path(__file__).resolve().parent.parent / path
        self.path = path
        self.speaker = speaker
        self.engine: _Piper | _Kokoro | None = None
        self.problem: str | None = None
        self.lock = threading.Lock()
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.making: dict[str, asyncio.Task] = {}   # lines being made now: everyone asking for one waits for the same
        self.asked: dict[int, list[float]] = {}
        self.loading: asyncio.Task | None = None
        self.warming: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        """Whether the server can speak (the model loads in the background at start)."""
        return self.problem is None and self.path is not None

    def _load(self) -> None:
        if self.engine is not None or self.problem:
            return
        if self.path is None:
            self.problem = "DEALER_VOICE_MODEL is empty"
            return
        if not self.path.is_file():
            self.problem = f"voice model {self.path} not found"
            log.warning("Dealer voice is off: %s (see the README to download it)", self.problem)
            return
        kind = _Kokoro if self.path.name.lower().startswith("kokoro") else _Piper
        t = time.monotonic()
        try:
            engine = kind(self.path, self.speaker)
            engine.wav("Ready.")   # the first line is slow; get it over with now
        except Exception as e:   # a missing package or broken model file must not take the Activity down
            self.problem = str(e) if isinstance(e, RuntimeError) else f"couldn't load {self.path}: {e}"
            if isinstance(e, RuntimeError):
                log.warning("Dealer voice is off: %s", self.problem)
            else:
                log.exception("Dealer voice is off: couldn't load %s", self.path)
            return
        self.engine = engine
        log.info("Dealer voice ready (%s, %.1fs)", engine.name, time.monotonic() - t)

    def _wav(self, text: str) -> bytes:
        with self.lock:
            return self.engine.wav(text)

    async def _warm(self) -> None:
        for text in COMMON_LINES:
            try:
                await self.say(text)
            except Exception:
                log.exception("Dealer voice: couldn't make %r ahead", text)
                return

    async def start(self) -> None:
        """Loads the model in the background, so the first line doesn't keep anyone waiting, then makes the lines
        every round needs."""
        if self.path is not None and self.loading is None:
            self.loading = asyncio.create_task(asyncio.to_thread(self._load))
            self.warming = asyncio.create_task(self._warm())

    def allowed(self, user_id: int) -> bool:
        now = time.monotonic()
        times = [t for t in self.asked.get(user_id, []) if now - t < RATE_WINDOW]
        if len(times) >= RATE_LINES:
            self.asked[user_id] = times
            return False
        times.append(now)
        self.asked[user_id] = times
        if len(self.asked) > 1000:
            self.asked = {u: ts for u, ts in self.asked.items() if ts and now - ts[-1] < RATE_WINDOW}
        return True

    async def say(self, text: str) -> bytes | None:
        """The line as a WAV file, or None when the server can't speak."""
        if self.loading is None:
            await self.start()
        await asyncio.shield(self.loading)
        if self.engine is None:
            return None
        text = _clean(text)
        wav = self.cache.get(text)
        if wav is not None:
            self.cache.move_to_end(text)
            return wav
        task = self.making.get(text)
        if task is None:
            task = self.making[text] = asyncio.create_task(asyncio.to_thread(self._wav, text))
            task.add_done_callback(lambda t: self._made(text, t))
        try:
            return await asyncio.shield(task)   # a page that goes away doesn't stop the line for the others
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Dealer voice: couldn't say %r", text)
            return None

    def _made(self, text: str, task: asyncio.Task) -> None:
        self.making.pop(text, None)
        if task.cancelled() or task.exception() is not None:
            return
        self.cache[text] = task.result()
        while len(self.cache) > CACHE_LINES:
            self.cache.popitem(last=False)
