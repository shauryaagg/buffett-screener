# Buffett Screener: Multi-Layer Investment Analysis Pipeline

## Context

Build an automated investment analysis pipeline inspired by Warren Buffett's analytical framework. The system uses `edgartools` to pull SEC 10-K filings and the Claude Agent SDK (running on Claude Max x20 subscription) to perform multi-agent qualitative analysis. The pipeline filters the universe of small-to-mid-cap product-based companies ($5M-$5B market cap, including banks and insurance companies but excluding commodity businesses) through 4 increasingly stringent layers, surfacing only companies with transparent management, durable moats, deep value pricing, and intelligent capital allocation. Also supports single-ticker deep analysis.

**Existing infrastructure to reuse:**
- `stock-screener/backend/edgar_service.py` — XBRL extraction, financial metrics, SQLite schema, 10-year data
- `stock-screener/backend/statements.py` — XBRL concept mappings
- `edgartools` library already installed locally

**Key constraint:** Claude Max x20 subscription — no per-call API cost, but token rate limits apply. Pipeline must pause/resume when limits are hit.

---

## Project Structure

```
buffett-screener/
├── config/
│   ├── __init__.py
│   ├── settings.py              # FMP key, SIC exclusions, thresholds, rate limit config
│   └── prompts.py               # All agent system prompts (Buffett-philosophy rubrics)
├── core/
│   ├── __init__.py
│   ├── pipeline.py              # Main orchestrator — sequential filter execution w/ pause/resume
│   ├── models.py                # Pydantic models (CompanyAnalysis, FilterResult, etc.)
│   └── database.py              # Extended SQLite schema + state tracking
├── data/
│   ├── __init__.py
│   ├── edgar_client.py          # 10-K text extraction by section, caching to DB
│   ├── financial_data.py        # Wraps stock-screener's EdgarService + adds owner earnings, ROIC, capital allocation XBRL
│   └── market_data.py           # FMP bulk price: NYSE + NASDAQ + AMEX + OTC, filter $5M-$5B
├── filters/
│   ├── __init__.py
│   ├── filter_base.py           # Abstract base: run(), pass/fail, result storage
│   ├── f1_business_type.py      # SIC code filter + market cap filter via FMP
│   ├── f2_management_quality.py # Agent SDK multi-agent 10-K qualitative analysis
│   ├── f3_valuation.py          # Earning power value + moat assessment + margin of safety
│   └── f4_capital_allocation.py # 10-year capital allocation intelligence assessment
├── agents/                      # AgentDefinition configs for Claude Agent SDK
│   ├── __init__.py
│   └── definitions.py           # All AgentDefinition objects with prompts and tool configs
├── cli.py                       # Typer CLI: run, analyze <ticker>, status, resume
├── run_pipeline.py              # Entry point
└── requirements.txt
```

---

## Implementation Plan

### Phase 1: Foundation (files: config/, core/, data/, requirements.txt, run_pipeline.py)

**Step 1: Project scaffold + dependencies**
- Create project directory and all `__init__.py` files
- `requirements.txt`: edgartools, claude-agent-sdk, typer, pydantic, requests, pandas
- `config/settings.py`:
  - FMP_API_KEY (from env)
  - EDGAR_IDENTITY (from env, already set: `shauryaagg2000@gmail.com`)
  - SIC exclusion ranges: 1000-1499 (mining), 1300-1389 (oil/gas), 100-999 (agriculture/commodities)
  - SIC inclusions: banks (6000-6199) and insurance (6300-6399) ARE included — these are product businesses
  - Market cap range: 5_000_000 to 5_000_000_000
  - Filter thresholds: f2_min_score=65, f3_margin_of_safety=0.50, f4_min_score=60
  - RATE_LIMIT_PAUSE_HOURS=2 (how long to pause when token limit hit)
  - DB_PATH, CACHE_DIR

**Step 2: Database schema** (`core/database.py`)
- Reuse existing `companies` + `stocks` tables from stock-screener by copying the DB or importing EdgarService
- Add new tables:

```sql
-- Pipeline state for pause/resume
CREATE TABLE pipeline_state (
    run_id TEXT PRIMARY KEY,
    current_filter INTEGER,     -- 1-4
    current_ticker_idx INTEGER, -- index into ticker list
    started_at TEXT,
    paused_at TEXT,
    completed_at TEXT,
    status TEXT                  -- running, paused, completed
);

-- Analysis results per company
CREATE TABLE analysis_results (
    ticker TEXT,
    run_id TEXT,
    -- Filter 1
    f1_passed BOOLEAN, f1_reason TEXT,
    -- Filter 2
    f2_passed BOOLEAN, f2_score REAL,
    f2_business_clarity REAL, f2_risk_honesty REAL,
    f2_mda_transparency REAL, f2_kpi_quality REAL, f2_tone REAL,
    f2_reasoning TEXT,
    -- Filter 3
    f3_passed BOOLEAN, f3_normalized_earnings REAL,
    f3_moat_type TEXT, f3_moat_strength REAL,
    f3_earning_power_multiple REAL, f3_intrinsic_value REAL,
    f3_current_price REAL, f3_margin_of_safety REAL,
    f3_reasoning TEXT,
    -- Filter 4
    f4_passed BOOLEAN, f4_score REAL,
    f4_buyback_quality REAL, f4_capital_return REAL,
    f4_acquisition_quality REAL, f4_debt_management REAL,
    f4_reasoning TEXT,
    -- Meta
    final_passed BOOLEAN,
    analyzed_at TEXT,
    UNIQUE(ticker, run_id)
);

-- Cache 10-K section text
CREATE TABLE tenk_cache (
    ticker TEXT, filing_date TEXT, accession TEXT,
    section TEXT,       -- 'item_1', 'item_1a', 'item_7'
    text_content TEXT,
    token_estimate INTEGER,
    UNIQUE(ticker, accession, section)
);
```

**Step 3: Pydantic models** (`core/models.py`)
- `CompanyInfo`: ticker, name, sic, industry, market_cap, price
- `FilterResult`: passed, score, reasoning, details_dict
- `ManagementQualityScore`: business_clarity, risk_honesty, mda_transparency, kpi_quality, tone_authenticity (each 0-10)
- `ValuationResult`: normalized_earnings, moat_type, moat_strength, multiple, intrinsic_value, margin_of_safety
- `CapitalAllocationScore`: buyback_quality, capital_return, acquisition_quality, debt_management, reinvestment_quality (each 0-10)
- `FullAnalysis`: combines all filter results

**Step 4: FMP market data** (`data/market_data.py`)
- `fetch_all_prices()` — **4 bulk API calls total, one per exchange:**
  - `GET /api/v3/quotes/nyse?apikey={key}` — returns ALL NYSE stocks in 1 call (price, mktCap, name, etc.)
  - `GET /api/v3/quotes/nasdaq?apikey={key}` — returns ALL NASDAQ stocks in 1 call
  - `GET /api/v3/quotes/amex?apikey={key}` — returns ALL AMEX stocks in 1 call
  - `GET /api/v3/batch-request-end-of-day-prices?date=YYYY-MM-DD&apikey={key}` — returns ALL end-of-day prices including OTC in 1 call. Use this for OTC stocks not covered by the exchange-specific endpoints. Alternatively, `GET /api/v3/quotes/otc?apikey={key}` if FMP supports it — test at implementation time.
  - Each call returns a JSON array with: `symbol`, `price` (last close), `open`, `mktCap`, `name`, `exchange`, `volume`
  - **OTC is critical** — many $5M-$5B companies trade OTC (OTCQX, OTCQB, Pink Sheets) and file 10-Ks with the SEC. These are prime Buffett-style hunting grounds.
  - Combine all results into one dict, deduplicate by ticker, filter to $5M-$5B market cap
  - Return: `{ticker: {price, market_cap, name, exchange}}`
  - Cache result for the day (one pipeline run uses stale-by-a-day prices at most — fine for value investing, we're not day trading)
  - **Total API calls: 4.** Well within FMP free tier (250/day).

**Step 5: Edgar text extraction** (`data/edgar_client.py`)
- `get_tenk_sections(ticker, filing_date=None)`:
  - `company = Company(ticker)`
  - `filing = company.latest("10-K")` or specific date
  - `tenk = filing.obj()` — returns TenK object
  - Extract: `tenk['Item 1']`, `tenk['Item 1A']`, `tenk['Item 7']`
  - Cache each section to `tenk_cache` table
  - Return dict of section text
- `get_historical_mda(ticker, years=10)`:
  - Get last N 10-K filings
  - Extract Item 7 from each
  - Return list of (year, mda_text) tuples
- Key file to reference: stock-screener's edgartools at `edgar/company_reports.py` (TenK class with `__getitem__`)

**Step 6: Financial data** (`data/financial_data.py`)
- Import `EdgarService` from stock-screener (add to sys.path)
- Extend with additional XBRL concepts for capital allocation:
  - `PaymentsForRepurchaseOfCommonStock`, `PaymentsOfDividends`
  - `PaymentsToAcquireBusinessesNetOfCashAcquired`
  - `DepreciationDepletionAndAmortization`
  - `CommonStockSharesOutstanding`
- Add calculations:
  - **Owner earnings** = net_income + depreciation - maintenance_capex
  - **ROIC** = NOPAT / invested_capital
  - **Normalized earnings** = 5yr avg of owner earnings (drop best + worst)
  - **Capital intensity** = capex / revenue

---

### Phase 2: Filter 1 — Business Type + Market Cap (file: filters/f1_business_type.py)

**Goal: Keep product-based businesses (including banks and insurance). Remove commodity/non-product businesses.**

Product-based = companies with a clear product, brand name, or identifiable service sold to consumers or businesses. This INCLUDES:
- Banks (like Hingham Institution for Savings — a "product" is their lending/deposit franchise)
- Insurance companies (like Berkshire's insurance ops — the "product" is underwriting)
- Software, tech, consumer brands, retailers, manufacturers of branded goods, financial services
- Any company where the customer chooses them for reasons beyond just the lowest price

EXCLUDE:
- Pure commodity businesses: oil & gas E&P, mining, basic metals, agricultural commodities
- Companies where the product is undifferentiated and price is the only competitive factor
- Holding companies with no identifiable operating business
- Shell companies, blank check companies, SPACs

**Implementation:**
1. Load FMP bulk prices from all exchanges (NYSE, NASDAQ, AMEX, OTC) → filter to $5M-$5B market cap
2. Cross-reference with SEC company data (edgartools) to get SIC codes
3. SIC-based exclusions (commodity/non-product):
   - SIC 1000-1499 (Mining — gold, coal, metals, oil/gas extraction)
   - SIC 1300-1389 (Oil & gas extraction specifically)
   - SIC 100-999 (Agriculture, forestry, fishing — commodity producers)
   - SIC 2911 (Petroleum refining)
   - SIC 3312-3317 (Steel works, blast furnaces — commodity metals)
4. SIC-based INCLUSIONS (explicitly keep):
   - SIC 6000-6199 (Banks — these ARE product businesses with lending/deposit franchises)
   - SIC 6300-6399 (Insurance — underwriting is a product)
   - SIC 2000-3999 (Manufacturing — but see exclusions above for commodity metals)
   - SIC 5000-5999 (Retail/wholesale)
   - SIC 7000-8999 (Services, tech, software)
5. For ambiguous SIC codes, use a Haiku agent call: "Does this company have a clear product or branded service, or is it a pure commodity business?"
6. Verify each company has at least one 10-K filing available
7. Store results in `analysis_results` table

**Expected pass rate:** ~50-65% of universe. OTC adds significant breadth — many overlooked small companies live here.

---

### Phase 3: Filter 2 — Management Quality (files: filters/f2_management_quality.py, agents/definitions.py, config/prompts.py)

This is the core intellectual engine. Uses Claude Agent SDK with subagents.

**Agent architecture — 3 specialist analysts + 1 synthesizer:**

```python
# In agents/definitions.py

business_analyst = AgentDefinition(
    description="Analyzes Item 1 (Business Description) of 10-K for clarity and moat articulation",
    prompt=BUSINESS_ANALYST_PROMPT,  # from prompts.py
    tools=["Read", "Grep"],
    model="sonnet"
)

risk_analyst = AgentDefinition(
    description="Analyzes Item 1A (Risk Factors) for intellectual honesty vs boilerplate",
    prompt=RISK_ANALYST_PROMPT,
    tools=["Read", "Grep"],
    model="sonnet"
)

mda_analyst = AgentDefinition(
    description="Deep analysis of Item 7 (MD&A) for management transparency and KPI quality",
    prompt=MDA_ANALYST_PROMPT,
    tools=["Read", "Grep"],
    model="opus"  # Hardest qualitative judgment — needs Opus
)
```

**Prompt philosophy (in config/prompts.py):**

Every prompt in this system must embody the thinking of a serious value investor — not a checklist of financial ratios, but a genuine attempt to understand the business as an owner would. The prompts will be long and detailed because the quality of analysis depends entirely on the quality of instruction.

**Core framing for ALL agent prompts:**

> You are evaluating this company as if you were a passive 50% owner of this business. Your partner — the active 50% owner (i.e., management) — has written you this annual report. You need to determine: Is my partner being straight with me? Do they understand the business? Are they making intelligent decisions with our money? Would I be comfortable going on a 10-year vacation and leaving them in charge?

**Filter 2 — Management Quality Prompts (the most critical):**

**BUSINESS_ANALYST_PROMPT** (for Item 1 — Business Description):
> Read this business description as a prospective owner, not an analyst. After reading it, you should be able to explain to a 12-year-old exactly how this company makes money, who its customers are, why those customers choose this company over alternatives, and what could go wrong.
>
> Evaluate:
> - **Clarity of business model**: Can you draw a simple diagram of how money flows into and out of this business? Or is the description so full of jargon and buzzwords that you'd have no idea what the company actually does? Jeff Bezos's first Amazon letter described the business so simply and clearly that anyone could understand it. That's the standard.
> - **Moat articulation**: Does management understand and articulate their competitive advantage? Not in vague terms ("our talented team" or "our innovative culture") but in specific, structural terms — network effects, switching costs, regulatory barriers, scale advantages, brand loyalty with specific evidence? Buffett says the key question is: "If you had a billion dollars and the best manager in the world, could you create a company that would take significant market share from this one?" Does the business description help you answer that question?
> - **Honest self-assessment**: Does management acknowledge where they are weak? Do they discuss competitive threats honestly? Or does the description read like a sales pitch?
>
> A great business description makes you feel like the CEO sat down across from you and explained their business like a partner. A poor one makes you feel like you're reading a press release.

**RISK_ANALYST_PROMPT** (for Item 1A — Risk Factors):
> Most risk factor sections are useless — they're written by lawyers to protect the company from lawsuits, not to inform owners about real risks. Your job is to determine whether this risk section is boilerplate CYA or whether management is genuinely trying to help you understand what could go wrong.
>
> Signs of GENUINE risk disclosure:
> - Risks are specific to THIS company, not generic ("our industry is competitive" — useless)
> - Management quantifies the impact where possible ("a 1% increase in interest rates would reduce our earnings by $X")
> - Risks are discussed in order of actual materiality, not alphabetically or by legal category
> - Management discusses risks they've actually experienced and how they dealt with them
> - New risks appear year-over-year that reflect real changes in the business
>
> Signs of BOILERPLATE risk disclosure:
> - Identical to last year's filing with minor date changes
> - Every risk starts with "We may..." followed by something that applies to any company
> - No quantification of any risk's potential impact
> - No discussion of what management is doing about each risk
> - Risks feel like they were written by outside counsel, not by someone who runs the business
>
> An honest risk section is worth its weight in gold. It tells you management understands the threats to their business and is thinking about them. A boilerplate one tells you either management doesn't understand their risks, or they don't respect you enough to share them.

**MDA_ANALYST_PROMPT** (for Item 7 — MD&A — this is the most important section):
> The Management Discussion & Analysis is where you learn whether management thinks like owners or like hired employees who just want to keep their jobs. This is the most important section of any 10-K for a value investor.
>
> You are the passive 50% owner of this business. The person who wrote this report is your active partner who runs the business day-to-day. Evaluate whether this reads like a candid report from a trusted partner, or like a marketing document from a hired investor relations department.
>
> **What great MD&A looks like:**
> - Bezos's 1997 Amazon letter: focused entirely on long-term value creation, explained exactly why short-term losses were rational, quantified the opportunity, and acknowledged what could go wrong
> - Buffett's Berkshire letters: discusses mistakes openly ("I made a big mistake buying Dexter Shoe"), explains the economics of each business simply, uses per-share intrinsic value as the measuring stick
> - Hingham Institution for Savings: a small bank that writes reports with extraordinary clarity about their lending decisions, capital allocation, and competitive position
>
> **Evaluate specifically:**
>
> 1. **KPI Quality**: What metrics does management highlight? This reveals what they actually optimize for.
>    - TERRIBLE KPIs: EBITDA or adjusted EBITDA in a capital-heavy business (hides the reality that the business needs massive reinvestment just to maintain itself), "adjusted" anything that always adjusts upward, non-GAAP metrics that strip out stock-based compensation (that's real dilution), revenue growth without discussing profitability
>    - GOOD KPIs: Owner earnings or free cash flow, return on invested capital, revenue per customer/user, unit economics, same-store sales for retailers, book value per share for financial companies, combined ratio for insurers, net interest margin for banks. Metrics that reflect the actual economics of ownership.
>
> 2. **Transparency about problems**: Does management discuss what went wrong this year? Every business has problems. If the MD&A only discusses wins, management is either delusional or dishonest. Buffett: "Managers that always promise to 'make the numbers' will at some point be tempted to make up the numbers."
>
> 3. **Explanation of the WHY**: When revenue went up 15%, does management explain WHY? Was it pricing power, volume, acquisition, or a one-time event? When margins compressed, do they explain the structural cause? Or do they just narrate the numbers you can already read in the financial statements?
>
> 4. **Capital allocation discussion**: Does management discuss how they're deploying your capital? Buybacks, dividends, acquisitions, organic reinvestment — and the reasoning behind each decision? A great report explains why buying back stock at the current price is or isn't a good use of capital. A poor report just says "we returned $X to shareholders" as if that's inherently good.
>
> 5. **Forward-looking honesty**: Does management set realistic expectations? Or do they promise the moon? Buffett says he'd rather have a manager who under-promises and over-delivers. Watch for constant upward revision of targets that keeps actual results always just out of reach.
>
> Be STRINGENT. Most companies fail this test. A score of 7+/10 on any dimension should mean this is genuinely exceptional — top 10-15% of all public companies. Think: "Would I give this person $10 million of my own money to manage based on how they communicate?"

**Early exit optimization:** If Item 1 analysis scores < 3 on business clarity, skip the more expensive MD&A Opus call.

**Implementation flow:**
1. Write 10-K sections to temp files in working directory
2. Call `query()` with main orchestrator prompt that delegates to subagents
3. Subagents read the section files, produce structured JSON scores
4. Main agent synthesizes into final ManagementQualityScore
5. Use `output_format` with JSON schema for structured output
6. Threshold: weighted score >= 65/100 to pass (MD&A 50%, Business 25%, Risk 25%)

**Token limit handling:**
- Wrap `query()` calls in try/except for rate limit errors
- On rate limit: save pipeline state to `pipeline_state` table, sleep for configured hours, resume

---

### Phase 4: Filter 3 — Valuation (file: filters/f3_valuation.py)

**Philosophy: No formula fits all businesses. Buffett doesn't plug numbers into a DCF spreadsheet. He understands the business first, then asks "what is this business worth to a private owner?" The valuation approach must adapt to the nature of each business.**

**Step 1: Prepare financial context (pure Python)**
- Pull 10-year financials from DB
- Compute key data points (not formulas — raw facts for the agent to reason about):
  - Revenue trajectory, margin trajectory, return on equity trajectory
  - Owner earnings each year (net income + D&A - maintenance capex)
  - Cash generation vs reported earnings (quality of earnings)
  - Debt levels relative to earning power
  - Capital intensity (how much capex is needed to sustain/grow)
  - For banks: net interest margin, loan loss provisions, book value growth, ROA
  - For insurance: combined ratio, float growth, investment returns on float

**Step 2: Valuation Agent (Claude Agent SDK, Opus)**
This is a single Opus agent that receives:
- The full financial summary (10 years of key data points)
- The business description from Item 1 of the 10-K
- The MD&A highlights from Filter 2

The agent's system prompt embodies Buffett's valuation thinking. The agent must:

1. **Understand the business economics first.** A bank is valued differently from a software company which is valued differently from a retailer. The agent determines what kind of business this is and what the right valuation framework is:
   - A bank with consistent ROE above cost of equity? Look at price-to-book relative to ROE. A bank earning 15% ROE is worth well above book.
   - A capital-light software business with recurring revenue? Look at earning power — what could margins be if growth investment stopped?
   - A retailer with same-store sales data? Look at unit economics and rollout potential.
   - A business reinvesting everything (like early Amazon)? Don't look at current earnings — estimate what steady-state margins would be once reinvestment normalizes.
   - A mature business with no growth? Look at owner earnings yield — what does the business throw off to an owner?

2. **Determine the business's true earning power.** Not GAAP net income — what the business could earn for an owner under normal conditions. For a business suppressing margins to grow, estimate normalized margins. For a business inflating earnings with accounting tricks, adjust downward.

3. **Assess the moat.** Not a checklist — a genuine understanding of WHY this business earns above-average returns and whether that will persist. Network effects, switching costs, brand loyalty, regulatory advantages, scale economics, cost advantages. Be specific about THIS company's moat, not generic categories.

4. **Determine what a reasonable private buyer would pay** to earn a 10% compounded return. This is the intrinsic value. The agent reasons through this — it's not a formula. A commodity business with no moat might only be worth 9-10x normal earnings. A business with a durable moat and reinvestment runway might be worth 25x+ because the earnings will compound.

5. **Compare to current market price.** Pass only if the stock is selling for less than 50% of intrinsic value — a genuine margin of safety that accounts for the possibility of being wrong.

**VALUATION_AGENT_PROMPT** (full prompt for the Opus valuation agent):
> You are valuing this business as a prospective private buyer. You are not a Wall Street analyst building a DCF model with 47 assumptions. You are a business person asking: "What is this business worth to me as an owner, and what price would I need to pay to earn a 10% compounded annual return over the next decade?"
>
> There is no single formula that works for all businesses. A bank is not valued the same way as a software company. A retailer is not valued the same way as an insurer. Your job is to understand what kind of business this is and apply the right framework.
>
> **For a bank:** The key question is return on equity relative to cost of equity. A bank consistently earning 12-15% ROE on a growing book of equity is worth well above book value. Hingham Institution for Savings earns ~15% ROE and is worth 2-3x book. A bank earning 6% ROE is worth less than book. Look at: net interest margin stability, loan loss history, deposit franchise quality, efficiency ratio. Price-to-book relative to sustainable ROE is the right framework.
>
> **For an insurer:** Look at underwriting profit (combined ratio below 100%), the value of the float (free money to invest if underwriting is profitable), and investment returns. An insurer with a consistent combined ratio of 95% and $1B in float is enormously valuable because that float costs LESS than nothing. Berkshire's insurance model.
>
> **For a capital-light business with recurring revenue (software, subscriptions):** What matters is earning power — if this company stopped all growth investments tomorrow, what would it earn? Many high-growth software companies are "unprofitable" only because they're spending heavily on sales and R&D. Estimate steady-state margins. A business with 80% gross margins, strong retention, and genuine switching costs might have steady-state net margins of 25-35%. Apply that to current revenue to estimate earning power.
>
> **For a retailer:** Unit economics and rollout potential. What does a mature store earn? How many more stores can they open? What's the return on a new store investment? Same-store sales growth is the heartbeat — if it's positive and above inflation, the business is healthy.
>
> **For a manufacturer with a brand:** Pricing power is everything. Can they raise prices faster than input costs? Look at gross margin trajectory. A branded manufacturer with expanding gross margins has pricing power and is worth more than one with compressing margins.
>
> **For a business reinvesting everything to grow (like early Amazon):** Don't look at current earnings — they're intentionally suppressed. Estimate what margins would be at maturity. Amazon had 1% net margins when it could have had 5-10% — it chose growth over profits. Estimate the business at scale with normalized margins, then discount back.
>
> **General principles:**
> - A commodity business with no moat: an owner should earn at least 10% on their purchase price. That means paying no more than ~10x normal earnings. But beware: many commodity businesses earn above-normal returns at the peak of the cycle. Use mid-cycle or trough earnings.
> - The more durable the competitive advantage and the more room for reinvestment at high returns, the more the business is worth. This isn't a formula — it's judgment about the durability of economics.
> - Always think in terms of: "What would a knowledgeable private buyer pay for the entire business?" Not what the stock market says today, but what the business is actually worth as a going concern.
>
> Return your intrinsic value estimate as a SINGLE NUMBER (your best estimate), along with detailed reasoning that explains exactly why this specific business, given its specific economics, is worth that amount. Then compare to the current market price.
>
> **The stock must be selling for less than 50% of your intrinsic value estimate to pass.** This is a massive margin of safety. It means you can be quite wrong and still do well. If a business is worth $100/share and sells for $45, you have a margin of safety. If it sells for $55, it doesn't pass — even though it's cheap. Be conservative in your estimate and demanding in your margin of safety.

The agent returns: intrinsic_value_estimate, current_price, margin_of_safety, reasoning (detailed explanation of valuation logic specific to this business), moat_assessment, earning_power_estimate.

**No hardcoded multiples.** The agent reasons about the right price for each business individually.

---

### Phase 5: Filter 4 — Capital Allocation (file: filters/f4_capital_allocation.py)

**Two-stage: quantitative screening + qualitative 10-year trend analysis**

**Stage 1: Quantitative (pure Python)**
For each year over 10 years, calculate:
- Buyback yield (buyback $ / market cap)
- Were buybacks done below intrinsic value? (if we can estimate per-year)
- Dividend payout ratio
- Acquisition spending as % of FCF
- Net debt change
- Share count trend (dilution vs. shrinkage)
- ROIC vs cost of capital — is reinvestment creating value?

Flag: cash piling up with no deployment (Japanese net-net trap indicator)

**Stage 2: Qualitative — MD&A Trend Analysis (Claude Agent SDK)**
- For each of 10 years: use a Sonnet agent to extract a ~500-word summary of capital allocation commentary from Item 7
- Then: send all 10 summaries + quantitative trends to an Opus synthesis agent

**CAPITAL_ALLOCATION_PROMPT** (for the Opus synthesis agent):
> You are evaluating 10 years of capital allocation decisions by this management team. Capital allocation is the single most important job of a CEO — deciding what to do with every dollar the business generates. A business that earns 15% on capital but whose management then redeploys that capital at 5% returns will destroy value over time despite operating well.
>
> You have 10 years of MD&A commentary and financial data. Look for patterns across the full decade:
>
> **What intelligent capital allocation looks like:**
> - Buying back shares aggressively when the stock is cheap relative to intrinsic value (not at all-time highs to offset dilution from stock options — that's not a buyback, that's a transfer from shareholders to management)
> - Paying dividends when the company cannot reinvest at returns above its cost of capital
> - Making acquisitions that are clearly accretive to per-share value — not just growing the empire. For each major acquisition, can you see it was done at a reasonable price and generated good returns?
> - Taking on debt only when the cost of debt is well below the return on capital deployed
> - Reinvesting in the business when returns on incremental capital are high
>
> **What poor capital allocation looks like (value traps):**
> - The Japanese net-net problem: company trades below net cash, but management keeps piling cash on the balance sheet year after year. They won't buy back stock, they won't pay dividends, they won't make acquisitions. The cash just sits there earning nothing while the operating business earns poor returns. This is a value trap — cheap on paper, but the value never gets unlocked.
> - Serial acquirers who overpay: management does deal after deal, always at "strategic" premiums, often using stock. Revenue grows but per-share economics stagnate or decline. Goodwill on the balance sheet keeps growing.
> - Buybacks at the top: company buys back billions in stock when P/E is 30, then stops buying when P/E is 10 during a downturn. This is value destruction — transferring wealth from continuing shareholders to departing ones.
> - Empire building: management grows the company beyond the point where incremental returns justify it. Revenue doubles but ROIC halves.
> - Excessive stock-based compensation that offsets buybacks: "look at our $1B buyback program" while simultaneously issuing $800M in stock options. Net effect: shareholders are getting diluted, not enriched.
>
> **Track the share count over 10 years.** This is the simplest and most honest test. If the share count is up over 10 years, management has been diluting you regardless of what their buyback press releases say.
>
> Evaluate:
> - **Capital return adequacy (0-10):** When cash is building up and ROIC is declining, is management returning capital? Or hoarding?
> - **Buyback timing intelligence (0-10):** Were buybacks concentrated when the stock was cheap? Or at peaks?
> - **Acquisition track record (0-10):** Have acquisitions created per-share value? Or destroyed it?
> - **Debt management (0-10):** Is leverage prudent relative to earnings stability? Or reckless?
> - **Reinvestment quality (0-10):** Is incremental capital being deployed at returns above cost of capital?

**Threshold:** weighted score >= 60/100 to pass

---

### Phase 6: Pipeline Orchestrator + CLI (files: core/pipeline.py, cli.py)

**Pipeline orchestrator (`core/pipeline.py`):**
```python
class Pipeline:
    async def run(self, run_id, tickers=None):
        # Load or create pipeline state
        # For each filter (1-4):
        #   For each ticker that passed previous filter:
        #     Run filter, store result
        #     On token limit error: save state, sleep, resume
        #     Update pipeline_state after each ticker
        # Generate final report

    async def run_single(self, ticker):
        # Run all 4 filters on one ticker with verbose output
        # Return FullAnalysis

    async def resume(self, run_id):
        # Load pipeline_state, continue from where we left off
```

**Rate limit pause/resume logic:**
```python
try:
    result = await run_agent_analysis(...)
except (ProcessError, ClaudeSDKError) as e:
    if "rate_limit" in str(e).lower() or "token" in str(e).lower():
        save_pipeline_state(run_id, current_filter, current_idx)
        print(f"Token limit reached. Pausing for {PAUSE_HOURS}h. Run `resume {run_id}` to continue.")
        await asyncio.sleep(PAUSE_HOURS * 3600)
        # Auto-resume
```

**CLI (`cli.py` using Typer):**
```
python run_pipeline.py run [--limit N] [--resume RUN_ID]
python run_pipeline.py analyze TICKER [--verbose]
python run_pipeline.py status [RUN_ID]
python run_pipeline.py results [--format csv|json]
python run_pipeline.py db init
```

---

### Phase 7: Testing & Validation

**Test companies (known outcomes):**
- Should PASS all filters: Berkshire Hathaway (BRK-B) if in market cap range, Hingham Institution for Savings (HIFS)
- Should PASS Filter 2 but may fail Filter 3 on price: well-run but fairly priced companies
- Should FAIL Filter 2: companies with opaque/marketing-style 10-Ks
- Should FAIL Filter 4: Japanese-style cash hoarders, serial acquirers at high prices

**Validation approach:**
1. Run single-ticker analysis on 5-10 known companies
2. Verify filter scores match human judgment
3. Tune thresholds based on results
4. Then scale to broader universe

---

## Critical Files to Modify/Reference

| File | Purpose |
|------|---------|
| `stock-screener/backend/edgar_service.py` | Import EdgarService for XBRL extraction and 10yr data |
| `stock-screener/backend/statements.py` | Import XBRL concept mappings, extend with capital allocation fields |
| edgartools `company_reports.py` (TenK class) | `tenk['Item 7']` etc. for section text extraction |
| edgartools `_filings.py` (Filing class) | `.obj()`, `.text()`, `.markdown()` for filing access |

## Dependencies

```
edgartools>=3.0
claude-agent-sdk
typer
pydantic>=2.0
pandas
requests
python-dotenv
```

## Verification Plan

1. `python run_pipeline.py db init` — creates all tables
2. `python run_pipeline.py analyze HIFS --verbose` — single ticker test on Hingham (known great 10-K)
3. `python run_pipeline.py analyze AAPL --verbose` — test on Apple (well-known company for sanity check)
4. `python run_pipeline.py run --limit 20` — small batch to verify pipeline flow + pause/resume
5. Inspect `analysis_results` table for reasonable scores and pass/fail decisions
6. Scale up: `python run_pipeline.py run --limit 500`
