import os
import json
import time
import re
import asyncio
import aiohttp
from typing import List, Dict
from pydantic import BaseModel
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
from groq import Groq
from langdetect import detect
import transliterate
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
GROQ_API = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama3-70b-8192"
CACHE_EXPIRY = 600  # 10 minutes
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

class Tool(BaseModel):
    name: str
    description: str
    parameters: dict

class ToolCall(BaseModel):
    name: str
    arguments: dict

NEWS_TOOLS = [
    {
        "name": "get_news",
        "description": "Fetches the latest news from various categories",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "search_news",
        "description": "Searches for news based on specific keywords or topics",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term for news, e.g., 'cryptocurrency' or 'Novak Djokovic'"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "general_qa",
        "description": "General questions and conversation about news and current topics",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "User input for processing"
                }
            },
            "required": ["question"]
        }
    }
]

def is_cyrillic(text: str) -> bool:
    return bool(re.search("[\u0400-\u04FF]", text))

def format_response(text: str, is_cyr: bool) -> str:
    return transliterate.translit(text, 'sr') if is_cyr else text

class NewsBot:
    def __init__(self):
        self.groq_client = Groq(api_key=GROQ_API)
        self.available_tools = NEWS_TOOLS

    async def select_tool(self, user_input: str, context: dict) -> ToolCall:
        """Selects the appropriate tool using LLM."""
        try:
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": f"""
                        Select the most appropriate tool to process the input.
                        Context: {context.get('last_news_topic', 'None')}
                        User input: {user_input}
                        """
                    }
                ],
                tools=[{"type": "function", "function": tool} for tool in self.available_tools],
                tool_choice="auto"
            )
            
            tool_call = response.choices[0].message.tool_calls[0]
            return ToolCall(
                name=tool_call.function.name,
                arguments=json.loads(tool_call.function.arguments))
        except Exception as e:
            print(f"Error selecting tool: {e}")
            return ToolCall(name="general_qa", arguments={"question": user_input})

    async def execute_tool(self, tool_call: ToolCall, context: dict) -> str:
        """Executes the selected tool."""
        if tool_call.name == "get_news":
            return await self._handle_get_news(context)
        elif tool_call.name == "search_news":
            return await self._handle_search_news(tool_call.arguments['query'], context)
        elif tool_call.name == "general_qa":
            return await self._handle_general_qa(tool_call.arguments['question'], context)
        else:
            return "I couldn't process your request."

    async def _handle_get_news(self, context: dict) -> str:
        """Handles general news requests."""
        articles = await self.fetch_news()
        if not articles:
            return "No news available at the moment."
        
        summaries = []
        full_text = ""
        for article in articles:
            summary = await self.summarize(
                f"{article.get('title', '')}\n{article.get('description', '')}",
                is_cyrillic(article.get('title', '')))
            summaries.append(f"📰 {summary}\n{article.get('url', '')}")
            full_text += f"{article.get('title')}\n{article.get('description')}\n\n"
        
        # Update context
        context.update({
            'last_news': full_text,
            'timestamp': time.time(),
            'last_news_topic': 'general'
        })
        
        return "\n\n".join(summaries)

    async def _handle_search_news(self, query: str, context: dict) -> str:
        """Handles news search requests."""
        articles = await self.fetch_news(query)
        if not articles:
            return f"No results found for: {query}"
        
        # Update context
        context.update({
            'last_news': "\n".join([f"{a['title']}: {a['description']}" for a in articles]),
            'timestamp': time.time(),
            'last_news_topic': query
        })
        
        # Format response
        summaries = [
            f"📌 {await self.summarize(a['title'], is_cyrillic(a['title']))}\n{a['url']}"
            for a in articles[:3]
        ]
        return f"🔍 Results for '{query}':\n\n" + "\n\n".join(summaries)

    async def _handle_general_qa(self, question: str, context: dict) -> str:
        """Handles general questions."""
        if context.get('last_news'):
            return await self.answer_follow_up(question, context)
        else:
            return await self.generate_general_response(question)

    async def answer_follow_up(self, question: str, context: dict) -> str:
        """Answers follow-up questions based on previous news context."""
        try:
            news_context = context.get('last_news', '')[:2000]
            user_lang = detect(question)
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": f"News context: {news_context}\nRespond in {user_lang}."},
                    {"role": "user", "content": question}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error answering follow-up: {e}")
            return "An error occurred while processing your question."

    async def generate_general_response(self, question: str) -> str:
        """Generates a general response for non-news-related questions."""
        try:
            user_lang = detect(question)
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": question}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error generating general response: {e}")
            return "I couldn't generate a response for your question."

    async def fetch_news(self, query: str = None) -> list:
        """Fetches news from multiple APIs."""
        async with aiohttp.ClientSession() as session:
            tasks = []
            for api in NEWS_APIS:
                url = api["url"](query)
                tasks.append(self._fetch_api(session, url, api["parser"]))
            
            results = await asyncio.gather(*tasks)
            return [item for sublist in results for item in sublist][:MAX_ARTICLES]

    async def _fetch_api(self, session, url, parser):
        """Fetches data from a single API."""
        try:
            async with session.get(url) as response:
                data = await response.json()
                return parser(data)
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            return []

    async def summarize(self, text: str, is_cyr: bool) -> str:
        """Summarizes text using Groq."""
        try:
            response = await asyncio.to_thread(
                self.groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "Summarize the following text in 2-3 sentences in Serbian."},
                    {"role": "user", "content": text[:3000]}
                ],
                temperature=0.5
            )
            return format_response(response.choices[0].message.content, is_cyr)
        except Exception as e:
            print(f"Error summarizing text: {e}")
            return format_response("I couldn't summarize this article.", is_cyr)

# Telegram handlers
news_bot = NewsBot()

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("📰 Dobrodošli na News Bot! Pošaljite poruku za vijesti ili aktuelne teme.")

async def reset(update: Update, context: CallbackContext) -> None:
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

async def handle_message(update: Update, context: CallbackContext):
    user_input = update.message.text
    is_cyr = is_cyrillic(user_input)
    
    tool_call = await news_bot.select_tool(user_input, context.user_data)
    
    response = await news_bot.execute_tool(tool_call, context.user_data)
    
    await update.message.reply_text(format_response(response, is_cyr))
    
    if tool_call.name in ['get_news', 'search_news']:
        context.user_data['last_news_topic'] = tool_call.arguments.get('query', 'general')

def main():
    try:
        app = Application.builder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("reset", reset))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("Bot started successfully!")
        app.run_polling()
    except Exception as e:
        print(f"Critical error: {str(e)}")
        print("Check the following:")
        print("- Is the .env file present?")
        print("- Are the API keys valid?")
        print("- Is the internet connection stable?")

if __name__ == "__main__":
    main()
