# fomoRoller

Roll it on the playa, hear what you're missing.

The project has two halves that run in different places at different times:

## `generator/` — runs ahead of time, on a Mac

Fetches the Burning Man event/camp APIs, builds a ~90-second script for every
30-minute slot of the event week, and synthesizes one MP3 per slot
(`audio/YYYY-MM-DD_HH-MM.mp3`) with a random voice per file.

```sh
python generator/EventstoMP3.py              # dry run: fetch + report, no TTS
python generator/EventstoMP3.py --generate   # synthesize all MP3s
python generator/EventstoMP3.py --generate --slot 2026-09-01_00-00  # one slot
```

Settings live in `.env` at the repo root (see `.env.example`). The TTS
backend is selected with `TTS_PROVIDER`: `google` (default; free tier covers
the whole run), `elevenlabs`, or `typecast`.

The resulting `audio/` directory is the SD card payload (~300 MB).

## `pi/` — runs on the Raspberry Pi, on the playa

Plays the MP3 matching the current time slot when the roller is rolled.
No network, no API keys — just the SD card audio. (Code coming soon.)
