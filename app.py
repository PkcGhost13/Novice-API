import asyncio
import aiohttp
import csv
import time
import json
import random
import re
import math
import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
from motor.motor_asyncio import AsyncIOMotorClient

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

origins = [
    "*",
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:5500",
    "http://localhost:3000",
    "http://localhost:10798",
    "http://127.0.0.0:8000",
    "https://example.com",
    "https://www.example.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

config_file = "config.json"
try:
    with open(config_file, "r") as f:
        config = json.load(f)
except FileNotFoundError:
    raise SystemExit("Config file not found. Exiting...")

async def fetch_page(session, url):
    headers = {"User-Agent": random.choice(config["user_agents"])}
    try:
        async with session.get(url, headers=headers) as response:
            return await response.text()
    except aiohttp.ClientError as e:
        raise SystemExit(f"Error fetching page: {e}")

async def parse_page(html, url, collection):
    try:
        soup = BeautifulSoup(html, config["parser"])
        headlines = soup.title.text
        for css in soup('link', {'rel': 'stylesheet'}):
            css.extract()
        for js in soup('script'):
            js.extract()
        for tag in soup.find_all():
            if re.match(r'^[\W\s]+$', tag.text):
                tag.decompose()
        html_string = soup.prettify()
        story = soup.text
        if collection is not None:
            await collection.insert_one({"headlines": headlines, "story": story, "url": url, "html": html_string})
        else:
            print(f"Collection '{collection}' does not exist. Skipping insertion.")
    except Exception as e:
        raise SystemExit(f"Error parsing page: {e}")

@app.on_event("startup")
async def startup_db_client():
    try:
        app.mongodb = AsyncIOMotorClient(config["mongodb_uri"])[config["mongodb_database"]]
    except Exception as e:
        raise SystemExit(f"Error connecting to database: {e}")

@app.on_event("shutdown")
async def shutdown_db_client():
    try:
        await app.mongodb.client.close()
    except Exception as e:
        print(f"Error closing database connection: {e}")

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/reader")
async def reader(request: Request):
    return templates.TemplateResponse("reader.html", {"request": request})

class News(BaseModel):
    headlines: str
    story: str
    url: str
    html: str

class HTML(BaseModel):
    html: str

def page(url):
    headers = {"User-Agent": random.choice(config["user_agents"])}
    try:
        response = requests.get(url, headers=headers)
        return response
    except requests.RequestException as e:
        print(f"Error fetching page: {e}")
        return None

@app.get("/read")
def read(url: str):
    res = page(url)
    if res is not None:
        soup = BeautifulSoup(res.content, 'html.parser')
        for css in soup('link', {'rel': 'stylesheet'}):
            css.extract()
        for js in soup('script'):
            js.extract()
        for tag in soup.find_all():
            if re.match(r'^[\W\s]+$', tag.text):
                tag.decompose()
        final = HTML(html=soup.prettify())
        return final
    else:
        return {"error": "Failed to fetch page"}

@app.get("/news/{source}/{key}/{size}")
async def get_news(source: str, key: str, size: int):
    collection_name = key
    pg = 1
    
    try:
        app.mongodb = AsyncIOMotorClient(config["mongodb_uri"])[config["mongodb_database"]]
    except Exception as e:
        raise SystemExit(f"Error connecting to database: {e}")

    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        link = config[source + '_url']
        pages = math.ceil(size / config["app_" + source])
        while pg <= pages:
            tasks = []
            tasks.append(asyncio.ensure_future(scrape_data(session, source, size, key, pg, link, collection_name)))
            pg += 1
            await asyncio.gather(*tasks)

    end_time = time.time()
    elapsed_time_ms = (end_time - start_time) * 1000
    print(f"Scraping completed in {elapsed_time_ms:.2f} milliseconds.")

    return {"message": "Scraping completed."}

async def scrape_data(session, src, size, key, pg, link, collection_name):
    url = link.format(key=key, page=pg)
    global c
    try:
        async with session.get(url) as response:
            soup = BeautifulSoup(await response.text(), config["parser"])
            links = soup.find_all("a")
            count = 0

            collection = app.mongodb[collection_name]

            tasks = []
            for link in links:
                if c >= size:
                    break
                a = link.get("href")
                if (
                    (src != "toi" or (src == "toi" and "articleshow" in a))
                    and (src != "ndtv" or (src == "ndtv" and count % 2 == 0))
                    and c < config["app_" + src]
                ):
                    existing_entry = await collection.find_one({"url": a})
                    if not existing_entry:
                        tasks.append(asyncio.ensure_future(parse_page(await fetch_page(session, a), a, collection)))
                        c += 1
                count += 1
            await asyncio.gather(*tasks)
    except aiohttp.ClientError as e:
        print(f"Error fetching page: {e}")
    except Exception as e:
        print(f"Error scraping data: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, port=8001)
