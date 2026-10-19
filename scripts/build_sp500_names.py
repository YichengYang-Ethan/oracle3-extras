"""Rebuild oracle3_extras/market/data/sp500.csv: S&P 500 companies and the names to match.

    python scripts/build_sp500_names.py

Reads the current constituents from the datasets/s-and-p-500-companies project
(ODC-PDDL), strips corporate suffixes ("Inc.", "Corporation") and descriptive
words ("Technologies", "Air Lines") to get the name people use, adds common
aliases, and drops names that are also everyday words, places, people, teams
or products. A company left with no name is matched only by a ticker in a
market title.
"""

import csv
import io
import re
import urllib.request
from pathlib import Path

SOURCE = 'https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv'
OUT = (
    Path(__file__).resolve().parents[1]
    / 'oracle3_extras'
    / 'market'
    / 'data'
    / 'sp500.csv'
)

CORPORATE = re.compile(
    r',?\s+(Inc\.?|Incorporated|Corporation|Corp\.?|Co\.?|Company|plc|PLC|Ltd\.?|Limited|N\.V\.|S\.A\.|SE|AG|LP|L\.P\.|Holdings?|Group|\(The\)|\(Class [A-C]\))\s*$'
)
DESCRIPTIVE = re.compile(
    r'\s+(Technologies|Technology|Platforms|Global|International|Brands|Systems|Enterprises|Financial|Services|Industries|'
    r'Communications|Entertainment|Solutions|Labs|Laboratories|Motor|Motors|Air Lines|Airlines|Stores|Health|Healthcare|Software|'
    r'Semiconductor|Semiconductors|Pharmaceuticals|Therapeutics|Resources|Energy|Properties|Trust|Bancorp|Bank|Markets|Networks|'
    r'Devices|Instruments|Foods|Beverage|Restaurants|Hotels|Resorts|Cruises|Holdings|Group|Corporation|Company|Inc\.?)$'
)
# Extra names people use for the company, and short tickers worth matching as words.
ALIASES = {
    'GOOGL': ['Alphabet', 'Google'],
    'GOOG': [],
    'META': ['Meta', 'Facebook', 'Instagram', 'WhatsApp'],
    'BRK.B': ['Berkshire Hathaway'],
    'XOM': ['ExxonMobil', 'Exxon'],
    'JPM': ['JPMorgan', 'JP Morgan'],
    'AMD': ['AMD', 'Advanced Micro Devices'],
    'LLY': ['Eli Lilly'],
    'KO': ['Coca-Cola'],
    'PEP': ['PepsiCo', 'Pepsi'],
    'NVDA': ['Nvidia', 'NVIDIA'],
    'IBM': ['IBM'],
    'GM': ['General Motors'],
    'F': ['Ford Motor'],
    'MCD': ["McDonald's"],
    'WBD': ['Warner Bros. Discovery', 'Warner Bros'],
    'BKNG': ['Booking Holdings', 'Booking.com'],
    'HPE': ['Hewlett Packard Enterprise'],
    'HPQ': ['HP Inc.'],
    'T': ['AT&T'],
    'VZ': ['Verizon'],
    'TMUS': ['T-Mobile'],
    'DIS': ['Disney'],
    'BAC': ['Bank of America'],
    'WFC': ['Wells Fargo'],
    'GS': ['Goldman Sachs'],
    'MS': ['Morgan Stanley'],
    'C': ['Citigroup', 'Citibank'],
    'AXP': ['American Express', 'Amex'],
    'UNH': ['UnitedHealth'],
    'CVS': ['CVS Health', 'CVS'],
    'UPS': ['UPS'],
    'FDX': ['FedEx'],
    'TSLA': ['Tesla'],
    'AAPL': ['Apple'],
    'MSFT': ['Microsoft'],
    'AMZN': ['Amazon'],
    'NFLX': ['Netflix'],
    'ORCL': ['Oracle'],
    'CRM': ['Salesforce'],
    'INTC': ['Intel'],
    'AVGO': ['Broadcom'],
    'QCOM': ['Qualcomm'],
    'COIN': ['Coinbase'],
    'HOOD': ['Robinhood'],
    'PLTR': ['Palantir'],
    'UBER': ['Uber'],
    'ABNB': ['Airbnb'],
    'DASH': ['DoorDash'],
    'SBUX': ['Starbucks'],
    'NKE': ['Nike'],
    'COST': ['Costco'],
    'WMT': ['Walmart'],
    'HD': ['Home Depot'],
    'LOW': ["Lowe's"],
    'TGT': ['Target Corporation'],
    'BA': ['Boeing'],
    'LMT': ['Lockheed Martin'],
    'RTX': ['Raytheon', 'RTX Corporation'],
    'GE': ['GE Aerospace'],
    'CMG': ['Chipotle'],
    'DPZ': ["Domino's"],
    'YUM': ['Yum! Brands'],
    'PG': ['Procter & Gamble'],
    'JNJ': ['Johnson & Johnson'],
    'MRK': ['Merck'],
    'PFE': ['Pfizer'],
    'ABBV': ['AbbVie'],
    'MU': ['Micron'],
    'TXN': ['Texas Instruments'],
    'ADBE': ['Adobe'],
    'NOW': ['ServiceNow'],
    'SNOW': ['Snowflake'],
    'DDOG': ['Datadog'],
    'CRWD': ['CrowdStrike'],
    'PANW': ['Palo Alto Networks'],
    'SMCI': ['Supermicro', 'Super Micro'],
    'DELL': ['Dell'],
    'CSCO': ['Cisco'],
    'ANET': ['Arista'],
    'APP': ['AppLovin'],
    'RDDT': ['Reddit'],
    'SPOT': ['Spotify'],
    'LUV': ['Southwest Airlines'],
    'DAL': ['Delta Air Lines'],
    'UAL': ['United Airlines'],
    'AAL': ['American Airlines'],
    'CCL': ['Carnival Cruise', 'Carnival Corporation'],
    'MSTR': ['MicroStrategy'],
    'V': [],
    'MA': ['Mastercard'],
    'PYPL': ['PayPal'],
    'XYZ': ['Block, Inc.'],
    'SCHW': ['Charles Schwab', 'Schwab'],
    'BX': ['Blackstone'],
    'KKR': ['KKR'],
    'MO': ['Altria'],
    'PM': ['Philip Morris'],
    'CVNA': ['Carvana'],
    'EBAY': ['eBay'],
    'ETSY': ['Etsy'],
    'EXPE': ['Expedia'],
    'MAR': ['Marriott'],
    'HLT': ['Hilton'],
    'RCL': ['Royal Caribbean'],
    'NCLH': ['Norwegian Cruise'],
    'WDAY': ['Workday'],
    'DE': ['Deere', 'John Deere'],
    'CMI': ['Cummins Inc.'],
    'ODFL': ['Old Dominion Freight'],
    'RMD': ['ResMed'],
    'MKC': ['McCormick & Company'],
    'HST': ['Host Hotels & Resorts'],
    'AJG': ['Arthur J. Gallagher'],
    'MMM': ['3M'],
    'AES': ['AES Corporation'],
    'CSX': ['CSX'],
    'KLAC': ['KLA'],
    'AON': ['Aon'],
    'CME': ['CME Group'],
    'FFIV': ['F5'],
    'PTC': ['PTC Inc.'],
    'EQT': ['EQT Corporation'],
    'CDW': ['CDW'],
    'DOW': ['Dow Inc.'],
    'L': ['Loews'],
    'INTU': ['Intuit'],
    'ISRG': ['Intuitive Surgical'],
    'MDT': ['Medtronic'],
    'ABT': ['Abbott'],
}
# Names that are also everyday words, places, teams or people: never matched alone.
AMBIGUOUS = {
    # Everyday words
    'Target',
    'Ball',
    'Match',
    'Pool',
    'Public',
    'General',
    'First',
    'Global',
    'Equity',
    'Realty',
    'Dollar',
    'Discover',
    'Capital',
    'State',
    'Union',
    'National',
    'International',
    'Universal',
    'Block',
    'Square',
    'Gap',
    'Steel',
    'Globe',
    'Super',
    'Expand',
    'Builders',
    'Carrier',
    'Principal',
    'Prudential',
    'Travelers',
    'Quanta',
    'Progressive',
    'Tapestry',
    'Mosaic',
    'Arch',
    'Booking',
    'Visa',
    'Snap',
    'Waters',
    'Gen',
    'Fifth',
    'Regions',
    'Citizens',
    'Regency',
    'Constellation',
    'Dominion',
    'Republic',
    'Charter',
    'Northern',
    'Southern',
    'Western',
    'Eastern',
    'Pacific',
    'Atlantic',
    'Federal',
    'American',
    'United',
    'Apollo',
    'Mohawk',
    'Corning',
    'Delta',
    # Places, people, teams and legal terms
    'Fox',
    'Ford',
    'Ross',
    'Henry',
    'Smith',
    'Edison',
    'Duke',
    'Huntington',
    'Lincoln',
    'Franklin',
    'Marathon',
    'Phillips',
    'Brown',
    'Johnson',
    'Morgan',
    'Stanley',
    'Martin',
    'Robert',
    'Ralph',
    'Lauren',
    'Devon',
    'Dover',
    'Parker',
    'Emerson',
    'Eaton',
    'Otis',
    'Hartford',
    'Baxter',
    'Cardinal',
    'Diamondback',
    'Carnival',
    'Hess',
    'Chevron',
    'Lowe',
    'Nasdaq',
    'U.S.',
    'Dow',
    # Found by matching open Kalshi events on 19 October 2026: people, places,
    # products and words that collide with a company's short name
    'Abbott',
    'Align',
    'Analog',
    'Atmos',
    'Bloom',
    "Casey's",
    'Cincinnati',
    'Coherent',
    'Cummins',
    'Darden',
    'Everest',
    'Ferguson',
    'Flex',
    'Hilton',
    'Jacobs',
    'Lennox',
    'Monster',
    'Motorola',
    'MSCI',
    'RTX',
    'Rollins',
    'Roper',
    'Schwab',
    'Southwest',
    'Texas',
    'Tyler',
    'Tyson',
    'Vertex',
    'Vici',
    'Wynn',
    'Zebra',
    'Charles River',
    'Old Dominion',
    'Palo Alto',
    'McCormick',
    'Host',
}
MIN_SINGLE_WORD = 4

with urllib.request.urlopen(SOURCE) as response:  # noqa: S310 - fixed https URL
    source = io.StringIO(response.read().decode('utf-8'))
with source as src, OUT.open('w', newline='', encoding='utf-8') as out:
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(['ticker', 'company', 'sector', 'names'])
    dropped = []
    for row in csv.DictReader(src):
        ticker, company, sector = (
            row['Symbol'],
            row['Security'].replace('|', '').strip(),
            row['GICS Sector'],
        )
        full = CORPORATE.sub('', company.replace('|', '')).strip().strip(',')
        full = re.sub(r'\s*&$', '', full).strip()
        short = full
        for _ in range(3):
            short = re.sub(
                r'\s*&$', '', DESCRIPTIVE.sub('', short).strip().strip(',')
            ).strip()
        names = []
        for name in [*ALIASES.get(ticker, []), full, short]:
            if not name or name in names or '(' in name:
                continue
            words = name.split()
            if name in AMBIGUOUS or (
                len(words) == 1
                and (
                    len(name) < MIN_SINGLE_WORD and name not in ALIASES.get(ticker, [])
                )
            ):
                continue
            if len(words) == 1 and words[0] in AMBIGUOUS:
                continue
            names.append(name)
        if ticker in ('GOOG', 'FOX', 'NWS'):
            names = []  # second share class: markets map to GOOGL, FOXA, NWSA
        if not names:
            dropped.append(f'{ticker} ({company})')
        writer.writerow([ticker, company, sector, ';'.join(names)])
print('companies without a usable name (matched by ticker only):', len(dropped))
print(', '.join(dropped))
