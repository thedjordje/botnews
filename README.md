# BotNews Telegram Bot

BotNews is an asynchronous Telegram bot that fetches latest headlines, searches news by topic, and answers follow-up questions using Groq LLM.

## Features

- Fetch top headlines from multiple providers
- Search news by keywords
- Generate short summaries for articles
- Keep per-user conversation context for follow-up questions
- Handle both Latin and Cyrillic Serbian inputs

## Tech Stack

- Python 3.10+
- python-telegram-bot (async)
- aiohttp
- Groq API
- NewsAPI + GNews

## Project Structure

- bot.py: Main application and bot handlers
- .env.example: Example environment variables
- requirements.txt: Python dependencies

## Prerequisites

- Python 3.10 or newer
- Telegram bot token from BotFather
- Groq API key
- At least one news provider key (NewsAPI and/or GNews)

## Setup

1. Clone repository and enter project folder.
2. Create and activate virtual environment.
3. Install dependencies.
4. Copy `.env.example` to `.env` and fill in keys.
5. Run the bot.

Example commands:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

## Environment Variables

- TELEGRAM_TOKEN: Telegram bot token (required)
- GROQ_API_KEY: Groq API key (required)
- NEWS_API_KEY: NewsAPI key (optional but recommended)
- GNEWS_API: GNews API key (optional but recommended)

If both news API keys are missing or invalid, news fetching will fail.

## Telegram Commands

- /start: Show welcome message
- /reset: Clear current conversation context

## Production Notes

- Do not commit `.env`.
- Rotate API keys if accidentally exposed.
- Add process manager (systemd, Docker, or PM2 equivalent) for uptime.
- Consider adding structured tests before deployment.

## License

You can add your preferred license (for example MIT) before publishing.
