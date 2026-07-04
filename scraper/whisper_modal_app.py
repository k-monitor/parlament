"""Modal (modal.com) service that transcribes sitting-day recordings on a GPU.

Forced-alignment sentence timing (requirements TIM-1) needs word-level timestamps.
This deploys whisper-large-v3-turbo behind a small Modal app: the host
(``parlamonitor/whisper_modal.py``) hands it a day's whole-recording HLS URL, the
container decodes the audio with ffmpeg and transcribes it with ``faster-whisper``
(CTranslate2), returning ``[start, end, text]`` words in the day-stream's own
timeline. The host caches those and does the cheap alignment offline.

Deploy (from the ``scraper/`` directory):

    pip install modal
    modal token new                       # once, to authenticate
    modal deploy whisper_modal_app.py     # builds the image (bakes the model), deploys

Smoke-test the deployed service (transcribes ~1 min of a recording if given a URL):

    PARLAMONITOR_WHISPER_TEST_M3U8="https://…/playlist.m3u8" modal run whisper_modal_app.py

Cost control: the model loads once per container (``@enter``); a VAD filter skips
silence and breaks (a plenary day is ~⅓ non-speech), so GPU seconds track real
speech; containers are capped (``PARLAMONITOR_WHISPER_MODAL_MAX_CONTAINERS``) and
scale to zero after ``scaledown_window`` idle, so an idle deployment costs nothing
and a run's credit tracks the audio actually transcribed. The host only ever sends
cache-miss sittings, dispatched in parallel with ``.map``.

Tunables (env at *deploy* time):
  PARLAMONITOR_WHISPER_MODAL_APP            app name (must match the client)  [parlamonitor-whisper]
  PARLAMONITOR_WHISPER_MODEL                faster-whisper model id           [large-v3-turbo]
  PARLAMONITOR_WHISPER_MODAL_GPU            GPU type, e.g. "T4"/"L4"/"A10G"   [L4]
  PARLAMONITOR_WHISPER_COMPUTE_TYPE         CTranslate2 compute type          [float16]
  PARLAMONITOR_WHISPER_BATCH_SIZE           intra-day GPU batch size          [16]
  PARLAMONITOR_WHISPER_MODAL_MAX_CONTAINERS parallelism / credit ceiling      [10]
"""

from __future__ import annotations

import os
import subprocess

import modal

APP_NAME = os.environ.get("PARLAMONITOR_WHISPER_MODAL_APP", "parlamonitor-whisper").strip()
MODEL = os.environ.get("PARLAMONITOR_WHISPER_MODEL", "large-v3-turbo").strip()
GPU = os.environ.get("PARLAMONITOR_WHISPER_MODAL_GPU", "L4").strip() or None
COMPUTE_TYPE = os.environ.get("PARLAMONITOR_WHISPER_COMPUTE_TYPE", "float16").strip()
BATCH_SIZE = int(os.environ.get("PARLAMONITOR_WHISPER_BATCH_SIZE", "16"))
MAX_CONTAINERS = int(os.environ.get("PARLAMONITOR_WHISPER_MODAL_MAX_CONTAINERS", "10"))

_REFERER = "https://www.parlament.hu/web/guest/orszaggyulesi-naplo"

app = modal.App(APP_NAME)

image = (
    # CTranslate2 (faster-whisper's backend) needs the CUDA runtime + cuDNN 9 shared
    # libraries (libcublas.so.12, libcudnn*) at run time; debian_slim lacks them, so
    # start from NVIDIA's CUDA-cuDNN runtime image and add Python.
    modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04",
                              add_python="3.12")
    .apt_install("ffmpeg")
    # `requests` is imported by faster-whisper's utils when the package loads; the
    # base image doesn't pull it transitively, so name it explicitly (same class of
    # gotcha as `click` for the NLP image).
    .pip_install("faster-whisper==1.1.1", "numpy<2", "requests")
    # Bake the model into the image so containers start fast and the run does no
    # download. faster-whisper resolves the alias to its CTranslate2 HF repo.
    .env({"PARLAMONITOR_WHISPER_MODEL": MODEL})
    .run_commands(
        f"python -c \"from faster_whisper import download_model; "
        f"download_model('{MODEL}')\""
    )
)


def _decode_audio(url: str, playseq: str | None):
    """Decode an HLS recording to a 16 kHz mono float32 array via ffmpeg. Pings the
    ``playseq`` activation URL first (an on-demand VOD 404s until activated)."""
    import numpy as np

    if playseq:
        try:
            import urllib.request
            req = urllib.request.Request(playseq, headers={"Referer": _REFERER})
            urllib.request.urlopen(req, timeout=60).read()
        except Exception:
            pass
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error",
           "-headers", f"Referer: {_REFERER}\r\n",
           "-i", url, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode('utf-8', 'replace')[:400]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


@app.cls(
    image=image,
    gpu=GPU,
    max_containers=MAX_CONTAINERS,
    scaledown_window=60,     # scale to zero ~1 min after the last call (no idle cost)
    timeout=5400,            # a long (~6 h) sitting at batched turbo speed fits easily
)
class WhisperService:
    @modal.enter()
    def _load(self):
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        self.model = WhisperModel(MODEL, device="cuda", compute_type=COMPUTE_TYPE)
        self.pipe = BatchedInferencePipeline(self.model)

    @modal.method()
    def transcribe(self, job: dict) -> list:
        """Transcribe one day's recording. ``job`` = ``{m3u8, playseq, language}``;
        returns ``[[start, end, text], ...]`` words in the day-stream timeline."""
        audio = _decode_audio(job["m3u8"], job.get("playseq"))
        segments, _info = self.pipe.transcribe(
            audio, language=job.get("language") or "hu",
            word_timestamps=True, vad_filter=True, batch_size=BATCH_SIZE)
        words: list = []
        for seg in segments:
            for w in (seg.words or []):
                if w.start is None or w.end is None:
                    continue
                words.append([round(float(w.start), 3), round(float(w.end), 3),
                              (w.word or "").strip()])
        return words


@app.local_entrypoint()
def main():
    """`modal run whisper_modal_app.py` — quick check of the deployed service.

    Set ``PARLAMONITOR_WHISPER_TEST_M3U8`` to a real recording URL to transcribe it
    end to end; otherwise just confirms the service imports and the model is baked."""
    m3u8 = os.environ.get("PARLAMONITOR_WHISPER_TEST_M3U8")
    playseq = os.environ.get("PARLAMONITOR_WHISPER_TEST_PLAYSEQ")
    if not m3u8:
        print("Service deployed. Set PARLAMONITOR_WHISPER_TEST_M3U8 to transcribe a sample.")
        return
    words = WhisperService().transcribe.remote(
        {"m3u8": m3u8, "playseq": playseq, "language": "hu"})
    print(f"words: {len(words)}")
    for w in words[:12]:
        print(f"  {w[0]:8.2f}–{w[1]:8.2f}  {w[2]}")
