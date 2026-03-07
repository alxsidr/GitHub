# MS Learn Agent

CLI tool that converts Microsoft Learn courses into Word documents and MP3 audio.

## Setup
```bash
pip install -e .
```

## Usage
```bash
mslearn docx "https://learn.microsoft.com/en-us/training/paths/..." -o ./output
mslearn audio "https://learn.microsoft.com/en-us/training/paths/..." -o ./output
mslearn both "https://learn.microsoft.com/en-us/training/paths/..." -o ./output
```

## Architecture
- `catalog.py` — MS Learn Catalog API client (metadata: paths, modules, units)
- `scraper.py` — HTML scraper for unit content + image download
- `models.py` — Dataclasses: Course, Module, Unit, ContentBlock
- `exporters/docx_exporter.py` — Word document generation
- `exporters/audio_exporter.py` — MP3 via edge-tts (free)
- `cli.py` — Click CLI entry point

## Key facts
- MS Learn content is server-rendered HTML (no Selenium needed)
- Catalog API: `GET https://learn.microsoft.com/api/catalog/` (no auth)
- Unit URLs not in API — extracted by scraping module page TOC
- Images: relative `media/file.png` → `https://learn.microsoft.com/en-us/training/modules/{slug}/media/{file}`
- edge-tts: free, no API key, Microsoft Edge's neural TTS
