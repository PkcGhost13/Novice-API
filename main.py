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
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

app = FastAPI()

c = 0
data_to_return = []


@app.get("/")
async def root():
    return {"message": "API Connection was succesful"}


config_file = "config.json"
with open(config_file, "r") as f:
    config = json.load(f)


async def fetch_page(session, url):
    headers = {"User-Agent": random.choice(config["user_agents"])}
    try:
        async with session.get(url, headers=headers) as response:
            return await response.text()
    except Exception as e:
        print(f"Error fetching URL: {url}, Error: {str(e)}")
        return ""



async def parse_page(html, url, collection):
    soup = BeautifulSoup(html, config["parser"])
    headlines = soup.title.text
    for css in soup("link", {"rel": "stylesheet"}):
        css.extract()
    for js in soup("script"):
        js.extract()
    tags_to_remove = [
        "script",
        "noscript",
        "input",
        "link",
        "meta",
        "style",
        "a",
        "li",
        "title",
        "h4",
        "h3",
        "h2",
        "strong",
        "button",
        "img",
        "nav",
        "header",
        "footer",
        "figure",
    ]
    for tag in soup.find_all(tags_to_remove):
        tag.decompose()
    for tag in soup.find_all():
        if re.match(r"^[\W\s]+$", tag.text):
            tag.decompose()
    for tag in soup.find_all():
        if not tag.text.strip():
            tag.decompose()
    html_string = soup.prettify()
    story = soup.text
    if collection is not None:
        await collection.insert_one(
            {"headlines": headlines, "story": story, "url": url, "html": html_string}
        )
        return {"headlines": headlines, "story": story, "url": url, "html": html_string}
    else:
        print(f"Collection '{collection}' does not exist. Skipping insertion.")
        return None


@app.on_event("startup")
async def startup_db_client():
    app.mongodb = AsyncIOMotorClient(config["mongodb_uri"])[config["mongodb_database"]]


@app.on_event("shutdown")
async def shutdown_db_client():
    await app.mongodb.client.close()


class News(BaseModel):
    headlines: str
    story: str
    url: str
    html: str


class HTML(BaseModel):
    html: str


def page(url):
    headers = {"User-Agent": random.choice(config["user_agents"])}
    response = requests.get(url, headers=headers)
    return response


@app.get("/read")
def read(url: str):
    res = page(url)
    soup = BeautifulSoup(res.content, "html.parser")
    for css in soup("link", {"rel": "stylesheet"}):
        css.extract()
    for js in soup("script"):
        js.extract()
    title = soup.title.text
    tags_to_remove = [
        "script",
        "noscript",
        "input",
        "link",
        "meta",
        "style",
        "a",
        "li",
        "title",
        "h4",
        "h3",
        "h2",
        "strong",
        "button",
        "img",
        "nav",
        "header",
        "footer",
        "figure",
    ]
    for tag in soup.find_all(tags_to_remove):
        tag.decompose()
    for tag in soup.find_all():
        if re.match(r"^[\W\s]+$", tag.text):
            tag.decompose()
    for tag in soup.find_all():
        if not tag.text.strip():
            tag.decompose()
    final = HTML(html=soup.prettify())
    return final



@app.get("/news/{source}/{key}/{size}")
async def get_news(source: str, key: str, size: int):
    collection_name = key  # Set the collection name based on the keyword
    pg = 1
    db_uri = config["mongodb_uri"]
    db_name = config["mongodb_database"]
    
    start_time = time.time()  # Record the start time

    async with aiohttp.ClientSession() as session:
        link = config[source + '_url']
        pages = math.ceil(size / config["app_" + source])
        scraped_data = []  # Create a list to collect all scraped data
        while pg <= pages:
            tasks = []
            tasks.append(asyncio.ensure_future(scrape_data(session, source, size, key, pg, link, collection_name)))
            pg += 1
            scraped_data.extend(await asyncio.gather(*tasks))  # Extend the list with scraped data from this page

    end_time = time.time()  # Record the end time
    elapsed_time_ms = (end_time - start_time) * 1000  # Calculate elapsed time in milliseconds
    print(f"Scraping completed in {elapsed_time_ms:.2f} milliseconds.")  # Print the elapsed time with 2 decimal places

    return {"message": "Scraping completed.", "data": data_to_return}  # Return both a message and the scraped data


async def scrape_data(session, src, size, key, pg, link, collection_name):
    url = link.format(key=key, page=pg)
    global c
    async with session.get(url) as response:
        soup = BeautifulSoup(await response.text(), config["parser"])
        links = soup.find_all("a")
        count = 0

        # Use the AsyncIOMotorClient to get the MongoDB collection
        collection = app.mongodb[collection_name]

        tasks = []
        for link in links:
            if c >= size:
                break
            a = link.get("href")
            if a is None:
                continue
            if (
                (src != "toi" or (src == "toi" and "/articleshow/" in a))
                and (src != "ndtv" or (src == "ndtv" and count % 2 == 0))
                and c < config["app_" + src]
            ):
                print(a)
                # Check if an entry with the same URL already exists
                existing_entry = await collection.find_one({"url": a})
                if not existing_entry:
                    data = await parse_page(await fetch_page(session, a), a, collection)
                    data_to_return.append(data)  # Append the data to the list
                    c += 1
            count += 1
        await asyncio.gather(*tasks)
