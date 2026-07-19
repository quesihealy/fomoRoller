import base64
import html
import json
import os
import random
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
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

BURNING_MAN_API_KEY = os.environ.get("BURNING_MAN_API_KEY")
BURNING_MAN_YEAR = int(os.environ.get("BURNING_MAN_YEAR", datetime.now().year))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./audio")
DATA_DIR = os.environ.get("DATA_DIR", "./data")
BM_TZ = ZoneInfo("America/Los_Angeles")
BM_EVENTS_URL = "https://api.burningman.org/api/event"
BM_CAMPS_URL = "https://api.burningman.org/api/camp"

PAUSE_SECONDS = 2

PROVIDER_KEYS = {
    "google": ("GOOGLE_TTS_API_KEY", GOOGLE_TTS_API_KEY),
    "elevenlabs": ("ELEVENLABS_API_KEY", ELEVENLABS_API_KEY),
    "typecast": ("TYPECAST_API_KEY", TYPECAST_API_KEY),
}


def require_env():
    if TTS_PROVIDER not in PROVIDER_KEYS:
        sys.exit(f"Unknown TTS_PROVIDER '{TTS_PROVIDER}'. Use one of: {', '.join(PROVIDER_KEYS)}.")
    key_name, key_val = PROVIDER_KEYS[TTS_PROVIDER]
    missing = [
        name for name, val in {
            key_name: key_val,
            "BURNING_MAN_API_KEY": BURNING_MAN_API_KEY,
        }.items() if not val
    ]
    if missing:
        sys.exit(f"Missing required env vars: {', '.join(missing)}. See .env.example.")


# --- Google Cloud TTS ------------------------------------------------------
# Free tier: 4M chars/month WaveNet + 1M chars/month Neural2 — covers the
# whole project at 7 events/slot. Both families support SSML breaks
# (Chirp3 HD doesn't, so it's excluded).

def google_voice_pool():
    response = requests.get(
        "https://texttospeech.googleapis.com/v1/voices",
        params={"languageCode": "en-US", "key": GOOGLE_TTS_API_KEY},
        timeout=30,
    )
    response.raise_for_status()
    voices = [
        v["name"] for v in response.json()["voices"]
        if "Neural2" in v["name"] or "Wavenet" in v["name"]
    ]
    random.shuffle(voices)
    return [(name, name) for name in voices[:VOICE_POOL_SIZE]]


def google_tts(lines, voice_name, filepath):
    ssml = (
        "<speak>"
        + f' <break time="{PAUSE_SECONDS}s"/> '.join(html.escape(line) for line in lines)
        + "</speak>"
    )
    response = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        params={"key": GOOGLE_TTS_API_KEY},
        json={
            "input": {"ssml": ssml},
            "voice": {"languageCode": "en-US", "name": voice_name},
            "audioConfig": {"audioEncoding": "MP3"},
        },
        timeout=120,
    )
    response.raise_for_status()
    with open(filepath, "wb") as f:
        f.write(base64.b64decode(response.json()["audioContent"]))


# --- ElevenLabs -------------------------------------------------------------
# Paid plan required for Voice Library voices via API (free tier gets 402).

def elevenlabs_voice_pool():
    response = requests.get(
        "https://api.elevenlabs.io/v1/shared-voices",
        headers={"xi-api-key": ELEVENLABS_API_KEY},
        params={
            "page_size": VOICE_POOL_SIZE,
            "use_cases": "characters_animation",
            "language": "en",
            "sort": "usage_character_count_1y",
        },
        timeout=30,
    )
    response.raise_for_status()
    return [(v["name"], v["voice_id"]) for v in response.json()["voices"]]


def elevenlabs_tts(lines, voice_id, filepath):
    text = f' <break time="{PAUSE_SECONDS}.0s" /> '.join(lines)
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
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


# --- Typecast ---------------------------------------------------------------
# No SSML support: pauses are approximated with paragraph breaks. Untested
# until we have an API key.

def typecast_voice_pool():
    response = requests.get(
        "https://api.typecast.ai/v1/voices",
        headers={"X-API-KEY": TYPECAST_API_KEY},
        timeout=30,
    )
    response.raise_for_status()
    voices = response.json()
    random.shuffle(voices)
    return [(v["voice_name"], v["voice_id"]) for v in voices[:VOICE_POOL_SIZE]]


def typecast_tts(lines, voice_id, filepath):
    response = requests.post(
        "https://api.typecast.ai/v1/text-to-speech",
        headers={"X-API-KEY": TYPECAST_API_KEY, "Content-Type": "application/json"},
        json={
            "voice_id": voice_id,
            "text": "\n\n".join(lines),
            "model": "ssfm-v30",
            "output": {"audio_format": "mp3"},
        },
        timeout=120,
    )
    response.raise_for_status()
    with open(filepath, "wb") as f:
        f.write(response.content)


PROVIDERS = {
    "google": (google_voice_pool, google_tts),
    "elevenlabs": (elevenlabs_voice_pool, elevenlabs_tts),
    "typecast": (typecast_voice_pool, typecast_tts),
}


def build_voice_pool():
    if VOICE_ID:
        return [("pinned", VOICE_ID)]
    pool_fn, _ = PROVIDERS[TTS_PROVIDER]
    pool = pool_fn()
    if not pool:
        sys.exit(f"{TTS_PROVIDER} returned no voices. Set TTS_VOICE_ID to pin one instead.")
    return pool


def text_to_mp3(lines, voice_id, filepath):
    _, tts_fn = PROVIDERS[TTS_PROVIDER]
    tts_fn(lines, voice_id, filepath)
    print(f"  Saved: {filepath}")


def fetch_events():
    response = requests.get(
        BM_EVENTS_URL,
        headers={"X-API-Key": BURNING_MAN_API_KEY},
        params={"year": BURNING_MAN_YEAR},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    os.makedirs(DATA_DIR, exist_ok=True)
    cache_path = os.path.join(DATA_DIR, f"events_{BURNING_MAN_YEAR}.json")
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Cached response to {cache_path}")

    return data


def fetch_camps():
    cache_path = os.path.join(DATA_DIR, f"camps_{BURNING_MAN_YEAR}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            camps = json.load(f)
    else:
        response = requests.get(
            BM_CAMPS_URL,
            headers={"X-API-Key": BURNING_MAN_API_KEY},
            params={"year": BURNING_MAN_YEAR},
            timeout=30,
        )
        response.raise_for_status()
        camps = response.json()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(camps, f, indent=2)
        print(f"  Cached camps to {cache_path}")

    return {c["uid"]: c["name"] for c in camps if c.get("uid") and c.get("name")}


def build_slot_map(events):
    slots = {}
    for event in events:
        for occurrence in event.get("occurrence_set", []):
            start = datetime.fromisoformat(occurrence["start_time"]).astimezone(BM_TZ)
            end = datetime.fromisoformat(occurrence["end_time"]).astimezone(BM_TZ)

            slot = start.replace(minute=0 if start.minute < 30 else 30, second=0, microsecond=0)
            while slot < end:
                key = slot.strftime("%Y-%m-%d_%H-%M")
                slots.setdefault(key, []).append(event)
                slot += timedelta(minutes=30)
    return slots


def build_script(events, camp_names):
    """Return the list of lines to read; the provider decides how to pause between them."""
    lines = []
    for e in events:
        title = e.get("title", "Untitled")
        desc = e.get("description", "")
        snippet = desc[:120].rsplit(" ", 1)[0] + "..." if len(desc) > 120 else desc
        camp = camp_names.get(e.get("hosted_by_camp"))
        if camp:
            lines.append(f"{camp} is hosting {title}. {snippet}")
        else:
            lines.append(f"{title}. {snippet}")

    lines.append("Are you still FOMO-rolling? Chill dog.")
    return lines


def main():
    require_env()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Fetching events for {BURNING_MAN_YEAR}...")
    events = fetch_events()
    print(f"  Got {len(events)} events")

    # print("Building slot map...")
    # slot_map = build_slot_map(events)
    # print(f"  {len(slot_map)} active slots")

    # print("Fetching camp names...")
    # camp_names = fetch_camps()
    # print(f"  {len(camp_names)} camps")

    # print(f"Building voice pool ({TTS_PROVIDER})...")
    # voice_pool = build_voice_pool()
    # print(f"  {len(voice_pool)} voices: {', '.join(name for name, _ in voice_pool)}")

    # Uncomment to generate audio (will hit the TTS API and use credits/quota):
    # print("Generating audio...")
    # for slot_key, slot_events in sorted(slot_map.items()):
    #     filepath = os.path.join(OUTPUT_DIR, f"{slot_key}.mp3")
    #     if os.path.exists(filepath):
    #         print(f"  Skipping {slot_key} (already exists)")
    #         continue
    #     lines = build_script(slot_events, camp_names)
    #     voice_name, voice_id = random.choice(voice_pool)
    #     print(f"  {slot_key}: {len(slot_events)} events, voice={voice_name}")
    #     text_to_mp3(lines, voice_id, filepath)

    print("Done. Copy ./audio to the SD card.")


if __name__ == "__main__":
    main()
