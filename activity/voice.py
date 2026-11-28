"""The live blackjack dealer's voice, made on the bot's own machine with an offline neural text-to-speech.

Android's Discord app shows Activities in a WebView without the browser's speech synthesis, so the page can't speak
there by itself. Instead the page asks the server (POST /api/voice) to say a line, and plays the WAV it gets back.
Nothing is sent to any outside service.

Two engines, picked by the model file in DEALER_VOICE_MODEL (.env):
  - Kokoro (the default, voices/kokoro-v1.0.onnx with voices-v1.0.bin next to it; package kokoro-onnx): the most
    natural voice, about half a second per short sentence. DEALER_VOICE_NAME picks the speaker (default af_heart).
  - Piper (any other .onnx, like voices/en_US-lessac-medium.onnx; package piper-tts): more robotic, but very fast.
Without the package or the model file the server voice is off, and the pages fall back to the device's own voices.

The model takes about 400 MB, so it's loaded only when someone opens a table with a dealer (wake()) or asks for a
line that isn't ready yet, and let go again after IDLE_UNLOAD seconds without a new line. The lines already made stay
ready, so the ones every round needs don't wait for the model.
"""
from __future__ import annotations

import asyncio
import ctypes
import gc
import importlib.util
import io
import logging
import threading
import time
import wave
from collections import OrderedDict
from pathlib import Path

log = logging.getLogger("jaxcasino.activity.voice")

MAX_TEXT = 240               # characters in one line
CACHE_LINES = 400            # lines kept ready (the fixed ones, like "No more bets, thank you", are asked for a lot)
RATE_WINDOW = 60             # seconds
RATE_LINES = 60              # lines one player may ask for in that window (the pages ask one sentence at a time)
KOKORO_STYLES = "voices-v1.0.bin"
IDLE_UNLOAD = 15 * 60        # seconds without a new line before the model is let go
IDLE_CHECK = 60              # seconds between those checks
VOICE_THREADS = 4            # CPU threads for one line (onnxruntime would take every core, each with its own memory)
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
            import onnxruntime as ort
            from kokoro_onnx import Kokoro
        except ImportError:
            raise RuntimeError("kokoro-onnx is not installed (pip install kokoro-onnx)") from None
        self.np = np
        # its phonemizer warns about every line with numbers or punctuation it splits differently; harmless
        logging.getLogger("phonemizer").setLevel(logging.ERROR)
        # onnxruntime's own memory pool keeps the biggest line's working memory forever (100+ MB): off
        opts = ort.SessionOptions()
        opts.enable_cpu_mem_arena = False
        opts.intra_op_num_threads = VOICE_THREADS
        opts.inter_op_num_threads = 1
        session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.kokoro = Kokoro.from_session(session, str(styles))
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
        self.idle: asyncio.Task | None = None
        self.last_used = 0.0
        self.kind = _Kokoro if path is not None and path.name.lower().startswith("kokoro") else _Piper

    @property
    def available(self) -> bool:
        """Whether the server can speak (the model itself is loaded when it's first needed)."""
        return self.problem is None and self.path is not None

    def _check(self) -> None:
        """The quick checks, at start, so the pages know straight away whether the server can speak."""
        if self.path is None:
            self.problem = "DEALER_VOICE_MODEL is empty"
        elif not self.path.is_file():
            self.problem = f"voice model {self.path} not found"
            log.warning("Dealer voice is off: %s (see the README to download it)", self.problem)
        else:
            module, package = ("kokoro_onnx", "kokoro-onnx") if self.kind is _Kokoro else ("piper", "piper-tts")
            if importlib.util.find_spec(module) is None:
                self.problem = f"{package} is not installed (pip install {package})"
                log.warning("Dealer voice is off: %s", self.problem)

    def _load(self) -> None:
        if self.engine is not None or not self.available:
            return
        t = time.monotonic()
        try:
            engine = self.kind(self.path, self.speaker)
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
        """Checks that the server can speak; the model is loaded later, when it's needed (wake())."""
        self._check()

    def wake(self) -> None:
        """Someone opened a game with a dealer: loads the model in the background if it isn't loaded, so the first
        line doesn't keep them waiting, then makes the lines every round needs."""
        if not self.available:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:   # not in the bot's event loop (a test calling welcome())
            return
        self.last_used = time.monotonic()
        if self.loading is None:
            self.loading = asyncio.create_task(asyncio.to_thread(self._load))
            self.warming = asyncio.create_task(self._warm())
        if self.idle is None or self.idle.done():
            self.idle = asyncio.create_task(self._let_go_when_idle())

    async def _let_go_when_idle(self) -> None:
        """Lets the model go after IDLE_UNLOAD seconds without a new line; the next wake() loads it again."""
        while self.available:
            await asyncio.sleep(IDLE_CHECK)
            if (self.engine is None or self.making or self.loading is None or not self.loading.done()
                    or time.monotonic() - self.last_used < IDLE_UNLOAD):
                continue
            self.engine = None   # nothing is making a line (self.making is empty), so nothing else holds it
            self.loading = self.warming = None
            await asyncio.to_thread(self._free)
            log.info("Dealer voice let go after %d minutes without a new line", IDLE_UNLOAD // 60)

    def _free(self) -> None:
        with self.lock:
            gc.collect()
            try:   # give the freed memory back to the system (Linux); elsewhere it's just reused later
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except (OSError, AttributeError):
                pass

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
        if not self.available:
            return None
        text = _clean(text)
        self.last_used = time.monotonic()
        wav = self.cache.get(text)
        if wav is not None:   # ready, with or without the model loaded
            self.cache.move_to_end(text)
            return wav
        self.wake()
        await asyncio.shield(self.loading)
        if self.engine is None:
            return None
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
