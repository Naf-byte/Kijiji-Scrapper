# Kijiji Scrapper

[![Streamlit](https://img.shields.io/badge/Streamlit-Deployed-brightgreen)](https://kijiji-scrapper.streamlit.app/)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-1.45+-orange)](https://playwright.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

A fast, reliable **Kijiji.ca web scraper** with a **Streamlit interface**, powered by [Playwright](https://playwright.dev/).  
It extracts fresh listings (cars, motorcycles, heavy equipment, etc.) and saves them into a CSV file.

**Live Demo:** [kijiji-scrapper.streamlit.app](https://kijiji-scrapper.streamlit.app/)

---

## Features
- Scrapes Kijiji categories including Cars & Trucks, Motorcycles, and Heavy Equipment.  
- Extracts structured details such as name, price, seller, location, phone, mileage, transmission, fuel, and more.  
- Configurable maximum number of pages to scrape.  
- Automatically saves results to a CSV file with incremental flushes.  
- Streamlit interface with:  
  - Progress tracking  
  - Live scraping logs  
  - KPI dashboard  
  - CSV download  
- Built-in resilience: randomized user agent, headless Chromium, retry logic, and resource blocking for faster scraping.  

---

## Screenshots

**Main Interface**  
_Add a screenshot here showing sidebar controls, progress, and preview table._

**Live Logs**  
_Add a screenshot here showing the log panel with scraping progress._

---

## Quickstart

### 1. Clone the repository

git clone https://github.com/your-username/kijiji-scrapper.git
cd kijiji-scrapper


### 2. Install dependencies

**Python 3.9+ is recommended.**

(Optional) create and activate a virtual environment:

- python -m venv .venv
- source .venv/bin/activate  # Linux/Mac
- .venv\Scripts\activate     # Windows

**install Python requirements.**
- pip install -r requirements.txt

**Install Playwright browsers:**
- playwright install chromium

**##For Linux environments, install required system libraries:**
- sudo apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libgtk-3-0 libpango-1.0-0 libasound2 libpangocairo-1.0-0 \
    libcairo2 libx11-6 libx11-xcb1 libxcb1 libxext6 libxss1 libcups2 \
    libdbus-1-3 libatspi2.0-0

### 3. Run locally
streamlit run app.py

## Deployment (Streamlit Cloud)

This repository is configured for Streamlit Community Cloud:

- **packages.txt** provides required Chromium system libraries.
- **requirements.txt** lists Python dependencies.
- **streamlit/config.toml** contains UI theme settings.

Connect this repository to Streamlit Cloud and deploy. Chromium will install automatically on first run.

---

### How It Works
- Launches a headless Chromium browser with Playwright.
- Navigates Kijiji search result pages, filtering for fresh listings.
- Visits each listing page to extract details such as title, price, seller info, and vehicle attributes.
- Saves results incrementally into a CSV file (flushes every 10 rows).
- Updates the Streamlit UI with logs, KPIs, and CSV previews.




