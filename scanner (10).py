# v8
import os, re, json, time, logging, urllib.request, threading, csv, io, smtplib
from datetime import date, timedelta, datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse, quote as urlquote

import anthropic
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY       = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SEARCH_API_KEY   = os.environ.get("GOOGLE_SEARCH_API_KEY", "")   # optional — can also be set in Settings page
GOOGLE_SEARCH_CX        = os.environ.get("GOOGLE_SEARCH_CX", "")        # optional — can also be set in Settings page
REPORTS_DIR = Path("/app/reports")
REPORTS_DIR.mkdir(exist_ok=True)

# ── Persistent data files (all in /app/reports — Railway keeps them on redeploy) ──
SEEN_FILE      = REPORTS_DIR / "seen_leads.json"
ALL_LEADS_FILE = REPORTS_DIR / "all_leads.json"
STATUS_FILE    = REPORTS_DIR / "lead_status.json"
SETTINGS_FILE  = REPORTS_DIR / "settings.json"
WATCHLIST_FILE = REPORTS_DIR / "watchlist.json"

def load_json_file(path, default):
    try:
        p = Path(path)
        if p.exists(): return json.loads(p.read_text())
    except: pass
    return default

def save_json_file(path, data):
    Path(path).write_text(json.dumps(data, indent=2))

def load_settings():
    defaults = {
        "sensitivity": "normal",
        "geo_filter": "all",
        "email_to": "",
        "email_host": "",
        "email_port": "587",
        "email_user": "",
        "email_pass": "",
        "auto_scan_hour": "",
        "ai_article_cap": 40,
    }
    saved = load_json_file(SETTINGS_FILE, {})
    defaults.update(saved)
    return defaults

def save_settings(data):
    save_json_file(SETTINGS_FILE, data)

scan_state = {
    "running": False,
    "status": "Idle — click Run Scan to start",
    "articles": [],
    "filtered": [],
    "leads": [],
    "last_run": None,
}


# ── Sources ───────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Industry direct feeds
    "https://www.medtechdive.com/feeds/news/",
    "https://www.massdevice.com/feed/",
    "https://www.avnetwork.com/rss.xml",
    "https://electrek.co/feed/",
    "https://www.prnewswire.com/rss/news-releases-list.rss",
    "https://www.businesswire.com/rss/home/?rss=G7",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.fiercebiotech.com/rss.xml",
    "https://www.mobihealthnews.com/feed",
    "https://techcrunch.com/feed/",
    "https://venturebeat.com/feed/",
    "https://www.supplychaindive.com/feeds/news/",
    "https://www.inddist.com/rss.xml",
    "https://www.manufacturingdive.com/feeds/news/",

    # Funding signals
    "https://news.google.com/rss/search?q=medical+device+startup+series+A+B+funding+raised+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=electronics+hardware+startup+series+A+B+angel+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AV+hardware+startup+series+funding+raised+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+startup+angel+seed+funding+raised+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+raises+million+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+electronics+company+series+C+D+funding+growth+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=networking+wireless+hardware+company+series+funding+raised+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=iot+smart+device+hardware+startup+series+A+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=robotics+hardware+startup+series+funding+angel+raised+2026&hl=en-US&gl=US&ceid=US:en",

    # Hiring signals — engineering AND operations
    "https://news.google.com/rss/search?q=medical+device+company+hiring+hardware+operations+engineers+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=electronics+hardware+company+hiring+expanding+team+operations+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+hiring+VP+operations+supply+chain+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+startup+hiring+operations+manager+director+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medtech+AV+hardware+company+expanding+headcount+operations+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=networking+hardware+company+hiring+engineers+operations+2026&hl=en-US&gl=US&ceid=US:en",

    # Bay Area expansion
    "https://news.google.com/rss/search?q=hardware+company+moving+expanding+bay+area+san+francisco+silicon+valley+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+opens+office+bay+area+california+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=electronics+hardware+company+bay+area+california+expansion+headquarters+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=startup+hardware+relocating+moving+bay+area+silicon+valley+2026&hl=en-US&gl=US&ceid=US:en",

    # Awards signals
    "https://news.google.com/rss/search?q=CES+award+winner+hardware+medical+device+AV+electronics+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+award+winner+expo+innovation+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+electronics+company+wins+award+best+product+innovation+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=CES+innovation+award+hardware+electronics+startup+2026&hl=en-US&gl=US&ceid=US:en",

    # Trade show signals — all the shows you listed
    "https://news.google.com/rss/search?q=company+exhibiting+InfoComm+Infocomm+AV+hardware+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=company+exhibiting+Interop+networking+hardware+electronics+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+exhibiting+HIMSS+Medtrade+expo+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+electronics+company+ISC+security+show+exhibiting+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AV+hardware+NAB+show+exhibiting+broadcast+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+ISA+security+conference+exhibiting+2026&hl=en-US&gl=US&ceid=US:en",

    # Supply chain / onshoring signals
    "https://news.google.com/rss/search?q=hardware+company+onshoring+reshoring+US+manufacturing+supply+chain+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+supply+chain+onshoring+domestic+manufacturing+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+supply+chain+disruption+vendor+change+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=supply+chain+hardware+offshoring+onshoring+nearshoring+manufacturer+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=electronics+hardware+supply+chain+tariff+sourcing+change+2026&hl=en-US&gl=US&ceid=US:en",

    # Leadership & M&A
    "https://news.google.com/rss/search?q=medical+device+company+new+CEO+acquisition+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medtech+layoffs+restructuring+merger+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=electronics+hardware+company+CEO+acquisition+merger+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=professional+AV+company+acquisition+CEO+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+new+CEO+acquisition+restructuring+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=networking+wireless+hardware+company+new+CEO+merger+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:prnewswire.com+hardware+medical+device+CEO+funding+award+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:businesswire.com+hardware+merger+CEO+funding+award+2026&hl=en-US&gl=US&ceid=US:en",

    # LinkedIn & community discussions (via Google News index)
    "https://news.google.com/rss/search?q=supply+chain+onshoring+reshoring+hardware+linkedin+community+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=supply+chain+disruption+hardware+manufacturer+discussion+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=linkedin+supply+chain+hardware+electronics+onshoring+reshoring+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=linkedin+medical+device+supply+chain+procurement+community+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=linkedin+hardware+electronics+manufacturing+supply+chain+2026&hl=en-US&gl=US&ceid=US:en",
]

# ── Pre-filter keywords ───────────────────────────────────────────────────────
SIGNAL_KEYWORDS = [
    # Leadership
    "ceo","chief executive","president","appointed","named","hired",
    # M&A
    "acqui","merger","acquires","acquired","spinoff","divest",
    # Restructuring
    "layoff","restructur","workforce","job cut","reorgani",
    # Funding
    "series a","series b","series c","series d",
    "seed round","angel","angel round","angel funding","pre-seed",
    "raised","raises","funding round","venture","investment","million",
    "ipo","spac","went public",
    # Hiring — engineering AND operations
    "hiring","is hiring","now hiring","job opening","we're growing",
    "hardware engineer","electrical engineer","vp of engineering",
    "operations manager","director of operations","vp operations",
    "supply chain manager","procurement manager","head of operations",
    "growing team","expanding team","join our team",
    # Bay Area expansion
    "bay area","silicon valley","san francisco","san jose","palo alto",
    "moving to","expanding to","new office","headquarters",
    # Awards
    "award","winner","wins award","best of","innovation award",
    "ces award","ces innovation","product of the year",
    "exhibiting at","exhibit at","booth at","attending",
    # Trade shows
    "infocomm","interop","himss","medtrade","isa security",
    "nab show","ces ","ces 2025","isc west","mda","arab health",
    # Supply chain
    "onshoring","reshoring","nearshoring","offshoring",
    "supply chain","domestic manufacturing","us manufacturing",
    "vendor","supplier","procurement",
    # New companies
    "startup","founded","new company","launches company","spun out",
    "incubat","accelerat","y combinator","techstars",
    # Expansion
    "expand","expansion","new market","launch","launches","launched",
    "partner","partnership","alliance","agreement",
]

# ── Hardware keywords grounded in extroninc.com actual industries ─────────────
# Source: https://extroninc.com/product-assembly-and-test-2/
# Extron Inc works with: high-value electronics, medical/healthcare devices,
# EV charging, networking/WLAN, navigation devices, wireless hardware,
# robotic devices, fitness tech, and large-format electronics

HARDWARE_KEYWORDS = [
    # HIGH-VALUE ELECTRONICS (extroninc.com primary focus)
    "electronics","electronic hardware","high value electronics",
    "electronic assembly","circuit board","pcb","pcba",
    "embedded","firmware","microcontroller","electronic component",
    # NETWORKING & WIRELESS HARDWARE (router, WLAN, WAN — from extroninc.com)
    "router","wireless router","wlan","wireless wan","access point",
    "network appliance","network hardware","network rack","rack integration",
    "wifi device","wireless hardware","mesh network","sd-wan hardware",
    " wan ","networking hardware","network switch","ethernet hardware",
    # EV CHARGING HARDWARE (explicitly listed on extroninc.com)
    "ev charging","ev charger","charging station","evse","electric vehicle",
    "level 2 charger","dc fast charge","fleet charging","smart charger",
    "ev hardware","charging hardware",
    # NAVIGATION & GPS DEVICES (listed on extroninc.com)
    "navigation device","gps device","navigation hardware","gps hardware",
    "fleet tracking","telematics hardware","location hardware",
    # WIRELESS SMART DETECTORS & IOT (listed on extroninc.com)
    "smart detector","wireless detector","iot device","connected device",
    "smart sensor","wireless sensor","iot hardware","sensor hardware",
    "smart home hardware","building automation hardware",
    # MEDICAL ELECTRONICS & HEALTHCARE DEVICES (extroninc.com)
    "medical device","medical electronics","healthcare device","medtech",
    "diagnostic","imaging","patient monitoring","vital signs","wearable",
    "pulse oximeter","ecg","eeg","biosensor","health monitor",
    "telehealth hardware","point of care","clinical hardware",
    "non-invasive","noninvasive","continuous glucose","blood pressure monitor",
    "sinus","therapy device","dialysis","fitness tracker","personal fitness",
    # FITNESS & WELLNESS HARDWARE (technology-heavy fitness machines — extroninc.com)
    "fitness machine","fitness equipment","connected fitness","fitness hardware",
    "exercise equipment","treadmill hardware","peloton-type","wellness device",
    # ROBOTICS & AUTONOMOUS DEVICES (robotic vacuum — extroninc.com)
    "robotic","robot hardware","autonomous device","drone hardware",
    "robotic vacuum","autonomous vehicle hardware","robotic appliance",
    # 3D IMAGING & VISION SYSTEMS (listed on extroninc.com)
    "3d imaging","imaging system","machine vision","vision hardware",
    "lidar","depth sensor","3d scanner","optical hardware",
    # LARGE FORMAT / RACK HARDWARE (extroninc.com specializes in large-form-factor)
    "rack","rack mount","rack integration","server hardware","appliance",
    "large format electronics","industrial hardware",
    # GENERAL CATCH-ALL
    "hardware","device","equipment","instrument","sensor",
]

# Terms that suggest invasive medical, vehicles, or military — used to EXCLUDE leads
INVASIVE_EXCLUSIONS = [
    # Invasive medical — Extron Inc works with NON-INVASIVE devices only
    "surgical robot","implant","implantable","catheter","stent","pacemaker",
    "cochlear","insulin pump","intravascular","endoscopic","laparoscopic",
    "orthopedic implant","spinal","joint replacement","intraocular",
    "invasive","surgical implant","cardiac implant","deep brain","neurostimulator",
    "transcatheter","percutaneous","intraocular lens","bone screw",
    # Vehicles & automotive — NOT Extron's market
    "automobile","automotive","vehicle","car ","truck","motorcycle",
    "electric vehicle","ev car","ev truck","tesla","rivian","lucid motors",
    "ford ev","gm ev","autonomous vehicle","self-driving car","lidar car",
    "fleet vehicle","fleet management","fleet tracking vehicle",
    # Military & defense & government — NOT Extron's market
    "military","defense","defence","army","navy","air force","marines",
    "pentagon","department of defense","dod ","darpa","nato",
    "weapons system","missile","drone military","surveillance drone",
    "government contract","federal contract","gsa contract",
    "classified","intelligence agency","border patrol","law enforcement hardware",
]

# Terms that suggest non-OEM or too large — used to EXCLUDE leads
NON_OEM_EXCLUSIONS = [
    # Non-OEM business types
    "reseller","distributor","value-added reseller","var ","system integrator",
    "contract manufacturer","ems provider","electronics manufacturing service",
    "fulfillment company","third-party logistics","3pl","logistics provider",
    # Vehicles & automotive
    "automobile","automotive","self-driving car","autonomous vehicle",
    "electric vehicle manufacturer","ev manufacturer","car company","truck manufacturer",
    # Military & government
    "defense contractor","military contractor","government contractor",
    "department of defense","dod contract","pentagon","darpa","nato",
    "weapons system","missile system","military drone","surveillance system",
]

# Terms that suggest a company is too large — used to EXCLUDE leads  
LARGE_COMPANY_EXCLUSIONS = [
    "fortune 500","s&p 500","nasdaq 100","dow jones component",
    "billion dollar","multi-billion","$10 billion","$20 billion","$50 billion",
    "100,000 employees","50,000 employees","global workforce of",
    "johnson & johnson","medtronic","abbott","boston scientific","stryker",
    "philips","siemens healthineers","ge healthcare","samsung","lg electronics",
    "sony","panasonic","sharp","nec display","christie digital",
    "crestron","biamp","qsc","harman","bose","cisco","polycom","logitech",
    "tesla","blink charging","chargepoint","evgo","electrify america",
]

# ── Feed health tracking ──────────────────────────────────────────────────────
FEED_HEALTH_FILE = REPORTS_DIR / "feed_health.json"

def load_feed_health():
    return load_json_file(FEED_HEALTH_FILE, {})

def save_feed_health(data):
    save_json_file(FEED_HEALTH_FILE, data)

def update_feed_health(url, count, error=None):
    health = load_feed_health()
    key = url[:120]
    entry = health.get(key, {"url": url, "history": []})
    entry["last_checked"] = date.today().isoformat()
    entry["last_count"]   = count
    entry["history"]      = (entry.get("history", []) + [{"date": date.today().isoformat(), "count": count, "error": error}])[-14:]
    entry["total_runs"]   = entry.get("total_runs", 0) + 1
    entry["total_articles"] = entry.get("total_articles", 0) + count
    entry["consecutive_zeros"] = (entry.get("consecutive_zeros", 0) + 1) if count == 0 and not error else 0
    entry["error_streak"] = (entry.get("error_streak", 0) + 1) if error else 0
    entry["status"] = "error" if error else ("dead" if entry["consecutive_zeros"] >= 3 else ("weak" if entry["consecutive_zeros"] >= 1 else "ok"))
    health[key] = entry
    save_feed_health(health)

def fetch_rss(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", errors="replace")
        items = []
        for item in re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
            def tag(t):
                m = re.search(fr"<{t}[^>]*>(.*?)</{t}>", item, re.DOTALL)
                return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
            title = tag("title")
            if title:
                items.append({
                    "title": title,
                    "link": tag("link"),
                    "summary": tag("description"),
                    "pubDate": tag("pubDate"),
                })
        update_feed_health(url, len(items))
        log.info(f"  {len(items)} items from {url[:65]}")
        return items
    except Exception as e:
        log.warning(f"Feed failed {url[:55]}: {e}")
        return []

# ── SEC EDGAR full-text search ────────────────────────────────────────────────
# Searches SEC 8-K filings directly — legally required disclosures for leadership
# changes, M&A, and major restructuring events. Free, no API key needed.
SEC_QUERIES = [
    # Leadership changes
    '"appointed" "Chief Executive Officer"',
    '"named" "President and Chief Executive"',
    # M&A
    '"merger agreement" "acquisition"',
    '"definitive agreement to acquire"',
    # Restructuring
    '"workforce reduction" "restructuring"',
    '"reduction in force"',
    # New products / expansion
    '"new product" "launch" "hardware"',
]

def fetch_sec_edgar():
    """Query SEC EDGAR full-text search for recent 8-K filings."""
    results = []
    start_date = (date.today() - timedelta(days=90)).isoformat()
    end_date   = date.today().isoformat()
    base = "https://efts.sec.gov/LATEST/search-index?forms=8-K"

    for q in SEC_QUERIES:
        try:
            url = (f"{base}&q={urllib.request.quote(q)}"
                   f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
                   f"&_type=feed&action=getcompany")
            req = urllib.request.Request(url, headers={"User-Agent": "ExtronScanner/1.0 contact@extron.com"})
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8", errors="replace")
            # Parse as RSS/Atom
            for item in re.findall(r"<entry>(.*?)</entry>", raw, re.DOTALL) or re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
                def tag(t):
                    m = re.search(fr"<{t}[^>]*>(.*?)</{t}>", item, re.DOTALL)
                    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
                title   = tag("title") or tag("company-name")
                link    = tag("link") or tag("filing-href")
                summary = tag("summary") or tag("description") or ""
                pub     = tag("updated") or tag("published") or tag("pubDate") or ""
                # Extract company name from SEC filing title
                company = re.sub(r"\s*[(][^)]*[)]\s*", "", title).strip()
                if company:
                    results.append({
                        "title":   f"SEC 8-K: {title}",
                        "link":    link or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=8-K",
                        "summary": f"SEC filing: {summary[:300]}",
                        "pubDate": pub[:10] if pub else "",
                        "source":  "SEC EDGAR",
                    })
            log.info(f"  {len(results)} SEC 8-K filings for: {q[:50]}")
            time.sleep(0.5)  # be polite to SEC servers
        except Exception as e:
            log.warning(f"SEC EDGAR error for '{q[:40]}': {e}")
    return results

# ── Google Custom Search ──────────────────────────────────────────────────────
# Searches the entire web — not just RSS feeds. Surfaces company newsrooms,
# investor blogs, niche trade press, and startup sites that RSS misses.
# Requires GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX env vars.
# Free: 100 queries/day. Paid: $5 per 1,000 queries after that.
GOOGLE_SEARCH_QUERIES = [
    # Funding signals
    '"series A" OR "series B" OR "series C" OEM hardware manufacturer 2026',
    '"raised" "$" million hardware OEM medical device 2026',
    '"angel funding" OR "seed round" hardware electronics OEM 2026',
    # Leadership signals
    '"new CEO" OR "appointed CEO" hardware OEM manufacturer 2026',
    '"new president" hardware electronics manufacturer 2026',
    # Hiring signals
    '"VP of operations" OR "director of operations" hardware OEM 2026',
    '"hiring" "hardware engineer" OEM electronics manufacturer 2026',
    '"supply chain manager" OR "procurement manager" hardware OEM 2026',
    # Bay Area
    '"bay area" OR "silicon valley" hardware OEM manufacturer expanding 2026',
    # Trade shows
    '"InfoComm 2026" hardware OEM exhibiting',
    '"ISC West 2026" hardware OEM exhibiting',
    '"HIMSS 2026" medical device OEM exhibiting',
    # Awards
    '"CES 2026" innovation award hardware OEM',
    '"best new product" hardware OEM 2026',
    # Supply chain
    '"reshoring" OR "onshoring" hardware OEM manufacturer 2026',
    '"supply chain" change vendor hardware OEM 2026',
    # M&A
    '"acquisition" OR "merger" hardware OEM manufacturer 2026',
    # New companies
    '"founded" OR "launched" hardware OEM manufacturer 2026',
    # Medical devices specifically
    '"FDA clearance" OR "FDA cleared" medical device OEM 2026',
    '"510k" medical device OEM startup 2026',
]

def fetch_google_custom_search():
    """Search the entire web using Google Custom Search API."""
    # Try env vars first, then fall back to settings file
    api_key = GOOGLE_SEARCH_API_KEY or load_settings().get("google_search_api_key","").strip()
    cx      = GOOGLE_SEARCH_CX      or load_settings().get("google_search_cx","").strip()
    if not api_key or not cx:
        log.info("Google Custom Search skipped — API key or CX not configured (add in Settings page)")
        return []

    results = []
    base = "https://www.googleapis.com/customsearch/v1"

    for q in GOOGLE_SEARCH_QUERIES:
        try:
            url = (f"{base}?key={api_key}&cx={cx}"
                   f"&q={urllib.request.quote(q)}&num=10&dateRestrict=m3")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))

            for item in data.get("items", []):
                results.append({
                    "title":   item.get("title", ""),
                    "link":    item.get("link", ""),
                    "summary": item.get("snippet", ""),
                    "pubDate": "",
                    "source":  item.get("displayLink", "Google Search"),
                })
            log.info(f"  {len(data.get('items',[]))} results for: {q[:55]}")
            time.sleep(0.2)
        except Exception as e:
            log.warning(f"Google Search error for '{q[:40]}': {e}")

    log.info(f"Google Custom Search: {len(results)} total results")
    return results

# ── DuckDuckGo web search ─────────────────────────────────────────────────────
# Free, no API key, no account needed. Searches the entire web.
DUCKDUCKGO_QUERIES = [
    "OEM hardware manufacturer series A B funding 2026",
    "medical device OEM startup funding raised 2026",
    "electronics hardware OEM new CEO appointed 2026",
    "hardware OEM manufacturer acquisition merger 2026",
    "hardware OEM company hiring VP operations supply chain 2026",
    "medical device OEM hiring operations engineer 2026",
    "hardware OEM bay area silicon valley expansion 2026",
    "OEM electronics manufacturer CES award 2026",
    "hardware OEM InfoComm exhibiting 2026",
    "hardware OEM ISC West exhibiting 2026",
    "medical device OEM HIMSS exhibiting 2026",
    "OEM hardware manufacturer onshoring reshoring supply chain 2026",
    "electronics OEM layoffs restructuring 2026",
    "OEM hardware startup launched founded 2026",
    "medical device OEM FDA clearance launch 2026",
    "networking wireless hardware OEM funding CEO 2026",
    "IoT smart device OEM series funding 2026",
    "robotics hardware OEM startup funding 2026",
    "fitness technology hardware OEM funding launch 2026",
    "navigation telematics hardware OEM funding 2026",
]

def fetch_duckduckgo():
    """Search the entire web via DuckDuckGo HTML — no API key needed."""
    results = []
    for query in DUCKDUCKGO_QUERIES:
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.request.quote(query)}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8", errors="replace")

            # Parse result titles, URLs, and snippets from DuckDuckGo HTML
            found = 0
            for m in re.finditer(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>.*?'
                r'<a[^>]+class="result__snippet"[^>]*>([^<]*)</a>',
                raw, re.DOTALL
            ):
                link, title, snippet = m.group(1), m.group(2).strip(), m.group(3).strip()
                if link.startswith("http") and title:
                    results.append({
                        "title":   title,
                        "link":    link,
                        "summary": snippet,
                        "pubDate": "",
                        "source":  "DuckDuckGo Web Search",
                    })
                    found += 1
                if found >= 5:
                    break

            log.info(f"  DDG: {found} results for: {query[:55]}")
            time.sleep(1.5)  # be polite — DDG rate limits aggressive scrapers
        except Exception as e:
            log.warning(f"DuckDuckGo error for '{query[:40]}': {e}")

    log.info(f"DuckDuckGo total: {len(results)} results")
    return results

def within_90_days(pub_date_str):
    if not pub_date_str:
        return True
    cutoff = date.today() - timedelta(days=90)
    for fmt in ["%a, %d %b %Y", "%Y-%m-%d", "%d %b %Y"]:
        try:
            parts = pub_date_str.strip().split()
            d = time.strptime(" ".join(parts[:4]), fmt + " %H:%M:%S") if len(parts) > 3 else time.strptime(" ".join(parts[:3]), fmt)
            return date(d.tm_year, d.tm_mon, d.tm_mday) >= cutoff
        except:
            continue
    return True

def prefilter(articles):
    kept = []
    excluded_invasive = 0
    excluded_large    = 0
    for a in articles:
        text = (a.get("title","") + " " + a.get("summary","")).lower()
        has_hardware = any(k in text for k in HARDWARE_KEYWORDS)
        has_signal   = any(k in text for k in SIGNAL_KEYWORDS)
        if not (has_hardware and has_signal):
            continue
        # Exclude non-OEM companies
        if any(k in text for k in NON_OEM_EXCLUSIONS):
            continue
        # Exclude invasive medical
        if any(k in text for k in INVASIVE_EXCLUSIONS):
            excluded_invasive += 1
            continue
        # Exclude large enterprise companies
        if any(k in text for k in LARGE_COMPANY_EXCLUSIONS):
            excluded_large += 1
            continue
        kept.append(a)
    log.info(f"Pre-filter: {len(kept)} kept, {excluded_invasive} excluded (invasive), {excluded_large} excluded (large co.)")
    return kept

def ai_filter(articles):
    if not articles:
        return []
    settings = load_settings()
    cap = int(settings.get("ai_article_cap", 40))
    sensitivity = settings.get("sensitivity", "normal")
    geo_filter = settings.get("geo_filter", "all")

    # Watchlist: any article mentioning a watchlist company gets priority-boosted
    watchlist = [w.lower().strip() for w in load_watchlist() if w.strip()]
    def watchlist_match(a):
        txt = (a.get("title","") + " " + a.get("summary","") + " " + a.get("full_text","")).lower()
        return any(w in txt for w in watchlist)

    priority = [a for a in articles if watchlist_match(a)]
    rest = [a for a in articles if not watchlist_match(a)]
    articles_sorted = priority + rest
    top = articles_sorted[:cap]

    # Sensitivity affects confidence threshold in the prompt
    if sensitivity == "broad":
        conf_threshold = 20
        sensitivity_note = "Be generous — include partial matches and speculative leads. Aim for 15+ leads."
    elif sensitivity == "tight":
        conf_threshold = 60
        sensitivity_note = "Be strict — only include companies with very clear, strong signals. Quality over quantity."
    else:
        conf_threshold = 30
        sensitivity_note = "Balance quality and quantity. Aim for 8-12 solid leads."

    # Geography filter
    if geo_filter == "bay_area":
        geo_note = "PRIORITIZE companies in the San Francisco Bay Area, Silicon Valley, or anywhere in California. Still include other strong leads but flag Bay Area companies first."
    elif geo_filter == "us_only":
        geo_note = "Focus only on US-based companies. Exclude international companies unless they have a US office."
    else:
        geo_note = "Include companies from anywhere."

    # Watchlist note
    watchlist_note = ""
    if watchlist:
        watchlist_note = f"IMPORTANT: The following companies are on a priority watchlist — always include them if they appear in any article regardless of signal strength: {', '.join(load_watchlist())}."

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    # Include more text per article so Claude has enough context
    text = "\n\n".join(
        f"[{i+1}] TITLE: {a['title']}\n"
        f"    DATE: {a.get('pubDate','')}\n"
        f"    CONTENT: {(a.get('summary','') + ' ' + a.get('full_text','')).strip()[:800]}\n"
        f"    URL: {a['link']}"
        for i, a in enumerate(top)
    )

    # Stage 1: Ask Claude to identify leads from articles AND from its own knowledge
    prompt = f"""You are a B2B sales intelligence analyst for Extron Electronics (AV hardware manufacturer).

You have TWO jobs in this response:

JOB 1 — EXTRACT from the articles below: Find small-to-mid-size companies (under ~1,000 employees, not large enterprises) selling physical hardware at $300+/unit across any of the 10 target industries below, showing any trigger signal. Skip large public companies and pure software companies. NOTE: For EV charging — focus on the HARDWARE (charger units, EVSE equipment) not the EV automobile industry itself.

JOB 2 — SUPPLEMENT from your knowledge: Add additional real small/mid-size companies you know that fit Extron's profile and have shown signals in 2025 or 2026. Be specific — real company names, real events from 2025-2026. Do NOT include large well-known companies. Do NOT fabricate events — only include signals you are confident actually happened.

TARGET COMPANY PROFILE — this is critical, apply strictly:
- OEM ONLY: Companies must be Original Equipment Manufacturers (OEMs) — meaning they DESIGN and SELL their OWN branded physical hardware products under their own name. 
  INCLUDE: Companies that make and sell their own branded devices, equipment, or systems.
  EXCLUDE: Pure resellers, distributors, VARs (value-added resellers), contract manufacturers (CMOs/EMS), 
           retailers, system integrators, software-only companies, and service companies.
  A company qualifies as an OEM if they have their own product line with their own brand name on the hardware.
- SIZE: Small to mid-size OEMs only. Startups, early-stage, growth-stage, or established SMBs. 
  NO large enterprises, NO Fortune 500, NO publicly traded giants. Employee count ideally under 500, maximum ~1,000.
- UNIT PRICE: Hardware must sell for $300+ per unit minimum. Exclude commodity/consumer electronics under $300.
- HARDWARE FOCUS: Physical hardware products only — not pure software, not services, not apps. 
  The company must manufacture or brand their own hardware product line.

TARGET INDUSTRIES — based directly on Extron Inc's actual client base (extroninc.com/industries):

Extron Inc specializes in last-mile manufacturing, product assembly, fulfillment, and returns management for companies making PHYSICAL HARDWARE PRODUCTS. Their sweet spot is high-value electronics and healthcare devices. Look for companies in ANY of these categories:

1. HIGH-VALUE ELECTRONICS — any company making physical electronic hardware at $300+/unit:
   PCB assemblies, embedded systems, electronic devices, circuit-board-based products

2. NETWORKING & WIRELESS HARDWARE — routers, WLAN appliances, wireless WAN devices,
   network rack systems, access points, SD-WAN hardware, mesh network hardware

3. EV CHARGING HARDWARE — EV chargers, EVSE units, fleet charging systems,
   smart charging stations (NOT pure software charging networks)

4. NAVIGATION & TELEMATICS DEVICES — GPS hardware, navigation devices,
   fleet tracking hardware, location-based hardware products

5. WIRELESS SMART DETECTORS & IOT — smart detectors, wireless sensors,
   IoT hardware, connected devices, building automation hardware

6. NON-INVASIVE MEDICAL ELECTRONICS & HEALTHCARE DEVICES — diagnostic hardware,
   patient monitoring systems, wearables, fitness trackers, therapy devices,
   imaging systems (ultrasound, MRI accessories, X-ray), biosensors, telehealth hardware,
   sinus/pain therapy devices, blood pressure monitors, pulse oximeters, ECG/EEG monitors.
   IMPORTANT: NON-INVASIVE ONLY. Exclude anything that goes inside the body:
   implants, catheters, stents, pacemakers, surgical robots, endoscopic devices.

7. TECHNOLOGY-HEAVY FITNESS MACHINES — connected fitness equipment, smart exercise hardware,
   electronics-intensive fitness machines, personal fitness trackers

8. 3D IMAGING & VISION SYSTEMS — 3D imaging systems, machine vision hardware,
   LiDAR, depth sensors, optical hardware

9. ROBOTICS & AUTONOMOUS DEVICES — robotic appliances (e.g. robotic vacuums),
   autonomous hardware, drone hardware, robotic systems

10. LARGE-FORMAT ELECTRONICS — rack-and-stack network systems, large-format medical electronics,
    industrial electronics requiring final assembly and configuration

HARD EXCLUSIONS — do NOT include:
- Non-OEM companies: pure resellers, distributors, VARs, system integrators, contract manufacturers (Foxconn, Jabil, Flex, etc.)
- Companies that only sell other companies' hardware (no own-brand product line)
- Large public companies (Medtronic, Abbott, Philips, GE Healthcare, Siemens, Cisco, Netgear, ChargePoint, EVgo, Tesla, Peloton, iRobot, etc.)
- Pure software companies with no physical product
- Consumer electronics under $300/unit (cheap mass-market gadgets)
- Companies with over ~1,000 employees unless they are clearly a niche SMB
- Pure logistics, distribution, or retail companies (no hardware product of their own)
- INVASIVE medical devices: implants, catheters, stents, pacemakers, surgical robots, endoscopic/laparoscopic devices, anything inserted into the body
- VEHICLES & AUTOMOTIVE: cars, trucks, electric vehicles, autonomous vehicles, fleet vehicles — do not confuse EV charging hardware companies with EV/car companies
- MILITARY & DEFENSE & GOVERNMENT: weapons systems, defense contractors, military drones, government surveillance hardware, federal/DOD contractors

Signal types to look for:
- Series A/B/C/D funding, angel/seed funding, IPO
- Hiring hardware engineers OR operations/supply chain talent
- Moving to or expanding in Bay Area / Silicon Valley
- Won CES award or industry award
- Exhibiting at InfoComm, Interop, HIMSS, ISC West, NAB Show, Medtrade
- Onshoring/reshoring supply chain
- New CEO or leadership change
- Merger, acquisition, spinoff
- Layoffs or restructuring
- New product launch
- New company or startup (founded last 3 years)
- Well-established SMB going through any change

Return ONLY a raw JSON array. No markdown. No backticks. Start with [ end with ].
Include companies with confidence >= {conf_threshold}. {sensitivity_note} {geo_note} {watchlist_note}
Aim for at least 5-10 leads combining both jobs.

Each object must have ALL of these fields:
- name: company name (string)
- category: "High-Value Electronics" or "Networking / Wireless Hardware" or "EV Charging Hardware" or "Navigation / Telematics" or "IoT / Smart Detectors" or "Medical / Healthcare Devices" or "Fitness Technology" or "3D Imaging / Vision" or "Robotics / Autonomous" or "Large-Format Electronics"
- isOEM: true or false (must be true to qualify — does this company design and sell their own branded hardware?)
- stage: "Startup / Pre-revenue" or "Early Stage" or "Growth Stage" or "Established SMB" or "Public Company"
- hq: city, state (string)
- founded: year as integer or null
- ticker: stock ticker or null
- website: company website URL e.g. "https://www.company.com" or null
- unitPrice: estimated price per unit e.g. "$500-$2,000/unit" or "Early stage / TBD"
- annualRevenue: estimated annual revenue e.g. "$5M-$20M" or "Pre-revenue" or "Unknown"
- employees: estimated employee count as string e.g. "50-100" or "Unknown"
- keyProducts: full sentence describing their main hardware products in detail — what they are, what they do, and who uses them e.g. "Their flagship product is a non-invasive continuous glucose monitor worn on the upper arm, sold to diabetic patients and hospitals. They also make a companion bedside vital signs display unit used in ICUs."
- targetCustomers: detailed description of who buys their hardware — industry, company size, use case e.g. "Primary customers are mid-size hospitals and urgent care clinics purchasing in volume. Secondary market includes direct-to-consumer sales through pharmacy chains."
- topCompetitors: 2-3 direct competitors with a brief note on differentiation e.g. "Masimo (larger, more established), Nellcor (Medtronic subsidiary), iRhythm (patch-based vs wrist-based)"
- supplyChainNotes: detailed notes on where they manufacture, how they assemble, what their supply chain looks like e.g. "Currently contract manufactures final assembly in Guadalajara, Mexico. PCBs sourced from Taiwan. Exploring US-based assembly partner to reduce lead times and comply with domestic content requirements."
- description: 4-5 sentence detailed description of the company — what they make, how it works, what market problem they solve, how large their market is, and what makes them unique
- productFit: 3-4 sentences explaining specifically WHY this company is a strong fit for Extron Inc's services — focus on the PRODUCT ITSELF, not the reason for change. Explain what type of hardware it is (non-invasive medical device, high-value electronics, etc.), why it needs careful last-mile handling, whether it requires configuration or kitting before delivery, whether it has return/repair cycles, and whether demo units are relevant. Be specific to this company's actual product.
- signalType: one of "Series A Funding", "Series B Funding", "Series C Funding", "Series D Funding", "Angel / Seed Funding", "Hiring Hardware Engineers", "Hiring Operations Talent", "Bay Area Expansion", "CES Award", "Industry Award", "Trade Show — InfoComm", "Trade Show — Interop", "Trade Show — ISC West", "Trade Show — HIMSS", "Trade Show — NAB Show", "Trade Show — Other", "Supply Chain Onshoring", "Supply Chain Change", "New Company / Startup", "New CEO/Leadership", "M&A / Acquisition", "Layoffs / Restructuring", "Market Expansion", "New Product Launch", "New Partnership", "IPO / SPAC"
- signalDate: e.g. "March 2025" or "Q1 2025"
- signalDetail: 3-4 factual sentences about exactly what happened, what was announced, who was involved, and what it means for the company
- whyNow: 2 sentences on why this specific moment is the right time for Extron to reach out, tied to the signal
- extronFit: 2-3 sentences on specifically how Extron Inc's services (last-mile manufacturing, product assembly & test, order fulfillment, returns management, demo-unit programs) address this company's current operational needs given their situation
- additionalContext: 2-3 sentences of any other relevant context — market trends, regulatory environment, funding history, notable customers, recent press
- source: publication name
- sourceUrl: article URL
- urgencyScore: 0-100
- confidence: 0-100

Articles to analyze:
{text}"""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=4000,
            system="Return ONLY a raw JSON array. No markdown. No backticks. Start with [ and end with ].",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text
        log.info(f"Raw AI response (first 300 chars): {raw[:300]}")
        # Robust JSON extraction
        raw = raw.replace("```json","").replace("```","").strip()
        # Find outermost array
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            # Try to find any JSON object and wrap it
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1:
                raw = "[" + raw[start:end+1] + "]"
            else:
                log.error(f"No JSON found in response: {raw[:500]}")
                return []
        else:
            raw = raw[start:end+1]
        leads = json.loads(raw)
        if not isinstance(leads, list):
            leads = [leads]

        # Load dismissed companies to filter them out (feedback loop)
        dismissed = {k for k,v in load_statuses().items() if v.get("status") == "dismissed"}

        validated = []
        for lead in leads:
            name = lead.get("name","")
            key  = name.lower().strip()

            # Feedback loop: skip companies previously dismissed as "Not a fit"
            if key in dismissed:
                log.info(f"  Skipping dismissed company: {name}")
                continue

            # Confidence warning flags
            flags = []
            emp = str(lead.get("employees","")).lower()
            price = str(lead.get("unitPrice","")).lower()
            conf  = lead.get("confidence", 0)
            founded = lead.get("founded")
            signal_detail = lead.get("signalDetail","")
            source_url = lead.get("sourceUrl","")

            # Flag suspiciously round/vague numbers
            if emp in ["unknown","n/a","","varies"] or not emp:
                flags.append("employee count unknown")
            if any(x in price for x in ["tbd","early stage","unknown","n/a",""]):
                flags.append("unit price unconfirmed")
            if conf < 40:
                flags.append(f"low AI confidence ({conf}%)")
            if not source_url or source_url in ["null","None",""]:
                flags.append("no source URL — AI knowledge only")
            if not signal_detail or len(signal_detail) < 20:
                flags.append("signal detail is vague")
            if founded and int(founded) > date.today().year:
                flags.append("invalid founding year")

            lead["qa_flags"]     = flags
            lead["qa_flag_count"] = len(flags)
            lead["qa_passed"]    = len(flags) == 0
            validated.append(lead)

        log.info(f"AI found {len(leads)} leads → {len(validated)} after dismissed filter ({len(leads)-len(validated)} skipped)")
        return validated
    except Exception as e:
        log.error(f"AI error: {e}")
        return []

def load_seen():
    try: return set(load_json_file(SEEN_FILE, []))
    except: return set()

def save_seen(seen):
    save_json_file(SEEN_FILE, list(seen))

def deduplicate(leads, seen):
    fresh = []
    for l in leads:
        key = l.get("name","").lower().strip()
        if key and key not in seen:
            fresh.append(l); seen.add(key)
    return fresh

def load_all_leads():
    return load_json_file(ALL_LEADS_FILE, [])

def save_all_leads(leads):
    save_json_file(ALL_LEADS_FILE, leads)

def append_to_history(new_leads):
    existing = load_all_leads()
    existing_names = {l.get("name","").lower().strip() for l in existing}
    for l in new_leads:
        key = l.get("name","").lower().strip()
        if key not in existing_names:
            l["added_date"] = date.today().isoformat()
            existing.append(l)
            existing_names.add(key)
    existing.sort(key=lambda x: x.get("urgencyScore",0), reverse=True)
    save_all_leads(existing)

def load_statuses():
    return load_json_file(STATUS_FILE, {})

def save_status(company_key, status):
    statuses = load_statuses()
    if status == "clear":
        statuses.pop(company_key, None)
    else:
        statuses[company_key] = {"status": status, "updated": date.today().isoformat()}
    save_json_file(STATUS_FILE, statuses)

def load_watchlist():
    return load_json_file(WATCHLIST_FILE, [])

def save_watchlist(companies):
    save_json_file(WATCHLIST_FILE, companies)

# ── Colors ────────────────────────────────────────────────────────────────────
NAVY=colors.HexColor('#0C447C'); WHITE=colors.white
AMBER=colors.HexColor('#633806'); AMBER_BG=colors.HexColor('#FAEEDA'); AMBER_MID=colors.HexColor('#BA7517')
RED=colors.HexColor('#791F1F'); RED_BG=colors.HexColor('#FCEBEB'); RED_MID=colors.HexColor('#E24B4A')
GREEN=colors.HexColor('#27500A'); GREEN_BG=colors.HexColor('#EAF3DE'); GREEN_MID=colors.HexColor('#2E7D32')
PURPLE=colors.HexColor('#4A0080'); PURPLE_BG=colors.HexColor('#F3E5FF')
TEAL=colors.HexColor('#006064'); TEAL_BG=colors.HexColor('#E0F7FA')
ORANGE=colors.HexColor('#E65100'); ORANGE_BG=colors.HexColor('#FFF3E0')
GRAY_BG=colors.HexColor('#F1EFE8'); GRAY_TXT=colors.HexColor('#444441')
GRAY_LT=colors.HexColor('#F7F7F5'); BORDER=colors.HexColor('#E0E0E0')
TEXT2=colors.HexColor('#666666'); INK=colors.HexColor('#111111')

def ps(name, **kw): return ParagraphStyle(name, **kw)

def badge(text, bg, fg, w):
    return Table([[Paragraph(text, ps('b', fontName='Helvetica-Bold', fontSize=8, textColor=fg, leading=10))]],
        colWidths=[w], style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),
        ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),
        ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))

def urg_meta(u):
    if u>=85: return "HOT LEAD",RED_BG,RED,RED_MID,3.5
    if u>=70: return "HIGH PRIORITY",AMBER_BG,AMBER,AMBER_MID,1.0
    return "WATCH LIST",GRAY_BG,GRAY_TXT,BORDER,0.5

def cat_col(cat):
    c=(cat or "").lower()
    if "av" in c or "audio" in c: return colors.HexColor('#EAF0FB'),colors.HexColor('#1A3A6B')
    if "ev" in c or "charg" in c: return GREEN_BG,GREEN
    return colors.HexColor('#FDE8EC'),colors.HexColor('#7A1530')

def sig_col(sig):
    s=(sig or "").lower()
    if "series" in s or "ipo" in s: return PURPLE_BG,PURPLE
    if "angel" in s or "seed" in s: return PURPLE_BG,PURPLE
    if "hiring hardware" in s: return GREEN_BG,GREEN_MID
    if "hiring operations" in s: return TEAL_BG,TEAL
    if "bay area" in s: return colors.HexColor('#E3F2FD'),colors.HexColor('#0D47A1')
    if "award" in s or "ces" in s: return colors.HexColor('#FFFDE7'),colors.HexColor('#F57F17')
    if "trade show" in s: return colors.HexColor('#FCE4EC'),colors.HexColor('#880E4F')
    if "supply chain" in s or "onshoring" in s: return TEAL_BG,TEAL
    if "startup" in s or "new company" in s: return colors.HexColor('#E8F5E9'),colors.HexColor('#1B5E20')
    if "ceo" in s or "leader" in s: return colors.HexColor('#E3F2FD'),colors.HexColor('#0D47A1')
    if "acqui" in s or "merger" in s: return ORANGE_BG,ORANGE
    if "layoff" in s or "restructur" in s: return RED_BG,RED
    return GRAY_BG,GRAY_TXT

def make_card(l, CW):
    """One-page company profile — smaller fonts, tighter spacing, page break after."""
    u    = l.get("urgencyScore", 60)
    ulbl, ubg, ufc, ulc, ulw = urg_meta(u)
    cbg, cfg = cat_col(l.get("category", ""))
    sbg, sfc = sig_col(l.get("signalType", ""))
    stage    = l.get("stage", "")

    F  = 8    # base font size
    FL = 10   # leading for base
    P  = 4    # cell padding
    LW = 36*mm  # label column width

    def row(label, value):
        val = str(value).strip() if value else "—"
        # Truncate very long values to keep rows single-line
        if len(val) > 160: val = val[:157] + "..."
        return Table([[
            Paragraph(label, ps('rl', fontName='Helvetica-Bold', fontSize=F, textColor=TEXT2, leading=FL)),
            Paragraph(val,   ps('rv', fontName='Helvetica',      fontSize=F, textColor=INK,  leading=FL)),
        ]], colWidths=[LW, CW - LW - 8*mm], style=TableStyle([
            ('VALIGN',       (0,0), (-1,-1), 'TOP'),
            ('LINEBELOW',    (0,0), (-1,-1), 0.2, BORDER),
            ('TOPPADDING',   (0,0), (-1,-1), P),
            ('BOTTOMPADDING',(0,0), (-1,-1), P),
            ('LEFTPADDING',  (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ]))

    def link_row(label, url):
        if not url or not str(url).startswith('http'):
            return row(label, url or "—")
        display = str(url)
        return Table([[
            Paragraph(label, ps('rl', fontName='Helvetica-Bold', fontSize=F, textColor=TEXT2, leading=FL)),
            Paragraph(f'<link href="{url}"><u>{display}</u></link>',
                ps('lnk', fontName='Helvetica', fontSize=F, textColor=colors.HexColor('#185FA5'), leading=FL)),
        ]], colWidths=[LW, CW - LW - 8*mm], style=TableStyle([
            ('VALIGN',       (0,0), (-1,-1), 'TOP'),
            ('LINEBELOW',    (0,0), (-1,-1), 0.2, BORDER),
            ('TOPPADDING',   (0,0), (-1,-1), P),
            ('BOTTOMPADDING',(0,0), (-1,-1), P),
            ('LEFTPADDING',  (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
        ]))

    article_url = l.get('sourceUrl', '') or ''
    website_url = l.get('website',   '') or ''
    if website_url and not website_url.startswith('http'):
        website_url = ''
    if not website_url:
        slug = l.get('name','').lower().replace(' ','').replace(',','').replace('.','').replace("'","")
        website_url = f"https://www.{slug}.com"

    # ── HEADER (navy bar) ─────────────────────────────────────────────────────
    header = Table([[
        Table([
            [Paragraph(l.get("name",""), ps('cn', fontName='Helvetica-Bold', fontSize=14, textColor=WHITE, leading=17))],
            [Paragraph(f"{l.get('hq','')}  ·  OEM  ·  Est. {l.get('founded','?')}  ·  {l.get('ticker') or 'Private'}",
                ps('cs', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#ccddee'), leading=11))],
        ], colWidths=[CW - 68*mm]),
        Table([[badge(l.get("category","").upper(), cbg, cfg, 30*mm), Spacer(2,1), badge(ulbl, ubg, ufc, 26*mm)]],
            colWidths=[32*mm, 3*mm, 28*mm], style=TableStyle([
                ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
                ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
                ('VALIGN',(0,0),(-1,-1),'MIDDLE')])),
    ]], colWidths=[CW - 66*mm, 66*mm], style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),NAVY),
        ('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),
        ('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
    ]))

    # ── METRICS BAR ──────────────────────────────────────────────────────────
    metrics = Table([[
        Paragraph(f"Urgency: {u}%", ps('um', fontName='Helvetica-Bold', fontSize=8, textColor=ufc, leading=11)),
        Paragraph(f"Confidence: {l.get('confidence',50)}%", ps('cm', fontName='Helvetica', fontSize=8, textColor=TEXT2, leading=11)),
        Paragraph(f"Stage: {stage}", ps('sm', fontName='Helvetica', fontSize=8, textColor=TEXT2, leading=11)),
        Paragraph(f"Signal date: {l.get('signalDate','')}", ps('dm', fontName='Helvetica', fontSize=8, textColor=TEXT2, leading=11)),
        Paragraph(f"Employees: {l.get('employees','?')}", ps('em', fontName='Helvetica', fontSize=8, textColor=TEXT2, leading=11)),
    ]], colWidths=[22*mm, 24*mm, 40*mm, 30*mm, CW-116*mm], style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1), ubg),
        ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),4),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('LINEAFTER',(0,0),(-2,-1),0.3,BORDER),
    ]))

    # ── THREE-COLUMN BODY ─────────────────────────────────────────────────────
    # Left col: signal + why now + extron fit
    # Right col: company profile details (two sub-columns)
    CL = 88*mm   # left column width
    CR = CW - CL - 4*mm  # right column width

    sig_detail       = l.get('signalDetail','')
    why_text         = l.get('whyNow','')
    fit_text         = l.get('extronFit','') or "Extron Inc can provide last-mile assembly, fulfillment, and returns management."
    product_fit_text = l.get('productFit','')
    desc_text        = l.get('description','')

    left_col = Table([
        [Paragraph(f"{l.get('signalType','').upper()}", ps('st', fontName='Helvetica-Bold', fontSize=7, textColor=sfc, leading=9))],
        [Paragraph(sig_detail, ps('sd', fontName='Helvetica', fontSize=8, textColor=INK, leading=11))],
        [Spacer(1,3)],
        [Paragraph("Why reach out now:", ps('wl', fontName='Helvetica-Bold', fontSize=7, textColor=AMBER, leading=9))],
        [Paragraph(why_text, ps('wt', fontName='Helvetica', fontSize=8, textColor=AMBER, leading=11))],
        [Spacer(1,3)],
        [Paragraph("Why this product fits Extron:", ps('pfl', fontName='Helvetica-Bold', fontSize=7, textColor=colors.HexColor('#27500A'), leading=9))],
        [Paragraph(product_fit_text or fit_text, ps('pft', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#27500A'), leading=11))],
        [Spacer(1,3)],
        [Paragraph("Extron services opportunity:", ps('el', fontName='Helvetica-Bold', fontSize=7, textColor=colors.HexColor('#0C447C'), leading=9))],
        [Paragraph(fit_text, ps('et', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#0C447C'), leading=11))],
        [Spacer(1,3)],
        [Paragraph("About the company:", ps('dl', fontName='Helvetica-Bold', fontSize=7, textColor=TEXT2, leading=9))],
        [Paragraph(desc_text, ps('dt', fontName='Helvetica', fontSize=8, textColor=INK, leading=11))],
    ], colWidths=[CL], style=TableStyle([
        ('BACKGROUND',(0,0),(0,1), sbg),
        ('BACKGROUND',(0,3),(0,4), AMBER_BG),
        ('BACKGROUND',(0,6),(0,7), colors.HexColor('#EAF3DE')),
        ('BACKGROUND',(0,9),(0,10), colors.HexColor('#E6F1FB')),
        ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(0,0),6),('BOTTOMPADDING',(0,1),(0,1),6),
        ('TOPPADDING',(0,3),(0,3),6),('BOTTOMPADDING',(0,4),(0,4),6),
        ('TOPPADDING',(0,6),(0,6),6),('BOTTOMPADDING',(0,7),(0,7),6),
        ('TOPPADDING',(0,9),(0,9),6),('BOTTOMPADDING',(0,10),(0,10),6),
        ('TOPPADDING',(0,12),(0,12),4),
        ('TOPPADDING',(0,2),(0,2),0),('BOTTOMPADDING',(0,2),(0,2),0),
        ('TOPPADDING',(0,5),(0,5),0),('BOTTOMPADDING',(0,5),(0,5),0),
        ('TOPPADDING',(0,8),(0,8),0),('BOTTOMPADDING',(0,8),(0,8),0),
        ('TOPPADDING',(0,11),(0,11),0),('BOTTOMPADDING',(0,11),(0,11),0),
    ]))

    # Right column: two-column detail grid
    def drow(k, v):
        val = str(v).strip() if v else "—"
        return [
            Paragraph(k, ps('dk', fontName='Helvetica-Bold', fontSize=7, textColor=TEXT2, leading=9)),
            Paragraph(val, ps('dv', fontName='Helvetica', fontSize=8, textColor=INK, leading=10)),
        ]

    right_col = Table([
        drow("Key Products",     l.get('keyProducts','')),
        drow("Target Customers", l.get('targetCustomers','')),
        drow("Unit Price",       l.get('unitPrice','')),
        drow("Top Competitors",  l.get('topCompetitors','')),
        drow("Supply Chain",     l.get('supplyChainNotes','')),
        drow("Additional Notes", l.get('additionalContext','')),
    ], colWidths=[24*mm, CR - 24*mm], style=TableStyle([
        ('VALIGN',       (0,0), (-1,-1), 'TOP'),
        ('LINEBELOW',    (0,0), (-1,-1), 0.2, BORDER),
        ('TOPPADDING',   (0,0), (-1,-1), 3),
        ('BOTTOMPADDING',(0,0), (-1,-1), 3),
        ('LEFTPADDING',  (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 4),
        ('BACKGROUND',   (0,0), (-1,-1), GRAY_LT),
    ]))


    # ── BODY (left + right columns side by side) ─────────────────────────────
    body = Table([[left_col, Spacer(4,1), right_col]],
        colWidths=[CL, 4*mm, CR], style=TableStyle([
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0),
        ]))

    # QA flags if any
    qa = l.get('qa_flags',[])
    qa_elem = []
    if qa:
        qa_elem = [Spacer(1,2*mm),
            Paragraph("  ·  ".join(qa), ps('qf', fontName='Helvetica', fontSize=7, textColor=RED, leading=9))]

    return [
        Table([[Table([[header],[Spacer(1,2)],[metrics],[Spacer(1,2)],[body],*([[Spacer(1,2)],[e]] for e in qa_elem)],
            colWidths=[CW], style=TableStyle([
                ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
                ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))]],
            colWidths=[CW], style=TableStyle([
                ('BOX',(0,0),(-1,-1),0.75,ulc),('LINEBEFORE',(0,0),(0,-1),ulw,ulc),
                ('BACKGROUND',(0,0),(-1,-1),WHITE),
                ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
                ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])),
        PageBreak(),
    ]

    if not rows_html:
        rows_html = '<tr><td colspan="5" style="padding:28px;text-align:center;color:#999;font-size:13px">No results yet — run a scan first.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Extron Community Scanner</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:#f2f2f0;color:#111}}
    .wrap{{max-width:1100px;margin:0 auto;padding:28px 20px}}
    .header{{background:#0C447C;color:white;padding:24px 28px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}}
    .header h1{{font-size:20px;font-weight:600;color:white}}
    .header p{{font-size:13px;opacity:.75;margin-top:4px}}
    .nav-link{{color:rgba(255,255,255,.75);font-size:13px;text-decoration:none;background:rgba(255,255,255,.15);padding:7px 14px;border-radius:6px}}
    .nav-link:hover{{background:rgba(255,255,255,.25)}}
    .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin-bottom:20px}}
    .stat{{background:white;border-radius:8px;padding:14px;border:1px solid #e0e0e0}}
    .stat-num{{font-size:26px;font-weight:700;color:#0C447C}}
    .stat-label{{font-size:11px;color:#888;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}}
    .card{{background:white;border-radius:10px;padding:20px 24px;margin-bottom:14px;border:1px solid #e0e0e0}}
    .card h2{{font-size:14px;font-weight:600;margin:0 0 10px;color:#111}}
    .btn{{display:inline-block;background:#0C447C;color:white;border:none;padding:10px 24px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none}}
    .btn:hover{{background:#185FA5}}
    .btn-green{{background:#27500A}}.btn-green:hover{{background:#3a7010}}
    .btn-gray{{background:#666;margin-left:8px}}.btn-gray:hover{{background:#444}}
    .btn-disabled{{background:#999;cursor:not-allowed}}
    .scroll{{max-height:520px;overflow-y:auto;border:1px solid #eee;border-radius:6px}}
    table{{width:100%;border-collapse:collapse}}
    th{{padding:9px 8px;text-align:left;font-size:11px;color:#666;font-weight:600;background:#f7f7f5;border-bottom:1px solid #e8e8e8;position:sticky;top:0}}
    tr:hover td{{background:#fafafa}}
  </style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div>
      <h1>Community Scanner</h1>
      <p>Reddit &middot; Hacker News &middot; Stack Exchange &middot; AVS Forum &middot; AVIXA &middot; LinkedIn &middot; Quora</p>
    </div>
    <a href="/" class="nav-link">&larr; Back to News Scanner</a>
  </div>

  {status_html}

  <div class="stats">
    <div class="stat"><div class="stat-num">{len(results)}</div><div class="stat-label">Total found</div></div>
    <div class="stat"><div class="stat-num">{len(flagged)}</div><div class="stat-label">Flagged (5+)</div></div>
    <div class="stat"><div class="stat-num">{len(extron)}</div><div class="stat-label">Extron mentions</div></div>
    <div class="stat"><div class="stat-num">{len(COMM_SCANNERS)}</div><div class="stat-label">Sources</div></div>
  </div>

  <div class="card">
    <h2>Run a Community Scan</h2>
    <p style="font-size:13px;color:#555;margin-bottom:12px">
      Scans public forums and communities for supply chain, onshoring/offshoring, and Extron-related discussions.
      Uses the same signal keywords as the news scanner. Takes 1–2 minutes.
    </p>
    {"" if not source_pills else f'<div style="margin-bottom:12px;line-height:2">{source_pills}</div>'}
    <form method="POST" action="/community/run" style="display:inline">
      <button type="submit" class="btn {'btn-disabled' if cs['running'] else ''}" {"disabled" if cs["running"] else ""}>
        {"Scanning..." if cs["running"] else "Run Community Scan"}
      </button>
    </form>
    <a href="/community/export?mode=flagged" class="btn btn-green" style="margin-left:10px">Export Flagged CSV</a>
    <a href="/community/export?mode=all" class="btn btn-gray">Export All CSV</a>
  </div>

  <div class="card">
    <h2>Results — top {min(len(results),60)} of {len(results)} (sorted by score)</h2>
    <div class="scroll">
      <table>
        <thead><tr>
          <th>Post / Discussion</th>
          <th>Source</th>
          <th style="text-align:center">Score</th>
          <th>Signals</th>
          <th>Date</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>
</div>
</body>
</html>"""


# ── Email digest ──────────────────────────────────────────────────────────────
def send_email_digest(hot_leads, all_leads):
    settings = load_settings()
    to_addr   = settings.get("email_to","").strip()
    host      = settings.get("email_host","").strip()
    user      = settings.get("email_user","").strip()
    password  = settings.get("email_pass","").strip()
    port      = int(settings.get("email_port", 587) or 587)
    if not (to_addr and host and user and password):
        log.info("Email digest skipped — not configured")
        return
    today = date.today().strftime("%B %d, %Y")
    rows = "".join(
        f"""<tr style="border-bottom:1px solid #eee">
          <td style="padding:10px 8px;font-weight:600">{l.get("name","")}</td>
          <td style="padding:10px 8px;color:#555">{l.get("category","")}</td>
          <td style="padding:10px 8px;color:#555">{l.get("signalType","")}</td>
          <td style="padding:10px 8px;color:#791F1F;font-weight:700">{l.get("urgencyScore",0)}%</td>
          <td style="padding:10px 8px;font-size:12px;color:#555">{l.get("whyNow","")}</td>
        </tr>"""
        for l in sorted(hot_leads, key=lambda x: -x.get("urgencyScore",0))
    )
    html = f"""<html><body style="font-family:Arial,sans-serif;color:#111;max-width:700px;margin:0 auto">
      <div style="background:#0C447C;color:white;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="margin:0;font-size:18px">🔥 Extron Hot Leads — {today}</h2>
        <p style="margin:4px 0 0;font-size:13px;opacity:.8">{len(hot_leads)} hot leads · {len(all_leads)} total new leads this scan</p>
      </div>
      <div style="border:1px solid #ddd;border-top:none;padding:20px;border-radius:0 0 8px 8px">
        <table style="width:100%;border-collapse:collapse">
          <thead><tr style="background:#f7f7f5">
            <th style="padding:8px;text-align:left;font-size:12px;color:#666">Company</th>
            <th style="padding:8px;text-align:left;font-size:12px;color:#666">Category</th>
            <th style="padding:8px;text-align:left;font-size:12px;color:#666">Signal</th>
            <th style="padding:8px;text-align:left;font-size:12px;color:#666">Urgency</th>
            <th style="padding:8px;text-align:left;font-size:12px;color:#666">Why now</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
        <p style="margin-top:20px;font-size:12px;color:#999">Log in to your scanner dashboard to view full details and mark leads.</p>
      </div>
    </body></html>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Extron Scanner] {len(hot_leads)} Hot Leads — {today}"
    msg["From"]    = user
    msg["To"]      = to_addr
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, password)
        s.sendmail(user, to_addr, msg.as_string())
    log.info(f"Email digest sent to {to_addr}")

# ── CSV export ────────────────────────────────────────────────────────────────
def leads_to_csv(leads):
    out = io.StringIO()
    fields = ["name","category","stage","hq","founded","ticker","website",
              "unitPrice","employees","signalType","signalDate","signalDetail",
              "whyNow","urgencyScore","confidence","source","sourceUrl",
              "description","additionalContext","added_date"]
    w = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    statuses = load_statuses()
    for l in leads:
        row = dict(l)
        key = l.get("name","").lower().strip()
        row["status"] = statuses.get(key, {}).get("status","new")
        w.writerow(row)
    return out.getvalue()

# ── Auto-scheduler ────────────────────────────────────────────────────────────
def auto_scheduler():
    log.info("Auto-scheduler started")
    last_run_date = None
    while True:
        try:
            settings = load_settings()
            hour_str = settings.get("auto_scan_hour","").strip()
            if hour_str:
                now = datetime.utcnow()
                target_hour = int(hour_str)
                if now.hour == target_hour and last_run_date != now.date():
                    if not scan_state["running"]:
                        log.info(f"Auto-scheduler triggering scan at {now}")
                        last_run_date = now.date()
                        threading.Thread(target=run_scan, daemon=True).start()
        except Exception as e:
            log.warning(f"Auto-scheduler error: {e}")
        time.sleep(60)

# ── Web UI ────────────────────────────────────────────────────────────────────
def build_page():
    s         = scan_state
    settings  = load_settings()
    statuses  = load_statuses()
    all_leads = load_all_leads()

    if s["running"]:
        status_html = f'<div style="background:#E6F1FB;border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:13px;color:#0C447C;border:1px solid #B5D4F4;font-weight:500">{s["status"]}</div>'
    elif s["last_run"]:
        leads   = s.get("leads",[])
        hot_cnt = sum(1 for l in leads if l.get("urgencyScore",0)>=85)
        status_html = f'<div style="background:#EAF3DE;border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:13px;color:#27500A;border:1px solid #A5D6A7;font-weight:500">Scan complete &mdash; {len(leads)} new leads ({hot_cnt} hot). Last run: {s["last_run"]}</div>'
    else:
        status_html = ""

    current_leads = s.get("leads", [])
    statuses  = load_statuses()

    def urg_color(u):
        if u>=85: return "#791F1F","#FCEBEB","HOT"
        if u>=70: return "#633806","#FAEEDA","HIGH"
        return "#444441","#F1EFE8","WATCH"
    def cat_badge(c):
        c=c.lower()
        if "high-value" in c or "high value" in c: return "#0C447C","#E6F1FB"
        if "network" in c or "wireless" in c or "wlan" in c: return "#0D47A1","#E3F2FD"
        if "ev" in c or "charg" in c: return "#27500A","#EAF3DE"
        if "navigation" in c or "telematics" in c: return "#4A3000","#FFF3E0"
        if "iot" in c or "detector" in c or "smart" in c: return "#006064","#E0F7FA"
        if "medical" in c or "health" in c or "healthcare" in c: return "#791F1F","#FCEBEB"
        if "fitness" in c: return "#880E4F","#FCE4EC"
        if "imaging" in c or "vision" in c or "3d" in c: return "#4A0080","#F3E5FF"
        if "robotic" in c or "autonomous" in c: return "#37474F","#ECEFF1"
        if "large" in c or "rack" in c: return "#1B5E20","#E8F5E9"
        return "#444441","#F1EFE8"

    def lead_card(l):
        u   = l.get("urgencyScore",0)
        fc,bg,lbl = urg_color(u)
        cfc,cbg = cat_badge(l.get("category",""))
        key = l.get("name","").lower().strip()
        st  = statuses.get(key, {}).get("status","new")
        st_colors = {"contacted":"#27500A","dismissed":"#791F1F","watch":"#633806","new":"#888"}
        st_bgs    = {"contacted":"#EAF3DE","dismissed":"#FCEBEB","watch":"#FAEEDA","new":"#F1EFE8"}
        stc = st_colors.get(st,"#888"); stbg = st_bgs.get(st,"#F1EFE8")
        status_badge = "" if st=="new" else f'<span style="background:{stbg};color:{stc};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{st.upper()}</span>'
        website_btn  = f'<a href="{l.get("website","")}" target="_blank" style="padding:3px 10px;border-radius:4px;background:#0C447C;color:white;font-size:11px;text-decoration:none;margin-left:auto">Visit website</a>' if l.get("website") else ""
        return (f'<div class="lead-card" data-category="{l.get("category","").lower()}" data-signal="{l.get("signalType","").lower()}" data-status="{st}" data-urgency="{u}" data-geo="{l.get("hq","").lower()}" data-name="{l.get("name","").lower()}" style="background:white;border-radius:8px;border:1px solid #e0e0e0;border-left:4px solid {fc};padding:16px 18px;margin-bottom:10px">'
            f'<div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:8px">'
            f'<div><span style="font-size:15px;font-weight:600;color:#111">{l.get("name","")}</span><span style="font-size:12px;color:#888;margin-left:8px">{l.get("hq","")} &middot; {l.get("stage","")}</span></div>'
            f'<div style="display:flex;gap:6px;flex-wrap:wrap"><span style="background:{cbg};color:{cfc};padding:2px 10px;border-radius:4px;font-size:11px;font-weight:600">{l.get("category","")}</span><span style="background:{bg};color:{fc};padding:2px 10px;border-radius:4px;font-size:11px;font-weight:700">{lbl} {u}%</span>{status_badge}</div>'
            f'</div>'
            f'<div style="background:#f7f7f5;border-radius:6px;padding:10px 12px;margin-bottom:8px"><span style="font-size:11px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.04em">{l.get("signalType","").upper()} &middot; {l.get("signalDate","")}</span><p style="margin:4px 0 0;font-size:13px;color:#333;line-height:1.5">{l.get("signalDetail","")}</p></div>'
            f'<p style="font-size:12px;color:#555;line-height:1.5;margin:0 0 4px"><b style="color:#633806">Why reach out:</b> {l.get("whyNow","")}</p>'
            f'<p style="font-size:12px;color:#888;margin:0 0 10px;line-height:1.5">{l.get("description","")}</p>'
            + (f'<p style="font-size:12px;color:#0C447C;line-height:1.5;margin:0 0 8px;padding:8px 10px;background:#E6F1FB;border-radius:5px"><b style="color:#0C447C">Extron fit:</b> {l["extronFit"]}</p>' if l.get('extronFit') else '')
            + f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-bottom:10px">'
            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Key products</p><p style="font-size:12px;color:#333;line-height:1.4">{l["keyProducts"]}</p></div>' if l.get('keyProducts') else '')
            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Target customers</p><p style="font-size:12px;color:#333;line-height:1.4">{l["targetCustomers"]}</p></div>' if l.get('targetCustomers') else '')
            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Competitors</p><p style="font-size:12px;color:#333;line-height:1.4">{l["topCompetitors"]}</p></div>' if l.get('topCompetitors') else '')

            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Supply chain</p><p style="font-size:12px;color:#333;line-height:1.4">{l["supplyChainNotes"]}</p></div>' if l.get('supplyChainNotes') and l.get('supplyChainNotes','').lower() not in ['unknown','n/a',''] else '')
            + f'</div>'
            + f'<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">'
            f'<span style="font-size:11px;color:#888">Status:</span>'
            f'<button onclick="setStatus(\'{key}\',\'contacted\')" style="padding:3px 10px;border-radius:4px;border:1px solid #A5D6A7;background:{"#EAF3DE" if st=="contacted" else "#fff"};color:{"#27500A" if st=="contacted" else "#555"};font-size:11px;cursor:pointer">Contacted</button>'
            f'<button onclick="setStatus(\'{key}\',\'watch\')" style="padding:3px 10px;border-radius:4px;border:1px solid #FAC775;background:{"#FAEEDA" if st=="watch" else "#fff"};color:{"#633806" if st=="watch" else "#555"};font-size:11px;cursor:pointer">Watch</button>'
            f'<button onclick="setStatus(\'{key}\',\'dismissed\')" style="padding:3px 10px;border-radius:4px;border:1px solid #F7C1C1;background:{"#FCEBEB" if st=="dismissed" else "#fff"};color:{"#791F1F" if st=="dismissed" else "#555"};font-size:11px;cursor:pointer">Not a fit</button>'
            f'<button onclick="setStatus(\'{key}\',\'clear\')" style="padding:3px 10px;border-radius:4px;border:1px solid #ddd;background:#fff;color:#888;font-size:11px;cursor:pointer">Clear</button>'
            f'{website_btn}</div></div>')

    current_cards = "".join(lead_card(l) for l in sorted(current_leads, key=lambda x:-x.get("urgencyScore",0))) or '<p style="color:#999;font-size:13px;padding:16px 0">No leads from latest scan yet.</p>'

    hot_total  = sum(1 for l in all_leads if l.get("urgencyScore",0)>=85)
    new_today  = sum(1 for l in all_leads if l.get("added_date","")==date.today().isoformat())
    contacted  = sum(1 for v in statuses.values() if v.get("status")=="contacted")
    watching   = sum(1 for v in statuses.values() if v.get("status")=="watch")

    files = sorted(REPORTS_DIR.glob("*.pdf"), reverse=True)
    pdf_links = "".join(
        f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #f0f0f0"><span style="font-size:13px;color:#0C447C;font-weight:500;flex:1">{f.name}</span><span style="color:#bbb;font-size:12px">{f.stat().st_size//1024} KB</span><a href="/download/{f.name}" style="background:#0C447C;color:white;font-size:11px;padding:4px 12px;border-radius:4px;text-decoration:none">Download</a></div>'
        for f in files
    ) or '<p style="color:#999;font-size:13px">No PDF reports yet.</p>'

    articles     = s.get("filtered",[])
    article_rows = "".join(
        f'<tr><td style="padding:8px;font-size:12px"><a href="{a.get("link","#")}" target="_blank" style="color:#0C447C;text-decoration:none">{a.get("title","")[:110]}</a></td><td style="padding:8px;font-size:11px;color:#999;white-space:nowrap">{a.get("pubDate","")[:16]}</td></tr>'
        for a in articles
    ) or '<tr><td colspan="2" style="padding:20px;color:#999;font-size:13px;text-align:center">Run a scan to see articles.</td></tr>'

    ah = settings.get("auto_scan_hour","").strip()
    auto_note = f'<span style="font-size:12px;color:#27500A;margin-left:12px">&#9989; Auto-scan {ah}:00 UTC daily</span>' if ah else '<span style="font-size:12px;color:#888;margin-left:12px">Auto-scan off &mdash; <a href="/settings" style="color:#0C447C">configure</a></span>'
    last = f"Last scan: {s['last_run']}" if s["last_run"] else "No scans run yet"

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Extron Lead Intelligence</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f2f0;color:#111}}.wrap{{max-width:980px;margin:0 auto;padding:24px 16px}}.header{{background:#0C447C;color:white;padding:18px 24px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}.header h1{{font-size:18px;font-weight:600;color:white}}.nav{{display:flex;gap:8px;flex-wrap:wrap}}.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:5px}}.nav a.active{{background:rgba(255,255,255,.3);color:white;font-weight:600}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:18px}}.stat{{background:white;border-radius:8px;padding:14px;border:1px solid #e0e0e0}}.stat-num{{font-size:24px;font-weight:700;color:#0C447C}}.stat-label{{font-size:11px;color:#888;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}}.card{{background:white;border-radius:10px;padding:18px 22px;margin-bottom:14px;border:1px solid #e0e0e0}}.card h2{{font-size:14px;font-weight:600;margin:0 0 12px;color:#111}}.btn{{display:inline-block;background:#0C447C;color:white;border:none;padding:9px 20px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none}}.btn-sm{{padding:5px 12px;font-size:12px}}.btn-green{{background:#27500A}}.btn-gray{{background:#666}}.filter-bar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px;background:white;border-radius:8px;padding:10px 14px;border:1px solid #e0e0e0}}.filter-bar select,.filter-bar input{{padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px}}.scroll{{max-height:400px;overflow-y:auto;border:1px solid #eee;border-radius:6px}}table{{width:100%;border-collapse:collapse}}th{{padding:8px;text-align:left;font-size:11px;color:#666;font-weight:600;background:#f7f7f5;border-bottom:1px solid #e8e8e8}}#toast{{position:fixed;bottom:24px;right:24px;background:#1a1a2e;color:#fff;padding:10px 16px;border-radius:7px;font-size:13px;opacity:0;transition:opacity .3s;pointer-events:none;z-index:999}}</style></head>
<body><div class="wrap">
<div class="header"><div><h1>Extron Lead Intelligence</h1><p style="font-size:12px;opacity:.7;margin-top:3px">{last}</p></div>
<nav class="nav"><a href="/" class="active">Dashboard</a><a href="/history">Lead history</a><a href="/feed-health">Feed health</a><a href="/settings">Settings</a><a href="/community">Community</a></nav></div>
{status_html}
<div class="stats">
<div class="stat"><div class="stat-num">{len(all_leads)}</div><div class="stat-label">Total leads ever</div></div>
<div class="stat"><div class="stat-num">{hot_total}</div><div class="stat-label">Hot leads (85+)</div></div>
<div class="stat"><div class="stat-num">{new_today}</div><div class="stat-label">New today</div></div>
<div class="stat"><div class="stat-num">{contacted}</div><div class="stat-label">Contacted</div></div>
<div class="stat"><div class="stat-num">{watching}</div><div class="stat-label">Watching</div></div>
</div>
<div class="card"><div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:12px"><h2 style="margin:0">Run a scan</h2><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">{auto_note}<a href="/export/csv?scope=current" class="btn btn-green btn-sm">Export current CSV</a><a href="/export/csv?scope=all" class="btn btn-gray btn-sm">Export all CSV</a></div></div>
<form method="POST" action="/run"><button type="submit" class="btn {"btn-disabled" if s["running"] else ""}" {"disabled" if s["running"] else ""}>{"Scanning..." if s["running"] else "Run Scan Now"}</button></form></div>
<div class="card"><h2>Latest scan results ({len(current_leads)} leads)</h2>
<div class="filter-bar">
<select id="fCat" onchange="filterCards()"><option value="">All categories</option><option value="high-value">High-Value Electronics</option><option value="networking">Networking / Wireless</option><option value="ev charging">EV Charging</option><option value="navigation">Navigation / Telematics</option><option value="iot">IoT / Smart Detectors</option><option value="medical">Medical / Healthcare</option><option value="fitness">Fitness Technology</option><option value="imaging">3D Imaging / Vision</option><option value="robotic">Robotics / Autonomous</option><option value="large-format">Large-Format Electronics</option></select>
<select id="fSig" onchange="filterCards()"><option value="">All signals</option><option value="funding">Funding</option><option value="hiring">Hiring</option><option value="acquisition">M&A</option><option value="supply chain">Supply chain</option><option value="ceo">Leadership</option><option value="award">Award</option><option value="trade show">Trade show</option></select>
<select id="fStatus" onchange="filterCards()"><option value="">All statuses</option><option value="new">New</option><option value="contacted">Contacted</option><option value="watch">Watch</option><option value="dismissed">Not a fit</option></select>
<select id="fGeo" onchange="filterCards()"><option value="">All locations</option><option value="bay area">Bay Area</option><option value="california">California</option><option value="new york">New York</option></select>
<input type="text" id="fSearch" placeholder="Search company..." oninput="filterCards()" style="min-width:150px">
<span id="filterCount" style="font-size:12px;color:#888"></span>
</div>
<div id="leadCards">{current_cards}</div></div>
<div class="card"><h2>PDF reports ({len(files)} available)</h2><div style="max-height:200px;overflow-y:auto">{pdf_links}</div></div>
<div class="card"><h2>Articles analyzed ({len(articles)})</h2><div class="scroll"><table><thead><tr><th>Article</th><th>Date</th></tr></thead><tbody>{article_rows}</tbody></table></div></div>
</div><div id="toast"></div>
<script>
function filterCards(){{var cat=document.getElementById("fCat").value.toLowerCase(),sig=document.getElementById("fSig").value.toLowerCase(),status=document.getElementById("fStatus").value.toLowerCase(),geo=document.getElementById("fGeo").value.toLowerCase(),search=document.getElementById("fSearch").value.toLowerCase(),vis=0;document.querySelectorAll(".lead-card").forEach(function(c){{var show=(!cat||c.dataset.category.includes(cat))&&(!sig||c.dataset.signal.includes(sig))&&(!status||c.dataset.status===status)&&(!geo||c.dataset.geo.includes(geo))&&(!search||c.dataset.name.includes(search));c.style.display=show?"":"none";if(show)vis++;}});document.getElementById("filterCount").textContent=vis+" shown";}}
function setStatus(key,status){{fetch("/lead/status",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{key:key,status:status}})}}).then(function(){{toast(status==="clear"?"Status cleared":"Marked: "+status);setTimeout(function(){{location.reload();}},800);}});}}
function toast(msg){{var el=document.getElementById("toast");el.textContent=msg;el.style.opacity="1";setTimeout(function(){{el.style.opacity="0";}},2200);}}
</script></body></html>"""


def build_history_page():
    statuses  = load_statuses()
    all_leads = load_all_leads()

    def urg_color(u):
        if u>=85: return "#791F1F","#FCEBEB","HOT"
        if u>=70: return "#633806","#FAEEDA","HIGH"
        return "#444441","#F1EFE8","WATCH"
    def cat_col(c):
        c=c.lower()
        if "high-value" in c or "high value" in c: return "#0C447C","#E6F1FB"
        if "network" in c or "wireless" in c or "wlan" in c: return "#0D47A1","#E3F2FD"
        if "ev" in c or "charg" in c: return "#27500A","#EAF3DE"
        if "navigation" in c or "telematics" in c: return "#4A3000","#FFF3E0"
        if "iot" in c or "detector" in c or "smart" in c: return "#006064","#E0F7FA"
        if "medical" in c or "health" in c or "healthcare" in c: return "#791F1F","#FCEBEB"
        if "fitness" in c: return "#880E4F","#FCE4EC"
        if "imaging" in c or "vision" in c or "3d" in c: return "#4A0080","#F3E5FF"
        if "robotic" in c or "autonomous" in c: return "#37474F","#ECEFF1"
        if "large" in c or "rack" in c: return "#1B5E20","#E8F5E9"
        return "#444441","#F1EFE8"

    rows = ""
    for l in sorted(all_leads, key=lambda x: (-x.get("urgencyScore",0), x.get("added_date",""))):
        u = l.get("urgencyScore",0)
        fc,bg,lbl = urg_color(u)
        cfc,cbg   = cat_col(l.get("category",""))
        key       = l.get("name","").lower().strip()
        st        = statuses.get(key,{}).get("status","new")
        st_bgs    = {"contacted":"#EAF3DE","dismissed":"#FCEBEB","watch":"#FAEEDA","new":"#F1EFE8"}
        st_fgs    = {"contacted":"#27500A","dismissed":"#791F1F","watch":"#633806","new":"#888"}
        website_btn = f'<a href="{l.get("website","")}" target="_blank" onclick="event.stopPropagation()" style="padding:3px 10px;border-radius:4px;background:#0C447C;color:white;font-size:11px;text-decoration:none">Website</a>' if l.get("website") else ""
        rows += (f'<tr class="hist-row" data-category="{l.get("category","").lower()}" data-status="{st}" data-name="{l.get("name","").lower()}" data-signal="{l.get("signalType","").lower()}" style="border-bottom:1px solid #f0f0f0;cursor:pointer" onclick="toggleDetail(this)">'
            f'<td style="padding:10px 8px;font-size:13px;font-weight:600">{l.get("name","")}</td>'
            f'<td style="padding:10px 8px"><span style="background:{cbg};color:{cfc};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{l.get("category","")}</span></td>'
            f'<td style="padding:10px 8px;font-size:12px;color:#555">{l.get("signalType","")}</td>'
            f'<td style="padding:10px 8px"><span style="background:{bg};color:{fc};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">{lbl} {u}%</span></td>'
            f'<td style="padding:10px 8px"><span style="background:{st_bgs.get(st,"#f1efe8")};color:{st_fgs.get(st,"#888")};padding:2px 8px;border-radius:4px;font-size:11px">{st}</span></td>'
            f'<td style="padding:10px 8px;font-size:11px;color:#999">{l.get("added_date","")}</td>'
            f'<td style="padding:10px 8px;font-size:11px;color:#555">{l.get("hq","")}</td></tr>'
            f'<tr class="detail-row" style="display:none;background:#fafaf8"><td colspan="7" style="padding:14px 16px">'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px">'
            f'<div><p style="font-size:11px;font-weight:600;color:#888;margin-bottom:4px">SIGNAL</p><p style="font-size:13px;color:#333;line-height:1.5">{l.get("signalDetail","")}</p></div>'
            f'<div><p style="font-size:11px;font-weight:600;color:#888;margin-bottom:4px">WHY REACH OUT</p><p style="font-size:13px;color:#333;line-height:1.5">{l.get("whyNow","")}</p></div>'
            f'</div><div style="display:flex;gap:6px;flex-wrap:wrap">'
            f'<button onclick="event.stopPropagation();setHistStatus(\'{key}\',\'contacted\')" style="padding:3px 10px;border-radius:4px;border:1px solid #A5D6A7;background:{"#EAF3DE" if st=="contacted" else "#fff"};color:{"#27500A" if st=="contacted" else "#555"};font-size:11px;cursor:pointer">Contacted</button>'
            f'<button onclick="event.stopPropagation();setHistStatus(\'{key}\',\'watch\')" style="padding:3px 10px;border-radius:4px;border:1px solid #FAC775;background:{"#FAEEDA" if st=="watch" else "#fff"};color:{"#633806" if st=="watch" else "#555"};font-size:11px;cursor:pointer">Watch</button>'
            f'<button onclick="event.stopPropagation();setHistStatus(\'{key}\',\'dismissed\')" style="padding:3px 10px;border-radius:4px;border:1px solid #F7C1C1;background:{"#FCEBEB" if st=="dismissed" else "#fff"};color:{"#791F1F" if st=="dismissed" else "#555"};font-size:11px;cursor:pointer">Not a fit</button>'
            f'<button onclick="event.stopPropagation();setHistStatus(\'{key}\',\'clear\')" style="padding:3px 10px;border-radius:4px;border:1px solid #ddd;background:#fff;color:#888;font-size:11px;cursor:pointer">Clear</button>'
            f'{website_btn}</div></td></tr>')

    if not rows:
        rows = '<tr><td colspan="7" style="padding:32px;text-align:center;color:#999;font-size:13px">No leads yet. Run a scan first.</td></tr>'

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lead History</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f2f0;color:#111}}.wrap{{max-width:1100px;margin:0 auto;padding:24px 16px}}.header{{background:#0C447C;color:white;padding:18px 24px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}.header h1{{font-size:18px;font-weight:600;color:white}}.nav{{display:flex;gap:8px}}.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:5px}}.nav a.active{{background:rgba(255,255,255,.3);color:white;font-weight:600}}.filter-bar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px;background:white;border-radius:8px;padding:10px 14px;border:1px solid #e0e0e0}}.filter-bar select,.filter-bar input{{padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px}}.btn{{display:inline-block;background:#0C447C;color:white;border:none;padding:7px 14px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;text-decoration:none}}.btn-green{{background:#27500A}}.hist-row:hover td{{background:#fafaf8}}table{{width:100%;border-collapse:collapse}}th{{padding:9px 8px;text-align:left;font-size:11px;color:#666;font-weight:600;background:#f7f7f5;border-bottom:1px solid #e8e8e8;position:sticky;top:0}}#toast{{position:fixed;bottom:24px;right:24px;background:#1a1a2e;color:#fff;padding:10px 16px;border-radius:7px;font-size:13px;opacity:0;transition:opacity .3s;pointer-events:none;z-index:999}}</style></head>
<body><div class="wrap">
<div class="header"><div><h1>Lead History</h1><p style="font-size:12px;opacity:.7;margin-top:2px">{len(all_leads)} total leads</p></div>
<nav class="nav"><a href="/">Dashboard</a><a href="/history" class="active">Lead history</a><a href="/feed-health">Feed health</a><a href="/settings">Settings</a><a href="/community">Community</a></nav></div>
<div class="filter-bar">
<select id="hCat" onchange="filterHist()"><option value="">All categories</option><option value="high-value">High-Value Electronics</option><option value="networking">Networking / Wireless</option><option value="ev charging">EV Charging</option><option value="navigation">Navigation / Telematics</option><option value="iot">IoT / Smart Detectors</option><option value="medical">Medical / Healthcare</option><option value="fitness">Fitness Technology</option><option value="imaging">3D Imaging / Vision</option><option value="robotic">Robotics / Autonomous</option><option value="large-format">Large-Format Electronics</option></select>
<select id="hSig" onchange="filterHist()"><option value="">All signals</option><option value="funding">Funding</option><option value="hiring">Hiring</option><option value="acquisition">M&A</option><option value="supply chain">Supply chain</option><option value="ceo">Leadership</option><option value="award">Award</option></select>
<select id="hStatus" onchange="filterHist()"><option value="">All statuses</option><option value="new">New</option><option value="contacted">Contacted</option><option value="watch">Watch</option><option value="dismissed">Not a fit</option></select>
<input type="text" id="hSearch" placeholder="Search company..." oninput="filterHist()" style="min-width:160px">
<span id="hCount" style="font-size:12px;color:#888"></span>
<a href="/export/csv?scope=all" class="btn btn-green" style="margin-left:auto">Export all CSV</a>
<a href="/export/csv?scope=contacted" class="btn" style="margin-left:4px">Export contacted</a>
</div>
<div style="background:white;border-radius:10px;border:1px solid #e0e0e0;overflow:hidden"><div style="max-height:75vh;overflow-y:auto">
<table><thead><tr><th>Company</th><th>Category</th><th>Signal</th><th>Urgency</th><th>Status</th><th>Added</th><th>HQ</th></tr></thead>
<tbody id="histBody">{rows}</tbody></table></div></div>
</div><div id="toast"></div>
<script>
function filterHist(){{var cat=document.getElementById("hCat").value.toLowerCase(),sig=document.getElementById("hSig").value.toLowerCase(),status=document.getElementById("hStatus").value.toLowerCase(),search=document.getElementById("hSearch").value.toLowerCase(),vis=0;document.querySelectorAll(".hist-row").forEach(function(row){{var show=(!cat||row.dataset.category.includes(cat))&&(!sig||row.dataset.signal.includes(sig))&&(!status||row.dataset.status===status)&&(!search||row.dataset.name.includes(search));var detail=row.nextElementSibling;row.style.display=show?"":"none";if(detail&&detail.classList.contains("detail-row"))detail.style.display="none";if(show)vis++;}});document.getElementById("hCount").textContent=vis+" leads";}}
function toggleDetail(row){{var d=row.nextElementSibling;if(d&&d.classList.contains("detail-row"))d.style.display=d.style.display==="none"?"":"none";}}
function setHistStatus(key,status){{fetch("/lead/status",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{key:key,status:status}})}}).then(function(){{toast(status==="clear"?"Cleared":"Marked: "+status);setTimeout(function(){{location.reload();}},600);}});}}
function toast(msg){{var el=document.getElementById("toast");el.textContent=msg;el.style.opacity="1";setTimeout(function(){{el.style.opacity="0";}},2200);}}
filterHist();
</script></body></html>"""


def build_settings_page(saved_msg=""):
    settings  = load_settings()
    watchlist = load_watchlist()
    wl_text   = "\n".join(watchlist)
    msg_html  = f'<div style="background:#EAF3DE;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#27500A;border:1px solid #A5D6A7">{saved_msg}</div>' if saved_msg else ""
    def sel(name, val): return "selected" if settings.get(name)==val else ""

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Settings</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f2f0;color:#111}}.wrap{{max-width:700px;margin:0 auto;padding:24px 16px}}.header{{background:#0C447C;color:white;padding:18px 24px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}.header h1{{font-size:18px;font-weight:600;color:white}}.nav{{display:flex;gap:8px}}.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:5px}}.nav a.active{{background:rgba(255,255,255,.3);color:white;font-weight:600}}.card{{background:white;border-radius:10px;padding:20px 24px;margin-bottom:14px;border:1px solid #e0e0e0}}.card h2{{font-size:14px;font-weight:600;margin:0 0 14px;color:#111}}.field{{margin-bottom:14px}}label{{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;text-transform:uppercase;letter-spacing:.04em}}input,select,textarea{{width:100%;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px;font-family:inherit}}textarea{{resize:vertical;height:90px}}.help{{font-size:11px;color:#888;margin-top:4px}}.btn{{background:#0C447C;color:white;border:none;padding:10px 24px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer}}.row2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}</style></head>
<body><div class="wrap">
<div class="header"><div><h1>Settings</h1></div>
<nav class="nav"><a href="/">Dashboard</a><a href="/history">History</a><a href="/feed-health">Feed health</a><a href="/settings" class="active">Settings</a><a href="/community">Community</a></nav></div>
{msg_html}
<form method="POST" action="/settings">
<div class="card"><h2>Google Custom Search (optional)</h2>
<p style="font-size:13px;color:#555;margin-bottom:14px">Searches the entire web — not just RSS feeds. Gets 100 free searches/day. <a href="https://programmablesearchengine.google.com/" target="_blank" style="color:#0C447C">Set up here</a> (free).</p>
<div class="row2">
<div class="field"><label>Google Search API Key</label><input type="text" name="google_search_api_key" value="{settings.get('google_search_api_key','')}" placeholder="AIza..."></div>
<div class="field"><label>Search Engine ID (CX)</label><input type="text" name="google_search_cx" value="{settings.get('google_search_cx','')}" placeholder="017..."></div>
</div>
<p class="help">Leave blank to skip Google Custom Search. When filled in, the scanner searches the entire web for OEM hardware companies using 20+ targeted queries.</p>
</div>
<div class="card"><h2>Scanner behaviour</h2>
<div class="row2">
<div class="field"><label>Signal sensitivity</label><select name="sensitivity"><option value="broad" {sel("sensitivity","broad")}>Broad — more leads</option><option value="normal" {sel("sensitivity","normal")}>Normal — balanced</option><option value="tight" {sel("sensitivity","tight")}>Tight — fewer, stronger leads</option></select><p class="help">Controls AI confidence threshold.</p></div>
<div class="field"><label>Max articles sent to AI</label><input type="number" name="ai_article_cap" value="{settings['ai_article_cap']}" min="10" max="80"><p class="help">Was hardcoded at 25. Higher = better coverage, slower scan.</p></div>
</div>
<div class="row2">
<div class="field"><label>Geography filter</label><select name="geo_filter"><option value="all" {sel("geo_filter","all")}>All locations</option><option value="bay_area" {sel("geo_filter","bay_area")}>Bay Area priority</option><option value="us_only" {sel("geo_filter","us_only")}>US companies only</option></select></div>
<div class="field"><label>Auto-scan hour (UTC 0–23)</label><input type="text" name="auto_scan_hour" value="{settings['auto_scan_hour']}" placeholder="e.g. 7 = 7am UTC. Leave blank to disable."><p class="help">Scanner runs automatically every day at this hour.</p></div>
</div></div>
<div class="card"><h2>Company watchlist</h2>
<div class="field"><label>Companies to always watch</label><textarea name="watchlist" placeholder="One company per line&#10;e.g. ChargePoint&#10;Crestron">{wl_text}</textarea><p class="help">Articles mentioning these companies are always included as leads regardless of signal strength.</p></div></div>
<div class="card"><h2>Email digest (optional)</h2>
<p style="font-size:13px;color:#555;margin-bottom:14px">Sends an email with hot leads (urgency 85+) after each scan. Works with Gmail, SendGrid, or any SMTP server.</p>
<div class="field"><label>Send digest to</label><input type="email" name="email_to" value="{settings['email_to']}" placeholder="you@yourcompany.com"></div>
<div class="row2">
<div class="field"><label>SMTP host</label><input type="text" name="email_host" value="{settings['email_host']}" placeholder="smtp.gmail.com"></div>
<div class="field"><label>SMTP port</label><input type="text" name="email_port" value="{settings['email_port']}" placeholder="587"></div>
</div>
<div class="row2">
<div class="field"><label>SMTP username</label><input type="text" name="email_user" value="{settings['email_user']}" placeholder="you@gmail.com"></div>
<div class="field"><label>SMTP password</label><input type="password" name="email_pass" value="{settings['email_pass']}" placeholder="app password"></div>
</div>
<p class="help">For Gmail: use an App Password (not your main password). <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:#0C447C">Get one here</a>.</p>
</div>
<button type="submit" class="btn">Save settings</button>
</form></div></body></html>"""


def build_feed_health_page():
    health = load_feed_health()
    entries = sorted(health.values(), key=lambda x: (x.get("status","ok")!="error", x.get("status","ok")!="dead", x.get("status","ok")!="weak", -x.get("total_articles",0)))

    status_counts = {"ok":0,"weak":0,"dead":0,"error":0}
    for e in entries:
        st = e.get("status","ok")
        status_counts[st] = status_counts.get(st,0) + 1

    def st_badge(st):
        colors_map = {"ok":("OK","#EAF3DE","#27500A"), "weak":("WEAK","#FAEEDA","#633806"), "dead":("DEAD","#FCEBEB","#791F1F"), "error":("ERROR","#FCEBEB","#791F1F")}
        lbl,bg,fc = colors_map.get(st,("?","#f0f0f0","#333"))
        return f'<span style="background:{bg};color:{fc};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{lbl}</span>'

    rows = ""
    for e in entries:
        url_short = e.get("url","")[:70] + ("..." if len(e.get("url",""))>70 else "")
        hist = e.get("history",[])
        sparkline = " ".join(str(h.get("count",0)) for h in hist[-7:])
        spark_html = "".join(
            f'<span style="display:inline-block;width:10px;height:{min(24, max(3, h.get("count",0)*2))}px;background:{"#27500A" if h.get("count",0)>0 else "#F7C1C1"};border-radius:1px;margin-right:1px;vertical-align:bottom" title="{h.get("date","")} : {h.get("count",0)} articles"></span>'
            for h in hist[-7:]
        ) or "<span style='color:#bbb;font-size:11px'>no history</span>"
        rows += (f'<tr style="border-bottom:1px solid #f0f0f0">'
            f'<td style="padding:10px 8px;font-size:11px;color:#555;max-width:300px;word-break:break-all">{url_short}</td>'
            f'<td style="padding:10px 8px">{st_badge(e.get("status","ok"))}</td>'
            f'<td style="padding:10px 8px;font-size:12px;text-align:center;font-weight:600;color:#0C447C">{e.get("last_count",0)}</td>'
            f'<td style="padding:10px 8px;font-size:12px;text-align:center;color:#555">{e.get("total_articles",0)}</td>'
            f'<td style="padding:10px 8px;font-size:11px;color:#999">{e.get("last_checked","never")}</td>'
            f'<td style="padding:10px 8px">{spark_html}</td>'
            f'</tr>')

    if not rows:
        rows = '<tr><td colspan="6" style="padding:32px;text-align:center;color:#999;font-size:13px">No feed data yet — run a scan first.</td></tr>'

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Feed Health</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f2f0;color:#111}}.wrap{{max-width:1100px;margin:0 auto;padding:24px 16px}}.header{{background:#0C447C;color:white;padding:18px 24px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}.header h1{{font-size:18px;font-weight:600;color:white}}.nav{{display:flex;gap:8px}}.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:5px}}.nav a.active{{background:rgba(255,255,255,.3);color:white;font-weight:600}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px}}.stat{{background:white;border-radius:8px;padding:14px;border:1px solid #e0e0e0}}.stat-num{{font-size:24px;font-weight:700;color:#0C447C}}.stat-label{{font-size:11px;color:#888;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}}table{{width:100%;border-collapse:collapse;background:white;border-radius:10px;overflow:hidden;border:1px solid #e0e0e0}}th{{padding:9px 8px;text-align:left;font-size:11px;color:#666;font-weight:600;background:#f7f7f5;border-bottom:1px solid #e8e8e8}}</style></head>
<body><div class="wrap">
<div class="header"><div><h1>Feed Health</h1><p style="font-size:12px;opacity:.7;margin-top:2px">{len(entries)} feeds tracked</p></div>
<nav class="nav"><a href="/">Dashboard</a><a href="/history">History</a><a href="/feed-health" class="active">Feed health</a><a href="/settings">Settings</a><a href="/community">Community</a></nav></div>
<div class="stats">
<div class="stat"><div class="stat-num" style="color:#27500A">{status_counts["ok"]}</div><div class="stat-label">Healthy feeds</div></div>
<div class="stat"><div class="stat-num" style="color:#633806">{status_counts["weak"]}</div><div class="stat-label">Weak (0 last run)</div></div>
<div class="stat"><div class="stat-num" style="color:#791F1F">{status_counts["dead"]}</div><div class="stat-label">Dead (3+ zeros)</div></div>
<div class="stat"><div class="stat-num" style="color:#791F1F">{status_counts["error"]}</div><div class="stat-label">Erroring</div></div>
</div>
<p style="font-size:13px;color:#555;margin-bottom:14px;background:white;padding:12px 16px;border-radius:8px;border:1px solid #e0e0e0">
<b>How to read this:</b> Each bar in the sparkline = one scan. Green = articles found, red = zero articles. A <b>DEAD</b> feed has returned 0 articles 3+ scans in a row and should be reviewed or replaced.
</p>
<table><thead><tr><th>Feed URL</th><th>Status</th><th style="text-align:center">Last scan</th><th style="text-align:center">Total articles</th><th>Last checked</th><th>Last 7 scans</th></tr></thead>
<tbody>{rows}</tbody></table>
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        params = dict(parse_qs(parsed.query))

        if path in ["/", "/reports"]:
            self._html(build_page())
        elif path == "/history":
            self._html(build_history_page())
        elif path == "/settings":
            self._html(build_settings_page())
        elif path.startswith("/download/"):
            fname = path.replace("/download/",""); fpath = REPORTS_DIR/fname
            if fpath.exists() and fpath.suffix==".pdf":
                self.send_response(200); self.send_header("Content-Type","application/pdf")
                self.send_header("Content-Disposition",f'attachment; filename="{fname}"'); self.end_headers()
                self.wfile.write(fpath.read_bytes())
            else:
                self.send_response(404); self.end_headers(); self.wfile.write(b"Not found")
        elif path == "/export/csv":
            scope = params.get("scope",["current"])[0]
            if scope == "all":
                leads = load_all_leads()
            elif scope == "contacted":
                statuses = load_statuses()
                leads = [l for l in load_all_leads() if statuses.get(l.get("name","").lower().strip(),{}).get("status")=="contacted"]
            else:
                leads = scan_state.get("leads", [])
            csv_data = leads_to_csv(leads)
            fname = f"extron_leads_{scope}_{date.today().isoformat()}.csv"
            self.send_response(200); self.send_header("Content-Type","text/csv")
            self.send_header("Content-Disposition",f'attachment; filename="{fname}"'); self.end_headers()
            self.wfile.write(csv_data.encode())
        elif path == "/feed-health":
            self._html(build_feed_health_page())
        elif path in ["/community", "/community/"]:
            self._html(build_community_page())
        elif path.startswith("/community/export"):
            cp = dict(parse_qs(urlparse(path).query))
            mode = cp.get("mode",["flagged"])[0]
            results = _comm_load(COMM_RESULTS_FILE, [])
            if mode == "flagged": results = [r for r in results if r.get("flagged")]
            csv_data = _comm_to_csv(results)
            fname = f"extron_community_{mode}_{date.today().isoformat()}.csv"
            self.send_response(200); self.send_header("Content-Type","text/csv")
            self.send_header("Content-Disposition",f'attachment; filename="{fname}"'); self.end_headers()
            self.wfile.write(csv_data.encode())
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length).decode("utf-8","replace") if length else ""

        if path == "/run":
            if not scan_state["running"]:
                threading.Thread(target=run_scan, daemon=True).start()
            self.send_response(303); self.send_header("Location","/"); self.end_headers()
        elif path == "/community/run":
            if not comm_state["running"]:
                threading.Thread(target=run_community_scan, daemon=True).start()
            self.send_response(303); self.send_header("Location","/community"); self.end_headers()
        elif path == "/lead/status":
            try:
                data   = json.loads(body)
                key    = data.get("key","").strip()
                status = data.get("status","").strip()
                if key and status:
                    save_status(key, status)
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
        elif path == "/settings":
            try:
                form = dict(parse_qs(body))
                flat = {k: v[0] for k,v in form.items()}
                # Parse watchlist
                wl_raw = flat.pop("watchlist","")
                watchlist = [x.strip() for x in wl_raw.splitlines() if x.strip()]
                save_watchlist(watchlist)
                # Save remaining settings
                existing = load_settings()
                existing.update(flat)
                save_settings(existing)
                self._html(build_settings_page("Settings saved successfully."))
            except Exception as e:
                self._html(build_settings_page(f"Error saving: {e}"))
        else:
            self.send_response(404); self.end_headers()

    def _html(self, content):
        b = content.encode()
        self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _json(self, data):
        b = json.dumps(data).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


def start_web_server():
    port=int(os.environ.get("PORT",8080))
    server=HTTPServer(("0.0.0.0",port),Handler)
    log.info(f"Web UI on port {port}")
    server.serve_forever()

def run_scan():
    if scan_state["running"]: return
    scan_state["running"]=True
    scan_state["articles"]=[]; scan_state["filtered"]=[]; scan_state["leads"]=[]
    try:
        scan_state["status"]="Fetching articles from RSS feeds, SEC EDGAR, and Google Custom Search..."
        log.info("="*55)
        log.info("Starting Extron lead intelligence scan...")
        all_articles=[]

        # RSS feeds
        for feed in RSS_FEEDS:
            all_articles.extend(fetch_rss(feed))

        # SEC EDGAR 8-K filings — leadership changes, M&A, restructuring
        scan_state["status"] = "Scanning SEC EDGAR 8-K filings..."
        log.info("Fetching SEC EDGAR 8-K filings...")
        sec_results = fetch_sec_edgar()
        all_articles.extend(sec_results)
        log.info(f"SEC EDGAR: {len(sec_results)} filings added")

        # Google Custom Search — searches the entire web
        scan_state["status"] = "Running Google Custom Search..."
        log.info("Running Google Custom Search...")
        google_results = fetch_google_custom_search()
        all_articles.extend(google_results)
        log.info(f"Google Custom Search: {len(google_results)} results added")

        # DuckDuckGo web search — free, no API key, searches entire web
        scan_state["status"] = "Running DuckDuckGo web search..."
        log.info("Running DuckDuckGo web search...")
        ddg_results = fetch_duckduckgo()
        all_articles.extend(ddg_results)
        log.info(f"DuckDuckGo: {len(ddg_results)} results added")

        seen_set,unique=set(),[]
        for a in all_articles:
            t=a.get("title","").lower().strip()
            if t and t not in seen_set and within_90_days(a.get("pubDate","")):
                unique.append(a); seen_set.add(t)
        scan_state["articles"]=unique
        log.info(f"Unique articles within 90 days: {len(unique)}")
        # Log date distribution
        years = {}
        for a in unique:
            pd = a.get("pubDate","")
            y = pd[-4:] if pd and len(pd)>=4 else "unknown"
            try: int(y); years[y] = years.get(y,0)+1
            except: years["unknown"] = years.get("unknown",0)+1
        log.info(f"Article year distribution: {dict(sorted(years.items()))}")

        scan_state["status"]=f"Pre-filtering {len(unique)} articles..."
        filtered=prefilter(unique)
        scan_state["filtered"]=filtered

        scan_state["status"]=f"AI analyzing top {min(len(filtered), load_settings().get('ai_article_cap', 40))} articles..."
        leads=ai_filter(filtered)

        seen=load_seen()
        fresh=deduplicate(leads,seen)
        save_seen(seen)
        scan_state["leads"]=fresh

        scan_state["status"]="Generating PDF..."
        filename=REPORTS_DIR/f"Extron_Leads_{date.today().isoformat()}.pdf"
        generate_pdf(fresh,filename)

        import datetime as _dt_mod
        _utc_now = _dt_mod.datetime.now(_dt_mod.timezone.utc)
        _is_pdt = _utc_now.month > 3 and _utc_now.month < 11
        _pt_offset = _dt_mod.timedelta(hours=-7 if _is_pdt else -8)
        _pt_now = _utc_now.astimezone(_dt_mod.timezone(_pt_offset))
        scan_state["last_run"] = _pt_now.strftime("%B %d, %Y at %I:%M %p") + (" PDT" if _is_pdt else " PST")
        append_to_history(fresh)
        scan_state["status"]=f"Done — {len(fresh)} leads found."
        log.info("Scan complete.")
        # Email digest for hot leads
        try:
            hot = [l for l in fresh if l.get("urgencyScore",0) >= 85]
            if hot:
                send_email_digest(hot, fresh)
        except Exception as em:
            log.warning(f"Email digest failed: {em}")
    except Exception as e:
        scan_state["status"]=f"Error: {e}"
        log.error(f"Scan error: {e}",exc_info=True)
    finally:
        scan_state["running"]=False

if __name__=="__main__":
    log.info("Extron Scanner v8 starting...")
    threading.Thread(target=start_web_server, daemon=True).start()
    threading.Thread(target=auto_scheduler,   daemon=True).start()
    threading.Thread(target=run_scan,         daemon=True).start()
    while True:
        time.sleep(60)
