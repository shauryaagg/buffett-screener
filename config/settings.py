import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- API Keys & Identity ---
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
EDGAR_IDENTITY = os.getenv("EDGAR_IDENTITY", "Shaurya shauryaagg2000@gmail.com")

# --- SIC Code Exclusions (commodity/non-product businesses) ---
SIC_EXCLUSIONS = set()
SIC_EXCLUSIONS.update(range(100, 1000))      # Agriculture, forestry, fishing
SIC_EXCLUSIONS.update(range(1000, 1500))      # Mining (gold, coal, metals, oil/gas extraction)
SIC_EXCLUSIONS.update(range(1300, 1390))      # Oil & gas extraction (overlap with mining, explicit)
SIC_EXCLUSIONS.add(2911)                       # Petroleum refining
SIC_EXCLUSIONS.update(range(3312, 3318))       # Commodity metals (steel works, blast furnaces)

# --- SIC Code Inclusions (explicitly kept as product businesses) ---
SIC_INCLUSIONS = set()
SIC_INCLUSIONS.update(range(6000, 6200))      # Banks
SIC_INCLUSIONS.update(range(6300, 6400))      # Insurance
SIC_INCLUSIONS.update(range(2000, 4000))      # Manufacturing
SIC_INCLUSIONS.update(range(5000, 6000))      # Retail/wholesale
SIC_INCLUSIONS.update(range(7000, 9000))      # Services, tech, software

# Remove any overlapping exclusions from inclusions
SIC_INCLUSIONS -= SIC_EXCLUSIONS

# --- Market Cap Range ---
MARKET_CAP_MIN = 5_000_000
MARKET_CAP_MAX = 5_000_000_000

# --- Filter Thresholds ---
F2_MIN_SCORE = 65
F3_MARGIN_OF_SAFETY = 0.50
F4_MIN_SCORE = 60

# --- Rate Limiting ---
RATE_LIMIT_PAUSE_HOURS = 2

# --- Storage ---
DB_PATH = "buffett_screener.db"
CACHE_DIR = ".cache"

# --- External References ---
STOCK_SCREENER_PATH = "/Users/shauryaagg/Documents/GitHub/stock-screener/backend"
