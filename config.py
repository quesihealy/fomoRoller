"""Environment-driven settings, loaded once from .env."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# "google" (free tier covers the POC), "elevenlabs", or "typecast"
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "google")

GOOGLE_TTS_API_KEY = os.environ.get("GOOGLE_TTS_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
TYPECAST_API_KEY = os.environ.get("TYPECAST_API_KEY")

# Optional: pin a single voice for the active provider. If unset, each MP3
# gets a random voice from the provider's pool.
VOICE_ID = os.environ.get("TTS_VOICE_ID")
if not VOICE_ID and TTS_PROVIDER == "elevenlabs":
    VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
VOICE_POOL_SIZE = int(os.environ.get("VOICE_POOL_SIZE", "20"))

# Seconds of silence between events in a slot's readout
PAUSE_SECONDS = 2

BURNING_MAN_API_KEY = os.environ.get("BURNING_MAN_API_KEY")
BURNING_MAN_YEAR = int(os.environ.get("BURNING_MAN_YEAR", datetime.now().year))

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./audio")
DATA_DIR = os.environ.get("DATA_DIR", "./data")

BM_TZ = ZoneInfo("America/Los_Angeles")
