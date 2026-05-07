# Happy Hour Finder - AI Agent Instructions

## Project Overview
This is a Python application that finds happy hour deals using Google Maps scraping, parses them with NLP, syncs to Google Calendar, and shares via WhatsApp.

## Getting Started
- Install dependencies: `pip install -r requirements.txt`
- Run: `python src/main.py`
- See [setup_guide.md](setup_guide.md) for detailed setup including Google API and WhatsApp.

## Architecture
- **main.py**: CLI menu dispatcher
- **scanner.py**: Google Maps scraper
- **happy_hour_parser.py**: NLP parsing and enrichment
- **calendar_sync.py**: Google Calendar integration
- **whatsapp_bot.py**: WhatsApp automation

Data flows: Scan → Parse → Calendar → WhatsApp

## Conventions
- Config in `config/`: settings.json, friends.json, credentials.json
- Data in `data/`: JSON files for persistence
- Phone numbers in E.164 format (+1...)
- Use Path for file paths

## Common Pitfalls
- Google OAuth setup required manually
- Chrome must be installed
- WhatsApp QR scan once, profile persisted
- Selectors may break with UI updates

## Testing
Run individual modules: `python src/scanner.py`, etc.

For more details, see [setup_guide.md](setup_guide.md)