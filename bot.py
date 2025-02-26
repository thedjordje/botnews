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

# API ključevi
TOKEN = "7653576698:AAElF2WXPyg-MuxQaxyKeW099noDKLgwlxk"
NEWS_API_KEY = "d66e7779662147e58f7e2424dfeccbda"
LLAMA_API_KEY = "d46c1d655d3edcf1d9683516fd77bf1cca208325548071ea73efe47a67806ea6"

CACHE_EXPIRY = 900

def is_cache_valid(context: CallbackContext, key: str) -> bool:
    if key in context.user_data and "timestamp" in context.user_data[key]:
        return time.time() - context.user_data[key]["timestamp"] < CACHE_EXPIRY
    return False

def detect_language(text: str) -> str:
    try:
        return detect(text)
    except:
        return 'en'
    
def is_cyrillic(text: str) -> bool:
    return bool(re.search("[\u0400-\u04FF]", text))

def convert_to_latin(text: str) -> str:
    return transliterate.translit(text, reversed=True) if is_cyrillic(text) else text

async def start(update: Update, context: CallbackContext) -> None:
    welcome_message = """
    Zdravo! Ja sam AI News bot. Evo šta možeš da uradiš:
    - Recite "Želim nove vijesti", "Daj mi najnovije vesti" ili slično za najnovije vijesti
    - Recite "[ključne riječi]" ili [pojam]" za pretragu vijesti
    - Postavi pitanje o poslednjim vestima nakon što ih dobiješ
    - /reset - Restartuj konverzaciju i počni iznova
    """
    await update.message.reply_text(welcome_message)

async def reset(update: Update, context: CallbackContext) -> None:
    context.user_data.clear()
    await update.message.reply_text("Konverzacija je resetovana. Možete početi iznova.")

async def fetch_json(session, url, headers=None, params=None, data=None, method="GET"):
    async with session.request(method, url, headers=headers, params=params, json=data) as response:
        return await response.json()

async def summarize_text(text: str, is_cyr: bool) -> str:
    url = "https://api.together.xyz/v1/chat/completions"
    headers = {"Authorization": f"Bearer {LLAMA_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "messages": [{"role": "system", "content": "Sažmi sledeći tekst u 2-3 rečenice."},
                     {"role": "user", "content": text}]
    }

    async with aiohttp.ClientSession() as session:
        retries = 3
        for attempt in range(retries):
            response_json = await fetch_json(session, url, headers=headers, data=data, method="POST")
            
            if "choices" in response_json and response_json["choices"]:
                summary = response_json["choices"][0]["message"]["content"].strip()
                return transliterate.translit(summary, 'sr') if is_cyr else summary
            elif "error" in response_json and "rate limit" in response_json["error"]["message"]:
                await asyncio.sleep(10)
            else:
                return "Nisam uspio da sažmem ovu vijest. 😕"

    return "API je preopterećen. Pokušajte kasnije. 😕"

async def get_news(update: Update, context: CallbackContext) -> None:
    if is_cache_valid(context, "latest_news"):
        await update.message.reply_text(context.user_data["latest_news"]["data"])
        return
    
    url = f"https://newsapi.org/v2/top-headlines?country=us&apiKey={NEWS_API_KEY}"
    
    async with aiohttp.ClientSession() as session:
        data = await fetch_json(session, url)
        is_cyr = is_cyrillic(update.message.text)
    
        if data["status"] == "ok":
            articles = data["articles"][:3]
            summarized_news = []
            full_news = ""

            for article in articles:
                summary = await summarize_text(article["description"] or article["title"], is_cyr)
                summarized_news.append(f"🔹 {summary}\n{article['url']}")
                full_news += f"{article['title']}\n{article['description']}\n"

            context.user_data['last_news'] = full_news
            context.user_data['is_cyr'] = is_cyr

            context.user_data["latest_news"] = {
                "data": full_news,
                "timestamp": time.time()
            }

            news_text = "\n\n".join(summarized_news)
            await update.message.reply_text(news_text)
        else:
            await update.message.reply_text("Greška pri preuzimanju vijesti. Pokušajte kasnije.")

async def search_news(update: Update, context: CallbackContext) -> None:
    query = update.message.text
    print(f"Korisnički upit: {query}")  

    url = f"https://newsapi.org/v2/everything?q={query}&apiKey={NEWS_API_KEY}"

    async with aiohttp.ClientSession() as session:
        data = await fetch_json(session, url)
        print(f"API odgovor: {data}")  

        if data["status"] != "ok" or not data.get("articles"):
            await update.message.reply_text("Nema rezultata za ovu pretragu.")
            return

        articles = data["articles"][:3]
        summarized_news = []
        full_news = ""  # Ovaj string će čuvati pune vesti

        for article in articles:
            title = article.get("title", "Bez naslova")
            description = article.get("description", title)  # Ako nema opisa, koristi naslov
            print(f"Originalna vest: {title} - {description}")  

            summary = await summarize_text(description, is_cyrillic(update.message.text))
            print(f"Sažetak:")

            if not summary.strip():
                continue

            summarized_news.append(f"🔹 {summary}\n{article['url']}")
            full_news += f"{title}\n{description}\n"

        if not summarized_news:
            await update.message.reply_text("Nema relevantnih vesti za ovu pretragu.")
            return

        # Čuvamo pretražene vesti u user_data
        context.user_data["last_news"] = full_news
        context.user_data["is_cyr"] = is_cyrillic(update.message.text)

        news_text = "\n\n".join(summarized_news)
        print(f"Finalni tekst za slanje")  

        await update.message.reply_text(news_text)

async def handle_follow_up(update: Update, context: CallbackContext) -> None:
    if 'last_news' not in context.user_data:
        question = update.message.text
        words = question.split()
        
        stop_words = {"šta", "kako", "gde", "ko", "kada", "zašto", "mi", "ne", "se", "to"}
        key_words = [word for word in words if word.lower() not in stop_words]

        if key_words:
            context.args = key_words 
            await search_news(update, context)
        else:
            await update.message.reply_text("Nema dovoljno informacija za pretragu vesti. Molim vas, pokušajte ponovo.")
        return

    question = convert_to_latin(update.message.text)
    full_news = context.user_data['last_news']
    is_cyr = context.user_data['is_cyr']
    
    url = "https://api.together.xyz/v1/chat/completions"
    headers = {"Authorization": f"Bearer {LLAMA_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
        "messages": [
            {"role": "system", "content": "Odgovori na pitanje na osnovu sljedećih vijesti."},
            {"role": "user", "content": f"Vesti: {full_news}\n\nPitanje: {question}"}
        ]
    }
    
    async with aiohttp.ClientSession() as session:
        response_json = await fetch_json(session, url, headers=headers, data=data, method="POST")
    
        if "choices" in response_json and response_json["choices"]:
            answer = response_json["choices"][0]["message"]["content"].strip()
            answer = transliterate.translit(answer, 'sr') if is_cyr else answer
            await update.message.reply_text(answer)
        else:
            await update.message.reply_text("Nisam uspio da pronađem odgovor. 😕")

async def handle_news_request(update: Update, context: CallbackContext) -> None:
    if not update.message:
        return
    print(f"Primljen update: {update.to_dict()}")
    text = update.message.text
    converted_text = convert_to_latin(text).lower()

    news_patterns = [
        r"^(želim nove vijesti|daj mi nove vijesti|novosti|nove vesti|danasnje vesti|vesti danas|daj mi najnovije vesti|news|latest news|nove vijesti)$",
        r"\b(najnovije vesti|novosti|vijesti danas|nove vijesti)\b"
    ]
    
    for pattern in news_patterns:
        if re.search(pattern, converted_text, re.IGNORECASE):
            await get_news(update, context)
            return

    search_match = re.match(r"^(nađi mi|traži| ||zanima me|kako se to|pretraži|search|find)\s+(.+)", converted_text, re.IGNORECASE)
    if search_match:
        context.args = search_match.group(2).split()
        await search_news(update, context)
        return

    await handle_follow_up(update, context)


def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_news_request))

    print("Bot je pokrenut...")
    app.run_polling()

if __name__ == "__main__":
    main()
