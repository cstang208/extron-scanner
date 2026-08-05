# v8
import os, re, json, time, logging, urllib.request, threading, csv, io, smtplib
from datetime import date, timedelta, datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
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
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY     = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_CX      = os.environ.get("GOOGLE_SEARCH_CX", "")
REPORTS_DIR = Path("/app/reports")
REPORTS_DIR.mkdir(exist_ok=True)

# ── Serif font registration (Frank Ruhl Libre) ─────────────────────────────
FONT_DIR = Path(__file__).parent / "fonts"
try:
    pdfmetrics.registerFont(TTFont("Serif",        str(FONT_DIR / "FrankRuhlLibre-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("Serif-Medium",  str(FONT_DIR / "FrankRuhlLibre-Medium.ttf")))
    pdfmetrics.registerFont(TTFont("Serif-Bold",    str(FONT_DIR / "FrankRuhlLibre-Bold.ttf")))
    SERIF, SERIF_MEDIUM, SERIF_BOLD = "Serif", "Serif-Medium", "Serif-Bold"
except Exception as e:
    log.warning(f"Frank Ruhl Libre fonts not found, falling back to Times-Roman: {e}")
    SERIF, SERIF_MEDIUM, SERIF_BOLD = "Times-Roman", "Times-Bold", "Times-Bold"

# ── Persistent data files (all in /app/reports — Railway keeps them on redeploy) ──
SEEN_FILE      = REPORTS_DIR / "seen_leads.json"
ALL_LEADS_FILE = REPORTS_DIR / "all_leads.json"
STATUS_FILE    = REPORTS_DIR / "lead_status.json"
SETTINGS_FILE  = REPORTS_DIR / "settings.json"
WATCHLIST_FILE      = REPORTS_DIR / "watchlist.json"
FEEDBACK_FILE       = REPORTS_DIR / "feedback.json"
CONFIDENCE_FILE     = REPORTS_DIR / "confidence_scores.json"
OPTIMIZER_FILE      = REPORTS_DIR / "optimizer_state.json"
SEEN_COMPANIES_FILE = REPORTS_DIR / "seen_companies.json"
INPUTS_FILE         = REPORTS_DIR / "inputs.json"
PRESETS_FILE        = REPORTS_DIR / "presets.json"
DAILY_LEADS_FILE    = REPORTS_DIR / "daily_leads.json"
OPTIMIZER_LOG_FILE  = REPORTS_DIR / "optimizer_log.json"
RATINGS_FILE        = REPORTS_DIR / "lead_ratings.json"
NOTES_FILE          = REPORTS_DIR / "lead_notes.json"


# ── Location, category, signal and stage constants ───────────────────────────
ALL_LOCATIONS = [
    "Alabama","Alaska","Arizona","Arkansas","California","Colorado","Connecticut",
    "Delaware","Florida","Georgia","Hawaii","Idaho","Illinois","Indiana","Iowa",
    "Kansas","Kentucky","Louisiana","Maine","Maryland","Massachusetts","Michigan",
    "Minnesota","Mississippi","Missouri","Montana","Nebraska","Nevada",
    "New Hampshire","New Jersey","New Mexico","New York","North Carolina",
    "North Dakota","Ohio","Oklahoma","Oregon","Pennsylvania","Rhode Island",
    "South Carolina","South Dakota","Tennessee","Texas","Utah","Vermont",
    "Virginia","Washington","West Virginia","Wisconsin","Wyoming","Washington D.C.",
    "Canada","Ontario","British Columbia","Quebec","Alberta","Manitoba",
    "Saskatchewan","Nova Scotia","New Brunswick","PEI","Newfoundland",
    "Mexico","United Kingdom","Germany","Israel","Japan","South Korea",
    "Taiwan","Australia","France","Netherlands","Sweden","Singapore",
]

ALL_CATEGORIES = [
    "Professional AV Hardware","Non-Invasive Medical Devices",
    "High-Value Electronics","Networking / Wireless Hardware",
    "IoT / Smart Devices","Navigation / Telematics",
    "3D Imaging / Vision Systems","Robotics / Autonomous Hardware",
    "Fitness Technology","Large-Format Electronics",
]

ALL_SIGNALS = [
    "Hiring C-Suite","Hiring Director+ Operations","Hiring Director+ Supply Chain",
    "Hiring Director+ Manufacturing","Series A Funding","Series B Funding",
    "Series C Funding","Series D Funding","Angel / Seed Funding",
    "New Facility","New Warehouse","New Office",
    "M&A — AV Hardware","M&A — Medical Device","M&A — Electronics",
    "Trade Show — InfoComm","Trade Show — ISC West","Trade Show — HIMSS",
    "Trade Show — NAB Show","Trade Show — Interop","Trade Show — Medtrade",
    "Trade Show — Arab Health","CES Award","Industry Award",
    "Bay Area Expansion","Supply Chain Onshoring","Supply Chain Change",
    "New Company / Startup","New Product Launch","Layoffs / Restructuring",
    "Market Expansion","IPO / SPAC",
]

ALL_STAGES = [
    "Startup / Pre-revenue","Early Stage","Growth Stage",
    "Established SMB","Public Company",
]

DEFAULT_INPUTS = {
    "categories":           ALL_CATEGORIES,
    "signals":              ALL_SIGNALS,
    "locations":            ["Anywhere"],
    "stages":               ALL_STAGES,
    "employee_min":         1,
    "employee_max":         1000,
    "founded_after":        2000,
    "founded_before":       2026,
    "min_unit_price":       300,
    "min_funding_amount":   3,
    "require_embedded":     True,
    "oem_only":             True,
    "confidence_threshold": 15,
    "min_urgency_score":    0,
    "sensitivity":          "normal",
    "articles_per_scan":    50,
    "lookback_days":        90,
    "prefilter_mode":       "relaxed",
    "include_keywords":     [],
    "exclude_keywords":     [],
    "scan_auto":            False,
    "scan_interval_mins":   30,
    "scan_all_day":         True,
    "scan_start_hour":      8,
    "scan_end_hour":        20,
    "active_preset":        None,
    "optimizer_enabled":    True,
}

def load_json_file(path, default):
    try:
        p = Path(path)
        if p.exists(): return json.loads(p.read_text())
    except: pass
    return default

def save_json_file(path, data):
    Path(path).write_text(json.dumps(data, indent=2))



def load_inputs():
    try:
        saved = load_json_file(INPUTS_FILE, {})
        merged = dict(DEFAULT_INPUTS)
        merged.update(saved)
        return merged
    except:
        return dict(DEFAULT_INPUTS)

def save_inputs(data):
    save_json_file(INPUTS_FILE, data)

def load_presets():
    return load_json_file(PRESETS_FILE, {})

def save_presets(data):
    save_json_file(PRESETS_FILE, data)

def load_daily_leads():
    return load_json_file(DAILY_LEADS_FILE, {"date": "", "leads": []})

def save_daily_leads(data):
    save_json_file(DAILY_LEADS_FILE, data)


def load_ratings():
    return load_json_file(RATINGS_FILE, {})

def save_ratings(data):
    save_json_file(RATINGS_FILE, data)

def get_lead_rating(key):
    """Get rating info for a lead key. Returns dict with score and ts, or None."""
    ratings = load_ratings()
    return ratings.get(key)

def load_notes():
    return load_json_file(NOTES_FILE, {})

def save_notes(data):
    save_json_file(NOTES_FILE, data)

def get_lead_note(key):
    notes = load_notes()
    return notes.get(key, "")

def load_optimizer_log():
    return load_json_file(OPTIMIZER_LOG_FILE, {"adjustments": [], "last_run": None})

def save_optimizer_log(data):
    save_json_file(OPTIMIZER_LOG_FILE, data)

def get_api_cost_estimate():
    """Estimate daily API cost based on scans run today."""
    try:
        scores = load_confidence_scores()
        today = date.today().isoformat()
        today_scans = sum(1 for s in scores if s.get("date","") == today)
        cost_per_scan = 0.03  # average estimate
        return today_scans, round(today_scans * cost_per_scan, 2)
    except:
        return 0, 0.0

def load_feedback():
    return load_json_file(FEEDBACK_FILE, {})

def save_feedback(data):
    save_json_file(FEEDBACK_FILE, data)

def record_feedback(company_key, rating):
    """rating: 'good', 'wrong_industry', 'not_our_customer'"""
    fb = load_feedback()
    fb[company_key] = {"rating": rating, "ts": datetime.now().isoformat()}
    save_feedback(fb)

def load_confidence_scores():
    return load_json_file(CONFIDENCE_FILE, [])

def save_confidence_scores(scores):
    save_json_file(CONFIDENCE_FILE, scores)

def append_confidence_score(score, leads_count, on_target, off_target):
    scores = load_confidence_scores()
    scores.append({
        "date":       date.today().isoformat(),
        "score":      score,
        "total":      leads_count,
        "on_target":  on_target,
        "off_target": off_target,
    })
    # Keep last 90 days
    scores = scores[-90:]
    save_confidence_scores(scores)

def load_seen_companies():
    return load_json_file(SEEN_COMPANIES_FILE, {})

def save_seen_companies(data):
    save_json_file(SEEN_COMPANIES_FILE, data)

def check_returning_lead(name):
    """Returns days_ago if seen before, else None."""
    seen = load_seen_companies()
    key  = name.lower().strip()
    if key in seen:
        try:
            first = date.fromisoformat(seen[key])
            return (date.today() - first).days
        except: pass
    return None

def mark_company_seen(name):
    seen = load_seen_companies()
    key  = name.lower().strip()
    if key not in seen:
        seen[key] = date.today().isoformat()
    save_seen_companies(seen)

def load_optimizer_state():
    return load_json_file(OPTIMIZER_FILE, {
        "last_optimized": None,
        "adjustments": [],
        "keyword_scores": {},
    })

def save_optimizer_state(data):
    save_json_file(OPTIMIZER_FILE, data)

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
    "https://news.google.com/rss/search?q=EV+charging+startup+series+A+B+angel+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AV+hardware+startup+series+funding+raised+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+startup+angel+seed+funding+raised+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+raises+million+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+series+C+D+funding+growth+2026&hl=en-US&gl=US&ceid=US:en",

    # Hiring signals — engineering AND operations
    "https://news.google.com/rss/search?q=medical+device+company+hiring+hardware+operations+engineers+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+hardware+company+hiring+expanding+team+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+hiring+VP+operations+supply+chain+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+startup+hiring+operations+manager+director+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medtech+AV+hardware+company+expanding+headcount+operations+2026&hl=en-US&gl=US&ceid=US:en",

    # Bay Area expansion
    "https://news.google.com/rss/search?q=hardware+company+moving+expanding+bay+area+san+francisco+silicon+valley+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+opens+office+bay+area+california+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+company+bay+area+california+expansion+headquarters+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=startup+hardware+relocating+moving+bay+area+silicon+valley+2026&hl=en-US&gl=US&ceid=US:en",

    # Awards signals
    "https://news.google.com/rss/search?q=CES+award+winner+hardware+medical+device+AV+EV+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+award+winner+expo+innovation+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+wins+award+best+product+innovation+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=CES+innovation+award+hardware+startup+2026&hl=en-US&gl=US&ceid=US:en",

    # Trade show signals
    "https://news.google.com/rss/search?q=company+exhibiting+InfoComm+Infocomm+AV+hardware+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=company+exhibiting+Interop+networking+hardware+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+exhibiting+HIMSS+Medtrade+expo+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+company+exhibiting+show+expo+conference+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+ISC+security+show+exhibiting+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AV+hardware+NAB+show+exhibiting+broadcast+2026&hl=en-US&gl=US&ceid=US:en",

    # Supply chain / onshoring signals
    "https://news.google.com/rss/search?q=hardware+company+onshoring+reshoring+US+manufacturing+supply+chain+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+supply+chain+onshoring+domestic+manufacturing+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+supply+chain+disruption+vendor+change+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=supply+chain+hardware+offshoring+onshoring+nearshoring+manufacturer+2026&hl=en-US&gl=US&ceid=US:en",

    # Leadership & M&A
    "https://news.google.com/rss/search?q=medical+device+company+new+CEO+acquisition+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medtech+layoffs+restructuring+merger+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+company+CEO+acquisition+merger+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=professional+AV+company+acquisition+CEO+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+new+CEO+acquisition+restructuring+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:prnewswire.com+hardware+medical+device+CEO+funding+award+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:businesswire.com+hardware+merger+CEO+funding+award+2026&hl=en-US&gl=US&ceid=US:en",

    # LinkedIn community discussions (via Google News index)
    "https://news.google.com/rss/search?q=supply+chain+onshoring+reshoring+hardware+linkedin+community+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=supply+chain+disruption+hardware+manufacturer+discussion+2026&hl=en-US&gl=US&ceid=US:en",

    # Additional direct industry feeds — broader coverage beyond medtech/AV
    "https://www.fierceelectronics.com/rss.xml",
    "https://www.roboticsbusinessreview.com/feed/",
    "https://www.iotworldtoday.com/feed",
    "https://www.securityinfowatch.com/rss",
    "https://www.embedded.com/feed/",
    "https://www.edn.com/feed/",

    # Robotics / Autonomous Hardware — previously had no dedicated queries
    "https://news.google.com/rss/search?q=robotics+hardware+startup+funding+series+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=autonomous+robot+hardware+company+CEO+hiring+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=robotics+company+new+facility+manufacturing+expansion+2026&hl=en-US&gl=US&ceid=US:en",

    # Fitness Technology — previously had no dedicated queries
    "https://news.google.com/rss/search?q=connected+fitness+hardware+startup+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=fitness+wearable+hardware+company+CEO+launch+2026&hl=en-US&gl=US&ceid=US:en",

    # 3D Imaging / Vision Systems — previously had no dedicated queries
    "https://news.google.com/rss/search?q=machine+vision+3D+imaging+hardware+startup+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=lidar+depth+sensor+hardware+company+funding+expansion+2026&hl=en-US&gl=US&ceid=US:en",

    # Navigation / Telematics — previously had no dedicated queries
    "https://news.google.com/rss/search?q=GPS+fleet+telematics+hardware+startup+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=navigation+device+hardware+company+CEO+hiring+expansion+2026&hl=en-US&gl=US&ceid=US:en",

    # IoT / Smart Devices — previously covered only by generic hardware terms
    "https://news.google.com/rss/search?q=IoT+smart+device+hardware+startup+series+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=industrial+IoT+sensor+hardware+company+manufacturing+2026&hl=en-US&gl=US&ceid=US:en",

    # Large-Format Electronics — previously had no dedicated queries
    "https://news.google.com/rss/search?q=large+format+display+kiosk+hardware+company+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=digital+signage+hardware+manufacturer+expansion+CEO+2026&hl=en-US&gl=US&ceid=US:en",

    # Networking / Wireless — broaden beyond current single generic angle
    "https://news.google.com/rss/search?q=networking+wireless+hardware+startup+series+funding+2026&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=SD-WAN+access+point+hardware+company+CEO+hiring+2026&hl=en-US&gl=US&ceid=US:en",
]

# ── Pre-filter keywords ───────────────────────────────────────────────────────
SIGNAL_KEYWORDS = [
    # Leadership (C-suite only)
    "chief executive","ceo","president","chief operating","coo",
    "appointed","named ceo","named president",
    # Director+ in ops/supply chain/manufacturing ONLY (not HR/Finance/Marketing)
    "director of operations","vp operations","vp of operations",
    "director of supply chain","supply chain director","vp supply chain",
    "director of manufacturing","vp manufacturing","vp of manufacturing",
    "head of operations","head of supply chain","head of manufacturing",
    "director of procurement","vp procurement","chief supply chain",
    "chief operations","svp operations","svp supply chain",
    # M&A — industry-specific
    "acqui","merger","acquires","acquired","spinoff","divest",
    # Restructuring
    "layoff","restructur","workforce","job cut","reorgani",
    # Funding — $3M+ threshold enforced in AI prompt
    "series a","series b","series c","series d",
    "seed round","angel funding","pre-seed",
    "raised","raises","funding round","million",
    "ipo","spac","went public",
    # NEW FACILITIES — new signal
    "new warehouse","new facility","new manufacturing","new distribution",
    "new office","opens facility","opens warehouse","new location",
    "expands facility","manufacturing expansion","distribution center",
    "new headquarters","relocates","new plant",
    # Bay Area expansion
    "bay area","silicon valley","san francisco","san jose","palo alto",
    # Awards
    "award","winner","wins award","innovation award",
    "ces award","ces innovation","product of the year",
    "exhibiting at","exhibit at","booth at",
    # Trade shows
    "infocomm","interop","himss","medtrade","isa security",
    "nab show","ces 2026","isc west","arab health",
    # Supply chain
    "onshoring","reshoring","nearshoring","offshoring",
    "supply chain","domestic manufacturing","us manufacturing",
    "vendor","supplier","procurement",
    # Embedded computing — new required qualifier
    "microprocessor","microcontroller","embedded","cpu","fpga","soc",
    "system on chip","embedded system","firmware","edge computing",
    # New companies
    "startup","founded","new company","spun out",
    "y combinator","techstars","incubat","accelerat",
    # Expansion
    "expand","expansion","new market","launch","launches","launched",
    "partner","partnership","alliance",
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
    # Invasive medical
    "surgical robot","implant","implantable","catheter","stent","pacemaker",
    "cochlear","insulin pump","intravascular","endoscopic","laparoscopic",
    "orthopedic implant","spinal","joint replacement","intraocular",
    "invasive","cardiac implant","deep brain","neurostimulator","percutaneous",
    # Vehicles & automotive (not EV charging hardware)
    "automobile","automotive","self-driving car","autonomous vehicle",
    "tesla","rivian","lucid motors","fleet vehicle","car company",
    # Military & government
    "military","defense contractor","department of defense","darpa","pentagon",
    "weapons system","missile","government contract","federal contract",
]

# Non-OEM business types to exclude
NON_OEM_EXCLUSIONS = [
    "reseller","distributor","value-added reseller"," var ",
    "system integrator","contract manufacturer","ems provider",
    "electronics manufacturing service","third-party logistics","3pl",
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


# ── SEC EDGAR ──────────────────────────────────────────────────────────────────
SEC_QUERIES = [
    '"appointed" "Chief Executive Officer"',
    '"named" "President and Chief Executive"',
    '"Chief Operating Officer" "appointed"',
    '"merger agreement" "acquisition"',
    '"definitive agreement to acquire"',
    '"workforce reduction" "restructuring"',
    '"new facility" "manufacturing"',
    '"warehouse" "distribution center" "opened"',
]

def fetch_sec_edgar():
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
            for item in re.findall(r"<entry>(.*?)</entry>", raw, re.DOTALL) or re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
                def tag(t):
                    m = re.search(fr"<{t}[^>]*>(.*?)</{t}>", item, re.DOTALL)
                    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
                title = tag("title") or tag("company-name")
                link  = tag("link") or "https://www.sec.gov"
                pub   = tag("updated") or tag("published") or ""
                if title:
                    results.append({"title": f"SEC 8-K: {title}", "link": link,
                                    "summary": f"SEC 8-K filing: {title}", "pubDate": pub[:10], "source": "SEC EDGAR"})
            time.sleep(0.5)
        except Exception as e:
            log.warning(f"SEC EDGAR error: {e}")
    log.info(f"SEC EDGAR: {len(results)} filings")
    return results

# ── Google Custom Search ────────────────────────────────────────────────────────
GOOGLE_SEARCH_QUERIES = [
    '"series A" OR "series B" OR "series C" OEM hardware manufacturer 2026',
    '"raised" "$" million hardware OEM medical device embedded 2026',
    '"new CEO" OR "appointed CEO" hardware OEM manufacturer 2026',
    '"COO" OR "Chief Operating Officer" hardware OEM appointed 2026',
    '"director of operations" OR "VP operations" hardware OEM 2026',
    '"new warehouse" OR "new facility" hardware OEM manufacturer 2026',
    '"bay area" OR "silicon valley" hardware OEM manufacturer expanding 2026',
    '"InfoComm 2026" hardware OEM exhibiting',
    '"ISC West 2026" hardware OEM exhibiting',
    '"HIMSS 2026" medical device OEM exhibiting',
    '"CES 2026" innovation award hardware OEM embedded',
    '"reshoring" OR "onshoring" hardware OEM manufacturer 2026',
    '"acquisition" hardware OEM medical device AV electronics 2026',
    '"FDA clearance" medical device OEM embedded processor 2026',
    '"microprocessor" OR "embedded" OEM hardware startup funding 2026',
]

def fetch_google_custom_search():
    api_key = GOOGLE_SEARCH_API_KEY or load_settings().get("google_search_api_key","").strip()
    cx      = GOOGLE_SEARCH_CX      or load_settings().get("google_search_cx","").strip()
    if not api_key or not cx:
        log.info("Google Custom Search skipped — not configured")
        return []
    results = []
    base = "https://www.googleapis.com/customsearch/v1"
    for q in GOOGLE_SEARCH_QUERIES:
        try:
            url = f"{base}?key={api_key}&cx={cx}&q={urllib.request.quote(q)}&num=10&dateRestrict=m3"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            for item in data.get("items", []):
                results.append({"title": item.get("title",""), "link": item.get("link",""),
                                 "summary": item.get("snippet",""), "pubDate": "", "source": item.get("displayLink","Google")})
            time.sleep(0.2)
        except Exception as e:
            log.warning(f"Google Search error: {e}")
    log.info(f"Google Custom Search: {len(results)} results")
    return results

# ── DuckDuckGo ─────────────────────────────────────────────────────────────────
DUCKDUCKGO_QUERIES = [
    "OEM hardware manufacturer series A B funding embedded 2026",
    "medical device OEM startup funding raised microprocessor 2026",
    "electronics hardware OEM new CEO COO appointed 2026",
    "hardware OEM new warehouse facility manufacturing 2026",
    "hardware OEM director operations supply chain hired 2026",
    "medical device OEM hiring director supply chain operations 2026",
    "hardware OEM bay area silicon valley expansion 2026",
    "OEM electronics manufacturer CES award embedded 2026",
    "hardware OEM InfoComm ISC West HIMSS exhibiting 2026",
    "OEM hardware manufacturer onshoring reshoring supply chain 2026",
    "electronics OEM acquisition merger AV medical 2026",
    "OEM hardware startup launched founded embedded CPU 2026",
    "medical device OEM FDA clearance launch embedded 2026",
    "IoT smart device OEM microcontroller FPGA funding 2026",
    "robotics hardware OEM embedded computing startup 2026",
]


# Full pool of DuckDuckGo queries — rotated each scan
DUCKDUCKGO_QUERY_POOL = [
    "OEM hardware manufacturer series A B funding embedded 2026",
    "medical device OEM startup funding raised microprocessor 2026",
    "electronics hardware OEM new CEO COO appointed 2026",
    "hardware OEM new warehouse facility manufacturing 2026",
    "hardware OEM director operations supply chain hired 2026",
    "medical device OEM hiring director supply chain operations 2026",
    "hardware OEM bay area silicon valley expansion 2026",
    "OEM electronics manufacturer CES award embedded 2026",
    "hardware OEM InfoComm ISC West HIMSS exhibiting 2026",
    "OEM hardware manufacturer onshoring reshoring supply chain 2026",
    "electronics OEM acquisition merger AV medical 2026",
    "OEM hardware startup launched founded embedded CPU 2026",
    "medical device OEM FDA clearance launch embedded 2026",
    "IoT smart device OEM microcontroller FPGA funding 2026",
    "robotics hardware OEM embedded computing startup 2026",
    "networking wireless hardware OEM funding CEO 2026",
    "fitness technology hardware OEM funding launch 2026",
    "navigation telematics hardware OEM funding 2026",
    "3D imaging vision hardware OEM startup funding 2026",
    "large format display hardware OEM acquisition funding 2026",
    "AV hardware OEM series B C funding raised 2026",
    "medical wearable OEM startup angel seed round 2026",
    "industrial electronics OEM new COO president appointed 2026",
    "hardware OEM new distribution center opens 2026",
    "electronics OEM VP supply chain director hired 2026",
    "medical device OEM new office California bay area 2026",
    "AV hardware OEM NAB show exhibiting broadcast 2026",
    "hardware OEM ISC west security show exhibiting 2026",
    "electronics OEM product launch embedded computing 2026",
    "medical device OEM layoffs restructuring vendor change 2026",
    "hardware startup OEM y combinator techstars embedded 2026",
    "AV hardware OEM acquisition merger 2026 company",
    "medical device OEM supply chain onshoring domestic 2026",
    "electronics hardware OEM series C D growth expansion 2026",
    "hardware OEM new manufacturing plant US production 2026",
    "medical device OEM HIMSS medtrade exhibiting 2026",
    "networking hardware OEM new CEO merger acquisition 2026",
    "IoT hardware OEM bay area silicon valley funding 2026",
    "robotics OEM director operations supply chain 2026",
    "fitness hardware OEM series A funding microcontroller 2026",
    "AV hardware OEM digital signage videoconferencing funding 2026",
    "medical OEM patient monitoring wearable startup raised 2026",
    "electronics OEM ruggedized industrial embedded FPGA funding 2026",
    "hardware OEM interop networking show exhibiting 2026",
    "medical device OEM FDA 510k clearance launch embedded 2026",
    "AV hardware OEM infocomm exhibiting signal processor 2026",
    "electronics OEM new president COO supply chain director 2026",
    "hardware OEM tariff onshoring reshoring vendor switch 2026",
    "medical device OEM acquisition merger diagnostics imaging 2026",
    "hardware OEM new facility warehouse Texas California 2026",
    "AV electronics OEM ces innovation award winner 2026",
    "medical hardware OEM telehealth biosensor funding series 2026",
    "electronics OEM test measurement equipment funding 2026",
    "hardware OEM smart device microprocessor startup funding 2026",
    "AV hardware OEM broadcast equipment new CEO appointed 2026",
    "medical device OEM point of care diagnostic startup 2026",
    "electronics OEM power supply industrial embedded funding 2026",
    "hardware OEM navigation GPS telematics funding CEO 2026",
    "medical device OEM ECG EEG monitor startup series 2026",
    "AV hardware OEM large format display kiosk funding 2026",
]

import random as _random
def get_ddg_queries(n=20):
    pool = list(DUCKDUCKGO_QUERY_POOL)
    _random.shuffle(pool)
    return pool[:n]

def fetch_duckduckgo():
    results = []
    for query in get_ddg_queries(20):
        try:
            url = f"https://html.duckduckgo.com/html/?q={urllib.request.quote(query)}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read().decode("utf-8", errors="replace")
            found = 0
            for m in re.finditer(r'class="result__a"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>.*?class="result__snippet"[^>]*>([^<]*)</a>', raw, re.DOTALL):
                link, title, snippet = m.group(1), m.group(2).strip(), m.group(3).strip()
                if link.startswith("http") and title:
                    results.append({"title": title, "link": link, "summary": snippet, "pubDate": "", "source": "DuckDuckGo"})
                    found += 1
                if found >= 5: break
            time.sleep(1.5)
        except Exception as e:
            log.warning(f"DuckDuckGo error: {e}")
    log.info(f"DuckDuckGo: {len(results)} results")
    return results

def within_lookback(pub_date_str, lookback_days=90):
    if not pub_date_str:
        return True
    cutoff = date.today() - timedelta(days=lookback_days)
    for fmt in ["%a, %d %b %Y", "%Y-%m-%d", "%d %b %Y"]:
        try:
            parts = pub_date_str.strip().split()
            d = time.strptime(" ".join(parts[:4]), fmt + " %H:%M:%S") if len(parts) > 3 else time.strptime(" ".join(parts[:3]), fmt)
            return date(d.tm_year, d.tm_mon, d.tm_mday) >= cutoff
        except:
            continue
    return True

# Kept as an alias so any other code still calling the old name keeps working.
def within_90_days(pub_date_str):
    return within_lookback(pub_date_str, 90)

def prefilter(articles, inputs=None):
    if inputs is None:
        inputs = load_inputs()
    mode       = inputs.get("prefilter_mode", "strict")
    include_kw = [k.lower().strip() for k in inputs.get("include_keywords", []) if k.strip()]
    exclude_kw = [k.lower().strip() for k in inputs.get("exclude_keywords", []) if k.strip()]

    kept = []
    excluded_invasive = 0
    excluded_large    = 0
    for a in articles:
        text = (a.get("title","") + " " + a.get("summary","")).lower()

        # Force exclude keywords
        if exclude_kw and any(k in text for k in exclude_kw):
            continue

        # Force include keywords override everything else
        if include_kw and any(k in text for k in include_kw):
            kept.append(a)
            continue

        # Exclusion filters always apply
        if any(k in text for k in NON_OEM_EXCLUSIONS):
            continue
        if any(k in text for k in INVASIVE_EXCLUSIONS):
            excluded_invasive += 1
            continue
        if any(k in text for k in LARGE_COMPANY_EXCLUSIONS):
            excluded_large += 1
            continue

        has_hardware = any(k in text for k in HARDWARE_KEYWORDS)
        has_signal   = any(k in text for k in SIGNAL_KEYWORDS)

        if mode == "off":
            kept.append(a)
        elif mode == "relaxed":
            if has_hardware or has_signal:
                kept.append(a)
        else:  # strict (default)
            if has_hardware and has_signal:
                kept.append(a)

    log.info(f"Pre-filter ({mode}): {len(kept)} kept, {excluded_invasive} invasive excluded, {excluded_large} large co. excluded")
    return kept

def ai_filter(articles, inputs=None):
    # Always call Claude even with 0 articles — Job 2 supplements from Claude knowledge
    if inputs is None:
        inputs = load_inputs()
    settings    = load_settings()
    cap         = int(inputs.get("articles_per_scan", settings.get("ai_article_cap", 40)))
    sensitivity = inputs.get("sensitivity", settings.get("sensitivity", "normal"))

    # Pull all inputs-defined criteria
    sel_cats    = inputs.get("categories",   ALL_CATEGORIES)
    sel_signals = inputs.get("signals",      ALL_SIGNALS)
    sel_locs    = inputs.get("locations",    ["Anywhere"])
    sel_stages  = inputs.get("stages",       ALL_STAGES)
    emp_min     = int(inputs.get("employee_min",    1))
    emp_max     = int(inputs.get("employee_max",    1000))
    founded_after  = int(inputs.get("founded_after",  2000))
    founded_before = int(inputs.get("founded_before", 2026))
    min_price   = int(inputs.get("min_unit_price",   300))
    min_funding = float(inputs.get("min_funding_amount", 3))
    require_emb = inputs.get("require_embedded", True)
    conf_threshold = int(inputs.get("confidence_threshold", 30))

    # Watchlist: any article mentioning a watchlist company gets priority-boosted
    watchlist = [w.lower().strip() for w in load_watchlist() if w.strip()]
    def watchlist_match(a):
        txt = (a.get("title","") + " " + a.get("summary","") + " " + a.get("full_text","")).lower()
        return any(w in txt for w in watchlist)

    priority = [a for a in articles if watchlist_match(a)]
    rest     = [a for a in articles if not watchlist_match(a)]
    top      = (priority + rest)[:cap]

    # Sensitivity note
    if sensitivity == "broad":
        sensitivity_note = f"Be generous — include partial matches. Aim for 15+ leads. Confidence >= {conf_threshold}."
    elif sensitivity == "tight":
        sensitivity_note = f"Be strict — only very clear signals. Quality over quantity. Confidence >= {conf_threshold}."
    else:
        sensitivity_note = f"Balance quality and quantity. Aim for 8-12 solid leads. Confidence >= {conf_threshold}."

    # Geography note
    if "Anywhere" in sel_locs or not sel_locs:
        geo_note = "Include companies from anywhere in the world."
    else:
        loc_str = ", ".join(sel_locs[:20])
        geo_note = f"ONLY include companies located in: {loc_str}."

    # Dynamic inputs note for prompt
    cats_str    = ", ".join(sel_cats)    if sel_cats    else "all categories"
    signals_str = ", ".join(sel_signals[:15]) if sel_signals else "all signals"
    stages_str  = ", ".join(sel_stages)  if sel_stages  else "all stages"
    inputs_note = f"""
ACTIVE SCAN CONFIGURATION (follow these strictly):
- Target categories: {cats_str}
- Target signals: {signals_str}
- Target locations: {geo_note}
- Target stages: {stages_str}
- Employee range: {emp_min} to {emp_max}
- Founded between: {founded_after} and {founded_before}
- Minimum unit price: ${min_price}+
- Minimum funding amount: ${min_funding}M+
- Embedded computing required: {"Yes" if require_emb else "No"}
- Confidence threshold: {conf_threshold}
"""

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
    prompt = f"""You are a B2B sales intelligence analyst for Extron Inc (last-mile manufacturing and supply chain services company based in Fremont, CA).

You have TWO jobs:

JOB 1 — EXTRACT from the articles below: Find qualifying OEM hardware companies showing trigger signals.

JOB 2 — SUPPLEMENT with search-verified companies: This job is MANDATORY, not optional — always run it regardless of how many candidates Job 1 found, including if Job 1 found zero. Think of 5-8 real companies from your knowledge that plausibly fit Extron's profile. For EACH one, use the web_search tool to find a real, current news article or press release confirming the specific signal you'd cite for that company (funding round, new hire, facility opening, etc.) — do not rely on memory alone. Only include a company in your final results if your search actually finds a genuine supporting article. IMPORTANT — after a search returns a result, copy the exact "url" field from that search result character-for-character into the sourceUrl field of your JSON output for that company. Do not paraphrase, shorten, reconstruct, or approximate the URL — copy it exactly as the search result gave it to you. If you search for a company and cannot find a supporting article, drop that company entirely rather than including it with a guessed or empty source — it is better to return fewer, fully-verified companies than any unverified one. Do not fabricate or reuse a company's own homepage as a substitute source.

═══════════════════════════════════════════════════
QUALIFYING COMPANY CRITERIA — all must be true:
═══════════════════════════════════════════════════

1. OEM ONLY: Company designs and sells its OWN branded physical hardware products.
   EXCLUDE: resellers, distributors, VARs, system integrators, contract manufacturers.

2. EMBEDDED COMPUTING REQUIRED: The product must contain a CPU, microprocessor,
   microcontroller, FPGA, SoC, or similar embedded computing component.
   This is non-negotiable — passive hardware with no processor does not qualify.

3. SIZE: Small to mid-size only. Under ~1,000 employees. No Fortune 500.

4. PRICE: Hardware sells for $300+ per unit minimum.

5. CATEGORY — must be in one of these:
   - Professional AV Hardware (displays, projectors, switchers, signal processors,
     AV control systems, digital signage, videoconferencing hardware, broadcast equipment)
   - Non-Invasive Medical Devices (diagnostic equipment, patient monitoring,
     wearables, biosensors, telehealth hardware, ECG/EEG monitors, imaging accessories)
   - High-Value Electronics (industrial electronics, test & measurement, power electronics,
     embedded systems, precision instruments, ruggedized hardware)
   - Networking / Wireless Hardware (routers, access points, SD-WAN hardware, mesh networks)
   - IoT / Smart Devices (connected sensors, smart detectors, industrial IoT)
   - Navigation / Telematics (GPS hardware, fleet tracking, telematics devices)
   - 3D Imaging / Vision Systems (machine vision cameras, LiDAR, depth sensors)
   - Robotics / Autonomous Hardware (robotic systems, autonomous devices)
   - Fitness Technology (connected fitness hardware, wearable fitness devices)
   - Large-Format Electronics (large displays, digital kiosks, interactive boards)

═══════════════════════════════════════════════════
TRIGGER SIGNALS — company must show at least one:
═══════════════════════════════════════════════════

HIRING (tightened — specific titles only):
- New CEO, President, or COO appointed
- Director-level or above hired in: Operations, Supply Chain, Manufacturing, Procurement
- EXCLUDE: HR, Finance, Marketing, Sales hires — these do NOT qualify

FUNDING ($3M minimum):
- Series A, B, C, or D funding of $3M or more
- Angel or seed round of $3M or more
- IPO or SPAC
- EXCLUDE: Funding rounds below $3M — too early to be a real customer

NEW FACILITIES (new signal — high priority):
- Opening new warehouse, distribution center, or manufacturing facility
- New office location or headquarters
- Expanding existing facility significantly

M&A (industry-specific only):
- Merger or acquisition involving AV hardware, medical devices, or electronics OEMs
- EXCLUDE: M&A outside these industries

OTHER SIGNALS:
- Exhibiting at InfoComm, ISC West, HIMSS, NAB Show, Interop, Medtrade, Arab Health
- Won CES Innovation Award or major industry award
- Moving to or expanding in the Bay Area / Silicon Valley
- Supply chain onshoring, reshoring, or vendor change
- Layoffs or restructuring (signals vendor renegotiation opportunity)
- New product launch (especially embedded hardware)
- New company or startup founded in last 3 years

═══════════════════════════════════════════════════
HARD EXCLUSIONS:
═══════════════════════════════════════════════════
- Invasive medical devices (implants, catheters, stents, pacemakers, surgical robots)
- Automobiles, EVs as vehicles, automotive companies
- Military, defense, government contractors
- Large enterprises / Fortune 500
- Pure software companies
- Non-OEM companies (resellers, distributors, contract manufacturers)
- Funding rounds under $3M
- Hiring signals that are NOT C-suite or Director+ in ops/supply chain/manufacturing

{sensitivity_note}
{inputs_note}
{watchlist_note}

═══════════════════════════════════════════════════
CONFIDENCE SCORING INSTRUCTIONS:
═══════════════════════════════════════════════════
For each lead, assign a confidenceScore (0-100) based on:
- 90-100: Perfect fit — clear OEM, embedded hardware, strong verified signal, right industry
- 70-89: Strong fit — good OEM match, signal is clear, industry is right
- 50-69: Moderate fit — probably qualifies but some uncertainty
- 30-49: Weak fit — might qualify, signal is weak or industry is borderline
- Below 30: Do not include

Also assign isOnTarget: true if this is clearly in Extron's industry space, false if borderline.
This is used to calculate the daily confidence score for the scanner.

Return ONLY a raw JSON array. No markdown. No backticks. Start with [ end with ].
Aim for 8-14 leads. Include anything with confidenceScore >= {conf_threshold}. Be GENEROUS — it is better to include borderline companies than miss good ones. If in doubt, include it.
IMPORTANT: Never omit a real, qualifying company just because you're unsure you can write extensive detail for every field. Write as much as you confidently know — if that's only 1-2 sentences for a field instead of the target length, that's fine; write "Limited public information available" for a field rather than leaving a company out entirely. Including a real qualifying company with shorter-than-ideal detail is always better than returning fewer leads or none. Do not return an empty array if any plausible candidates exist in the articles or your own knowledge.

Each object must have ALL fields:
- name: company name
- category: one of the 10 categories above
- isOEM: true (must be true to qualify)
- hasEmbeddedComputing: true (must be true — does product contain CPU/microprocessor/MCU/FPGA/SoC?)
- embeddedComputingNote: brief description of the embedded component e.g. "ARM Cortex-M microcontroller" or "custom FPGA signal processor"
- isOnTarget: true/false — is this clearly in Extron's industry space?
- stage: "Startup / Pre-revenue" or "Early Stage" or "Growth Stage" or "Established SMB" or "Public Company"
- hq: city, state
- founded: year as integer or null
- ticker: stock ticker or null
- website: company website URL or null
- unitPrice: estimated price per unit or "Early stage / TBD"
- employees: estimated count e.g. "50-200" or "Unknown"
- keyProducts: 2-3 full sentences describing their main hardware products in detail — specs, what it does, how it's used
- targetCustomers: 2-3 sentences on who buys their hardware and why
- topCompetitors: 3-4 direct competitors, each with a short note on how this company differs from them
- supplyChainNotes: 2-3 sentences on what is known about their manufacturing, assembly, and supply chain today
- companyBackground: 4-6 sentences of company history and context: founding story, funding history to date, leadership, notable milestones. This is often the reader's first exposure to the company, so give them the full picture.
- description: 5-7 sentences covering what they make, how the product works, what problem it solves, market size, competitive differentiation
- productFit: 4-5 sentences on WHY this specific product fits Extron Inc — product type (embedded hardware, non-invasive medical device, etc.), last-mile handling needs, configuration requirements, return/repair cycles, demo unit relevance. Be specific to this company's actual product, not generic.
- signalType: one of "Series A Funding", "Series B Funding", "Series C Funding", "Series D Funding", "Angel / Seed Funding", "Hiring C-Suite", "Hiring Director+ Operations", "Hiring Director+ Supply Chain", "Hiring Director+ Manufacturing", "New Facility", "New Warehouse", "New Office", "M&A — AV Hardware", "M&A — Medical Device", "M&A — Electronics", "Layoffs / Restructuring", "Trade Show — InfoComm", "Trade Show — ISC West", "Trade Show — HIMSS", "Trade Show — NAB Show", "Trade Show — Other", "CES Award", "Industry Award", "Bay Area Expansion", "Supply Chain Onshoring", "Supply Chain Change", "New Product Launch", "New Company / Startup", "IPO / SPAC", "Market Expansion"
- signalDate: e.g. "March 2026" or "Q1 2026"
- signalDetail: 4-6 factual sentences about exactly what happened, who was involved, dollar amounts / dates if known, what it means for the company's near-term plans
- recentDevelopments: 3-5 sentences on what else has happened recently besides the main signal — other news, product launches, hires, partnerships in the last 6-12 months. If nothing else is known, say so explicitly rather than inventing filler. If genuinely nothing else is known, leave this as an empty string rather than padding it.
- whyNow: 3-4 sentences on why this specific moment is ideal for Extron to reach out
- suggestedContact: Best person to contact based on the signal — name if known, otherwise title e.g. "VP of Operations" or "John Smith, newly appointed COO"
- suggestedContactLinkedIn: LinkedIn URL if known, otherwise null
- extronFit: 3-4 sentences on how Extron Inc's specific services (last-mile manufacturing, assembly & test, fulfillment, returns management, demo-unit programs) address this company's current needs. Do not repeat what productFit already said — this should add new points, not restate them.
- source: publication name
- sourceUrl: the URL of the actual news article/press release this signal came from. NEVER put the company's own homepage or product page here — if you don't have a genuine news article to cite (e.g. for a Job 2 knowledge-supplement company), leave this as null rather than substituting the company website.
- urgencyScore: 0-100
- confidenceScore: 0-100

Articles to analyze:
{text}"""

    try:
        # Streaming is required here — with max_tokens this high (32000), the
        # SDK refuses a non-streaming request outright because it could
        # plausibly run past 10 minutes, raising ValueError before any tokens
        # are even generated. That was the actual cause of every scan coming
        # back with zero leads, regardless of prompt content.
        #
        # web_search tool: lets Job 2 actually verify companies against real
        # articles instead of relying on training-data recall. max_uses caps
        # it at roughly 2-3 searches per Job 2 candidate.
        with client.messages.stream(
            model="claude-sonnet-5", max_tokens=32000,
            system="Return ONLY a raw JSON array. No markdown. No backticks. Start with [ and end with ].",
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 20}],
        ) as stream:
            resp = stream.get_final_message()
        # Find the actual text block, not just content[0] — the response can
        # include a ThinkingBlock (no .text attribute) before the real answer.
        raw = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
        stop_reason = getattr(resp, "stop_reason", None)
        log.info(f"Raw AI response (first 300 chars): {raw[:300]}  [stop_reason={stop_reason}]")

        # Pull every real URL the web search tool actually found, straight from
        # the API's own structured search-result blocks — not from whatever the
        # model chose to type into its JSON. This exists because the model can
        # search successfully, decide a company is verified, and then still fail
        # to correctly copy that exact URL into the sourceUrl field it writes —
        # that transcription step is model output, not guaranteed. Any lead
        # that comes back without a valid sourceUrl gets backfilled from this
        # pool, so "the search worked" and "the link shows up" aren't two
        # separate things that can independently fail.
        found_urls = []
        for b in resp.content:
            if getattr(b, "type", None) == "web_search_tool_result":
                b_content = getattr(b, "content", None)
                if isinstance(b_content, list):
                    for r in b_content:
                        u = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None)
                        if u:
                            found_urls.append(u)
        log.info(f"web_search returned {len(found_urls)} real result URL(s) this call.")

        # Robust JSON extraction
        raw = raw.replace("```json","").replace("```","").strip()
        # Find outermost array
        start = raw.find("[")
        end = raw.rfind("]")
        leads = None
        if start != -1 and end != -1:
            try:
                leads = json.loads(raw[start:end+1])
            except json.JSONDecodeError:
                leads = None

        if leads is None:
            # Full parse failed — most likely the response got cut off mid-object
            # because it hit max_tokens before finishing (stop_reason == "max_tokens").
            # Rather than losing every lead in the batch, salvage whichever complete
            # {...} objects appeared before the cutoff.
            if stop_reason == "max_tokens":
                log.warning("AI response was truncated by max_tokens — attempting to salvage complete leads instead of discarding the whole batch.")
            leads = []
            if start != -1:
                depth = 0
                obj_start = None
                for i, ch in enumerate(raw[start:], start=start):
                    if ch == "{":
                        if depth == 0:
                            obj_start = i
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0 and obj_start is not None:
                            try:
                                leads.append(json.loads(raw[obj_start:i+1]))
                            except json.JSONDecodeError:
                                pass
                            obj_start = None
            if leads:
                log.info(f"Salvaged {len(leads)} complete lead(s) from a truncated/malformed response.")
            else:
                log.error(f"No JSON found or salvageable in response: {raw[:500]}")
                return []

        if not isinstance(leads, list):
            leads = [leads]

        # Load dismissed companies to filter them out (feedback loop)
        dismissed = {k for k,v in load_statuses().items() if v.get("status") == "dismissed"}

        validated = []
        _unused_urls = list(found_urls)  # each backfilled URL is used at most once
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
            conf  = lead.get("confidenceScore", lead.get("confidence", 50))
            founded = lead.get("founded")
            signal_detail = lead.get("signalDetail","")
            source_url = (lead.get("sourceUrl") or "").strip()

            # Backfill from real search results if the model found a source but
            # didn't correctly copy the URL into its own JSON output. Best-effort
            # (not guaranteed to be the exact right article for this company),
            # so it's flagged rather than treated as a confirmed exact match.
            if not source_url.startswith("http") and _unused_urls:
                source_url = _unused_urls.pop(0)
                lead["sourceUrl"] = source_url
                flags.append("source link auto-matched from search results — verify before using")

            # Flag suspiciously round/vague numbers
            if emp in ["unknown","n/a","","varies"] or not emp:
                flags.append("employee count unknown")
            if not price or price in ("tbd","early stage","early stage / tbd","unknown","n/a"):
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

        # Guaranteed emergency fallback: if the primary pass came back thin for
        # any reason (weak articles, model caution, anything upstream), make a
        # second, deliberately simple, article-independent call. This one asks
        # purely from Claude's general knowledge with a hard minimum count and
        # much lighter per-field requirements, so it can't be starved by the
        # same things that might have limited the primary pass.
        MIN_LEADS_TARGET = 5
        if len(validated) < MIN_LEADS_TARGET:
            log.warning(f"Only {len(validated)} leads after primary pass — running guaranteed knowledge-only fallback to reach at least {MIN_LEADS_TARGET}.")
            try:
                fallback_prompt = f"""You are a B2B sales intelligence analyst for Extron Inc (last-mile manufacturing and supply chain services company based in Fremont, CA, serving OEM hardware companies).

Think of {MIN_LEADS_TARGET + 6} REAL companies from your own knowledge that are small-to-mid-size OEM hardware makers (under ~1,000 employees) selling their own branded physical hardware products with embedded computing (CPU/microcontroller/FPGA/SoC), priced $300+ per unit, in one of: Professional AV Hardware, Non-Invasive Medical Devices, High-Value Electronics, Networking/Wireless Hardware, IoT/Smart Devices, Navigation/Telematics, 3D Imaging/Vision Systems, Robotics/Autonomous Hardware, Fitness Technology, Large-Format Electronics.

For EACH candidate, use the web_search tool to find a real, current news article or press release confirming a specific recent signal for that company (funding, hiring, new facility, product launch, etc.). Only keep a company in your final results if search actually finds a genuine supporting article — use the real URL from that search result as sourceUrl. Drop any company you cannot verify this way, even if it means returning fewer than {MIN_LEADS_TARGET}. Do not use a company's own homepage as a substitute source, and do not leave sourceUrl null or guessed.

Keep each field brief — 1-2 sentences is fine, do not omit a real, verified company just because you can't write extensively about it.

Return ONLY a raw JSON array, no markdown. Each object needs: name, category, isOEM (true), hasEmbeddedComputing (true), embeddedComputingNote, isOnTarget (true), stage, hq, founded, ticker, website, unitPrice, employees, keyProducts, targetCustomers, topCompetitors, supplyChainNotes, companyBackground, description, productFit, signalType (pick the most plausible recent signal type you're aware of), signalDate, signalDetail, recentDevelopments, whyNow, suggestedContact, suggestedContactLinkedIn, extronFit, source (name of the publication your search found), sourceUrl (the real URL from your search), urgencyScore (0-100), confidenceScore (0-100)."""

                with client.messages.stream(
                    model="claude-sonnet-5", max_tokens=8000,
                    system="Return ONLY a raw JSON array. No markdown. No backticks.",
                    messages=[{"role": "user", "content": fallback_prompt}],
                    tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 15}],
                ) as fb_stream:
                    fb_resp = fb_stream.get_final_message()
                fb_text = "".join(getattr(b, "text", "") for b in fb_resp.content if getattr(b, "type", None) == "text")
                fb_found_urls = []
                for b in fb_resp.content:
                    if getattr(b, "type", None) == "web_search_tool_result":
                        b_content = getattr(b, "content", None)
                        if isinstance(b_content, list):
                            for r in b_content:
                                u = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None)
                                if u:
                                    fb_found_urls.append(u)
                log.info(f"Fallback pass: web_search returned {len(fb_found_urls)} real result URL(s).")
                fb_raw = fb_text.replace("```json","").replace("```","").strip()
                fb_start, fb_end = fb_raw.find("["), fb_raw.rfind("]")
                if fb_start != -1 and fb_end != -1:
                    fb_leads = json.loads(fb_raw[fb_start:fb_end+1])
                    existing_names = {l.get("name","").lower().strip() for l in validated}
                    added = 0
                    fb_unused_urls = list(fb_found_urls)
                    for fl in fb_leads:
                        key = fl.get("name","").lower().strip()
                        if key and key not in existing_names and key not in dismissed:
                            fl_src = (fl.get("sourceUrl") or "").strip()
                            fl_flags = ["generated via emergency fallback pass"]
                            if not fl_src.startswith("http") and fb_unused_urls:
                                fl_src = fb_unused_urls.pop(0)
                                fl["sourceUrl"] = fl_src
                                fl_flags.append("source link auto-matched from search results — verify before using")
                            elif not fl_src.startswith("http"):
                                fl_flags.insert(0, "no source URL — AI knowledge only, unverified")
                            fl["qa_flags"] = fl_flags
                            fl["qa_flag_count"] = len(fl_flags)
                            fl["qa_passed"] = fl_src.startswith("http")
                            validated.append(fl)
                            existing_names.add(key)
                            added += 1
                    log.info(f"Fallback pass added {added} leads — total now {len(validated)}.")
            except Exception as fb_e:
                log.error(f"Guaranteed fallback pass also failed: {fb_e}")

        return validated
    except Exception as e:
        log.error(f"AI error: {e}")
        # Surface the real error directly on the dashboard status line — no
        # log-diving required. This is what actually failed, verbatim.
        scan_state["status"] = f"⚠ AI call failed: {type(e).__name__}: {e}"
        scan_state["last_ai_error"] = f"{type(e).__name__}: {e}"
        return []

def load_seen():
    try: return set(load_json_file(SEEN_FILE, []))
    except: return set()

def save_seen(seen):
    save_json_file(SEEN_FILE, list(seen))

def deduplicate(leads, seen):
    """Flag returning leads but always include them. Never permanently block any company."""
    fresh = []
    today = date.today()
    try:
        seen_companies = load_seen_companies()
    except:
        seen_companies = {}

    for l in leads:
        key = l.get("name","").lower().strip()
        if not key:
            continue
        # Check if seen before — flag it but always include
        last_seen = seen_companies.get(key)
        if last_seen:
            try:
                days_ago = (today - date.fromisoformat(last_seen)).days
                l["returningLead"] = True
                l["lastSeenDaysAgo"] = days_ago
            except:
                l["returningLead"] = False
                l["lastSeenDaysAgo"] = 0
        else:
            l["returningLead"] = False
            l["lastSeenDaysAgo"] = 0
        # Update seen date
        seen_companies[key] = today.isoformat()
        fresh.append(l)
        seen.add(key)

    try:
        save_seen_companies(seen_companies)
    except Exception as e:
        log.warning(f"Could not save seen companies: {e}")

    log.info(f"Deduplicate: {len(leads)} leads in → {len(fresh)} leads out")
    return fresh

def load_all_leads():
    return load_json_file(ALL_LEADS_FILE, [])

def save_all_leads(leads):
    save_json_file(ALL_LEADS_FILE, leads)

def update_daily_leads(new_leads):
    """Track leads found today and flag brand new ones from last scan."""
    today = date.today().isoformat()
    daily = load_daily_leads()
    if daily.get("date") != today:
        daily = {"date": today, "leads": [], "last_scan_names": []}

    existing_names = {l.get("name","").lower().strip() for l in daily["leads"]}
    last_scan_names = set(daily.get("last_scan_names", []))
    current_scan_names = []

    for l in new_leads:
        key = l.get("name","").lower().strip()
        current_scan_names.append(key)
        l["new_this_scan"] = key not in last_scan_names
        if key not in existing_names:
            daily["leads"].append(l)
            existing_names.add(key)

    daily["last_scan_names"] = current_scan_names
    save_daily_leads(daily)
    return len(daily["leads"])

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



def _sanitize_for_pdf(l):
    """Sanitize a lead dict — fill all None/missing fields with safe defaults."""
    l = dict(l)
    defaults = {
        "name":"Unknown Company","category":"High-Value Electronics","stage":"Unknown",
        "hq":"Unknown","founded":None,"ticker":None,"website":"","unitPrice":"Unknown",
        "employees":"Unknown","keyProducts":"Not specified","targetCustomers":"Not specified",
        "topCompetitors":"Unknown","supplyChainNotes":"Unknown","description":"No description available.",
        "productFit":"Not specified","signalType":"Unknown","signalDate":"",
        "signalDetail":"No detail available.","whyNow":"Not specified.",
        "suggestedContact":"VP of Operations","suggestedContactLinkedIn":None,
        "extronFit":"Extron can provide last-mile manufacturing and fulfillment services.",
        "source":"","sourceUrl":"","urgencyScore":50,
        "confidenceScore":50,"confidence":50,"returningLead":False,"lastSeenDaysAgo":0,
        "new_this_scan":False,"hasEmbeddedComputing":True,
        "embeddedComputingNote":"embedded computing","isOnTarget":True,"qa_flags":[],
        "annualRevenue":"","companyBackground":"","recentDevelopments":"",
    }
    for k, v in defaults.items():
        if k not in l or l[k] is None:
            l[k] = v
        elif isinstance(l.get(k), str) and not str(l[k]).strip() and isinstance(v, str) and v:
            l[k] = v

    # Safety net: if the AI put the company's own website in sourceUrl (no
    # real article to cite), don't let it render as a fake "source article"
    # link — clear it so the PDF shows "—" instead of a misleading duplicate.
    def _domain(u):
        u = (u or "").lower().replace("https://","").replace("http://","").replace("www.","").rstrip("/")
        return u.split("/")[0]
    src = l.get("sourceUrl","") or ""
    site = l.get("website","") or ""
    if src and site and _domain(src) == _domain(site) and _domain(src):
        l["sourceUrl"] = ""

    # Only a safety net for genuinely runaway text — cuts at the last full
    # sentence, never mid-word, and the ceiling is generous. This is meant to
    # catch a malformed multi-thousand-character field, not to shorten normal
    # writing — the web scanner card shows these fields at full length, so
    # the PDF should too.
    SAFETY_CAP = 2000
    for field in ("signalDetail","description","extronFit","productFit","whyNow",
                  "keyProducts","targetCustomers","supplyChainNotes",
                  "topCompetitors","embeddedComputingNote",
                  "companyBackground","recentDevelopments"):
        val = l.get(field, "")
        if isinstance(val, str) and len(val) > SAFETY_CAP:
            truncated = val[:SAFETY_CAP]
            last_period = truncated.rfind(". ")
            l[field] = (truncated[:last_period+1] if last_period > SAFETY_CAP*0.5 else truncated.rsplit(" ",1)[0])
    return l

def _safe(val, fallback="—"):
    """Return val as a clean string, or fallback if None/empty."""
    if val is None: return fallback
    s = str(val).strip()
    return s if s else fallback

def _xml_escape(s):
    """Escape &, <, > for ReportLab's Paragraph markup. ReportLab treats
    Paragraph text as a light XML dialect — real AI-generated text and real
    URLs very commonly contain a bare '&' (e.g. "R&D", or any Google News
    URL with '&hl=...&gl=...' in the query string), and an unescaped '&'
    crashes the PDF build with an XML parse error. This must be applied to
    every piece of dynamic text/URL before it's interpolated into a
    Paragraph string, whether as plain text or inside a <link href="..."> tag."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def make_card(l, CW):
    """One full page per company. Serif type, generous spacing, colored
    section blocks instead of a dense flat table."""
    u       = l.get("urgencyScore", 60)
    ulbl, ubg, ufc, ulc, ulw = urg_meta(u)
    cbg, cfg = cat_col(l.get("category",""))
    sbg, sfc = sig_col(l.get("signalType",""))

    def safe_str(val):
        # No truncation here — _sanitize_for_pdf already handles the one
        # safety-net cap for runaway text (at a sentence boundary, not
        # mid-word). This function just guarantees a clean string.
        s = str(val).strip() if val else "—"
        return s

    def tx(text, font=SERIF, size=9.5, color=INK, leading=None, align=None, raw=False):
        style = ps('_', fontName=font, fontSize=size, textColor=color,
                   leading=leading or size*1.45)
        if align: style.alignment = align
        if text in (None, ""):
            content = "—"
        else:
            # raw=True is only for strings we built ourselves containing
            # intentional <link>/<u> markup — everything else (AI-generated
            # text, names, URLs) must be escaped or a bare '&' crashes the
            # whole PDF build.
            content = str(text) if raw else _xml_escape(text)
        return Paragraph(content, style)

    name = l.get("name", "Unknown")
    if l.get("returningLead"):
        name += f"   ·   Returning lead, first seen {l.get('lastSeenDaysAgo','?')}d ago"

    article_url = l.get('sourceUrl','') or ''
    website_url = l.get('website','') or ''
    if not website_url.startswith('http'):
        slug = l.get('name','').lower().replace(' ','').replace("'","").replace('.','')
        website_url = f"https://www.{slug}.com"

    contact = _safe(l.get('suggestedContact'))
    cli = l.get('suggestedContactLinkedIn','') or ''

    story = []

    # ── 1. Header block — deep navy, generous padding, serif display name ──
    header_inner = Table([
        [tx(name, font=SERIF_BOLD, size=25, color=WHITE, leading=29)],
        [Spacer(1, 4)],
        [tx(f"{l.get('hq','—')}   ·   {l.get('stage','—')}   ·   {l.get('category','—')}",
            font=SERIF, size=10.5, color=colors.HexColor('#CBDDF2'), leading=15)],
    ])
    header_inner.setStyle(TableStyle([
        ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),0),
        ('TOPPADDING',(0,0),(-1,-1),0), ('BOTTOMPADDING',(0,0),(-1,-1),0),
    ]))

    badge = Table([[tx(f"{ulbl}", font=SERIF_BOLD, size=9.5, color=WHITE, leading=12)],
                    [tx(f"{u}% urgency", font=SERIF, size=8.5, color=colors.HexColor('#CBDDF2'), leading=11)],
                    [tx(f"{l.get('confidenceScore', l.get('confidence', 50))}% confidence",
                        font=SERIF, size=8.5, color=colors.HexColor('#CBDDF2'), leading=11)]],
        style=TableStyle([
            ('ALIGN',(0,0),(-1,-1),'RIGHT'),
            ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),0), ('BOTTOMPADDING',(0,0),(-1,-1),2),
        ]))

    header = Table([[header_inner, badge]], colWidths=[CW*0.72, CW*0.28],
        style=TableStyle([
            ('BACKGROUND',(0,0),(-1,-1), NAVY),
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('LEFTPADDING',(0,0),(0,-1), 20), ('RIGHTPADDING',(0,0),(0,-1), 8),
            ('LEFTPADDING',(1,0),(1,-1), 8), ('RIGHTPADDING',(1,0),(1,-1), 20),
            ('TOPPADDING',(0,0),(-1,-1), 22), ('BOTTOMPADDING',(0,0),(-1,-1), 22),
        ]))
    story.append(header)

    # thin urgency accent line directly under header
    story.append(Table([[""]], colWidths=[CW], rowHeights=[3],
        style=TableStyle([('BACKGROUND',(0,0),(-1,-1), ulc)])))
    story.append(Spacer(1, 7*mm))

    # ── 2. Signal block — tinted, the "why now" trigger ─────────────────────
    sig_label = f"{_safe(l.get('signalType')).upper()}   ·   {_safe(l.get('signalDate'))}"
    sig_rows = [[tx(sig_label, font=SERIF_BOLD, size=9.5, color=sfc, leading=13)]]
    if article_url.startswith('http'):
        sig_rows.append([tx(f'<link href="{_xml_escape(article_url)}"><u>Verify source</u></link>',
                             font=SERIF, size=8.5, color=sfc, leading=11, raw=True)])
    sig_rows.append([tx(safe_str(l.get('signalDetail')), font=SERIF, size=9.5, color=INK, leading=14.5)])
    last_row = len(sig_rows) - 1

    signal_block = Table(sig_rows, colWidths=[CW], style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1), sbg),
        ('LEFTPADDING',(0,0),(-1,-1),16), ('RIGHTPADDING',(0,0),(-1,-1),16),
        ('TOPPADDING',(0,0),(-1,-1),1), ('BOTTOMPADDING',(0,0),(-1,-1),1),
        ('TOPPADDING',(0,0),(0,0),12),                    # extra air above the label only
        ('BOTTOMPADDING',(0,last_row),(0,last_row),12),   # extra air below the body only
        ('TOPPADDING',(0,last_row),(0,last_row),6),       # small gap before body text
    ]))
    story.append(signal_block)
    story.append(Spacer(1, 2.5*mm))

    # ── QA flags — only shown when the data has caveats worth knowing ──────
    flags = l.get('qa_flags') or []
    if flags:
        flags_text = "Data caveats:  " + "   ·   ".join(flags)
        flags_block = Table([[tx(flags_text, font=SERIF, size=8, color=AMBER, leading=11)]],
            colWidths=[CW], style=TableStyle([
                ('BACKGROUND',(0,0),(-1,-1), AMBER_BG),
                ('LEFTPADDING',(0,0),(-1,-1),16), ('RIGHTPADDING',(0,0),(-1,-1),16),
                ('TOPPADDING',(0,0),(-1,-1),6), ('BOTTOMPADDING',(0,0),(-1,-1),6),
            ]))
        story.append(flags_block)

    story.append(Spacer(1, 5*mm))

    # ── 3. Section block helper — colored left rule + label + serif body ───
    def section(label, value, accent, bg=GRAY_LT):
        return Table([[
            Table([[""]], colWidths=[3], rowHeights=[None],
                  style=TableStyle([('BACKGROUND',(0,0),(-1,-1), accent)])),
            Table([
                [tx(label.upper(), font=SERIF_MEDIUM, size=8, color=accent, leading=11)],
                [tx(safe_str(value), font=SERIF, size=9.5, color=INK, leading=14.5)],
            ], style=TableStyle([
                ('LEFTPADDING',(0,0),(-1,-1),14), ('RIGHTPADDING',(0,0),(-1,-1),14),
                ('TOPPADDING',(0,0),(-1,-1),0), ('BOTTOMPADDING',(0,0),(-1,-1),0),
                ('TOPPADDING',(0,0),(0,0),10),    # air above the label
                ('BOTTOMPADDING',(0,0),(0,0),4),  # small gap between label and body
                ('BOTTOMPADDING',(0,1),(0,1),10), # air below the body
            ])),
        ]], colWidths=[3, CW-3], style=TableStyle([
            ('BACKGROUND',(0,0),(-1,-1), bg),
            ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),0), ('BOTTOMPADDING',(0,0),(-1,-1),0),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
        ]))

    story.append(section("Why reach out now", l.get('whyNow'), NAVY))
    story.append(Spacer(1, 2.5*mm))

    if l.get('companyBackground'):
        story.append(section("Company background", l.get('companyBackground'), TEXT2))
        story.append(Spacer(1, 2.5*mm))

    story.append(section("Company snapshot", l.get('description'), TEXT2))
    story.append(Spacer(1, 2.5*mm))

    if l.get('recentDevelopments'):
        story.append(section("Recent developments", l.get('recentDevelopments'), TEXT2))
        story.append(Spacer(1, 2.5*mm))

    # Product fit + Extron fit merged into one block — these two fields were
    # circling the same point (why this company's hardware needs Extron's
    # services) from two angles. One combined section says it once.
    combined_fit = safe_str(l.get('productFit'))
    extron_fit_txt = safe_str(l.get('extronFit'))
    if extron_fit_txt and extron_fit_txt != "—":
        combined_fit = f"{combined_fit}  {extron_fit_txt}"
    story.append(section("Product & Extron fit", combined_fit, NAVY))
    story.append(Spacer(1, 4*mm))

    # ── 4. Facts grid — quiet two-column table, serif labels ───────────────
    def fact(label, value):
        return [tx(label.upper(), font=SERIF_MEDIUM, size=7.5, color=TEXT2, leading=11),
                tx(safe_str(value), font=SERIF, size=9, color=INK, leading=13)]

    facts = Table([
        fact("Key products", l.get('keyProducts')),
        fact("Target customers", l.get('targetCustomers')),
        fact("Top competitors", l.get('topCompetitors')),
        fact("Supply chain notes", l.get('supplyChainNotes')),
        fact("Founded", l.get('founded')),
        fact("Employees", l.get('employees')),
        fact("Unit price", l.get('unitPrice')),
    ], colWidths=[38*mm, CW-38*mm], style=TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LINEBELOW',(0,0),(-1,-2), 0.5, BORDER),
        ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(-1,-1),8), ('BOTTOMPADDING',(0,0),(-1,-1),8),
    ]))
    story.append(facts)
    story.append(Spacer(1, 5*mm))
    story.append(HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=4*mm))

    # ── 5. Footer strip — contact / source / website ────────────────────────
    contact_cell = (tx(f'<link href="{_xml_escape(cli)}"><u>{_xml_escape(contact)}</u></link>', font=SERIF_MEDIUM,
                        size=9.5, color=colors.HexColor('#185FA5'), leading=13, raw=True)
                    if cli.startswith('http')
                    else tx(contact, font=SERIF_MEDIUM, size=9.5, color=INK, leading=13))

    footer = Table([
        [tx("CONTACT", font=SERIF_MEDIUM, size=7.5, color=TEXT2, leading=10), contact_cell],
        [tx("SOURCE ARTICLE", font=SERIF_MEDIUM, size=7.5, color=TEXT2, leading=10),
         tx(f'<link href="{_xml_escape(article_url)}"><u>{_xml_escape(article_url[:80])}</u></link>' if article_url else "—",
            font=SERIF, size=8.5, color=colors.HexColor('#185FA5'), leading=12, raw=True)],
        [tx("WEBSITE", font=SERIF_MEDIUM, size=7.5, color=TEXT2, leading=10),
         tx(f'<link href="{_xml_escape(website_url)}"><u>{_xml_escape(website_url[:80])}</u></link>',
            font=SERIF, size=8.5, color=colors.HexColor('#185FA5'), leading=12, raw=True)],
    ], colWidths=[30*mm, CW-30*mm], style=TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),0), ('RIGHTPADDING',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),5), ('BOTTOMPADDING',(0,0),(-1,-1),5),
    ]))
    story.append(footer)
    story.append(PageBreak())

    return story


def _build_pdf_bytes(leads):
    """Build PDF entirely in memory and return bytes. Never touches disk."""
    from io import BytesIO as _BIO
    buf = _BIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm,
        allowSplitting=1)
    W, H = A4
    CW = W - 36*mm
    today_str = date.today().strftime("%B %d, %Y")
    hot   = [l for l in leads if l.get("urgencyScore",0) >= 85]
    high  = [l for l in leads if 70 <= l.get("urgencyScore",0) < 85]
    watch = [l for l in leads if l.get("urgencyScore",0) < 70]
    story = []

    # Confidence score
    try:
        scores = load_confidence_scores()
        latest_score = scores[-1]["score"] if scores else None
    except:
        latest_score = None
    score_text = f"{latest_score}% accuracy" if latest_score is not None else "First scan"

    # Cover page
    logo_elem = Paragraph("EXTRON INC", ps('lg', fontName='Helvetica-Bold', fontSize=22, textColor=WHITE, leading=26))
    try:
        req = urllib.request.Request("https://www.extroninc.com/img/logo.png", headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            logo_elem = Image(_BIO(r.read()), width=44*mm, height=14*mm)
    except: pass

    cover = Table([
        [logo_elem], [Spacer(1,10)],
        [Paragraph("CONFIDENTIAL — EXTRON SALES INTELLIGENCE",
            ps('cl', fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#aaaacc'), leading=11))],
        [Spacer(1,8)],
        [Paragraph("Lead Intelligence Report",
            ps('ct', fontName='Helvetica-Bold', fontSize=26, textColor=WHITE, leading=32))],
        [Paragraph("OEM Hardware — Funding · Hiring · New Facilities · Awards · Trade Shows · Supply Chain",
            ps('cs', fontName='Helvetica', fontSize=10, textColor=colors.HexColor('#ccddee'), leading=15))],
        [Spacer(1,16)],
        [Table([[
            Table([[Paragraph("GENERATED",ps('ml',fontName='Helvetica',fontSize=7,textColor=colors.HexColor('#aaaacc'),leading=9))],
                   [Paragraph(today_str,ps('mv',fontName='Helvetica-Bold',fontSize=10,textColor=WHITE,leading=13))]],colWidths=[42*mm]),
            Table([[Paragraph("TOTAL",ps('ml',fontName='Helvetica',fontSize=7,textColor=colors.HexColor('#aaaacc'),leading=9))],
                   [Paragraph(str(len(leads)),ps('mn',fontName='Helvetica-Bold',fontSize=28,textColor=WHITE,leading=32))]],colWidths=[22*mm]),
            Table([[Paragraph("HOT",ps('ml',fontName='Helvetica',fontSize=7,textColor=colors.HexColor('#aaaacc'),leading=9))],
                   [Paragraph(str(len(hot)),ps('mn2',fontName='Helvetica-Bold',fontSize=28,textColor=colors.HexColor('#FF9999'),leading=32))]],colWidths=[22*mm]),
            Table([[Paragraph("HIGH",ps('ml',fontName='Helvetica',fontSize=7,textColor=colors.HexColor('#aaaacc'),leading=9))],
                   [Paragraph(str(len(high)),ps('mn3',fontName='Helvetica-Bold',fontSize=28,textColor=colors.HexColor('#FFD580'),leading=32))]],colWidths=[22*mm]),
            Table([[Paragraph("WATCH",ps('ml',fontName='Helvetica',fontSize=7,textColor=colors.HexColor('#aaaacc'),leading=9))],
                   [Paragraph(str(len(watch)),ps('mn4',fontName='Helvetica-Bold',fontSize=28,textColor=WHITE,leading=32))]],colWidths=[22*mm]),
            Table([[Paragraph("ACCURACY",ps('ml',fontName='Helvetica',fontSize=7,textColor=colors.HexColor('#aaaacc'),leading=9))],
                   [Paragraph(score_text,ps('mn5',fontName='Helvetica-Bold',fontSize=13,textColor=colors.HexColor('#90EE90'),leading=17))]],colWidths=[26*mm]),
        ]],colWidths=[42*mm,22*mm,22*mm,22*mm,22*mm,26*mm],style=TableStyle([
            ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),
            ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),
            ('BOTTOMPADDING',(0,0),(-1,-1),0)]))]
    ],colWidths=[CW],style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),NAVY),
        ('LEFTPADDING',(0,0),(-1,-1),22),('RIGHTPADDING',(0,0),(-1,-1),22),
        ('TOPPADDING',(0,0),(-1,-1),22),('BOTTOMPADDING',(0,0),(-1,-1),26)]))
    story += [cover, PageBreak()]

    # Summary table
    story.append(Paragraph("ALL LEADS AT A GLANCE",
        ps('h2',fontName='Helvetica-Bold',fontSize=10,textColor=TEXT2,leading=13,spaceAfter=6)))
    story.append(HRFlowable(width=CW,thickness=0.5,color=BORDER,spaceAfter=8))

    ratings = load_ratings()
    summary_rows = [[
        Paragraph("Company",   ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Category",  ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Signal",    ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Contact",   ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Urgency",   ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Rating",    ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
    ]]
    for l in sorted(leads, key=lambda x: -x.get("urgencyScore",0)):
        u   = l.get("urgencyScore",0)
        uc  = RED if u >= 85 else AMBER if u >= 70 else GRAY_TXT
        key = l.get("name","").lower().strip()
        r   = ratings.get(key,{}).get("score")
        rating_str = f"{r}/5" if r else "Unrated"
        summary_rows.append([
            Paragraph(_xml_escape(_safe(l.get("name"))),            ps('td',fontName='Helvetica-Bold',fontSize=9,textColor=INK,  leading=12)),
            Paragraph(_xml_escape(_safe(l.get("category"))),        ps('td',fontName='Helvetica',      fontSize=9,textColor=TEXT2,leading=12)),
            Paragraph(_xml_escape(_safe(l.get("signalType"))),      ps('td',fontName='Helvetica',      fontSize=9,textColor=TEXT2,leading=12)),
            Paragraph(_xml_escape(_safe(l.get("suggestedContact"))),ps('td',fontName='Helvetica',      fontSize=9,textColor=TEXT2,leading=12)),
            Paragraph(f"{u}%",                         ps('td',fontName='Helvetica-Bold', fontSize=9,textColor=uc,  leading=12)),
            Paragraph(rating_str,                      ps('td',fontName='Helvetica',      fontSize=9,textColor=TEXT2,leading=12)),
        ])
    story.append(Table(summary_rows,colWidths=[46*mm,28*mm,38*mm,34*mm,14*mm,22*mm],
        style=TableStyle([
            ('BACKGROUND',(0,0),(-1,0),GRAY_LT),('LINEBELOW',(0,0),(-1,0),0.5,BORDER),
            ('LINEBELOW',(0,1),(-1,-1),0.25,BORDER),
            ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),4),
            ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,GRAY_LT]),
        ])))
    story.append(PageBreak())

    # Lead cards
    def section(text, color):
        return [
            Paragraph(text, ps('sh',fontName='Helvetica-Bold',fontSize=12,textColor=color,leading=15,spaceAfter=6)),
            HRFlowable(width=CW,thickness=0.5,color=color,spaceAfter=8)
        ]

    if hot:
        story += section("HOT LEADS — Act Immediately", RED)
        for l in sorted(hot,  key=lambda x:-x.get("urgencyScore",0)):
            story.extend(make_card(l, CW))
    if high:
        story += section("HIGH PRIORITY", AMBER)
        for l in sorted(high, key=lambda x:-x.get("urgencyScore",0)):
            story.extend(make_card(l, CW))
    if watch:
        story += section("WATCH LIST", GRAY_TXT)
        for l in sorted(watch,key=lambda x:-x.get("urgencyScore",0)):
            story.extend(make_card(l, CW))
    if not leads:
        story.append(Spacer(1,10*mm))
        story.append(Paragraph("No qualifying leads in this selection.",
            ps('e',fontName='Helvetica',fontSize=12,textColor=TEXT2,leading=18)))

    doc.build(story)
    buf.seek(0)
    return buf.read()


def generate_pdf(leads, filename=None):
    """Generate PDF. If filename given, also save to disk. Always returns bytes."""
    pdf_bytes = _build_pdf_bytes(leads)
    if filename:
        try:
            Path(filename).write_bytes(pdf_bytes)
            log.info(f"PDF saved to disk: {filename} ({len(pdf_bytes)} bytes)")
        except Exception as e:
            log.warning(f"Could not save PDF to disk: {e}")
    return pdf_bytes


def _comm_fetch(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"[community] fetch failed {url[:60]}: {e}")
        return ""

def _comm_score(title, body=""):
    full = (title + " " + body).lower()
    signals, score = [], 0
    for kw in COMM_EXTRON_TERMS:
        if kw in full: signals.append(("extron_mention", kw)); score += 10
    for kw in COMM_SUPPLY_SIGNALS:
        if kw in full: signals.append(("supply_chain", kw)); score += 3
    for kw in COMM_CHANGE_SIGNALS:
        if kw in full: signals.append(("change_signal", kw)); score += 2
    for kw in COMM_HARDWARE_SIGNALS:
        if kw in full: signals.append(("hardware", kw)); score += 2
    return score, signals

def _comm_build(source_type, source_name, title, url, body="", author="", date_str=""):
    score, sigs = _comm_score(title, body)
    if score == 0:
        return None
    return {
        "id":            hashlib.md5((url + title).encode()).hexdigest()[:12],
        "source_type":   source_type,
        "source_name":   source_name,
        "title":         title[:200],
        "url":           url,
        "snippet":       body[:400].strip(),
        "author":        author,
        "date":          date_str or "",
        "score":         score,
        "signal_types":  list(set(s[0] for s in sigs)),
        "signal_keywords": list(set(s[1] for s in sigs))[:8],
        "flagged":       score >= 5,
        "extron_mention": any(s[0] == "extron_mention" for s in sigs),
        "scanned_at":    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

def _scan_reddit_hot():
    results = []
    for sub in REDDIT_SUBS:
        for listing in ["hot", "new"]:
            raw = _comm_fetch(f"https://www.reddit.com/r/{sub}/{listing}.json?limit=25")
            if not raw: continue
            try:
                for p in json.loads(raw).get("data",{}).get("children",[]):
                    d = p.get("data",{})
                    ts = d.get("created_utc", 0)
                    r = _comm_build("reddit", f"r/{sub}", d.get("title",""),
                        "https://reddit.com" + d.get("permalink",""),
                        d.get("selftext",""), d.get("author",""),
                        time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "")
                    if r: results.append(r)
            except: pass
            time.sleep(0.4)
    return results

def _scan_reddit_search():
    results = []
    for sub, query in REDDIT_SEARCHES:
        raw = _comm_fetch(
            f"https://www.reddit.com/r/{sub}/search.json?q={_urlparse.quote(query)}&restrict_sr=1&sort=new&limit=20")
        if not raw: continue
        try:
            for p in json.loads(raw).get("data",{}).get("children",[]):
                d = p.get("data",{})
                ts = d.get("created_utc", 0)
                r = _comm_build("reddit_search", f"r/{sub} search", d.get("title",""),
                    "https://reddit.com" + d.get("permalink",""),
                    d.get("selftext",""), d.get("author",""),
                    time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "")
                if r: results.append(r)
        except: pass
        time.sleep(0.5)
    return results

def _scan_hn():
    results = []
    queries = [
        "supply chain onshoring", "reshoring manufacturing",
        "supply chain hardware", "av hardware supply chain",
        "medical device supply chain", "ev charging supply chain",
    ]
    for q in queries:
        raw = _comm_fetch(f"https://hn.algolia.com/api/v1/search?query={_urlparse.quote(q)}&tags=story&hitsPerPage=20")
        if not raw: continue
        try:
            for hit in json.loads(raw).get("hits",[]):
                r = _comm_build("hacker_news", "Hacker News",
                    hit.get("title",""),
                    f"https://news.ycombinator.com/item?id={hit.get('objectID','')}",
                    hit.get("story_text","") or "",
                    hit.get("author",""), (hit.get("created_at","") or "")[:10])
                if r: results.append(r)
        except: pass
        time.sleep(0.3)
    # Who's hiring
    raw = _comm_fetch("https://hn.algolia.com/api/v1/search?query=who+is+hiring+hardware&tags=comment&hitsPerPage=25")
    if raw:
        try:
            for hit in json.loads(raw).get("hits",[]):
                body = hit.get("comment_text","") or ""
                title = (body[:80].replace("\n"," ") + "...") if len(body)>80 else body
                r = _comm_build("hacker_news", "HN: Who's Hiring", title,
                    f"https://news.ycombinator.com/item?id={hit.get('objectID','')}",
                    body, hit.get("author",""), (hit.get("created_at","") or "")[:10])
                if r: results.append(r)
        except: pass
    return results

def _scan_stack_exchange():
    results = []
    for site in ["electronics","engineering","iot"]:
        for kw in ["supply chain","onshoring","hardware manufacturing"]:
            raw = _comm_fetch(
                f"https://api.stackexchange.com/2.3/search?order=desc&sort=activity"
                f"&intitle={_urlparse.quote(kw)}&site={site}&pagesize=15&filter=withbody")
            if not raw: continue
            try:
                for item in json.loads(raw).get("items",[]):
                    body = re.sub(r"<[^>]+>"," ", item.get("body",""))
                    ts = item.get("creation_date",0)
                    r = _comm_build("stack_exchange", f"Stack Exchange: {site}",
                        item.get("title",""), item.get("link",""), body,
                        item.get("owner",{}).get("display_name",""),
                        time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "")
                    if r: results.append(r)
            except: pass
            time.sleep(0.4)
    return results

def _scan_avs_forum():
    results = []
    try:
        from bs4 import BeautifulSoup as _BS
        HAS_BS4 = True
    except ImportError:
        HAS_BS4 = False
    if not HAS_BS4: return results
    for term in AVS_TERMS:
        raw = _comm_fetch(f"https://www.avsforum.com/search/?q={_urlparse.quote(term)}&o=date")
        if not raw: continue
        try:
            soup = _BS(raw, "html.parser")
            for item in soup.select(".structItem--thread")[:10]:
                a = item.select_one(".structItem-title a")
                if not a: continue
                href = a.get("href","")
                link = f"https://www.avsforum.com{href}" if href.startswith("/") else href
                author, date_str = "", ""
                meta = item.select_one(".structItem-minor")
                if meta:
                    u = meta.select_one(".username")
                    t = meta.select_one("time")
                    if u: author = u.get_text(strip=True)
                    if t: date_str = t.get("datetime","")[:10]
                r = _comm_build("avs_forum", "AVS Forum", a.get_text(strip=True), link, "", author, date_str)
                if r: results.append(r)
        except: pass
        time.sleep(0.5)
    return results

def _scan_avixa():
    results = []
    try:
        from bs4 import BeautifulSoup as _BS
        HAS_BS4 = True
    except ImportError:
        HAS_BS4 = False
    if not HAS_BS4: return results
    for term in AVIXA_TERMS:
        raw = _comm_fetch(f"https://community.avixa.org/search?q={_urlparse.quote(term)}")
        if not raw: continue
        try:
            soup = _BS(raw, "html.parser")
            for item in soup.select(".search-result,article,.post-summary")[:8]:
                a = item.select_one("h2 a,h3 a,.title a")
                if not a: continue
                href = a.get("href","")
                link = f"https://community.avixa.org{href}" if href.startswith("/") else href
                body_el = item.select_one("p,.excerpt")
                body = body_el.get_text(strip=True) if body_el else ""
                r = _comm_build("avixa", "AVIXA Community", a.get_text(strip=True), link, body)
                if r: results.append(r)
        except: pass
        time.sleep(0.5)
    return results

def _scan_linkedin_google():
    results = []
    try:
        from bs4 import BeautifulSoup as _BS
        HAS_BS4 = True
    except ImportError:
        HAS_BS4 = False
    if not HAS_BS4: return results
    queries = [
        "site:linkedin.com/posts supply chain onshoring reshoring hardware",
        "site:linkedin.com/posts offshoring manufacturing medical device AV",
        "site:linkedin.com/pulse reshoring supply chain hardware",
    ]
    for q in queries:
        raw = _comm_fetch(f"https://www.google.com/search?q={_urlparse.quote(q)}&num=10")
        if not raw: continue
        try:
            soup = _BS(raw, "html.parser")
            for g in soup.select("div.g")[:8]:
                link_el = g.select_one("a[href]")
                title_el = g.select_one("h3")
                snip_el  = g.select_one(".VwiC3b")
                if not link_el or not title_el: continue
                href = link_el.get("href","")
                if not href.startswith("http"): continue
                r = _comm_build("linkedin_public", "LinkedIn (public)",
                    title_el.get_text(strip=True), href,
                    snip_el.get_text(strip=True) if snip_el else "")
                if r: results.append(r)
        except: pass
        time.sleep(1.5)
    return results

def _scan_quora():
    results = []
    try:
        from bs4 import BeautifulSoup as _BS
        HAS_BS4 = True
    except ImportError:
        HAS_BS4 = False
    if not HAS_BS4: return results
    for q in ["supply chain onshoring offshoring hardware","reshoring manufacturing usa electronics"]:
        raw = _comm_fetch(f"https://www.google.com/search?q={_urlparse.quote('site:quora.com '+q)}&num=8")
        if not raw: continue
        try:
            soup = _BS(raw, "html.parser")
            for g in soup.select("div.g")[:6]:
                link_el = g.select_one("a[href]")
                title_el = g.select_one("h3")
                snip_el  = g.select_one(".VwiC3b")
                if not link_el or not title_el: continue
                href = link_el.get("href","")
                if "quora.com" not in href: continue
                r = _comm_build("quora", "Quora",
                    title_el.get_text(strip=True), href,
                    snip_el.get_text(strip=True) if snip_el else "")
                if r: results.append(r)
        except: pass
        time.sleep(1.0)
    return results

COMM_SCANNERS = [
    ("Reddit (hot/new)",   _scan_reddit_hot),
    ("Reddit (search)",    _scan_reddit_search),
    ("Hacker News",        _scan_hn),
    ("Stack Exchange",     _scan_stack_exchange),
    ("AVS Forum",          _scan_avs_forum),
    ("AVIXA Community",    _scan_avixa),
    ("LinkedIn (public)",  _scan_linkedin_google),
    ("Quora",              _scan_quora),
]

def _comm_load(path, default):
    try:
        if Path(path).exists(): return json.loads(Path(path).read_text())
    except: pass
    return default

def _comm_save(path, data):
    Path(path).write_text(json.dumps(data, indent=2))

def run_community_scan():
    global comm_state
    if comm_state["running"]: return
    comm_state.update({"running":True,"sources":[],"progress":0,
                       "status":"Starting community scan..."})
    seen     = _comm_load(COMM_SEEN_FILE, {})
    existing = _comm_load(COMM_RESULTS_FILE, [])
    seen_ids = {r["id"] for r in existing}
    all_new  = []
    total    = len(COMM_SCANNERS)
    for i, (name, fn) in enumerate(COMM_SCANNERS):
        comm_state["status"]   = f"Scanning {name}..."
        comm_state["progress"] = int(i / total * 100)
        log.info(f"[community] scanning {name}")
        try:
            items = fn()
            fresh = [it for it in items if it and it["id"] not in seen_ids]
            for it in fresh:
                seen[it["id"]] = it["scanned_at"]
                seen_ids.add(it["id"])
            all_new.extend(fresh)
            comm_state["sources"].append({"name": name, "found": len(fresh)})
        except Exception as e:
            log.error(f"[community] {name} failed: {e}", exc_info=True)
            comm_state["sources"].append({"name": name, "found": 0, "error": str(e)})
    all_results = sorted(existing + all_new, key=lambda x: -x.get("score",0))
    _comm_save(COMM_RESULTS_FILE, all_results)
    _comm_save(COMM_SEEN_FILE,    seen)
    flagged = [r for r in all_results if r.get("flagged")]
    comm_state.update({
        "running":  False,
        "status":   f"Done — {len(all_new)} new posts found, {len(flagged)} flagged.",
        "last_run": time.strftime("%B %d, %Y %H:%M"),
        "total":    len(all_results),
        "flagged":  len(flagged),
        "progress": 100,
    })
    log.info(f"[community] scan complete: {len(all_new)} new, {len(flagged)} flagged total")

def _comm_to_csv(results):
    out = _io.StringIO()
    fields = ["title","url","source_name","source_type","score","signal_types",
              "signal_keywords","extron_mention","flagged","author","date","snippet","scanned_at"]
    w = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in results:
        row = dict(r)
        row["signal_types"]    = "|".join(r.get("signal_types",[]))
        row["signal_keywords"] = "|".join(r.get("signal_keywords",[]))
        w.writerow(row)
    return out.getvalue()

def build_community_page():
    cs = comm_state
    results  = _comm_load(COMM_RESULTS_FILE, [])
    flagged  = [r for r in results if r.get("flagged")]
    extron   = [r for r in results if r.get("extron_mention")]

    status_html = ""
    if cs["running"]:
        pct = cs.get("progress", 0)
        status_html = f'''<div style="background:#E6F1FB;border-radius:8px;padding:14px 18px;margin-bottom:16px;border:1px solid #B5D4F4">
          <div style="font-size:13px;color:#0C447C;font-weight:500;margin-bottom:8px">{cs["status"]}</div>
          <div style="background:#dde8f5;border-radius:4px;height:6px"><div style="background:#0C447C;width:{pct}%;height:6px;border-radius:4px;transition:width .3s"></div></div>
        </div>'''
    elif cs["last_run"]:
        status_html = f'''<div style="background:#EAF3DE;border-radius:8px;padding:14px 18px;margin-bottom:16px;border:1px solid #A5D6A7;font-size:13px;color:#27500A;font-weight:500">
          {cs["status"]} &mdash; Last run: {cs["last_run"]}
        </div>'''

    # source breakdown
    source_pills = "".join(
        f'<span style="display:inline-block;background:#f0f4ff;border:1px solid #dde4f5;border-radius:4px;padding:3px 10px;font-size:11px;color:#333;margin:3px">{s["name"]}: {s["found"]}</span>'
        for s in cs.get("sources", [])
    )

    # result rows (top 60)
    def sig_badge(s):
        colors_map = {"supply_chain":"#0C447C","change_signal":"#633806","hardware":"#27500A","extron_mention":"#791F1F"}
        bg_map     = {"supply_chain":"#E6F1FB","change_signal":"#FAEEDA","hardware":"#EAF3DE","extron_mention":"#FCEBEB"}
        return "".join(
            f'<span style="display:inline-block;background:{bg_map.get(t,"#f0f0f0")};color:{colors_map.get(t,"#333")};'
            f'border-radius:3px;padding:1px 7px;font-size:10px;margin:1px">{t.replace("_"," ")}</span>'
            for t in s)

    rows_html = ""
    for r in results[:60]:
        extron_flag = ' style="border-left:3px solid #791F1F"' if r.get("extron_mention") else (
                      ' style="border-left:3px solid #633806"' if r.get("flagged") else "")
        rows_html += f'''<tr{extron_flag}>
          <td style="padding:10px 8px;font-size:12px;min-width:280px">
            <a href="{r["url"]}" target="_blank" style="color:#0C447C;text-decoration:none;font-weight:500">{r["title"][:100]}</a>
            <div style="color:#888;font-size:11px;margin-top:3px">{r.get("snippet","")[:120]}{"…" if len(r.get("snippet",""))>120 else ""}</div>
          </td>
          <td style="padding:10px 8px;font-size:11px;color:#555;white-space:nowrap">{r.get("source_name","")}</td>
          <td style="padding:10px 8px;font-size:11px;text-align:center;font-weight:700;color:#0C447C">{r.get("score",0)}</td>
          <td style="padding:10px 8px">{sig_badge(r.get("signal_types",[]))}</td>
          <td style="padding:10px 8px;font-size:11px;color:#999;white-space:nowrap">{r.get("date","")}</td>
        </tr>'''

    if not rows_html:
        rows_html = '<tr><td colspan="5" style="padding:28px;text-align:center;color:#999;font-size:13px">No results yet — run a scan first.</td></tr>'

    # Build rating leaderboard
    ratings = load_ratings()
    cat_scores  = {}
    sig_scores  = {}
    for key, r in ratings.items():
        score = r.get("score",0)
        cat   = r.get("category","Unknown")
        sig   = r.get("signalType","Unknown")
        if cat not in cat_scores: cat_scores[cat]  = []
        if sig not in sig_scores: sig_scores[sig]  = []
        cat_scores[cat].append(score)
        sig_scores[sig].append(score)

    def leaderboard_row(label, scores, color):
        avg = round(sum(scores)/len(scores),1)
        bar_w = int(avg/5*100)
        return f'''<div style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:2px">
            <span style="font-size:12px;color:#333">{label}</span>
            <span style="font-size:12px;font-weight:700;color:{color}">{avg}/5 ({len(scores)} rated)</span>
          </div>
          <div style="background:#eee;border-radius:99px;height:8px">
            <div style="background:{color};width:{bar_w}%;height:8px;border-radius:99px"></div>
          </div>
        </div>'''

    cat_board  = "".join(leaderboard_row(k, v, "#27500A" if sum(v)/len(v)>=3.5 else "#791F1F")
                         for k,v in sorted(cat_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if cat_scores else "<p style='color:#999;font-size:12px'>No ratings yet — rate leads using the 1-5 buttons on the dashboard.</p>"
    sig_board  = "".join(leaderboard_row(k, v, "#0C447C" if sum(v)/len(v)>=3.5 else "#E65100")
                         for k,v in sorted(sig_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if sig_scores else ""
    rating_leaderboard_html = f'''
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Product Category</h3>{cat_board}</div>
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Signal Type</h3>{sig_board if sig_board else "<p style='color:#999;font-size:12px'>No ratings yet.</p>"}</div>
      </div>'''

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
          <td style="padding:10px 8px;color:#555">{_safe(l.get("signalType"))}</td>
          <td style="padding:10px 8px;color:#791F1F;font-weight:700">{l.get("urgencyScore",0)}%</td>
          <td style="padding:10px 8px;font-size:12px;color:#555">{_safe(l.get("whyNow"))}</td>
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


def run_self_optimizer():
    """Reads feedback, finds patterns, logs optimizer adjustments."""
    feedback = load_feedback()
    if len(feedback) < 10:
        log.info("Self-optimizer: not enough feedback yet (need 10+ rated leads)")
        return
    state = load_optimizer_state()
    good_signals, bad_signals = {}, {}
    for key, fb in feedback.items():
        rating = fb.get("rating","")
        sig    = fb.get("signalType","unknown")
        cat    = fb.get("category","unknown")
        if rating == "good":
            good_signals[sig] = good_signals.get(sig,0)+1
            good_signals[cat] = good_signals.get(cat,0)+1
        elif rating in ["wrong_industry","not_our_customer"]:
            bad_signals[sig]  = bad_signals.get(sig,0)+1
            bad_signals[cat]  = bad_signals.get(cat,0)+1
    adjustments = []
    for sig, count in bad_signals.items():
        if count >= 3:
            adjustments.append(f"Signal '{sig}' produced {count} bad leads — consider tightening")
    for sig, count in good_signals.items():
        if count >= 3:
            adjustments.append(f"Signal '{sig}' produced {count} good leads — performing well")
    state["last_optimized"] = datetime.now().isoformat()
    state["adjustments"]    = adjustments[-20:]
    state["good_signals"]   = good_signals
    state["bad_signals"]    = bad_signals
    save_optimizer_state(state)
    log.info(f"Self-optimizer: {len(adjustments)} notes from {len(feedback)} feedback entries")


def build_inputs_page(msg=""):
    """Full inputs configuration page."""
    inputs  = load_inputs()
    presets = load_presets()
    opt_log = load_optimizer_log()

    active_preset = inputs.get("active_preset","")

    # Helper to checked attr
    def chk(val, lst): return 'checked' if val in lst else ''
    def sel(val, opt): return 'selected' if val == opt else ''

    # Categories checkboxes
    cats_html = "".join(
        f'''<label style="display:flex;align-items:center;gap:8px;margin:4px 0;font-size:13px;cursor:pointer">
          <input type="checkbox" name="categories" value="{c}" {chk(c, inputs.get("categories",[]))}> {c}
        </label>''' for c in ALL_CATEGORIES
    )

    # Signals checkboxes
    sigs_html = "".join(
        f'''<label style="display:flex;align-items:center;gap:8px;margin:4px 0;font-size:13px;cursor:pointer">
          <input type="checkbox" name="signals" value="{s}" {chk(s, inputs.get("signals",[]))}> {s}
        </label>''' for s in ALL_SIGNALS
    )

    # Stages checkboxes
    stages_html = "".join(
        f'''<label style="display:flex;align-items:center;gap:8px;margin:4px 0;font-size:13px;cursor:pointer">
          <input type="checkbox" name="stages" value="{s}" {chk(s, inputs.get("stages",[]))}> {s}
        </label>''' for s in ALL_STAGES
    )

    # Locations multi-select
    sel_locs = inputs.get("locations", ["Anywhere"])
    anywhere_checked = "checked" if "Anywhere" in sel_locs else ""
    locs_html = '''<label style="display:flex;align-items:center;gap:8px;margin:4px 0;font-size:13px;font-weight:600;cursor:pointer">
      <input type="checkbox" name="locations" value="Anywhere" ''' + anywhere_checked + '''> Anywhere (default)
    </label><hr style="margin:8px 0;border:none;border-top:1px solid #eee">'''
    locs_html += "".join(
        f'''<label style="display:flex;align-items:center;gap:8px;margin:3px 0;font-size:12px;cursor:pointer">
          <input type="checkbox" name="locations" value="{loc}" {chk(loc, sel_locs)}> {loc}
        </label>''' for loc in ALL_LOCATIONS
    )

    # Presets
    preset_options = "".join(f'<option value="{k}" {sel(k, active_preset)}>{k}</option>' for k in presets)
    preset_html = f'''
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <select name="load_preset" id="presetSelect" style="font-size:13px;padding:7px;border-radius:6px;border:1px solid #ddd">
          <option value="">-- Load a saved preset --</option>
          {preset_options}
        </select>
        <button type="button" onclick="loadPreset()" class="btn-sm btn-blue">Load</button>
        <button type="button" onclick="deletePreset()" class="btn-sm btn-red">Delete</button>
      </div>
      <div style="display:flex;gap:8px;margin-top:8px;align-items:center">
        <input type="text" name="save_preset_name" id="presetName" placeholder="Preset name e.g. Medical Focus"
          style="font-size:13px;padding:7px;border-radius:6px;border:1px solid #ddd;flex:1">
        <button type="button" onclick="savePreset()" class="btn-sm btn-green">Save as Preset</button>
      </div>
    '''

    # Optimizer log
    opt_changes = opt_log.get("adjustments", [])
    opt_html = "".join(f'<li style="font-size:12px;color:#333;margin:4px 0">{c}</li>' for c in opt_changes[-10:]) \
               or '<li style="font-size:12px;color:#999">No automatic adjustments yet — rate more leads to train the optimizer.</li>'
    opt_last = opt_log.get("last_run","Never")

    msg_html = f'<div style="background:#EAF3DE;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#27500A;border:1px solid #A5D6A7">{msg}</div>' if msg else ""

    inc_kw = ", ".join(inputs.get("include_keywords", []))
    exc_kw = ", ".join(inputs.get("exclude_keywords", []))

    # Build rating leaderboard
    ratings = load_ratings()
    cat_scores  = {}
    sig_scores  = {}
    for key, r in ratings.items():
        score = r.get("score",0)
        cat   = r.get("category","Unknown")
        sig   = r.get("signalType","Unknown")
        if cat not in cat_scores: cat_scores[cat]  = []
        if sig not in sig_scores: sig_scores[sig]  = []
        cat_scores[cat].append(score)
        sig_scores[sig].append(score)

    def leaderboard_row(label, scores, color):
        avg = round(sum(scores)/len(scores),1)
        bar_w = int(avg/5*100)
        return f'''<div style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:2px">
            <span style="font-size:12px;color:#333">{label}</span>
            <span style="font-size:12px;font-weight:700;color:{color}">{avg}/5 ({len(scores)} rated)</span>
          </div>
          <div style="background:#eee;border-radius:99px;height:8px">
            <div style="background:{color};width:{bar_w}%;height:8px;border-radius:99px"></div>
          </div>
        </div>'''

    cat_board  = "".join(leaderboard_row(k, v, "#27500A" if sum(v)/len(v)>=3.5 else "#791F1F")
                         for k,v in sorted(cat_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if cat_scores else "<p style='color:#999;font-size:12px'>No ratings yet — rate leads using the 1-5 buttons on the dashboard.</p>"
    sig_board  = "".join(leaderboard_row(k, v, "#0C447C" if sum(v)/len(v)>=3.5 else "#E65100")
                         for k,v in sorted(sig_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if sig_scores else ""
    rating_leaderboard_html = f'''
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Product Category</h3>{cat_board}</div>
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Signal Type</h3>{sig_board if sig_board else "<p style='color:#999;font-size:12px'>No ratings yet.</p>"}</div>
      </div>'''

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inputs — Extron Scanner</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:#f2f2f0;color:#111}}
.wrap{{max-width:1000px;margin:0 auto;padding:24px 20px}}
.header{{background:#0C447C;color:white;padding:20px 28px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between}}
.header h1{{font-size:19px;font-weight:600;color:white;margin:0}}
.nav{{display:flex;gap:8px;flex-wrap:wrap}}
.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:6px}}
.nav a:hover,.nav a.active{{background:rgba(255,255,255,.3);color:white}}
.card{{background:white;border-radius:10px;padding:20px 24px;margin-bottom:14px;border:1px solid #e0e0e0}}
.card h2{{font-size:14px;font-weight:600;margin:0 0 14px;color:#111;border-bottom:1px solid #eee;padding-bottom:8px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}}
.field label{{font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}}
.field input[type=text],.field input[type=number],.field select,.field textarea{{width:100%;font-size:13px;padding:8px 10px;border-radius:6px;border:1px solid #ddd;background:white}}
.field input[type=range]{{width:100%}}
.btn{{display:inline-block;padding:10px 22px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;border:none;text-decoration:none}}
.btn-primary{{background:#0C447C;color:white}}
.btn-primary:hover{{background:#185FA5}}
.btn-sm{{padding:6px 14px;font-size:12px;border-radius:5px;cursor:pointer;border:none;font-weight:600}}
.btn-blue{{background:#0C447C;color:white}}
.btn-green{{background:#27500A;color:white}}
.btn-red{{background:#791F1F;color:white}}
.section-box{{background:#f7f7f5;border-radius:8px;padding:14px;max-height:260px;overflow-y:auto;border:1px solid #e8e8e8}}
.toggle{{display:flex;align-items:center;gap:10px;font-size:13px}}
.toggle input[type=checkbox]{{width:18px;height:18px;cursor:pointer}}
</style>
</head><body><div class="wrap">
  <div class="header">
    <h1>Scan Inputs & Configuration {f"— Active: <b>{active_preset}</b>" if active_preset else ""}</h1>
    <div class="nav">
      <a href="/">Dashboard</a>
      <a href="/inputs" class="active">Inputs</a>
      <a href="/history">History</a>
      <a href="/feed-health">Feed Health</a>
      <a href="/settings">Settings</a>
      <a href="/confidence">Accuracy</a>
    </div>
  </div>

  {msg_html}

  <form method="POST" action="/inputs" id="inputsForm">

    <!-- Presets -->
    <div class="card">
      <h2>Saved Presets</h2>
      {preset_html}
    </div>

    <!-- Categories -->
    <div class="card">
      <h2>Target Product Categories</h2>
      <p style="font-size:12px;color:#666;margin-bottom:10px">Select which product categories the scanner should look for. Uncheck any you want to exclude.</p>
      <div class="section-box">{cats_html}</div>
      <div style="margin-top:8px;display:flex;gap:8px">
        <button type="button" onclick="checkAll('categories')" class="btn-sm btn-blue">Select All</button>
        <button type="button" onclick="uncheckAll('categories')" class="btn-sm btn-red">Clear All</button>
      </div>
    </div>

    <!-- Signals -->
    <div class="card">
      <h2>Trigger Signals</h2>
      <p style="font-size:12px;color:#666;margin-bottom:10px">Select which signals qualify a company as a lead. Uncheck signals you don't care about.</p>
      <div class="section-box">{sigs_html}</div>
      <div style="margin-top:8px;display:flex;gap:8px">
        <button type="button" onclick="checkAll('signals')" class="btn-sm btn-blue">Select All</button>
        <button type="button" onclick="uncheckAll('signals')" class="btn-sm btn-red">Clear All</button>
      </div>
    </div>

    <!-- Company Profile -->
    <div class="card">
      <h2>Company Profile Requirements</h2>
      <div class="grid2" style="margin-bottom:14px">
        <div class="field"><label>Minimum unit price ($)</label>
          <input type="number" name="min_unit_price" value="{inputs.get('min_unit_price',300)}" min="0" step="100"></div>
        <div class="field"><label>Minimum funding amount ($M)</label>
          <input type="number" name="min_funding_amount" value="{inputs.get('min_funding_amount',3)}" min="0" step="0.5"></div>
        <div class="field"><label>Min employees</label>
          <input type="number" name="employee_min" value="{inputs.get('employee_min',1)}" min="1"></div>
        <div class="field"><label>Max employees</label>
          <input type="number" name="employee_max" value="{inputs.get('employee_max',1000)}" min="1"></div>
        <div class="field"><label>Founded after year</label>
          <input type="number" name="founded_after" value="{inputs.get('founded_after',2000)}" min="1900" max="2026"></div>
        <div class="field"><label>Founded before year</label>
          <input type="number" name="founded_before" value="{inputs.get('founded_before',2026)}" min="1900" max="2026"></div>
      </div>
      <div style="display:flex;gap:20px;flex-wrap:wrap">
        <label class="toggle"><input type="checkbox" name="oem_only" {"checked" if inputs.get("oem_only",True) else ""}> OEM companies only</label>
        <label class="toggle"><input type="checkbox" name="require_embedded" {"checked" if inputs.get("require_embedded",True) else ""}> Require embedded computing (CPU/FPGA/MCU)</label>
      </div>
      <div style="margin-top:12px">
        <p style="font-size:12px;font-weight:600;color:#555;margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em">Company stages to include:</p>
        <div style="display:flex;flex-wrap:wrap;gap:12px">{stages_html}</div>
      </div>
    </div>

    <!-- Locations -->
    <div class="card">
      <h2>Target Locations</h2>
      <p style="font-size:12px;color:#666;margin-bottom:10px">Select "Anywhere" for no location filter, or choose specific states and countries.</p>
      <div class="section-box" style="max-height:300px">{locs_html}</div>
    </div>

    <!-- AI Behavior -->
    <div class="card">
      <h2>AI Behavior & Filtering</h2>
      <div class="grid2" style="margin-bottom:14px">
        <div class="field"><label>Confidence threshold (0-100)</label>
          <input type="number" name="confidence_threshold" value="{inputs.get('confidence_threshold',30)}" min="0" max="100">
          <p style="font-size:11px;color:#999;margin-top:3px">Leads below this score are excluded. Lower = more leads, higher = stricter.</p>
        </div>
        <div class="field"><label>Minimum urgency score (0-100)</label>
          <input type="number" name="min_urgency_score" value="{inputs.get('min_urgency_score',0)}" min="0" max="100">
          <p style="font-size:11px;color:#999;margin-top:3px">Only show leads with urgency at or above this score.</p>
        </div>
        <div class="field"><label>Articles sent to AI per scan</label>
          <input type="number" name="articles_per_scan" value="{inputs.get('articles_per_scan',40)}" min="5" max="100">
          <p style="font-size:11px;color:#999;margin-top:3px">More articles = more leads but slower scan and higher cost.</p>
        </div>
        <div class="field"><label>Lookback window (days)</label>
          <select name="lookback_days">
            <option value="7" {sel("7", str(inputs.get("lookback_days",90)))}>7 days</option>
            <option value="14" {sel("14", str(inputs.get("lookback_days",90)))}>14 days</option>
            <option value="30" {sel("30", str(inputs.get("lookback_days",90)))}>30 days</option>
            <option value="60" {sel("60", str(inputs.get("lookback_days",90)))}>60 days</option>
            <option value="90" {sel("90", str(inputs.get("lookback_days",90)))}>90 days (default)</option>
            <option value="180" {sel("180", str(inputs.get("lookback_days",90)))}>180 days</option>
          </select>
        </div>
        <div class="field"><label>Sensitivity level</label>
          <select name="sensitivity">
            <option value="broad" {sel("broad", inputs.get("sensitivity","normal"))}>Broad — more leads, lower bar</option>
            <option value="normal" {sel("normal", inputs.get("sensitivity","normal"))}>Normal — balanced</option>
            <option value="tight" {sel("tight", inputs.get("sensitivity","normal"))}>Tight — fewer, higher quality leads</option>
          </select>
        </div>
        <div class="field"><label>Pre-filter strictness</label>
          <select name="prefilter_mode">
            <option value="strict" {sel("strict", inputs.get("prefilter_mode","strict"))}>Strict — must match hardware AND signal keyword</option>
            <option value="relaxed" {sel("relaxed", inputs.get("prefilter_mode","strict"))}>Relaxed — must match hardware OR signal keyword</option>
            <option value="off" {sel("off", inputs.get("prefilter_mode","strict"))}>Off — send all articles to AI (slowest, most leads)</option>
          </select>
        </div>
      </div>
      <div class="grid2">
        <div class="field"><label>Include keywords (comma separated)</label>
          <input type="text" name="include_keywords" value="{inc_kw}" placeholder="e.g. firmware, embedded, FPGA">
          <p style="font-size:11px;color:#999;margin-top:3px">Articles containing these words always pass the filter.</p>
        </div>
        <div class="field"><label>Exclude keywords (comma separated)</label>
          <input type="text" name="exclude_keywords" value="{exc_kw}" placeholder="e.g. consumer, retail, vehicle">
          <p style="font-size:11px;color:#999;margin-top:3px">Articles containing these words are always excluded.</p>
        </div>
      </div>
    </div>

    <!-- Scan Schedule -->
    <div class="card">
      <h2>Scan Schedule</h2>
      <div class="grid2" style="margin-bottom:14px">
        <div>
          <label class="toggle" style="margin-bottom:10px">
            <input type="checkbox" name="scan_auto" {"checked" if inputs.get("scan_auto",False) else ""}> Enable automatic scanning
          </label>
          <div class="field" style="margin-top:8px"><label>Scan every X minutes</label>
            <input type="number" name="scan_interval_mins" value="{inputs.get('scan_interval_mins',30)}" min="5" max="1440">
          </div>
        </div>
        <div>
          <label class="toggle" style="margin-bottom:10px">
            <input type="checkbox" name="scan_all_day" {"checked" if inputs.get("scan_all_day",True) else ""}> Scan all day (24 hours)
          </label>
          <div class="grid2" style="margin-top:8px">
            <div class="field"><label>Start hour (PT, 0-23)</label>
              <input type="number" name="scan_start_hour" value="{inputs.get('scan_start_hour',8)}" min="0" max="23"></div>
            <div class="field"><label>End hour (PT, 0-23)</label>
              <input type="number" name="scan_end_hour" value="{inputs.get('scan_end_hour',20)}" min="0" max="23"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Self-Optimizer -->
    <div class="card">
      <h2>Self-Optimizer</h2>
      <p style="font-size:12px;color:#555;margin-bottom:10px">The optimizer automatically adjusts the confidence threshold and disables signals that consistently produce bad leads, based on your feedback ratings. Last run: {opt_last}</p>
      <p style="font-size:12px;font-weight:600;color:#333;margin-bottom:6px">Recent automatic adjustments:</p>
      <ul style="list-style:disc;padding-left:20px;margin-bottom:12px">{opt_html}</ul>
      <label class="toggle">
        <input type="checkbox" name="optimizer_enabled" {"checked" if inputs.get("optimizer_enabled",True) else ""}> Enable self-optimizer
      </label>
    </div>

    <!-- Save -->
    <div style="display:flex;gap:12px;margin-top:4px;padding-bottom:20px">
      <button type="submit" class="btn btn-primary">Save Inputs & Apply to Next Scan</button>
      <button type="button" onclick="resetDefaults()" class="btn" style="background:#666;color:white">Reset to Defaults</button>
    </div>

  </form>
</div>

<script>
function checkAll(name){{
  document.querySelectorAll(`input[name="${{name}}"]`).forEach(el=>el.checked=true);
}}
function uncheckAll(name){{
  document.querySelectorAll(`input[name="${{name}}"]`).forEach(el=>el.checked=false);
}}
function savePreset(){{
  const name = document.getElementById("presetName").value.trim();
  if(!name){{alert("Enter a preset name first.");return;}}
  const form = document.getElementById("inputsForm");
  const data = new FormData(form);
  data.append("action","save_preset");
  data.append("preset_name",name);
  fetch("/inputs",{{method:"POST",body:new URLSearchParams(data)}}).then(()=>location.reload());
}}
function loadPreset(){{
  const name = document.getElementById("presetSelect").value;
  if(!name){{alert("Select a preset first.");return;}}
  fetch("/inputs/load-preset",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{name}}) }}).then(()=>location.reload());
}}
function deletePreset(){{
  const name = document.getElementById("presetSelect").value;
  if(!name){{alert("Select a preset to delete.");return;}}
  if(!confirm(`Delete preset "${{name}}"?`))return;
  fetch("/inputs/delete-preset",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{name}})}}).then(()=>location.reload());
}}
function resetDefaults(){{
  if(confirm("Reset all inputs to defaults?")){{
    fetch("/inputs/reset",{{method:"POST"}}).then(()=>location.reload());
  }}
}}
</script>
</body></html>"""

def build_confidence_page():
    """Weekly confidence score trends page."""
    scores = load_confidence_scores()
    optimizer = load_optimizer_state()
    feedback  = load_feedback()

    # Build chart data
    if scores:
        labels = json.dumps([s["date"] for s in scores[-30:]])
        values = json.dumps([s["score"] for s in scores[-30:]])
        totals = json.dumps([s["total"] for s in scores[-30:]])
    else:
        labels = "[]"; values = "[]"; totals = "[]"

    # Feedback summary
    good  = sum(1 for f in feedback.values() if f.get("rating") == "good")
    wrong = sum(1 for f in feedback.values() if f.get("rating") == "wrong_industry")
    not_c = sum(1 for f in feedback.values() if f.get("rating") == "not_our_customer")
    total_fb = len(feedback)

    # Optimizer notes
    adjustments = optimizer.get("adjustments", [])
    adj_html = "".join(f'<li style="margin:4px 0;font-size:13px;color:#333">{a}</li>' for a in adjustments) or "<li style='color:#999;font-size:13px'>No adjustments yet — rate more leads to train the optimizer.</li>"

    latest = scores[-1] if scores else None
    latest_score_str = f"{latest['score']}% on-target ({latest['on_target']} of {latest['total']} leads)" if latest else "No scans yet"

    good_sigs  = optimizer.get("good_signals",  {})
    bad_sigs   = optimizer.get("bad_signals",   {})
    good_sig_html = "".join(f'<span style="background:#EAF3DE;color:#27500A;padding:2px 8px;border-radius:4px;font-size:12px;margin:2px;display:inline-block">{k} ({v})</span>' for k,v in sorted(good_sigs.items(), key=lambda x:-x[1])[:8]) or "<span style='color:#999;font-size:12px'>None yet</span>"
    bad_sig_html  = "".join(f'<span style="background:#FCEBEB;color:#791F1F;padding:2px 8px;border-radius:4px;font-size:12px;margin:2px;display:inline-block">{k} ({v})</span>' for k,v in sorted(bad_sigs.items(),  key=lambda x:-x[1])[:8]) or "<span style='color:#999;font-size:12px'>None yet</span>"

    # Build rating leaderboard
    ratings = load_ratings()
    cat_scores  = {}
    sig_scores  = {}
    for key, r in ratings.items():
        score = r.get("score",0)
        cat   = r.get("category","Unknown")
        sig   = r.get("signalType","Unknown")
        if cat not in cat_scores: cat_scores[cat]  = []
        if sig not in sig_scores: sig_scores[sig]  = []
        cat_scores[cat].append(score)
        sig_scores[sig].append(score)

    def leaderboard_row(label, scores, color):
        avg = round(sum(scores)/len(scores),1)
        bar_w = int(avg/5*100)
        return f'''<div style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:2px">
            <span style="font-size:12px;color:#333">{label}</span>
            <span style="font-size:12px;font-weight:700;color:{color}">{avg}/5 ({len(scores)} rated)</span>
          </div>
          <div style="background:#eee;border-radius:99px;height:8px">
            <div style="background:{color};width:{bar_w}%;height:8px;border-radius:99px"></div>
          </div>
        </div>'''

    cat_board  = "".join(leaderboard_row(k, v, "#27500A" if sum(v)/len(v)>=3.5 else "#791F1F")
                         for k,v in sorted(cat_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if cat_scores else "<p style='color:#999;font-size:12px'>No ratings yet — rate leads using the 1-5 buttons on the dashboard.</p>"
    sig_board  = "".join(leaderboard_row(k, v, "#0C447C" if sum(v)/len(v)>=3.5 else "#E65100")
                         for k,v in sorted(sig_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if sig_scores else ""
    rating_leaderboard_html = f'''
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Product Category</h3>{cat_board}</div>
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Signal Type</h3>{sig_board if sig_board else "<p style='color:#999;font-size:12px'>No ratings yet.</p>"}</div>
      </div>'''

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Confidence Score — Extron Scanner</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:#f2f2f0;color:#111}}
  .wrap{{max-width:900px;margin:0 auto;padding:28px 20px}}
  .header{{background:#0C447C;color:white;padding:24px 28px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between}}
  .header h1{{font-size:20px;font-weight:600;color:white}}
  .nav-link{{color:rgba(255,255,255,.75);font-size:13px;text-decoration:none;background:rgba(255,255,255,.15);padding:7px 14px;border-radius:6px}}
  .card{{background:white;border-radius:10px;padding:20px 24px;margin-bottom:14px;border:1px solid #e0e0e0}}
  .card h2{{font-size:14px;font-weight:600;margin:0 0 12px;color:#111}}
  .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px}}
  .stat{{background:white;border-radius:8px;padding:14px;border:1px solid #e0e0e0;text-align:center}}
  .stat-num{{font-size:28px;font-weight:700;color:#0C447C}}
  .stat-label{{font-size:11px;color:#888;margin-top:3px;text-transform:uppercase;letter-spacing:.04em}}
</style>
</head><body><div class="wrap">
  <div class="header">
    <div><h1>Confidence Score Tracker</h1><p style="font-size:13px;opacity:.75;margin-top:4px">How accurate is the scanner? Track improvement over time.</p></div>
    <a href="/" class="nav-link">&larr; Back</a>
  </div>

  <div class="stats">
    <div class="stat"><div class="stat-num">{len(scores)}</div><div class="stat-label">Total scans</div></div>
    <div class="stat"><div class="stat-num">{scores[-1]["score"] if scores else "—"}%</div><div class="stat-label">Latest accuracy</div></div>
    <div class="stat"><div class="stat-num">{total_fb}</div><div class="stat-label">Leads rated</div></div>
    <div class="stat"><div class="stat-num">{good}</div><div class="stat-label">Good leads</div></div>
    <div class="stat"><div class="stat-num">{wrong + not_c}</div><div class="stat-label">Bad leads</div></div>
  </div>

  <div class="card">
    <h2>Accuracy trend (last 30 scans)</h2>
    <p style="font-size:12px;color:#999;margin-bottom:14px">Latest: {latest_score_str}</p>
    <canvas id="scoreChart" height="80"></canvas>
    <script>
      new Chart(document.getElementById("scoreChart"), {{
        type: "line",
        data: {{
          labels: {labels},
          datasets: [
            {{label:"Accuracy %",data:{values},borderColor:"#0C447C",backgroundColor:"rgba(12,68,124,.1)",tension:.3,fill:true,pointRadius:4}},
          ]
        }},
        options:{{responsive:true,plugins:{{legend:{{position:"top"}}}},scales:{{y:{{min:0,max:100,ticks:{{callback:v=>v+"%"}}}}}}}}
      }});
    </script>
  </div>

  <div class="card">
    <h2>Feedback summary</h2>
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:14px">
      <div style="background:#EAF3DE;border-radius:6px;padding:10px 18px;font-size:13px;color:#27500A"><b>{good}</b> Good leads rated</div>
      <div style="background:#FFF3E0;border-radius:6px;padding:10px 18px;font-size:13px;color:#E65100"><b>{wrong}</b> Wrong industry</div>
      <div style="background:#FCEBEB;border-radius:6px;padding:10px 18px;font-size:13px;color:#791F1F"><b>{not_c}</b> Not our customer</div>
    </div>
    <p style="font-size:12px;color:#555">Rate leads on the main dashboard using the Good Lead / Wrong Industry / Not Our Customer buttons. The self-optimizer reads your ratings every 10 leads and adjusts the scanner filters automatically.</p>
  </div>

  <div class="card">
    <h2>Self-optimizer insights</h2>
    <p style="font-size:12px;color:#999;margin-bottom:10px">Last optimized: {optimizer.get("last_optimized","Never")}</p>
    <p style="font-size:13px;font-weight:600;color:#27500A;margin-bottom:6px">Signals performing well:</p>
    <div style="margin-bottom:12px">{good_sig_html}</div>
    <p style="font-size:13px;font-weight:600;color:#791F1F;margin-bottom:6px">Signals producing bad leads:</p>
    <div style="margin-bottom:14px">{bad_sig_html}</div>
    <p style="font-size:13px;font-weight:600;color:#333;margin-bottom:6px">Optimizer notes:</p>
    <ul style="list-style:disc;padding-left:20px">{adj_html}</ul>
  </div>

  <div class="card">
    <h2>Rating Leaderboard (1-5 scale)</h2>
    <p style="font-size:12px;color:#555;margin-bottom:12px">Average ratings by category and signal type, based on your 1-5 lead ratings. Higher = better match for Extron. The self-optimizer uses this to improve future scans.</p>
    {rating_leaderboard_html}
  </div>

</div></body></html>"""

def build_page():
    s         = scan_state
    settings  = load_settings()
    statuses  = load_statuses()
    all_leads = load_all_leads()

    if s.get("last_ai_error"):
        status_html = (
            f'<div style="background:#FCEBEB;border-radius:8px;padding:16px 18px;margin-bottom:16px;'
            f'font-size:14px;color:#791F1F;border:2px solid #E24B4A;font-weight:600">'
            f'⚠ The AI call failed on the last scan — this is the actual error:<br>'
            f'<span style="font-family:monospace;font-weight:400;font-size:13px">{s["last_ai_error"]}</span>'
            f'</div>'
        )
    elif s["running"]:
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
        key      = l.get("name","").lower().strip()
        prev_r   = get_lead_rating(key)   # previous 1-5 rating
        cur_note = get_lead_note(key)     # saved note
        st       = statuses.get(key, {}).get("status","new")
        st_colors = {"contacted":"#27500A","dismissed":"#791F1F","watch":"#633806","new":"#888"}
        st_bgs    = {"contacted":"#EAF3DE","dismissed":"#FCEBEB","watch":"#FAEEDA","new":"#F1EFE8"}
        stc = st_colors.get(st,"#888"); stbg = st_bgs.get(st,"#F1EFE8")
        status_badge = "" if st=="new" else f'<span style="background:{stbg};color:{stc};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{st.upper()}</span>'
        _ws = l.get("website","") or ""
        if not _ws or not _ws.startswith("http"):
            _slug = l.get("name","").lower().replace(" ","").replace(",","").replace(".","").replace("'","").replace("&","")
            _ws = f"https://www.{_slug}.com"
        website_btn  = f'<a href="{_ws}" target="_blank" style="padding:3px 10px;border-radius:4px;background:#0C447C;color:white;font-size:11px;text-decoration:none;margin-left:auto">Visit website</a>' 
        # Build signal box safely — avoids f-string quote conflicts
        _src_url  = l.get("sourceUrl","") or ""
        # Link tail sits at the end of the signal-detail paragraph itself, not
        # the header line above it — this is the "first section" link the
        # user asked for, separate from the "Read article" button lower down.
        _detail_link = (" <a href=\"" + _src_url + "\" target=\"_blank\" style=\"color:#0C447C;font-weight:600;font-size:12px;text-decoration:underline;white-space:nowrap\">&#8594; source</a>" if _src_url.startswith("http") else "")
        _sig_box  = ("<div style=\"background:#f7f7f5;border-radius:6px;padding:10px 12px;margin-bottom:8px\">"
                     + "<span style=\"font-size:11px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.04em\">"
                     + _safe(l.get("signalType")).upper() + " &middot; " + _safe(l.get("signalDate"))
                     + "</span>"
                     + "<p style=\"margin:4px 0 0;font-size:13px;color:#333;line-height:1.5\">" + _safe(l.get("signalDetail")) + _detail_link + "</p>"
                     + "</div>")
        return (f'<div class="lead-card" data-category="{l.get("category","").lower()}" data-signal="{_safe(l.get("signalType")).lower()}" data-status="{st}" data-urgency="{u}" data-geo="{l.get("hq","").lower()}" data-name="{l.get("name","").lower()}" style="background:white;border-radius:8px;border:1px solid #e0e0e0;border-left:4px solid {fc};padding:16px 18px;margin-bottom:10px">'
            f'<div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:8px">'
            f'<div><span style="font-size:15px;font-weight:600;color:#111">{l.get("name","")}</span><span style="font-size:12px;color:#888;margin-left:8px">{l.get("hq","")} &middot; {_safe(l.get("stage"))}</span></div>'
            f'<div style="display:flex;gap:6px;flex-wrap:wrap"><span style="background:{cbg};color:{cfc};padding:2px 10px;border-radius:4px;font-size:11px;font-weight:600">{l.get("category","")}</span><span style="background:{bg};color:{fc};padding:2px 10px;border-radius:4px;font-size:11px;font-weight:700">{lbl} {u}%</span>{status_badge}</div>'
            f'</div>'
            + _sig_box
            + f'<p style="font-size:12px;color:#555;line-height:1.5;margin:0 0 4px"><b style="color:#633806">Why reach out:</b> {_safe(l.get("whyNow"))}</p>'
            + f'<p style="font-size:12px;color:#888;margin:0 0 10px;line-height:1.5">{_safe(l.get("description"))}</p>'
            + (f'<p style="font-size:12px;color:#0C447C;line-height:1.5;margin:0 0 8px;padding:8px 10px;background:#E6F1FB;border-radius:5px"><b style="color:#0C447C">Extron fit:</b> {l["extronFit"]}</p>' if l.get('extronFit') else '')
            + f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-bottom:10px">'
            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Key products</p><p style="font-size:12px;color:#333;line-height:1.4">{l["keyProducts"]}</p></div>' if l.get('keyProducts') else '')
            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Target customers</p><p style="font-size:12px;color:#333;line-height:1.4">{l["targetCustomers"]}</p></div>' if l.get('targetCustomers') else '')
            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Competitors</p><p style="font-size:12px;color:#333;line-height:1.4">{l["topCompetitors"]}</p></div>' if l.get('topCompetitors') else '')
            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Est. revenue</p><p style="font-size:12px;color:#333;line-height:1.4">{l["annualRevenue"]}</p></div>' if l.get('annualRevenue') else '')
            + (f'<div style="background:#f7f7f5;border-radius:5px;padding:8px 10px"><p style="font-size:10px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:3px">Supply chain</p><p style="font-size:12px;color:#333;line-height:1.4">{l["supplyChainNotes"]}</p></div>' if l.get('supplyChainNotes') and _safe(l.get('supplyChainNotes')).lower() not in ['unknown','n/a',''] else '')
            + f'</div>'
            + f'<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px">'
            + (f'<a href="{_src_url}" target="_blank" style="padding:3px 10px;border-radius:4px;background:#f0f0f0;color:#0C447C;font-size:11px;text-decoration:none">Read article →</a>' if _src_url.startswith("http") else '')
            + f'{website_btn}</div>'
            + f'<div style="border-top:1px solid #eee;margin-top:10px;padding-top:10px">'
            + f'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">'
            + f'<button id="basket-{key}" data-key="{key}" onclick="toggleBasket(this.dataset.key,this)" style="font-size:11px;padding:5px 14px;border-radius:5px;border:1px solid #ddd;background:#f5f5f5;color:#333;cursor:pointer;font-weight:600">+ Add to Basket</button>'
            + f'<span style="font-size:11px;color:#888;margin-left:4px">Rate this lead for Extron fit:</span>'
            + f'<div style="display:flex;gap:3px;align-items:center">'
            + f'<button class="rb" data-key="{key}" data-score="1" data-sig="{l.get("signalType","").replace(chr(39),"")}" data-cat="{l.get("category","").replace(chr(39),"")}" onclick="rateLead(this.dataset.key,1,this.dataset.sig,this.dataset.cat,this)" style="width:30px;height:30px;border-radius:4px;border:1px solid #ddd;background:#e0e0e0;color:#333;cursor:pointer;font-weight:700">1</button>'
            + f'<button class="rb" data-key="{key}" data-score="2" data-sig="{l.get("signalType","").replace(chr(39),"")}" data-cat="{l.get("category","").replace(chr(39),"")}" onclick="rateLead(this.dataset.key,2,this.dataset.sig,this.dataset.cat,this)" style="width:30px;height:30px;border-radius:4px;border:1px solid #ddd;background:#e0e0e0;color:#333;cursor:pointer;font-weight:700">2</button>'
            + f'<button class="rb" data-key="{key}" data-score="3" data-sig="{l.get("signalType","").replace(chr(39),"")}" data-cat="{l.get("category","").replace(chr(39),"")}" onclick="rateLead(this.dataset.key,3,this.dataset.sig,this.dataset.cat,this)" style="width:30px;height:30px;border-radius:4px;border:1px solid #ddd;background:#e0e0e0;color:#333;cursor:pointer;font-weight:700">3</button>'
            + f'<button class="rb" data-key="{key}" data-score="4" data-sig="{l.get("signalType","").replace(chr(39),"")}" data-cat="{l.get("category","").replace(chr(39),"")}" onclick="rateLead(this.dataset.key,4,this.dataset.sig,this.dataset.cat,this)" style="width:30px;height:30px;border-radius:4px;border:1px solid #ddd;background:#e0e0e0;color:#333;cursor:pointer;font-weight:700">4</button>'
            + f'<button class="rb" data-key="{key}" data-score="5" data-sig="{l.get("signalType","").replace(chr(39),"")}" data-cat="{l.get("category","").replace(chr(39),"")}" onclick="rateLead(this.dataset.key,5,this.dataset.sig,this.dataset.cat,this)" style="width:30px;height:30px;border-radius:4px;border:1px solid #ddd;background:#e0e0e0;color:#333;cursor:pointer;font-weight:700">5</button>'
            + f'<span class="rl" style="font-size:11px;color:#888;margin-left:4px">' + (f'{prev_r["score"]}/5 rated {prev_r["date"]}' if prev_r else 'Unrated') + f'</span>'
            + f'</div></div>'
            + f'<textarea data-key="{key}" onblur="saveNote(this.dataset.key,this)" placeholder="Add notes about this lead..." style="width:100%;font-size:11px;padding:6px 8px;border-radius:5px;border:1px solid #ddd;resize:vertical;min-height:38px;font-family:inherit;box-sizing:border-box">{cur_note}</textarea>'
            + f'</div></div>')

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
    inputs = load_inputs()
    active_preset = inputs.get("active_preset","")
    preset_label = f" — Preset: {active_preset}" if active_preset else ""
    scans_today, api_cost = get_api_cost_estimate()
    daily = load_daily_leads()
    leads_today = len(daily.get("leads",[])) if daily.get("date") == date.today().isoformat() else 0
    last = f"Last scan: {s['last_run']}" if s["last_run"] else "No scans run yet"
    daily_summary = f"Today: {scans_today} scans · {leads_today} leads found · Est. API cost: ${api_cost:.2f}" if scans_today > 0 else ""

    # Build rating leaderboard
    ratings = load_ratings()
    cat_scores  = {}
    sig_scores  = {}
    for key, r in ratings.items():
        score = r.get("score",0)
        cat   = r.get("category","Unknown")
        sig   = r.get("signalType","Unknown")
        if cat not in cat_scores: cat_scores[cat]  = []
        if sig not in sig_scores: sig_scores[sig]  = []
        cat_scores[cat].append(score)
        sig_scores[sig].append(score)

    def leaderboard_row(label, scores, color):
        avg = round(sum(scores)/len(scores),1)
        bar_w = int(avg/5*100)
        return f'''<div style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:2px">
            <span style="font-size:12px;color:#333">{label}</span>
            <span style="font-size:12px;font-weight:700;color:{color}">{avg}/5 ({len(scores)} rated)</span>
          </div>
          <div style="background:#eee;border-radius:99px;height:8px">
            <div style="background:{color};width:{bar_w}%;height:8px;border-radius:99px"></div>
          </div>
        </div>'''

    cat_board  = "".join(leaderboard_row(k, v, "#27500A" if sum(v)/len(v)>=3.5 else "#791F1F")
                         for k,v in sorted(cat_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if cat_scores else "<p style='color:#999;font-size:12px'>No ratings yet — rate leads using the 1-5 buttons on the dashboard.</p>"
    sig_board  = "".join(leaderboard_row(k, v, "#0C447C" if sum(v)/len(v)>=3.5 else "#E65100")
                         for k,v in sorted(sig_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if sig_scores else ""
    rating_leaderboard_html = f'''
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Product Category</h3>{cat_board}</div>
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Signal Type</h3>{sig_board if sig_board else "<p style='color:#999;font-size:12px'>No ratings yet.</p>"}</div>
      </div>'''

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Extron Lead Intelligence</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f2f0;color:#111}}.wrap{{max-width:980px;margin:0 auto;padding:24px 16px}}.header{{background:#0C447C;color:white;padding:18px 24px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}.header h1{{font-size:18px;font-weight:600;color:white}}.nav{{display:flex;gap:8px;flex-wrap:wrap}}.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:5px}}.nav a.active{{background:rgba(255,255,255,.3);color:white;font-weight:600}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:18px}}.stat{{background:white;border-radius:8px;padding:14px;border:1px solid #e0e0e0}}.stat-num{{font-size:24px;font-weight:700;color:#0C447C}}.stat-label{{font-size:11px;color:#888;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}}.card{{background:white;border-radius:10px;padding:18px 22px;margin-bottom:14px;border:1px solid #e0e0e0}}.card h2{{font-size:14px;font-weight:600;margin:0 0 12px;color:#111}}.btn{{display:inline-block;background:#0C447C;color:white;border:none;padding:9px 20px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none}}.btn-sm{{padding:5px 12px;font-size:12px}}.btn-green{{background:#27500A}}.btn-gray{{background:#666}}.filter-bar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px;background:white;border-radius:8px;padding:10px 14px;border:1px solid #e0e0e0}}.filter-bar select,.filter-bar input{{padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px}}.scroll{{max-height:400px;overflow-y:auto;border:1px solid #eee;border-radius:6px}}table{{width:100%;border-collapse:collapse}}th{{padding:8px;text-align:left;font-size:11px;color:#666;font-weight:600;background:#f7f7f5;border-bottom:1px solid #e8e8e8}}#toast{{position:fixed;bottom:24px;right:24px;background:#1a1a2e;color:#fff;padding:10px 16px;border-radius:7px;font-size:13px;opacity:0;transition:opacity .3s;pointer-events:none;z-index:999}}</style></head>
<body><div class="wrap">
<div class="header"><div><h1>Extron Lead Intelligence</h1><p style="font-size:12px;opacity:.7;margin-top:3px">{last}</p></div>
<nav class="nav"><a href="/" class="active">Dashboard</a><a href="/inputs">Inputs</a><a href="/history">History</a><a href="/feed-health">Feed Health</a><a href="/settings">Settings</a><a href="/confidence">Accuracy</a></nav></div>
{status_html}
<div class="stats">
<div class="stat"><div class="stat-num">{len(all_leads)}</div><div class="stat-label">Total leads ever</div></div>
<div class="stat"><div class="stat-num">{hot_total}</div><div class="stat-label">Hot leads (85+)</div></div>
<div class="stat"><div class="stat-num">{new_today}</div><div class="stat-label">New today</div></div>
<div class="stat"><div class="stat-num">{contacted}</div><div class="stat-label">Contacted</div></div>
<div class="stat"><div class="stat-num">{watching}</div><div class="stat-label">Watching</div></div>
</div>
<div class="card"><div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:12px"><h2 style="margin:0">Run a scan</h2><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">{auto_note}<a href="/export/csv?scope=current" class="btn btn-green btn-sm">Export current CSV</a><a href="/export/csv?scope=all" class="btn btn-gray btn-sm">Export all CSV</a></div></div>
<form method="POST" action="/run" style="display:inline"><button type="submit" class="btn {"btn-disabled" if s["running"] else ""}" {"disabled" if s["running"] else ""}>{"Scanning..." if s["running"] else "Run Scan Now"}</button></form>
<a href="/generate-pdf" style="display:inline-block;margin-left:8px;padding:10px 20px;background:#791F1F;color:white;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none">Download All Leads PDF</a>
<button onclick="exportBasket('pdf')" style="margin-left:8px;padding:10px 20px;background:#27500A;color:white;border-radius:6px;font-size:13px;font-weight:600;border:none;cursor:pointer">Export Basket PDF <span id="basketCount" style="background:rgba(255,255,255,.3);border-radius:99px;padding:1px 7px;font-size:11px;margin-left:4px">0</span></button>

</div>
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
<div class="card">
  <h2>PDF Reports ({len(files)} available)</h2>
  <div style="max-height:280px;overflow-y:auto">
    {pdf_links if pdf_links else '<p style="color:#999;font-size:13px;padding:8px 0">No PDF reports yet — run a scan first.</p>'}
  </div>
</div>
<div class="card"><h2>Articles analyzed ({len(articles)})</h2><div class="scroll"><table><thead><tr><th>Article</th><th>Date</th></tr></thead><tbody>{article_rows}</tbody></table></div></div>
</div><div id="toast"></div>
<script>
function filterCards(){{var cat=document.getElementById("fCat").value.toLowerCase(),sig=document.getElementById("fSig").value.toLowerCase(),status=document.getElementById("fStatus").value.toLowerCase(),geo=document.getElementById("fGeo").value.toLowerCase(),search=document.getElementById("fSearch").value.toLowerCase(),vis=0;document.querySelectorAll(".lead-card").forEach(function(c){{var show=(!cat||c.dataset.category.includes(cat))&&(!sig||c.dataset.signal.includes(sig))&&(!status||c.dataset.status===status)&&(!geo||c.dataset.geo.includes(geo))&&(!search||c.dataset.name.includes(search));c.style.display=show?"":"none";if(show)vis++;}});document.getElementById("filterCount").textContent=vis+" shown";}}
function setStatus(key,status){{fetch("/lead/status",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{key:key,status:status}})}}).then(function(){{toast(status==="clear"?"Status cleared":"Marked: "+status);setTimeout(function(){{location.reload();}},800);}});}}
function toast(msg){{var el=document.getElementById("toast");el.textContent=msg;el.style.opacity="1";setTimeout(function(){{el.style.opacity="0";}},2200);}}
// ── Basket ─────────────────────────────────────────────────────
var basket={{}};
function toggleBasket(key,btn){{
  if(basket[key]){{delete basket[key];btn.textContent="+ Add to Basket";btn.style.background="#f5f5f5";btn.style.color="#333";}}
  else{{basket[key]=true;btn.textContent="✓ In Basket";btn.style.background="#27500A";btn.style.color="white";}}
  var cnt=Object.keys(basket).length;
  var el=document.getElementById("basketCount");
  if(el)el.textContent=cnt;
}}
function exportBasket(mode){{
  var keys=Object.keys(basket);
  if(keys.length===0){{alert("Add leads to your basket first.");return;}}
  fetch("/export-basket",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{keys:keys,mode:mode}})}})
    .then(function(r){{
      if(!r.ok){{throw new Error("Server returned "+r.status);}}
      var disposition = r.headers.get("Content-Disposition") || "";
      var match = disposition.match(/filename="?([^"]+)"?/);
      var filename = match ? match[1] : (mode==="csv" ? "Extron_Selected_Leads.csv" : "Extron_Selected_Leads.pdf");
      return r.blob().then(function(blob){{ return {{blob:blob, filename:filename}}; }});
    }})
    .then(function(result){{
      var url = window.URL.createObjectURL(result.blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = result.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast("Basket "+mode.toUpperCase()+" downloaded");
    }})
    .catch(function(e){{alert("Export failed: "+e);}});
}}
// ── 1-5 Rating ─────────────────────────────────────────────────
function rateLead(key,score,sig,cat,btn){{
  fetch("/lead/rate",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{key:key,score:score,signalType:sig,category:cat}})}})
    .then(function(){{
      var container=btn.parentElement;
      var btns=container.querySelectorAll(".rb");
      btns.forEach(function(b,i){{
        b.style.background=i<score?"#0C447C":"#e0e0e0";
        b.style.color=i<score?"white":"#333";
      }});
      var lbl=container.querySelector(".rl");
      if(lbl)lbl.textContent=score+"/5 rated";
      toast("Rated "+score+"/5");
    }});
}}
// ── Notes ───────────────────────────────────────────────────────
function saveNote(key,el){{
  fetch("/lead/note",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{key:key,note:el.value}})}})
    .then(function(){{toast("Note saved");}});
}}
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
        _ws2 = l.get("website","") or ""
        if not _ws2 or not _ws2.startswith("http"):
            _slug2 = l.get("name","").lower().replace(" ","").replace(",","").replace(".","").replace("'","").replace("&","")
            _ws2 = f"https://www.{_slug2}.com"
        website_btn = f'<a href="{_ws2}" target="_blank" onclick="event.stopPropagation()" style="padding:3px 10px;border-radius:4px;background:#0C447C;color:white;font-size:11px;text-decoration:none">Website</a>' 
        cur_note  = get_lead_note(key)
        _note_esc = cur_note.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
        rows += (f'<tr class="hist-row" data-category="{l.get("category","").lower()}" data-status="{st}" data-name="{l.get("name","").lower()}" data-signal="{_safe(l.get("signalType")).lower()}" style="border-bottom:1px solid #f0f0f0;cursor:pointer" onclick="toggleDetail(this)">'
            f'<td style="padding:10px 8px;font-size:13px;font-weight:600">{l.get("name","")}</td>'
            f'<td style="padding:10px 8px"><span style="background:{cbg};color:{cfc};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">{l.get("category","")}</span></td>'
            f'<td style="padding:10px 8px;font-size:12px;color:#555">{_safe(l.get("signalType"))}</td>'
            f'<td style="padding:10px 8px"><span style="background:{bg};color:{fc};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">{lbl} {u}%</span></td>'
            f'<td style="padding:10px 8px"><span style="background:{st_bgs.get(st,"#f1efe8")};color:{st_fgs.get(st,"#888")};padding:2px 8px;border-radius:4px;font-size:11px">{st}</span></td>'
            f'<td style="padding:10px 8px;font-size:11px;color:#999">{l.get("added_date","")}</td>'
            f'<td style="padding:10px 8px;font-size:11px;color:#555">{l.get("hq","")}</td></tr>'
            f'<tr class="detail-row" style="display:none;background:#fafaf8"><td colspan="7" style="padding:14px 16px">'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:10px">'
            f'<div><p style="font-size:11px;font-weight:600;color:#888;margin-bottom:4px">SIGNAL</p><p style="font-size:13px;color:#333;line-height:1.5">{_safe(l.get("signalDetail"))}</p></div>'
            f'<div><p style="font-size:11px;font-weight:600;color:#888;margin-bottom:4px">WHY REACH OUT</p><p style="font-size:13px;color:#333;line-height:1.5">{_safe(l.get("whyNow"))}</p></div>'
            f'</div>'
            f'<div style="margin-bottom:10px"><p style="font-size:11px;font-weight:600;color:#888;margin-bottom:4px">NOTES</p>'
            f'<textarea data-key="{key}" onclick="event.stopPropagation()" onblur="saveNote(this.dataset.key,this)" placeholder="Add notes about this lead..." style="width:100%;font-size:12px;padding:6px 8px;border-radius:5px;border:1px solid #ddd;resize:vertical;min-height:44px;font-family:inherit;box-sizing:border-box">{_note_esc}</textarea></div>'
            f'<div style="display:flex;gap:6px;flex-wrap:wrap">'
            f'<button onclick="event.stopPropagation();setHistStatus(\'{key}\',\'contacted\')" style="padding:3px 10px;border-radius:4px;border:1px solid #A5D6A7;background:{"#EAF3DE" if st=="contacted" else "#fff"};color:{"#27500A" if st=="contacted" else "#555"};font-size:11px;cursor:pointer">Contacted</button>'
            f'<button onclick="event.stopPropagation();setHistStatus(\'{key}\',\'watch\')" style="padding:3px 10px;border-radius:4px;border:1px solid #FAC775;background:{"#FAEEDA" if st=="watch" else "#fff"};color:{"#633806" if st=="watch" else "#555"};font-size:11px;cursor:pointer">Watch</button>'
            f'<button onclick="event.stopPropagation();setHistStatus(\'{key}\',\'dismissed\')" style="padding:3px 10px;border-radius:4px;border:1px solid #F7C1C1;background:{"#FCEBEB" if st=="dismissed" else "#fff"};color:{"#791F1F" if st=="dismissed" else "#555"};font-size:11px;cursor:pointer">Not a fit</button>'
            f'<button onclick="event.stopPropagation();setHistStatus(\'{key}\',\'clear\')" style="padding:3px 10px;border-radius:4px;border:1px solid #ddd;background:#fff;color:#888;font-size:11px;cursor:pointer">Clear</button>'
            f'{website_btn}</div></td></tr>')

    if not rows:
        rows = '<tr><td colspan="7" style="padding:32px;text-align:center;color:#999;font-size:13px">No leads yet. Run a scan first.</td></tr>'

    # Build rating leaderboard
    ratings = load_ratings()
    cat_scores  = {}
    sig_scores  = {}
    for key, r in ratings.items():
        score = r.get("score",0)
        cat   = r.get("category","Unknown")
        sig   = r.get("signalType","Unknown")
        if cat not in cat_scores: cat_scores[cat]  = []
        if sig not in sig_scores: sig_scores[sig]  = []
        cat_scores[cat].append(score)
        sig_scores[sig].append(score)

    def leaderboard_row(label, scores, color):
        avg = round(sum(scores)/len(scores),1)
        bar_w = int(avg/5*100)
        return f'''<div style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:2px">
            <span style="font-size:12px;color:#333">{label}</span>
            <span style="font-size:12px;font-weight:700;color:{color}">{avg}/5 ({len(scores)} rated)</span>
          </div>
          <div style="background:#eee;border-radius:99px;height:8px">
            <div style="background:{color};width:{bar_w}%;height:8px;border-radius:99px"></div>
          </div>
        </div>'''

    cat_board  = "".join(leaderboard_row(k, v, "#27500A" if sum(v)/len(v)>=3.5 else "#791F1F")
                         for k,v in sorted(cat_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if cat_scores else "<p style='color:#999;font-size:12px'>No ratings yet — rate leads using the 1-5 buttons on the dashboard.</p>"
    sig_board  = "".join(leaderboard_row(k, v, "#0C447C" if sum(v)/len(v)>=3.5 else "#E65100")
                         for k,v in sorted(sig_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if sig_scores else ""
    rating_leaderboard_html = f'''
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Product Category</h3>{cat_board}</div>
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Signal Type</h3>{sig_board if sig_board else "<p style='color:#999;font-size:12px'>No ratings yet.</p>"}</div>
      </div>'''

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lead History</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f2f0;color:#111}}.wrap{{max-width:1100px;margin:0 auto;padding:24px 16px}}.header{{background:#0C447C;color:white;padding:18px 24px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}.header h1{{font-size:18px;font-weight:600;color:white}}.nav{{display:flex;gap:8px}}.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:5px}}.nav a.active{{background:rgba(255,255,255,.3);color:white;font-weight:600}}.filter-bar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px;background:white;border-radius:8px;padding:10px 14px;border:1px solid #e0e0e0}}.filter-bar select,.filter-bar input{{padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px}}.btn{{display:inline-block;background:#0C447C;color:white;border:none;padding:7px 14px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;text-decoration:none}}.btn-green{{background:#27500A}}.hist-row:hover td{{background:#fafaf8}}table{{width:100%;border-collapse:collapse}}th{{padding:9px 8px;text-align:left;font-size:11px;color:#666;font-weight:600;background:#f7f7f5;border-bottom:1px solid #e8e8e8;position:sticky;top:0}}#toast{{position:fixed;bottom:24px;right:24px;background:#1a1a2e;color:#fff;padding:10px 16px;border-radius:7px;font-size:13px;opacity:0;transition:opacity .3s;pointer-events:none;z-index:999}}</style></head>
<body><div class="wrap">
<div class="header"><div><h1>Lead History</h1><p style="font-size:12px;opacity:.7;margin-top:2px">{len(all_leads)} total leads</p></div>
<nav class="nav"><a href="/">Dashboard</a><a href="/inputs">Inputs</a><a href="/history" class="active">History</a><a href="/feed-health">Feed Health</a><a href="/settings">Settings</a><a href="/confidence">Accuracy</a></nav></div>
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
function saveNote(key,el){{fetch("/lead/note",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{key:key,note:el.value}})}}).then(function(){{toast("Note saved");}});}}
function toast(msg){{var el=document.getElementById("toast");el.textContent=msg;el.style.opacity="1";setTimeout(function(){{el.style.opacity="0";}},2200);}}
filterHist();
</script></body></html>"""


def build_settings_page(saved_msg=""):
    settings  = load_settings()
    watchlist = load_watchlist()
    wl_text   = "\n".join(watchlist)
    msg_html  = f'<div style="background:#EAF3DE;border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:13px;color:#27500A;border:1px solid #A5D6A7">{saved_msg}</div>' if saved_msg else ""
    def sel(name, val): return "selected" if settings.get(name)==val else ""

    # Build rating leaderboard
    ratings = load_ratings()
    cat_scores  = {}
    sig_scores  = {}
    for key, r in ratings.items():
        score = r.get("score",0)
        cat   = r.get("category","Unknown")
        sig   = r.get("signalType","Unknown")
        if cat not in cat_scores: cat_scores[cat]  = []
        if sig not in sig_scores: sig_scores[sig]  = []
        cat_scores[cat].append(score)
        sig_scores[sig].append(score)

    def leaderboard_row(label, scores, color):
        avg = round(sum(scores)/len(scores),1)
        bar_w = int(avg/5*100)
        return f'''<div style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:2px">
            <span style="font-size:12px;color:#333">{label}</span>
            <span style="font-size:12px;font-weight:700;color:{color}">{avg}/5 ({len(scores)} rated)</span>
          </div>
          <div style="background:#eee;border-radius:99px;height:8px">
            <div style="background:{color};width:{bar_w}%;height:8px;border-radius:99px"></div>
          </div>
        </div>'''

    cat_board  = "".join(leaderboard_row(k, v, "#27500A" if sum(v)/len(v)>=3.5 else "#791F1F")
                         for k,v in sorted(cat_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if cat_scores else "<p style='color:#999;font-size:12px'>No ratings yet — rate leads using the 1-5 buttons on the dashboard.</p>"
    sig_board  = "".join(leaderboard_row(k, v, "#0C447C" if sum(v)/len(v)>=3.5 else "#E65100")
                         for k,v in sorted(sig_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if sig_scores else ""
    rating_leaderboard_html = f'''
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Product Category</h3>{cat_board}</div>
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Signal Type</h3>{sig_board if sig_board else "<p style='color:#999;font-size:12px'>No ratings yet.</p>"}</div>
      </div>'''

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Settings</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f2f0;color:#111}}.wrap{{max-width:700px;margin:0 auto;padding:24px 16px}}.header{{background:#0C447C;color:white;padding:18px 24px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}.header h1{{font-size:18px;font-weight:600;color:white}}.nav{{display:flex;gap:8px}}.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:5px}}.nav a.active{{background:rgba(255,255,255,.3);color:white;font-weight:600}}.card{{background:white;border-radius:10px;padding:20px 24px;margin-bottom:14px;border:1px solid #e0e0e0}}.card h2{{font-size:14px;font-weight:600;margin:0 0 14px;color:#111}}.field{{margin-bottom:14px}}label{{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;text-transform:uppercase;letter-spacing:.04em}}input,select,textarea{{width:100%;padding:8px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px;font-family:inherit}}textarea{{resize:vertical;height:90px}}.help{{font-size:11px;color:#888;margin-top:4px}}.btn{{background:#0C447C;color:white;border:none;padding:10px 24px;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer}}.row2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}</style></head>
<body><div class="wrap">
<div class="header"><div><h1>Settings</h1></div>
<nav class="nav"><a href="/">Dashboard</a><a href="/inputs">Inputs</a><a href="/history">History</a><a href="/feed-health">Feed Health</a><a href="/settings" class="active">Settings</a><a href="/confidence">Accuracy</a></nav></div>
{msg_html}
<form method="POST" action="/settings">
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

    # Build rating leaderboard
    ratings = load_ratings()
    cat_scores  = {}
    sig_scores  = {}
    for key, r in ratings.items():
        score = r.get("score",0)
        cat   = r.get("category","Unknown")
        sig   = r.get("signalType","Unknown")
        if cat not in cat_scores: cat_scores[cat]  = []
        if sig not in sig_scores: sig_scores[sig]  = []
        cat_scores[cat].append(score)
        sig_scores[sig].append(score)

    def leaderboard_row(label, scores, color):
        avg = round(sum(scores)/len(scores),1)
        bar_w = int(avg/5*100)
        return f'''<div style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:2px">
            <span style="font-size:12px;color:#333">{label}</span>
            <span style="font-size:12px;font-weight:700;color:{color}">{avg}/5 ({len(scores)} rated)</span>
          </div>
          <div style="background:#eee;border-radius:99px;height:8px">
            <div style="background:{color};width:{bar_w}%;height:8px;border-radius:99px"></div>
          </div>
        </div>'''

    cat_board  = "".join(leaderboard_row(k, v, "#27500A" if sum(v)/len(v)>=3.5 else "#791F1F")
                         for k,v in sorted(cat_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if cat_scores else "<p style='color:#999;font-size:12px'>No ratings yet — rate leads using the 1-5 buttons on the dashboard.</p>"
    sig_board  = "".join(leaderboard_row(k, v, "#0C447C" if sum(v)/len(v)>=3.5 else "#E65100")
                         for k,v in sorted(sig_scores.items(), key=lambda x:-sum(x[1])/len(x[1]))) if sig_scores else ""
    rating_leaderboard_html = f'''
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Product Category</h3>{cat_board}</div>
        <div><h3 style="font-size:13px;font-weight:600;color:#111;margin-bottom:10px">By Signal Type</h3>{sig_board if sig_board else "<p style='color:#999;font-size:12px'>No ratings yet.</p>"}</div>
      </div>'''

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Feed Health</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f2f2f0;color:#111}}.wrap{{max-width:1100px;margin:0 auto;padding:24px 16px}}.header{{background:#0C447C;color:white;padding:18px 24px;border-radius:10px;margin-bottom:20px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px}}.header h1{{font-size:18px;font-weight:600;color:white}}.nav{{display:flex;gap:8px}}.nav a{{color:rgba(255,255,255,.8);font-size:12px;text-decoration:none;background:rgba(255,255,255,.15);padding:6px 12px;border-radius:5px}}.nav a.active{{background:rgba(255,255,255,.3);color:white;font-weight:600}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px}}.stat{{background:white;border-radius:8px;padding:14px;border:1px solid #e0e0e0}}.stat-num{{font-size:24px;font-weight:700;color:#0C447C}}.stat-label{{font-size:11px;color:#888;margin-top:2px;text-transform:uppercase;letter-spacing:.04em}}table{{width:100%;border-collapse:collapse;background:white;border-radius:10px;overflow:hidden;border:1px solid #e0e0e0}}th{{padding:9px 8px;text-align:left;font-size:11px;color:#666;font-weight:600;background:#f7f7f5;border-bottom:1px solid #e8e8e8}}</style></head>
<body><div class="wrap">
<div class="header"><div><h1>Feed Health</h1><p style="font-size:12px;opacity:.7;margin-top:2px">{len(entries)} feeds tracked</p></div>
<nav class="nav"><a href="/">Dashboard</a><a href="/inputs">Inputs</a><a href="/history">History</a><a href="/feed-health" class="active">Feed Health</a><a href="/settings">Settings</a><a href="/confidence">Accuracy</a></nav></div>
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
        # Safety net: any uncaught exception anywhere in request handling
        # used to propagate all the way up and — combined with the old
        # single-threaded server — could take down the whole site's ability
        # to respond to anything else. Now it's caught here, logged, and
        # turned into a normal error page instead.
        try:
            self._do_GET()
        except Exception as e:
            log.error(f"Unhandled error in do_GET ({self.path}): {e}", exc_info=True)
            try:
                self._html(f"<h2 style='font-family:sans-serif;padding:40px'>Something went wrong loading this page: {e}</h2><p><a href='/'>Back to dashboard</a></p>")
            except Exception:
                pass

    def _do_GET(self):
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
        elif path == "/reports":
            files = sorted(REPORTS_DIR.glob("*.pdf"), reverse=True)
            rows = "".join(
                f'<tr><td style="padding:10px;font-size:13px"><a href="/download/{f.name}" style="color:#0C447C">{f.name}</a></td>'
                f'<td style="padding:10px;font-size:12px;color:#888">{f.stat().st_size//1024} KB</td>'
                f'<td style="padding:10px"><a href="/download/{f.name}" style="background:#0C447C;color:white;padding:4px 12px;border-radius:4px;font-size:12px;text-decoration:none">Download</a></td></tr>'
                for f in files
            ) or '<tr><td colspan="3" style="padding:20px;color:#999;text-align:center">No PDF reports found in /app/reports/</td></tr>'
            self._html(f'''<!DOCTYPE html><html><head><title>PDF Reports</title></head>
<body style="font-family:sans-serif;max-width:700px;margin:40px auto;padding:20px">
<h1 style="color:#0C447C">PDF Reports ({len(files)} available)</h1>
<p style="color:#666;margin-bottom:16px">Reports directory: /app/reports/</p>
<table style="width:100%;border-collapse:collapse;border:1px solid #eee">
<thead><tr style="background:#f5f5f3"><th style="padding:10px;text-align:left">File</th><th style="padding:10px;text-align:left">Size</th><th style="padding:10px;text-align:left">Action</th></tr></thead>
<tbody>{rows}</tbody></table>
<p style="margin-top:16px"><a href="/" style="color:#0C447C">Back to Dashboard</a></p>
</body></html>''')
        elif path == "/generate-pdf":
            try:
                leads_now = scan_state.get("leads",[])
                if not leads_now:
                    leads_now = load_all_leads()[-20:]
                if not leads_now:
                    self._html("<h2 style='font-family:sans-serif;padding:40px'>No leads yet — run a scan first. <a href='/'>Back</a></h2>")
                else:
                    safe      = [_sanitize_for_pdf(l) for l in leads_now]
                    fname     = f"Extron_Leads_{date.today().isoformat()}.pdf"
                    pdf_bytes = generate_pdf(safe)
                    log.info(f"PDF download: {len(pdf_bytes)} bytes")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                    self.send_header("Content-Length", str(len(pdf_bytes)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    chunk_size = 8192
                    for i in range(0, len(pdf_bytes), chunk_size):
                        self.wfile.write(pdf_bytes[i:i+chunk_size])
                    self.wfile.flush()
            except Exception as e:
                log.error(f"PDF download failed: {e}", exc_info=True)
                self._html(f"<h2 style='font-family:sans-serif;padding:40px'>PDF Error: {e}</h2><p><a href='/'>Back to dashboard</a></p>")
        elif path == "/basket-pdf":
            try:
                keys = scan_state.get("basket_keys", [])
                if not keys:
                    self._html("<h2 style='font-family:sans-serif;padding:40px'>No basket leads saved. Add leads to basket first. <a href='/'>Back</a></h2>")
                else:
                    all_l = scan_state.get("leads",[]) + load_all_leads()
                    basket_leads, seen_k = [], set()
                    for l in all_l:
                        k = l.get("name","").lower().strip()
                        if k in keys and k not in seen_k:
                            basket_leads.append(l)
                            seen_k.add(k)
                    safe = [_sanitize_for_pdf(l) for l in basket_leads]
                    fname = f"Extron_Basket_{date.today().isoformat()}.pdf"
                    pdf_bytes = generate_pdf(safe)
                    log.info(f"Basket PDF: {len(pdf_bytes)} bytes, {len(basket_leads)} leads")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                    self.send_header("Content-Length", str(len(pdf_bytes)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    chunk_size = 8192
                    for i in range(0, len(pdf_bytes), chunk_size):
                        self.wfile.write(pdf_bytes[i:i+chunk_size])
                    self.wfile.flush()
            except Exception as e:
                log.error(f"Basket PDF failed: {e}", exc_info=True)
                self._html(f"<h2 style='font-family:sans-serif;padding:40px'>Basket PDF Error: {e}</h2><p><a href='/'>Back</a></p>")

        elif path in ["/inputs", "/inputs/"]:
            self._html(build_inputs_page())
        elif path == "/confidence":
            self._html(build_confidence_page())
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
        try:
            self._do_POST()
        except Exception as e:
            log.error(f"Unhandled error in do_POST ({self.path}): {e}", exc_info=True)
            try:
                self._html(f"<h2 style='font-family:sans-serif;padding:40px'>Something went wrong: {e}</h2><p><a href='/'>Back to dashboard</a></p>")
            except Exception:
                pass

    def _do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length).decode("utf-8","replace") if length else ""

        if path == "/run":
            if not scan_state["running"]:
                threading.Thread(target=run_scan, daemon=True).start()
            self.send_response(303); self.send_header("Location","/"); self.end_headers()


        elif path == "/export-basket":
            # Export only basket leads as PDF — basket keys sent as JSON body
            try:
                data  = json.loads(body)
                keys  = data.get("keys",[])
                mode  = data.get("mode","pdf")
                all_l = scan_state.get("leads",[]) + load_all_leads()
                # Match leads by name key
                basket_leads = []
                seen_keys = set()
                for l in all_l:
                    k = l.get("name","").lower().strip()
                    if k in keys and k not in seen_keys:
                        basket_leads.append(l)
                        seen_keys.add(k)
                safe = [_sanitize_for_pdf(l) for l in basket_leads]
                fname = f"Extron_Selected_Leads_{date.today().isoformat()}"
                if mode == "csv":
                    import csv, io as _io
                    out = _io.StringIO()
                    fields = ["name","category","hq","stage","signalType","signalDate",
                              "signalDetail","urgencyScore","confidenceScore","suggestedContact",
                              "pitchAngle","website","sourceUrl","description","extronFit"]
                    w = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
                    w.writeheader()
                    w.writerows(safe)
                    csv_bytes = out.getvalue().encode()
                    self.send_response(200)
                    self.send_header("Content-Type","text/csv")
                    self.send_header("Content-Disposition",f'attachment; filename="{fname}.csv"')
                    self.send_header("Content-Length",str(len(csv_bytes)))
                    self.end_headers()
                    self.wfile.write(csv_bytes)
                else:
                    pdf_bytes = generate_pdf(safe)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/pdf")
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}.pdf"')
                    self.send_header("Content-Length", str(len(pdf_bytes)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    # Write in chunks to avoid truncation
                    chunk_size = 8192
                    for i in range(0, len(pdf_bytes), chunk_size):
                        self.wfile.write(pdf_bytes[i:i+chunk_size])
                    self.wfile.flush()
            except Exception as e:
                log.error(f"Basket export failed: {e}",exc_info=True)
                self._json({"ok":False,"error":str(e)})

        elif path == "/lead/rate":
            # 1-5 rating for a lead
            try:
                data  = json.loads(body)
                key   = data.get("key","").strip()
                score = int(data.get("score",0))
                sig   = data.get("signalType","")
                cat   = data.get("category","")
                if key and 1 <= score <= 5:
                    ratings = load_ratings()
                    ratings[key] = {
                        "score":     score,
                        "signalType":sig,
                        "category":  cat,
                        "ts":        datetime.now().isoformat(),
                        "date":      date.today().isoformat(),
                    }
                    save_ratings(ratings)
                    # Also store in feedback for optimizer compatibility
                    fb = load_feedback()
                    fb[key] = {
                        "rating":     "good" if score >= 4 else "not_our_customer" if score <= 2 else "wrong_industry",
                        "score":      score,
                        "signalType": sig,
                        "category":   cat,
                        "ts":         datetime.now().isoformat(),
                    }
                    save_feedback(fb)
                    # Run optimizer every 5 ratings
                    if len(ratings) % 5 == 0:
                        threading.Thread(target=run_self_optimizer, daemon=True).start()
                self._json({"ok":True})
            except Exception as e:
                self._json({"ok":False,"error":str(e)})

        elif path == "/lead/note":
            # Save a note for a lead
            try:
                data = json.loads(body)
                key  = data.get("key","").strip()
                note = data.get("note","").strip()
                if key:
                    notes = load_notes()
                    notes[key] = note
                    save_notes(notes)
                self._json({"ok":True})
            except Exception as e:
                self._json({"ok":False,"error":str(e)})
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
        elif path == "/lead/feedback":
            try:
                data   = json.loads(body)
                key    = data.get("key","").strip()
                rating = data.get("rating","").strip()
                sig    = data.get("signalType","")
                cat    = data.get("category","")
                if key and rating:
                    fb = load_feedback()
                    fb[key] = {"rating": rating, "signalType": sig, "category": cat,
                               "ts": datetime.now().isoformat()}
                    save_feedback(fb)
                    # Run optimizer every 10 feedback entries
                    if len(fb) % 10 == 0:
                        threading.Thread(target=run_self_optimizer, daemon=True).start()
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
        elif path in ["/inputs", "/inputs/"]:
            try:
                from urllib.parse import parse_qs as _pqs
                form = _pqs(body, keep_blank_values=True)

                action = form.get("action",[""])[0]

                def getlist(k): return form.get(k, [])
                def getone(k, default=""): return form.get(k, [default])[0]

                inputs = load_inputs()

                # Multi-select fields
                inputs["categories"] = getlist("categories") or []
                inputs["signals"]    = getlist("signals")    or []
                inputs["locations"]  = getlist("locations")  or ["Anywhere"]
                inputs["stages"]     = getlist("stages")     or []

                # Numeric fields
                for field, default in [
                    ("min_unit_price",300), ("min_funding_amount",3),
                    ("employee_min",1), ("employee_max",1000),
                    ("founded_after",2000), ("founded_before",2026),
                    ("confidence_threshold",30), ("min_urgency_score",0),
                    ("articles_per_scan",40), ("lookback_days",90),
                    ("scan_interval_mins",30), ("scan_start_hour",8), ("scan_end_hour",20),
                ]:
                    try: inputs[field] = float(getone(field, str(default))) if "." in str(getone(field, str(default))) else int(getone(field, str(default)))
                    except: inputs[field] = default

                # String fields
                inputs["sensitivity"]    = getone("sensitivity", "normal")
                inputs["prefilter_mode"] = getone("prefilter_mode", "strict")

                # Keyword lists
                inc_raw = getone("include_keywords","")
                exc_raw = getone("exclude_keywords","")
                inputs["include_keywords"] = [k.strip() for k in inc_raw.split(",") if k.strip()]
                inputs["exclude_keywords"] = [k.strip() for k in exc_raw.split(",") if k.strip()]

                # Checkboxes (present = True, absent = False)
                inputs["oem_only"]          = "oem_only" in form
                inputs["require_embedded"]  = "require_embedded" in form
                inputs["scan_auto"]         = "scan_auto" in form
                inputs["scan_all_day"]      = "scan_all_day" in form
                inputs["optimizer_enabled"] = "optimizer_enabled" in form

                # Save preset if requested
                if action == "save_preset":
                    preset_name = getone("preset_name","").strip()
                    if preset_name:
                        presets = load_presets()
                        presets[preset_name] = dict(inputs)
                        save_presets(presets)
                        inputs["active_preset"] = preset_name

                save_inputs(inputs)
                self._html(build_inputs_page("Inputs saved successfully. They will be used on the next scan."))
            except Exception as e:
                self._html(build_inputs_page(f"Error saving inputs: {e}"))

        elif path == "/inputs/load-preset":
            try:
                data = json.loads(body)
                name = data.get("name","")
                presets = load_presets()
                if name in presets:
                    inputs = presets[name]
                    inputs["active_preset"] = name
                    save_inputs(inputs)
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif path == "/inputs/delete-preset":
            try:
                data = json.loads(body)
                name = data.get("name","")
                presets = load_presets()
                if name in presets:
                    del presets[name]
                    save_presets(presets)
                    inputs = load_inputs()
                    if inputs.get("active_preset") == name:
                        inputs["active_preset"] = None
                        save_inputs(inputs)
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})

        elif path == "/inputs/reset":
            save_inputs(dict(DEFAULT_INPUTS))
            self._json({"ok": True})

        elif path == "/settings":
            try:
                from urllib.parse import parse_qs as _pqs
                form = dict(_pqs(body))
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
    # ThreadingHTTPServer instead of plain HTTPServer: the old single-threaded
    # server handled one request at a time, so a slow request (or a request
    # that hit an unhandled exception) could make the entire site unresponsive
    # to everyone else. Each request now runs in its own thread — a problem
    # with one request can no longer take down the whole app.
    server=ThreadingHTTPServer(("0.0.0.0",port),Handler)
    server.daemon_threads = True
    log.info(f"Web UI on port {port} (threaded)")
    server.serve_forever()

def run_scan():
    if scan_state["running"]: return
    scan_state["running"]=True
    scan_state["articles"]=[]; scan_state["filtered"]=[]; scan_state["leads"]=[]
    scan_state["last_ai_error"]=None
    try:
        scan_state["status"]="Fetching articles from 70+ sources..."
        log.info("="*55)
        log.info("Starting Extron lead intelligence scan...")
        all_articles=[]
        for feed in RSS_FEEDS:
            all_articles.extend(fetch_rss(feed))

        seen_set,unique=set(),[]
        configured_lookback = int(load_inputs().get("lookback_days", 90))
        for a in all_articles:
            t=a.get("title","").lower().strip()
            if t and t not in seen_set and within_lookback(a.get("pubDate",""), configured_lookback):
                unique.append(a); seen_set.add(t)
        scan_state["articles"]=unique
        log.info(f"Unique articles within {configured_lookback} days: {len(unique)}")

        # Filter out articles the AI has already analyzed in a previous scan —
        # this was previously dead code (load_seen/save_seen existed but were
        # never called), so every scan re-fed the same recent headlines to the
        # AI with no memory of what it had already looked at.
        already_seen = load_seen()
        def _article_key(a):
            return (a.get("link") or a.get("title","")).strip().lower()
        never_seen = [a for a in unique if _article_key(a) not in already_seen]
        log.info(f"Articles not yet analyzed in a previous scan: {len(never_seen)} of {len(unique)}")
        # If filtering leaves too little to work with (e.g. a very quiet news
        # day), fall back to the full set rather than starving the AI of input.
        articles_for_scan = never_seen if len(never_seen) >= 5 else unique

        scan_state["status"]=f"Pre-filtering {len(articles_for_scan)} new articles..."
        current_inputs = load_inputs()
        filtered=prefilter(articles_for_scan, current_inputs)
        scan_state["filtered"]=filtered

        cap = int(current_inputs.get("articles_per_scan", 40))
        scan_state["status"]=f"AI analyzing top {min(len(filtered), cap)} articles..."
        leads=ai_filter(filtered, current_inputs)

        # Mark every article actually sent to the AI this scan as seen, so
        # future scans don't re-analyze it. Keep the set bounded so it
        # doesn't grow forever.
        try:
            updated_seen = already_seen | {_article_key(a) for a in filtered[:cap]}
            save_seen(set(list(updated_seen)[-5000:]))
        except Exception as seen_e:
            log.warning(f"Could not persist seen-articles list: {seen_e}")

        # Capture the AI error (if any) before later status updates would
        # otherwise silently overwrite it — this is what actually goes wrong,
        # shown right on the dashboard instead of buried in logs.
        ai_error = scan_state.get("last_ai_error")

        log.info(f"ai_filter returned {len(leads)} leads")
        seen=set()
        fresh=deduplicate(leads,seen)
        log.info(f"After deduplicate: {len(fresh)} fresh leads")

        # Safety net: if a scan genuinely comes back empty (API hiccup, thin
        # news day, etc.), don't leave the dashboard looking broken — fall
        # back to the most recent leads on record and say so clearly, rather
        # than silently showing zero.
        if not fresh:
            log.warning("Scan returned 0 fresh leads — falling back to most recent historical leads so the dashboard isn't empty.")
            all_hist = load_all_leads()
            # all_leads.json is sorted by urgencyScore descending (see append_to_history),
            # not by date — so "most recent" has to be read from added_date explicitly,
            # not just sliced off one end of the list.
            fallback = sorted(all_hist, key=lambda x: x.get("added_date",""), reverse=True)[:20]
            if fallback:
                for l in fallback:
                    l["fromPreviousScan"] = True
                fresh = fallback
                scan_state["status"] = f"This scan found 0 new leads — showing {len(fresh)} most recent leads from earlier scans instead."
        scan_state["leads"]=fresh

        scan_state["status"]="Generating PDF..."
        pdf_leads = fresh if fresh else leads
        safe_leads = [_sanitize_for_pdf(l) for l in pdf_leads]
        log.info(f"Generating PDF with {len(safe_leads)} leads...")
        try:
            pdf_bytes = generate_pdf(safe_leads)
            scan_state["last_pdf_bytes"] = pdf_bytes
            scan_state["last_pdf_name"]  = f"Extron_Leads_{date.today().isoformat()}.pdf"
            # Also try to save to disk
            try:
                fpath = REPORTS_DIR / scan_state["last_pdf_name"]
                fpath.write_bytes(pdf_bytes)
                log.info(f"PDF saved to volume: {fpath} ({len(pdf_bytes)} bytes)")
            except Exception as disk_e:
                log.warning(f"PDF disk save failed (volume issue): {disk_e} — PDF still available in memory")
        except Exception as pdf_err:
            log.error(f"PDF generation FAILED: {pdf_err}", exc_info=True)
            scan_state["last_pdf_bytes"] = None

        from datetime import timezone as _tz, timedelta as _td
        _utc_now = datetime.now(_tz.utc)
        _is_pdt  = 3 < _utc_now.month < 11
        _pt_now  = _utc_now.astimezone(_tz(_td(hours=-7 if _is_pdt else -8)))
        scan_state["last_run"] = _pt_now.strftime("%B %d, %Y at %I:%M %p") + (" PDT" if _is_pdt else " PST")
        update_daily_leads(fresh)
        append_to_history(fresh)
        # If the AI call itself failed, keep that visible — it's more useful
        # than a generic "Done" message that hides what actually happened.
        if ai_error:
            scan_state["status"] = f"⚠ Scan finished, but the AI call failed: {ai_error}  (showing {len(fresh)} fallback/historical leads instead)"
        else:
            scan_state["status"]=f"Done — {len(fresh)} leads found."
        # Run self-optimizer if we have enough feedback
        threading.Thread(target=run_self_optimizer, daemon=True).start()
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
        # Even on error, try to save whatever leads we got
        try:
            partial = scan_state.get("leads", [])
            if partial:
                filename = REPORTS_DIR / f"Extron_Leads_{date.today().isoformat()}.pdf"
                generate_pdf(partial, filename)
                log.info(f"Partial PDF saved with {len(partial)} leads despite error")
        except Exception as pdf_e:
            log.error(f"Could not save partial PDF: {pdf_e}")
    finally:
        scan_state["running"] = False
        # Always verify PDF exists for today
        today_pdf = REPORTS_DIR / f"Extron_Leads_{date.today().isoformat()}.pdf"
        if not today_pdf.exists():
            try:
                leads_so_far = scan_state.get("leads", [])
                generate_pdf(leads_so_far, today_pdf)
                log.info(f"Fallback PDF created: {today_pdf}")
            except Exception as fb_e:
                log.error(f"Fallback PDF failed: {fb_e}")

if __name__=="__main__":
    log.info("Extron Scanner v8 starting...")
    # On startup, clear seen companies older than 7 days so leads can resurface
    try:
        seen = load_seen_companies()
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        seen = {k: v for k, v in seen.items() if v >= cutoff}
        save_seen_companies(seen)
        log.info(f"Startup: kept {len(seen)} companies seen in last 7 days")
    except Exception as e:
        log.warning(f"Could not clean seen companies: {e}")
    threading.Thread(target=start_web_server, daemon=True).start()
    threading.Thread(target=auto_scheduler,   daemon=True).start()
    threading.Thread(target=run_scan,         daemon=True).start()
    while True:
        time.sleep(60)
