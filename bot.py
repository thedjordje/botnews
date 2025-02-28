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
# API ključevi
TOKEN = os.getenv("TOKEN")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
LLAMA_API_KEY =os.getenv("LLAMA_API_KEY")
MEDIASTACK_API=os.getenv("MEDIASTACK_API")
GNEWS_API=os.getenv("GNEWS_API")
GROQ_API=os.getenv("GROQ_API")
GROQ_MODEL="llama3-70b-8192"

JINA_API_URL = "https://api.jina.ai/v1/chat/completions"

CACHE_EXPIRY=900

async def fetch_news_from_sources(query=None):
    async with aiohttp.ClientSession() as session:
        tasks = []
        
        # NewsAPI
        newsapi_url = f"https://newsapi.org/v2/top-headlines?country=us&apiKey={NEWS_API_KEY}" if not query else f"https://newsapi.org/v2/everything?q={query}&apiKey={NEWS_API_KEY}"
        tasks.append(fetch_json(session, newsapi_url))
        
        # MediaStack API
        mediastack_url = f"http://api.mediastack.com/v1/news?access_key={MEDIASTACK_API}&countries=us" if not query else f"http://api.mediastack.com/v1/news?access_key={MEDIASTACK_API}&keywords={query}"
        tasks.append(fetch_json(session, mediastack_url))
        
        # GNews API
        gnews_url = f"https://gnews.io/api/v4/top-headlines?token={GNEWS_API}&lang=en" if not query else f"https://gnews.io/api/v4/search?q={query}&token={GNEWS_API}"
        tasks.append(fetch_json(session, gnews_url))
        
        responses = await asyncio.gather(*tasks)
        return responses

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
    - Unesite šta želite za pretragu vijesti
    - Postavi pitanje o poslednjim vijestima nakon što ih dobiješ
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


async def determine_intent(user_input: str) -> dict:
    client = Groq(api_key=GROQ_API)
    try:
        # Promijenite system prompt u determine_intent funkciji
        system_prompt = """Analiziraj upit i odredi tip zahtjeva:
        1. get_news - za generičke zahtjeve bez specifičnih pojmova
        2. search_news - ako sadrži specifične pojmove ili imena
        3. handle_follow_up - ako se odnosi na prethodno dobijene vijesti"""
        
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )

        response = json.loads(completion.choices[0].message.content)
        return {
            "function": response.get("function", "search_news"),
            "parameters": response.get("parameters", {})
        }
        
    except Exception as e:
        print(f"Greška pri određivanju namjere: {str(e)}")
        return {"function": "search_news", "parameters": {"query": user_input}}
    

async def handle_news_request(update: Update, context: CallbackContext) -> None:
    try:
        user_input = update.message.text
        print(f"Primljen upit: {user_input}")

        intent = await determine_intent(convert_to_latin(user_input))
        intent.setdefault("parameters", {}) 
        
        print(f"Prepoznata namjera: {intent}")

        if intent["function"] == "get_news":
            await get_news(update, context)
            
        elif intent["function"] == "search_news":
            query = intent["parameters"].get("query", user_input)
            context.args = [query]
            await search_news(update, context)
            
        elif intent["function"] == "handle_follow_up":
            await handle_follow_up(update, context)
            
        else:
            await update.message.reply_text("Molim vas formulišite upit preciznije.")

    except Exception as e:
        print(f"Greška u handle_news_request: {str(e)}")
        await update.message.reply_text("Došlo je do greške. Pokušajte ponovo s drugim upitom.")


async def get_news(update: Update, context: CallbackContext) -> None:
    if is_cache_valid(context, "latest_news"):
        await update.message.reply_text(context.user_data["latest_news"]["data"])
        return

    responses = await fetch_news_from_sources()
    is_cyr = is_cyrillic(update.message.text)
    
    all_articles = []
    for data in responses:
        if not data:
            continue
        if "articles" in data: 
            articles = data["articles"]
        elif "data" in data: 
            articles = data["data"]
        else:
            continue
        
        for article in articles:
            item = {
                "title": article.get("title", "Bez naslova"),
                "description": article.get("description") or article.get("content") or article.get("title"),
                "url": article.get("url") or article.get("link")
            }
            if item["url"]: 
                all_articles.append(item)

    # Uklanjanje duplikata
    seen_urls = set()
    unique_articles = []
    for article in all_articles:
        if article["url"] not in seen_urls:
            seen_urls.add(article["url"])
            unique_articles.append(article)
    
    if not unique_articles:
        await update.message.reply_text("Trenutno nema dostupnih vijesti.")
        return

    summarized_news = []
    full_news = ""
    for article in unique_articles[:3]: 
        summary_text = article["description"] or article["title"]
        summary = await summarize_text(summary_text, is_cyr)
        summarized_news.append(f"🔹 {summary}\n{article['url']}")
        full_news += f"{article['title']}\n{article['description']}\n\n"

    context.user_data['last_news'] = full_news
    context.user_data['last_news_type'] = 'general'
    context.user_data['is_cyr'] = is_cyr

    context.user_data["latest_news"] = {
        "data": "\n\n".join(summarized_news),
        "timestamp": time.time()
    }
    await update.message.reply_text(context.user_data["latest_news"]["data"])

async def search_news(update: Update, context: CallbackContext) -> None:
    query = ' '.join(context.args) if context.args else update.message.text
    print(f"Pretraga za: {query}")

    responses = await fetch_news_from_sources(query)
    is_cyr = is_cyrillic(update.message.text)
    
    all_articles = []
    for data in responses:
        if not data:
            continue
        if "articles" in data:
            articles = data["articles"]
        elif "data" in data:
            articles = data["data"]
        else:
            continue
        
        for article in articles:
            item = {
                "title": article.get("title", "Bez naslova"),
                "description": article.get("description") or article.get("content") or article.get("title"),
                "url": article.get("url") or article.get("link")
            }
            if item["url"]:
                all_articles.append(item)

    # Filtriranje 
    query_lower = query.lower()
    filtered_articles = [
    a for a in all_articles
    if query_lower in ((a["title"] or "") + (a["description"] or "")).lower()   
    ][:10] 
 

    if not filtered_articles:
        await update.message.reply_text("Nema rezultata za ovu pretragu.")
        return

    summarized_news = []
    full_news = ""
    for article in filtered_articles[:3]:
        summary_text = article["description"] or article["title"]
        summary = await summarize_text(summary_text, is_cyr)
        summarized_news.append(f"🔹 {summary}\n{article['url']}")
        full_news += f"{article['title']}\n{article['description']}\n\n"

    context.user_data['last_news'] = full_news
    context.user_data['last_news_type'] = 'search'
    context.user_data['is_cyr'] = is_cyr

    await update.message.reply_text("\n\n".join(summarized_news))

async def handle_follow_up(update: Update, context: CallbackContext) -> None:
    if 'last_news' not in context.user_data:
        await update.message.reply_text("Prvo zatražite neke vijesti da bih mogao odgovarati na pitanja.")
        return

    client = Groq(api_key=GROQ_API)
    try:
        # Formiraj poboljšani upit sa kontekstom
        question = f"""
        Korisnik pita: {update.message.text}
        Kontekst vesti: {context.user_data['last_news'][:2000]}
        Odgovori koristeći samo informacije iz konteksta.
        Ako odgovor nije u vijestima, reci da ne znaš.
        """
        
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": question}],
            temperature=0.3
        )

        answer = completion.choices[0].message.content
        if context.user_data.get('is_cyr', False):
            answer = transliterate.translit(answer, 'sr')
        
        await update.message.reply_text(answer)

    except Exception as e:
        print(f"Greška pri obradi follow-up: {str(e)}")
        await update.message.reply_text("Došlo je do greške pri obradi vašeg pitanja.")

async def handle_news_request(update: Update, context: CallbackContext) -> None:
    try:
        user_input = update.message.text
        user_id = update.effective_user.id
        
        # Prvo provjeri da li je follow-up pitanje
        if 'last_news' in context.user_data:
            is_follow_up = await check_follow_up_relevance(
                user_input=user_input,
                news_context=context.user_data['last_news'],
                context=context
            )
            if is_follow_up:
                await handle_follow_up(update, context)
                return

        # Ako nije follow-up, nastavi sa normalnom obradom
        intent = await determine_intent(convert_to_latin(user_input))
        
        if intent["function"] == "get_news":
            await get_news(update, context)
        elif intent["function"] == "search_news":
            await search_news(update, context)
        elif intent["function"] == "handle_follow_up":
            await handle_follow_up(update, context)
        else:
            await update.message.reply_text("Molim vas postavite pitanje jasnije.")

    except Exception as e:
        print(f"Greška: {str(e)}")
        await update.message.reply_text("Došlo je do greške. Pokušajte ponovo sa jasnijim upitom.")

FOLLOW_UP_PROMPT = """Da li se sljedeći upit odnosi na prethodne vesti?
Prethodne vesti (kratak pregled): {news_context}
Upit: {user_input}

Odgovori samo sa DA ili NE."""

async def check_follow_up_relevance(user_input: str, news_context: str, context: CallbackContext) -> bool:
    """Provjerava da li se upit odnosi na postojeći kontekst vesti"""
    client = Groq(api_key=GROQ_API)
    
    try:
        short_context = news_context[:1000] 
        
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system", 
                    "content": f"Odgovori DA ako se pitanje odnosi na ove vesti, NE ako je nova tema.\nVesti: {short_context}\nPitanje: {user_input}"
                },
            ],
            temperature=0.1,
            max_tokens=3
        )
        
        answer = response.choices[0].message.content.strip().upper()
        return "DA" in answer
        
    except Exception as e:
        print(f"Greška u provjeri relevantnosti: {str(e)}")
        return False
    

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_news_request))

    print("Bot je pokrenut...")
    app.run_polling()

if __name__ == "__main__":
    main() #
