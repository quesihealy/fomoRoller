"""TTS backends behind a common interface.

Each provider lists its available voices and renders a slot's script lines
to an MP3, applying pauses between lines however its API supports.
"""

import base64
import html
import io
import sys
from abc import ABC, abstractmethod
from typing import NamedTuple

import requests

import config


class Voice(NamedTuple):
    name: str
    voice_id: str


class TTSProvider(ABC):
    name: str
    api_key_env: str

    def __init__(self, api_key: str | None):
        if not api_key:
            sys.exit(f"Missing {self.api_key_env} for TTS provider '{self.name}'. See .env.example.")
        self.api_key = api_key

    @abstractmethod
    def voices(self) -> list[Voice]:
        """All voices worth rotating through for this provider."""

    @abstractmethod
    def synthesize(self, lines: list[str], voice: Voice, filepath: str) -> None:
        """Render script lines to an MP3 at filepath."""


class GoogleTTS(TTSProvider):
    """Google Cloud TTS using the Chirp3-HD voices — the most natural-sounding
    tier, and still inside the free 1M-chars/month quota for the whole project.

    Chirp3-HD doesn't support SSML, so the between-event pauses can't be done
    with <break> tags. Instead each line is synthesized on its own and the
    clips are spliced together with PAUSE_SECONDS of silence (needs ffmpeg via
    pydub). Older SSML-capable voices (Neural2/WaveNet) still work in one shot."""

    name = "google"
    api_key_env = "GOOGLE_TTS_API_KEY"
    SYNTH_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

    def voices(self) -> list[Voice]:
        response = requests.get(
            "https://texttospeech.googleapis.com/v1/voices",
            params={"languageCode": "en-US", "key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        return [
            Voice(v["name"], v["name"])
            for v in response.json()["voices"]
            if "Chirp3-HD" in v["name"]
        ]

    def synthesize(self, lines: list[str], voice: Voice, filepath: str) -> None:
        if "Chirp3" in voice.voice_id:
            self._synthesize_spliced(lines, voice, filepath)
        else:
            self._synthesize_ssml(lines, voice, filepath)

    def _synthesize_ssml(self, lines: list[str], voice: Voice, filepath: str) -> None:
        pause = f' <break time="{config.PAUSE_SECONDS}s"/> '
        ssml = "<speak>" + pause.join(html.escape(line) for line in lines) + "</speak>"
        response = requests.post(
            self.SYNTH_URL,
            params={"key": self.api_key},
            json={
                "input": {"ssml": ssml},
                "voice": {"languageCode": "en-US", "name": voice.voice_id},
                "audioConfig": {"audioEncoding": "MP3"},
            },
            timeout=120,
        )
        response.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(response.json()["audioContent"]))

    def _synth_line(self, text: str, voice: Voice) -> bytes:
        """Synthesize one plain-text line to MP3 bytes (no SSML)."""
        response = requests.post(
            self.SYNTH_URL,
            params={"key": self.api_key},
            json={
                "input": {"text": text},
                "voice": {"languageCode": "en-US", "name": voice.voice_id},
                "audioConfig": {"audioEncoding": "MP3"},
            },
            timeout=120,
        )
        response.raise_for_status()
        return base64.b64decode(response.json()["audioContent"])

    def _synthesize_spliced(self, lines: list[str], voice: Voice, filepath: str) -> None:
        from pydub import AudioSegment  # lazy: only Chirp3-HD needs ffmpeg

        gap = AudioSegment.silent(duration=int(config.PAUSE_SECONDS * 1000))
        combined = None
        for line in lines:
            clip = AudioSegment.from_file(io.BytesIO(self._synth_line(line, voice)), format="mp3")
            combined = clip if combined is None else combined + gap + clip
        combined.export(filepath, format="mp3")


class ElevenLabsTTS(TTSProvider):
    """ElevenLabs Voice Library character voices. Requires a paid plan:
    the free tier rejects library voices via the API (402)."""

    name = "elevenlabs"
    api_key_env = "ELEVENLABS_API_KEY"

    def voices(self) -> list[Voice]:
        response = requests.get(
            "https://api.elevenlabs.io/v1/shared-voices",
            headers={"xi-api-key": self.api_key},
            params={
                "page_size": config.VOICE_POOL_SIZE,
                "use_cases": "characters_animation",
                "language": "en",
                "sort": "usage_character_count_1y",
            },
            timeout=30,
        )
        response.raise_for_status()
        return [Voice(v["name"], v["voice_id"]) for v in response.json()["voices"]]

    def synthesize(self, lines: list[str], voice: Voice, filepath: str) -> None:
        text = f' <break time="{config.PAUSE_SECONDS}.0s" /> '.join(lines)
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice.voice_id}",
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            json={
                "text": text,
                # Library voices don't support the legacy monolingual_v1 model
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
            },
            timeout=120,
        )
        response.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(response.content)


class TypecastTTS(TTSProvider):
    """Typecast character voices. No SSML support: pauses are approximated
    with paragraph breaks. Untested until we have an API key."""

    name = "typecast"
    api_key_env = "TYPECAST_API_KEY"

    def voices(self) -> list[Voice]:
        response = requests.get(
            "https://api.typecast.ai/v1/voices",
            headers={"X-API-KEY": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        return [Voice(v["voice_name"], v["voice_id"]) for v in response.json()]

    def synthesize(self, lines: list[str], voice: Voice, filepath: str) -> None:
        response = requests.post(
            "https://api.typecast.ai/v1/text-to-speech",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            json={
                "voice_id": voice.voice_id,
                "text": "\n\n".join(lines),
                "model": "ssfm-v30",
                "output": {"audio_format": "mp3"},
            },
            timeout=120,
        )
        response.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(response.content)


_PROVIDERS: dict[str, tuple[type[TTSProvider], str | None]] = {
    "google": (GoogleTTS, config.GOOGLE_TTS_API_KEY),
    "elevenlabs": (ElevenLabsTTS, config.ELEVENLABS_API_KEY),
    "typecast": (TypecastTTS, config.TYPECAST_API_KEY),
}


def get_provider(name: str) -> TTSProvider:
    if name not in _PROVIDERS:
        sys.exit(f"Unknown TTS_PROVIDER '{name}'. Use one of: {', '.join(_PROVIDERS)}.")
    cls, api_key = _PROVIDERS[name]
    return cls(api_key)
