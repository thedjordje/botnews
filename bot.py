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
JINA_API="jina_ceaf8a53c36e4874b25cd505ef754dd7hr4I9wVb5_PuScaZpax6POXgKDKP"
MEDIASTACK_API="fe94d030ca1d0a2556dcf5b564515aba"
GNEWS_API="1a76969c70467fd2ff1e6757bbf48552"

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

NEWS_FUNCTIONS = [
    {
        "name": "get_news",
        "description": "Get the latest news and headlines. It is used when the user requests new news, latest news, today's news or similar.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "search_news",
        "description": "Search news by keywords or by sentence context. It is used when the user is looking for news on a certain topic or specific information and during any input, unless it is a subquery for already received news.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords, e.g. 'what happened...' 'when did it happen...' 'politics', 'sport', 'economy' '' ' '"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "handle_follow_up",
        "description": "Answers to questions about previously received news. It is used when the user asks a question about already received news.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

async def determine_intent(user_input: str) -> dict:
    headers = {
        "Authorization": f"Bearer {JINA_API}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "jina-reranker-v1-base-en",
        "messages": [{"role": "user", "content": user_input}],
        "functions": NEWS_FUNCTIONS,
        "function_call": "auto"
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(JINA_API_URL, headers=headers, json=payload) as response:
                response_data = await response.json()
                
                if "choices" not in response_data:
                    if any(word in user_input.lower() for word in ["vijesti", "vesti", "novosti", "najnovije"]):
                        return {"function": "get_news", "parameters": {}}
                    return {"function": "fallback", "parameters": {}}
                    
                choice = response_data["choices"][0]
                if "message" not in choice or "function_call" not in choice["message"]:
                    # Ručna detekcija
                    if any(word in user_input.lower() for word in ["vijesti", "vesti", "novosti", "najnovije"]):
                        return {"function": "get_news", "parameters": {}}
                    return {"function": "handle_follow_up", "parameters": {}}

                function_call = choice["message"]["function_call"]
                return {
                    "function": function_call["name"],
                    "parameters": json.loads(function_call.get("arguments", "{}"))
                }
        except Exception as e:
            print(f"Greška pri određivanju namere: {str(e)}")
            # Fallback na ručnu detekciju
            if any(word in user_input.lower() for word in ["vijesti", "vesti", "novosti", "najnovije"]):
                return {"function": "get_news", "parameters": {}}
            return {"function": "fallback", "parameters": {}}
        
async def handle_news_request(update: Update, context: CallbackContext) -> None:
    try:
        user_input = update.message.text
        print(f"Primljen upit: {user_input}")  # Debug logging
        
        intent = await determine_intent(convert_to_latin(user_input))
        print(f"Prepoznata namera: {intent}")  # Debug logging
        
        if intent["function"] == "get_news":
            await get_news(update, context)
        elif intent["function"] == "search_news":
            context.args = [intent["parameters"].get("query", "")]
            await search_news(update, context)
        elif intent["function"] == "handle_follow_up":
            await handle_follow_up(update, context)
        else:
            await update.message.reply_text("Nisam siguran kako da odgovorim na to. Možete pokušati sa preciznijim upitom.")
    except Exception as e:
        print(f"Greška u handle_news_request: {str(e)}")
        await update.message.reply_text("Došlo je do greške pri obradi vašeg zahtjeva. Pokušajte ponovo.")

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
        await update.message.reply_text("Trenutno nema dostupnih vesti.")
        return

    summarized_news = []
    full_news = ""
    for article in unique_articles[:5]: 
        summary_text = article["description"] or article["title"]
        summary = await summarize_text(summary_text, is_cyr)
        summarized_news.append(f"🔹 {summary}\n{article['url']}")
        full_news += f"{article['title']}\n{article['description']}\n\n"

    context.user_data['last_news'] = full_news
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
        if query_lower in (a["title"] + a["description"]).lower()
    ][:10] 

    if not filtered_articles:
        await update.message.reply_text("Nema rezultata za ovu pretragu.")
        return

    summarized_news = []
    full_news = ""
    for article in filtered_articles[:5]:
        summary_text = article["description"] or article["title"]
        summary = await summarize_text(summary_text, is_cyr)
        summarized_news.append(f"🔹 {summary}\n{article['url']}")
        full_news += f"{article['title']}\n{article['description']}\n\n"

    context.user_data['last_news'] = full_news
    context.user_data['is_cyr'] = is_cyr

    await update.message.reply_text("\n\n".join(summarized_news))

async def handle_follow_up(update: Update, context: CallbackContext) -> None:
    # Provjera da li imamo prethodne vijesti
    if 'last_news' not in context.user_data:
        # Ako nema prethodnih vijesti, pokušaj pretragu
        intent = await determine_intent(convert_to_latin(update.message.text))
        if intent["function"] == "search_news":
            context.args = [intent["parameters"].get("query", "")]
            return await search_news(update, context)
        
        await update.message.reply_text("Prvo zatražite neke vijesti da bih mogao odgovoriti na pitanja.")
        return

    question = convert_to_latin(update.message.text)
    full_news = context.user_data['last_news']
    is_cyr = context.user_data['is_cyr']
    
    url = "https://api.together.xyz/v1/chat/completions"
    headers = {"Authorization": f"Bearer {LLAMA_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": "meta-llama/Llama-3-70b-chat-hf",
        "messages": [
            {"role": "system", "content": "Odgovori na pitanje na osnovu sljedećih vijesti."},
            {"role": "user", "content": f"Vesti: {full_news}\n\nPitanje: {question}"}
        ],
        "temperature": 0.7
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            response_json = await fetch_json(session, url, headers=headers, data=data, method="POST")
            
            if "choices" in response_json and response_json["choices"]:
                answer = response_json["choices"][0]["message"]["content"].strip()
                answer = transliterate.translit(answer, 'sr') if is_cyr else answer
                await update.message.reply_text(answer)
            else:
                await update.message.reply_text("Nisam uspio da pronađem odgovor. 😕")
        except Exception as e:
            print(f"Greška pri obradi pitanja: {str(e)}")
            await update.message.reply_text("Došlo je do greške pri obradi vašeg pitanja. Pokušajte ponovo.")

async def handle_news_request(update: Update, context: CallbackContext) -> None:
    try:
        user_input = update.message.text
        intent = await determine_intent(convert_to_latin(user_input))
        
        if intent["function"] == "get_news":
            await get_news(update, context)
        elif intent["function"] == "search_news":
            context.args = [intent["parameters"].get("query", "")]
            await search_news(update, context)
        elif intent["function"] == "handle_follow_up":
            await handle_follow_up(update, context)
        else:
            await update.message.reply_text("Nisam siguran kako da odgovorim na to. Možete pokušati sa preciznijim upitom.")
    except Exception as e:
        print(f"Greška u handle_news_request: {str(e)}")
        await update.message.reply_text("Došlo je do greške pri obradi vašeg zahtjeva. Pokušajte ponovo.")



def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_news_request))

    print("Bot je pokrenut...")
    app.run_polling()

if __name__ == "__main__":
    main()
