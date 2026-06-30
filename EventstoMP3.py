import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
EVENTS_API_URL = os.environ.get("EVENTS_API_URL")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./audio")
BM_TZ = ZoneInfo("America/Los_Angeles")


def require_env():
    missing = [
        name for name, val in {
            "ELEVENLABS_API_KEY": ELEVENLABS_API_KEY,
            "ELEVENLABS_VOICE_ID": VOICE_ID,
            "EVENTS_API_URL": EVENTS_API_URL,
        }.items() if not val
    ]
    if missing:
        sys.exit(f"Missing required env vars: {', '.join(missing)}. See .env.example.")


def fetch_events():
    response = requests.get(EVENTS_API_URL, timeout=30)
    response.raise_for_status()
    return response.json()


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


def build_script(slot_key, events):
    dt = datetime.strptime(slot_key, "%Y-%m-%d_%H-%M").replace(tzinfo=BM_TZ)
    time_str = dt.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")

    lines = [f"It's {time_str} on the playa. Here's what's happening right now."]

    for e in events:
        title = e.get("title", "Untitled")
        desc = e.get("description", "")
        etype = e.get("event_type", {}).get("label", "")
        snippet = desc[:120].rsplit(" ", 1)[0] + "..." if len(desc) > 120 else desc
        lines.append(f"{title}. {etype}. {snippet}")

    lines.append("Get out there. You're missing it.")
    return " ... ".join(lines)


def text_to_mp3(text, filepath):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
    response = requests.post(
        url,
        headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": "eleven_monolingual_v1",
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

    print("Fetching events...")
    events = fetch_events()
    print(f"  Got {len(events)} events")

    print("Building slot map...")
    slot_map = build_slot_map(events)
    print(f"  {len(slot_map)} active slots")

    # Uncomment to generate audio (will hit ElevenLabs API and use credits):
    # print("Generating audio...")
    # for slot_key, slot_events in sorted(slot_map.items()):
    #     filepath = os.path.join(OUTPUT_DIR, f"{slot_key}.mp3")
    #     if os.path.exists(filepath):
    #         print(f"  Skipping {slot_key} (already exists)")
    #         continue
    #     script = build_script(slot_key, slot_events)
    #     print(f"  {slot_key}: {len(slot_events)} events, {len(script)} chars")
    #     text_to_mp3(script, filepath)

    print("Done. Copy ./audio to the SD card.")


if __name__ == "__main__":
    main()
