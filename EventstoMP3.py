"""Fetch Burning Man events and render each 30-minute slot to an MP3.

By default this is a dry run: it fetches and caches data, then reports what
would be generated. Pass --generate to synthesize audio with the provider
selected by TTS_PROVIDER (see config.py / .env.example).
"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta

import requests

import config
import tts


def fetch_events() -> list[dict]:
    response = requests.get(
        "https://api.burningman.org/api/event",
        headers={"X-API-Key": config.BURNING_MAN_API_KEY},
        params={"year": config.BURNING_MAN_YEAR},
        timeout=30,
    )
    response.raise_for_status()
    events = response.json()

    os.makedirs(config.DATA_DIR, exist_ok=True)
    cache_path = os.path.join(config.DATA_DIR, f"events_{config.BURNING_MAN_YEAR}.json")
    with open(cache_path, "w") as f:
        json.dump(events, f, indent=2)
    print(f"  Cached response to {cache_path}")

    return events


def fetch_camps() -> dict[str, str]:
    """Map camp uid -> camp name, cached locally."""
    cache_path = os.path.join(config.DATA_DIR, f"camps_{config.BURNING_MAN_YEAR}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            camps = json.load(f)
    else:
        response = requests.get(
            "https://api.burningman.org/api/camp",
            headers={"X-API-Key": config.BURNING_MAN_API_KEY},
            params={"year": config.BURNING_MAN_YEAR},
            timeout=30,
        )
        response.raise_for_status()
        camps = response.json()
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(camps, f, indent=2)
        print(f"  Cached camps to {cache_path}")

    return {c["uid"]: c["name"] for c in camps if c.get("uid") and c.get("name")}


def build_slot_map(events: list[dict]) -> dict[str, list[dict]]:
    """Map slot key (YYYY-MM-DD_HH-MM, half-hour grid) -> events active then."""
    slots: dict[str, list[dict]] = {}
    for event in events:
        for occurrence in event.get("occurrence_set", []):
            start = datetime.fromisoformat(occurrence["start_time"]).astimezone(config.BM_TZ)
            end = datetime.fromisoformat(occurrence["end_time"]).astimezone(config.BM_TZ)

            slot = start.replace(minute=0 if start.minute < 30 else 30, second=0, microsecond=0)
            while slot < end:
                key = slot.strftime("%Y-%m-%d_%H-%M")
                slots.setdefault(key, []).append(event)
                slot += timedelta(minutes=30)
    return slots


def build_script(events: list[dict], camp_names: dict[str, str]) -> list[str]:
    """Lines to read for one slot; the provider decides how to pause between them."""
    if len(events) > config.EVENTS_PER_SLOT:
        events = random.sample(events, config.EVENTS_PER_SLOT)

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


def build_voice_pool(provider: tts.TTSProvider) -> list[tts.Voice]:
    if config.VOICE_ID:
        return [tts.Voice("pinned", config.VOICE_ID)]

    voices = provider.voices()
    if not voices:
        sys.exit(f"{provider.name} returned no voices. Set TTS_VOICE_ID to pin one instead.")
    random.shuffle(voices)
    return voices[: config.VOICE_POOL_SIZE]


def generate_audio(slot_map, camp_names, provider, only_slot=None):
    voice_pool = build_voice_pool(provider)
    print(f"  {len(voice_pool)} voices: {', '.join(v.name for v in voice_pool)}")

    print("Generating audio...")
    for slot_key, slot_events in sorted(slot_map.items()):
        if only_slot and slot_key != only_slot:
            continue
        filepath = os.path.join(config.OUTPUT_DIR, f"{slot_key}.mp3")
        if os.path.exists(filepath):
            print(f"  Skipping {slot_key} (already exists)")
            continue
        lines = build_script(slot_events, camp_names)
        voice = random.choice(voice_pool)
        print(f"  {slot_key}: {len(slot_events)} events, voice={voice.name}")
        provider.synthesize(lines, voice, filepath)
        print(f"  Saved: {filepath}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generate",
        action="store_true",
        help="synthesize MP3s (hits the TTS API and uses credits/quota); default is a dry run",
    )
    parser.add_argument(
        "--slot",
        metavar="YYYY-MM-DD_HH-MM",
        help="with --generate, only render this one slot (handy for samples)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not config.BURNING_MAN_API_KEY:
        sys.exit("Missing required env var BURNING_MAN_API_KEY. See .env.example.")
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    print(f"Fetching events for {config.BURNING_MAN_YEAR}...")
    events = fetch_events()
    print(f"  Got {len(events)} events")

    print("Building slot map...")
    slot_map = build_slot_map(events)
    print(f"  {len(slot_map)} active slots")

    print("Fetching camp names...")
    camp_names = fetch_camps()
    print(f"  {len(camp_names)} camps")

    if args.generate:
        provider = tts.get_provider(config.TTS_PROVIDER)
        print(f"Using TTS provider: {provider.name}")
        generate_audio(slot_map, camp_names, provider, only_slot=args.slot)
    else:
        total_chars = sum(
            len(" ".join(build_script(v, camp_names))) for v in slot_map.values()
        )
        print(f"Dry run: would generate {len(slot_map)} MP3s (~{total_chars:,} chars).")
        print("Re-run with --generate to synthesize audio.")

    print("Done. Copy ./audio to the SD card.")


if __name__ == "__main__":
    main()
