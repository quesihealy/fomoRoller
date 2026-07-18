import json
import os
import random
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
# Optional: pin a single voice. If unset, each MP3 gets a random popular character voice.
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
VOICE_POOL_SIZE = int(os.environ.get("VOICE_POOL_SIZE", "20"))
BURNING_MAN_API_KEY = os.environ.get("BURNING_MAN_API_KEY")
BURNING_MAN_YEAR = int(os.environ.get("BURNING_MAN_YEAR", datetime.now().year))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./audio")
DATA_DIR = os.environ.get("DATA_DIR", "./data")
BM_TZ = ZoneInfo("America/Los_Angeles")
BM_EVENTS_URL = "https://api.burningman.org/api/event"
BM_CAMPS_URL = "https://api.burningman.org/api/camp"


def require_env():
    missing = [
        name for name, val in {
            "ELEVENLABS_API_KEY": ELEVENLABS_API_KEY,
            "BURNING_MAN_API_KEY": BURNING_MAN_API_KEY,
        }.items() if not val
    ]
    if missing:
        sys.exit(f"Missing required env vars: {', '.join(missing)}. See .env.example.")


def build_voice_pool():
    """Return [(name, voice_id)] of the most-used character voices in the Voice Library.

    Library voices work directly by ID — no need to save them to My Voices
    (not available to free tier via API, though).
    """
    if VOICE_ID:
        return [("pinned", VOICE_ID)]

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

    pool = [(v["name"], v["voice_id"]) for v in response.json()["voices"]]
    if not pool:
        sys.exit("Voice Library search returned no voices. Set ELEVENLABS_VOICE_ID to pin one instead.")
    return pool


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
    return ' <break time="2.0s" /> '.join(lines)


def text_to_mp3(text, voice_id, filepath):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    response = requests.post(
        url,
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
    print(f"  Saved: {filepath}")


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

    # print("Building voice pool...")
    # voice_pool = build_voice_pool()
    # print(f"  {len(voice_pool)} voices: {', '.join(name for name, _ in voice_pool)}")

    # Uncomment to generate audio (will hit ElevenLabs API and use credits):
    # print("Generating audio...")
    # for slot_key, slot_events in sorted(slot_map.items()):
    #     filepath = os.path.join(OUTPUT_DIR, f"{slot_key}.mp3")
    #     if os.path.exists(filepath):
    #         print(f"  Skipping {slot_key} (already exists)")
    #         continue
    #     script = build_script(slot_events, camp_names)
    #     voice_name, voice_id = random.choice(voice_pool)
    #     print(f"  {slot_key}: {len(slot_events)} events, {len(script)} chars, voice={voice_name}")
    #     text_to_mp3(script, voice_id, filepath)

    print("Done. Copy ./audio to the SD card.")


if __name__ == "__main__":
    main()
