from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import requests
import json
import time
import re
import transliterate
from langdetect import detect
import aiohttp
import asyncio
from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
GROQ_API = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "mixtral-8x7b-32768"

GROQ_MODEL="llama3-70b-8192"

CACHE_EXPIRY = 600
MAX_ARTICLES = 5     
NEWS_APIS = [
    {
        "name": "NewsAPI",
        "url": lambda q: f"https://newsapi.org/v2/{'everything' if q else 'top-headlines'}?q={q}&language=en&apiKey={NEWS_API_KEY}" if q else f"https://newsapi.org/v2/top-headlines?country=us&apiKey={NEWS_API_KEY}",
        "parser": lambda data: data.get("articles", [])
    },
    {
        "name": "GNews",
        "url": lambda q: f"https://gnews.io/api/v4/{'search' if q else 'top-headlines'}?q={q}&token={os.getenv('GNEWS_API')}&lang=en",
        "parser": lambda data: data.get("articles", [])
    }
]

#POMOCNE FUNKCIJE
def detect_language(text: str) -> str:
    try:
        return detect(text)
    except:
        return 'en'

def is_cyrillic(text: str) -> bool:
    return bool(re.search("[\u0400-\u04FF]", text))

def format_response(text: str, is_cyr: bool) -> str:
    return transliterate.translit(text, 'sr') if is_cyr else text

async def reset(update: Update, context: CallbackContext) -> None:
    """Potpuno resetuje konverzaciju i čisti sve podatke"""
    context.user_data.clear()
    
    restart_message = (
        "🔄 Konverzacija je resetovana!\n\n"
        "Možete početi potpuno iznova. Evo opcija:\n"
        "- Pošaljite bilo koju poruku za najnovije vijesti\n"
        "- Postavite specifično pitanje (npr. 'Vijesti o tenisu')\n"
        "- Pitajte bilo šta o aktualnim temama"
    )
    
    await update.message.reply_chat_action(action="typing")
    await asyncio.sleep(1.5)
    
    await update.message.reply_text(restart_message)
    
    await asyncio.sleep(0.5)
    await start(update, context)

class NewsBot:
    def __init__(self):
        self.groq_client = Groq(api_key=GROQ_API)
        
    async def determine_intent(self, text: str, context: dict) -> dict:
        """Poboljšana klasifikacija namjere sa kontekstom"""
        system_prompt = f"""
        Analiziraj upit i odredi tip zahtjeva. Dostupan kontekst: {context.get('last_news_topic','')}
        Tipovi:
        1. get_news - zahtjev za opće vijesti bez specifičnih pojmova
        2. search_news - sadrži specifične pojmove, imena ili lokacije
        3. follow_up - upit se odnosi na prethodno dobivene vijesti
        
        Odgovori u JSON formatu: {{"intent": "...", "query": "...", "reason": "..."}}
        """
        
        try:
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                response_format={"type": "json_object"},
                temperature=0.3
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"Greška u određivanju namjere: {e}")
            return {"intent": "search_news", "query": text}

    async def fetch_news(self, query: str = None) -> list:
        """Asinkrono dohvaćanje vijesti sa multiple API-ja"""
        async with aiohttp.ClientSession() as session:
            tasks = []
            for api in NEWS_APIS:
                url = api["url"](query)
                tasks.append(
                    self._fetch_api(session, url, api["parser"])
                )
            
            results = await asyncio.gather(*tasks)
            return [item for sublist in results for item in sublist][:MAX_ARTICLES]

    async def _fetch_api(self, session, url, parser):
        """Pomocna funkcija za dohvat pojedinačnog API-ja"""
        try:
            async with session.get(url) as response:
                data = await response.json()
                return parser(data)
        except Exception as e:
            print(f"Greska pri dohvatu {url}: {e}")
            return []

    async def summarize(self, text: str, is_cyr: bool) -> str:
        """Sažimanje teksta koristeći Groq"""
        try:
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "Sažmi sljedeći tekst na 2-3 rečenice na srpskom jeziku."},
                    {"role": "user", "content": text[:3000]}
                ],
                temperature=0.5
            )
            return format_response(response.choices[0].message.content, is_cyr)
        except Exception as e:
            print(f"Greška pri sazimanju: {e}")
            return format_response("Nisam uspio sazeti ovaj clanak.", is_cyr)

# --- TELEGRAM HANDLERI ---
news_bot = NewsBot()

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("📰 Dobrodošli u News Bot!\n\nPošaljite bilo koju poruku za vijesti, ili pitanje o aktualnim temama.")

async def handle_message(update: Update, context: CallbackContext):
    user_input = update.message.text
    user_id = update.effective_user.id
    is_cyr = is_cyrillic(user_input)
    
    # Provera cache-a
    if context.user_data.get('last_news') and time.time() - context.user_data.get('timestamp',0) < CACHE_EXPIRY:
        if await check_follow_up(user_input, context):
            return await handle_follow_up(update, context, is_cyr)
    
    # Određivanje namjere
    intent = await news_bot.determine_intent(
        text=user_input,
        context=context.user_data
    )
    
    # Obrada prema namjeri
    if intent['intent'] == 'get_news':
        await send_news(update, context, is_cyr)
    elif intent['intent'] == 'search_news':
        await search_news(update, context, intent['query'], is_cyr)
    else:
        await handle_follow_up(update, context, is_cyr)

async def send_news(update: Update, context: CallbackContext, is_cyr: bool):
    articles = await news_bot.fetch_news()
    
    if not articles:
        await update.message.reply_text(format_response("Trenutno nema dostupnih vijesti.", is_cyr))
        return
    
    summaries = []
    full_text = ""
    for article in articles:
        summary = await news_bot.summarize(
            f"{article.get('title', '')}\n{article.get('description', '')}",
            is_cyr
        )
        summaries.append(f"📰 {summary}\n{article.get('url', '')}")
        full_text += f"{article.get('title')}\n{article.get('description')}\n\n"
    
    # Cuvanje konteksta
    context.user_data.update({
        'last_news': full_text,
        'timestamp': time.time(),
        'last_news_topic': 'general'
    })
    
    await update.message.reply_text("\n\n".join(summaries))

async def search_news(update: Update, context: CallbackContext, query: str, is_cyr: bool):
    """Pretraga specifičnih vijesti"""
    articles = await news_bot.fetch_news(query)
    
    if not articles:
        await update.message.reply_text(format_response(f"Nema rezultata za: {query}", is_cyr))
        return
    
    # Cuvanje konteksta
    context.user_data.update({
        'last_news': "\n".join([f"{a['title']}: {a['description']}" for a in articles]),
        'timestamp': time.time(),
        'last_news_topic': query
    })
    
    # Prikaz rezultata
    response = format_response(f"🔍 Rezultati za '{query}':\n\n", is_cyr)
    response += "\n\n".join([
        f"📌 {await news_bot.summarize(a['title'], is_cyr)}\n{a['url']}"
        for a in articles[:3]
    ])
    
    await update.message.reply_text(response)

async def check_follow_up(user_input: str, context: CallbackContext) -> bool:
    """Provjera da li se upit odnosi na prethodne vijesti"""
    try:
        last_topic = context.user_data.get('last_news_topic', '')
        
        response = await asyncio.to_thread(
            news_bot.groq_client.chat.completions.create,
            model=GROQ_MODEL,
            messages=[{
                "role": "user", 
                "content": f"Da li se sljedeci upit odnosi na teme: {last_topic}?\nUpit: {user_input}\nOdgovori samo sa DA ili NE."
            }],
            temperature=0.1,
            max_tokens=2
        )
        return "DA" in response.choices[0].message.content.upper()
    except Exception as e:
        print(f"Greska u check_follow_up: {e}")
        return False

async def handle_follow_up(update: Update, context: CallbackContext, is_cyr: bool):
    """Obrada follow-up pitanja"""
    try:
        news_context = context.user_data.get('last_news', '')[:2000]
        
        response = await asyncio.to_thread(
            news_bot.groq_client.chat.completions.create,
            model=GROQ_MODEL,
            messages=[{
                "role": "system",
                "content": f"Kontekst vijesti: {news_context}\nOdgovori na srpskom jeziku."
            },{
                "role": "user",
                "content": update.message.text
            }]
        )
        answer = format_response(response.choices[0].message.content, is_cyr)
        await update.message.reply_text(answer)
    except Exception as e:
        print(f"Greska u handle_follow_up: {e}")
        await update.message.reply_text(format_response("Došlo je do greške pri obradi pitanja.", is_cyr))

async def handle_follow_up(update: Update, context: CallbackContext, is_cyr: bool):
    """Obrada follow-up pitanja"""
    try:
        response = await asyncio.to_thread(
            news_bot.groq_client.chat.completions.create,
            model=GROQ_MODEL,
            messages=[{
                "role": "system",
                "content": f"Kontekst vijesti: {context.user_data['last_news'][:2000]}\nOdgovori na srpskom jeziku."
            },{
                "role": "user",
                "content": update.message.text
            }]
        )
        answer = format_response(response.choices[0].message.content, is_cyr)
        await update.message.reply_text(answer)
    except Exception as e:
        print(f"Greska u handle_follow_up: {e}")
        await update.message.reply_text(format_response("Došlo je do greške pri obradi pitanja.", is_cyr))

def main():
    try:
        app = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("reset", reset))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("Bot je uspjesno pokrenut!")
        app.run_polling()
        
    except Exception as e:
        print(f"Kritična greska: {str(e)}")
        print("Proverite: ")
        print("- Da li je .env fajl prisutan")
        print("- Da li su API kljucevi validni")
        print("- Internet konekciju")

if __name__ == "__main__":
    main()
