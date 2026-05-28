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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
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
    "https://news.google.com/rss/search?q=medical+device+startup+series+A+B+funding+raised+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+startup+series+A+B+angel+funding+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AV+hardware+startup+series+funding+raised+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+startup+angel+seed+funding+raised+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+raises+million+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+series+C+D+funding+growth+2025&hl=en-US&gl=US&ceid=US:en",

    # Hiring signals — engineering AND operations
    "https://news.google.com/rss/search?q=medical+device+company+hiring+hardware+operations+engineers+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+hardware+company+hiring+expanding+team+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+hiring+VP+operations+supply+chain+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+startup+hiring+operations+manager+director+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medtech+AV+hardware+company+expanding+headcount+operations+2025&hl=en-US&gl=US&ceid=US:en",

    # Bay Area expansion
    "https://news.google.com/rss/search?q=hardware+company+moving+expanding+bay+area+san+francisco+silicon+valley+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+opens+office+bay+area+california+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+company+bay+area+california+expansion+headquarters+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=startup+hardware+relocating+moving+bay+area+silicon+valley+2025&hl=en-US&gl=US&ceid=US:en",

    # Awards signals
    "https://news.google.com/rss/search?q=CES+award+winner+hardware+medical+device+AV+EV+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+award+winner+expo+innovation+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+wins+award+best+product+innovation+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=CES+innovation+award+hardware+startup+2025&hl=en-US&gl=US&ceid=US:en",

    # Trade show signals
    "https://news.google.com/rss/search?q=company+exhibiting+InfoComm+Infocomm+AV+hardware+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=company+exhibiting+Interop+networking+hardware+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+company+exhibiting+HIMSS+Medtrade+expo+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+company+exhibiting+show+expo+conference+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+ISC+security+show+exhibiting+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AV+hardware+NAB+show+exhibiting+broadcast+2025&hl=en-US&gl=US&ceid=US:en",

    # Supply chain / onshoring signals
    "https://news.google.com/rss/search?q=hardware+company+onshoring+reshoring+US+manufacturing+supply+chain+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+supply+chain+onshoring+domestic+manufacturing+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+supply+chain+disruption+vendor+change+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=supply+chain+hardware+offshoring+onshoring+nearshoring+manufacturer+2025&hl=en-US&gl=US&ceid=US:en",

    # Leadership & M&A
    "https://news.google.com/rss/search?q=medical+device+company+new+CEO+acquisition+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medtech+layoffs+restructuring+merger+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+company+CEO+acquisition+merger+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=professional+AV+company+acquisition+CEO+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+new+CEO+acquisition+restructuring+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:prnewswire.com+hardware+medical+device+CEO+funding+award+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:businesswire.com+hardware+merger+CEO+funding+award+2025&hl=en-US&gl=US&ceid=US:en",

    # LinkedIn community discussions (via Google News index)
    "https://news.google.com/rss/search?q=supply+chain+onshoring+reshoring+hardware+linkedin+community+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=supply+chain+disruption+hardware+manufacturer+discussion+2025&hl=en-US&gl=US&ceid=US:en",
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

HARDWARE_KEYWORDS = [
    "medical device","medtech","diagnostic","imaging","monitor","surgical",
    "patient","clinical","hospital","infusion","wearable","implant",
    "ev charging","charging station","charger","evse","electric vehicle",
    "audiovisual"," av ","display","projector","signal processor",
    "switcher","control system","broadcast","videoconfer","digital signage",
    "hardware","device","equipment","instrument","sensor","electronics",
]

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
        log.info(f"  {len(items)} items from {url[:65]}")
        return items
    except Exception as e:
        log.warning(f"Feed failed {url[:55]}: {e}")
        return []

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
    for a in articles:
        text = (a.get("title","") + " " + a.get("summary","")).lower()
        has_hardware = any(k in text for k in HARDWARE_KEYWORDS)
        has_signal   = any(k in text for k in SIGNAL_KEYWORDS)
        if has_hardware and has_signal:
            kept.append(a)
    log.info(f"Pre-filter: {len(kept)} articles matched hardware + signal keywords")
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

JOB 1 — EXTRACT from the articles below: Find any companies that sell hardware ($300+/unit) in AV, EV charging, or medical devices, showing any signal (funding, hiring, awards, trade shows, expansion, M&A, new CEO, supply chain changes).

JOB 2 — SUPPLEMENT from your knowledge: Based on the article topics and companies mentioned, add any additional real companies you know about from your training data that fit Extron's criteria and have shown signals in the last 6 months. Be specific — use real company names, real events.

Categories Extron cares about:
- Professional AV hardware (displays, projectors, switchers, signal processors, digital signage, videoconferencing)
- EV charging hardware (charging stations, EVSE, fleet chargers)  
- Medical devices (diagnostic, monitoring, imaging, surgical, wearable)

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
- category: "AV Hardware" or "EV Charging" or "Medical Devices"
- stage: "Startup / Pre-revenue" or "Early Stage" or "Growth Stage" or "Established SMB" or "Public Company"
- hq: city, state (string)
- founded: year as integer or null
- ticker: stock ticker or null
- website: company website URL e.g. "https://www.company.com" or null
- unitPrice: estimated price per unit e.g. "$500-$2,000/unit" or "Early stage / TBD"
- employees: estimated employee count as string e.g. "50-100" or "Unknown"
- description: 2-sentence description of what the company does and sells
- signalType: one of "Series A Funding", "Series B Funding", "Series C Funding", "Series D Funding", "Angel / Seed Funding", "Hiring Hardware Engineers", "Hiring Operations Talent", "Bay Area Expansion", "CES Award", "Industry Award", "Trade Show — InfoComm", "Trade Show — Interop", "Trade Show — ISC West", "Trade Show — HIMSS", "Trade Show — NAB Show", "Trade Show — Other", "Supply Chain Onshoring", "Supply Chain Change", "New Company / Startup", "New CEO/Leadership", "M&A / Acquisition", "Layoffs / Restructuring", "Market Expansion", "New Product Launch", "New Partnership", "IPO / SPAC"
- signalDate: e.g. "March 2025" or "Q1 2025"
- signalDetail: 2 factual sentences about what happened
- whyNow: 1 sentence on why this is a good time for Extron to reach out
- additionalContext: any other relevant details about the company, its products, competitors, or market position (1-2 sentences)
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
        log.info(f"AI found {len(leads)} qualifying leads")
        return leads
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
    u=l.get("urgencyScore",60)
    ulbl,ubg,ufc,ulc,ulw=urg_meta(u)
    cbg,cfg=cat_col(l.get("category",""))
    sbg,sfc=sig_col(l.get("signalType",""))
    stage=l.get("stage","")

    # Company header
    hdr=Table([[
        Table([
            [Paragraph(l.get("name",""),ps('cn',fontName='Helvetica-Bold',fontSize=15,textColor=INK,leading=19))],
            [Paragraph(f"{l.get('hq','')}  ·  {stage}  ·  Founded: {l.get('founded','Unknown')}  ·  {l.get('ticker') or 'Private'}",ps('cs',fontName='Helvetica',fontSize=10,textColor=TEXT2,leading=13))],
        ],colWidths=[CW-80*mm]),
        Table([[badge(l.get("category","").upper(),cbg,cfg,30*mm), badge(ulbl,ubg,ufc,28*mm)]],
            colWidths=[32*mm,30*mm],style=TableStyle([
                ('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),0),
                ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])),
    ]],colWidths=[CW-68*mm,68*mm],style=TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
        ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),7)]))

    # Signal box
    sig=Table([
        [Paragraph(f"{l.get('signalType','').upper()}  ·  {l.get('signalDate','')}",
            ps('sigl',fontName='Helvetica-Bold',fontSize=8,textColor=sfc,leading=10))],
        [Paragraph(l.get('signalDetail',''),ps('sigd',fontName='Helvetica',fontSize=10,textColor=INK,leading=14))],
    ],colWidths=[CW-10*mm],style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),sbg),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(0,0),8),('BOTTOMPADDING',(0,0),(0,0),3),
        ('TOPPADDING',(0,1),(-1,-1),0),('BOTTOMPADDING',(0,1),(-1,-1),8)]))

    # Why reach out
    why=Table([[
        Paragraph("Why reach out now: ",ps('wb',fontName='Helvetica-Bold',fontSize=10,textColor=AMBER,leading=14)),
        Paragraph(l.get('whyNow',''),ps('wt',fontName='Helvetica',fontSize=10,textColor=AMBER,leading=14)),
    ]],colWidths=[34*mm,CW-46*mm],style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),AMBER_BG),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('VALIGN',(0,0),(-1,-1),'TOP')]))

    # Company details grid
    unit_price = l.get('unitPrice','Unknown')
    website = l.get('website') or l.get('sourceUrl','')
    # Clean up website to just domain
    if website and website.startswith('http'):
        try:
            domain = website.split('/')[2]
        except:
            domain = website
    else:
        domain = website

    details_data = [
        [Paragraph("Unit Price", ps('dk',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10)),
         Paragraph("Company Stage", ps('dk',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10)),
         Paragraph("Confidence", ps('dk',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10)),
         Paragraph("Urgency Score", ps('dk',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10))],
        [Paragraph(unit_price, ps('dv',fontName='Helvetica',fontSize=11,textColor=INK,leading=14)),
         Paragraph(stage, ps('dv',fontName='Helvetica',fontSize=11,textColor=INK,leading=14)),
         Paragraph(f"{l.get('confidence',50)}%", ps('dv',fontName='Helvetica-Bold',fontSize=11,textColor=INK,leading=14)),
         Paragraph(f"{u}%", ps('dv',fontName='Helvetica-Bold',fontSize=11,textColor=ufc,leading=14))],
    ]
    details = Table(details_data, colWidths=[(CW-10*mm)/4]*4, style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),GRAY_LT),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),6),
        ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),
        ('LINEAFTER',(0,0),(-2,-1),0.5,BORDER),
        ('VALIGN',(0,0),(-1,-1),'TOP'),
    ]))

    # Source & article info
    source_text = f"<b>Source:</b> {l.get('source','')}"
    article_url = l.get('sourceUrl','')
    website_url = l.get('website','')

    links_parts = []
    if article_url:
        links_parts.append(f"Article: {article_url}")
    if website_url and website_url != article_url:
        links_parts.append(f"Website: {website_url}")

    source_box_data = [
        [Paragraph("SOURCE ARTICLE", ps('sk',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10,textTransform='uppercase'))],
        [Paragraph(f"{l.get('source','')} — {l.get('signalDate','')}", ps('sv',fontName='Helvetica-Bold',fontSize=10,textColor=INK,leading=13))],
    ]
    if article_url:
        source_box_data.append([Paragraph(f"{article_url}", ps('sl',fontName='Helvetica',fontSize=9,textColor=colors.HexColor('#185FA5'),leading=12))])
    if website_url and website_url != article_url:
        source_box_data.append([Paragraph(f"Website: {website_url}", ps('wl',fontName='Helvetica',fontSize=9,textColor=colors.HexColor('#185FA5'),leading=12))])

    source_box = Table(source_box_data, colWidths=[CW-10*mm], style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#F0F4FF')),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(0,0),8),('BOTTOMPADDING',(0,-1),(-1,-1),8),
        ('TOPPADDING',(0,1),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-2),2),
    ]))

    # Additional notes if available
    extra_rows = [[hdr],[sig],[Spacer(1,5)],[why],[Spacer(1,5)],[details],[Spacer(1,5)],[source_box]]

    # Add any extra fields the AI returned
    if l.get('additionalContext') or l.get('notes'):
        extra_text = l.get('additionalContext') or l.get('notes','')
        extra_rows.append([Spacer(1,4)])
        extra_rows.append([Table([[
            Paragraph("Additional context: ",ps('nb',fontName='Helvetica-Bold',fontSize=9,textColor=GRAY_TXT,leading=12)),
            Paragraph(extra_text,ps('nt',fontName='Helvetica',fontSize=9,textColor=GRAY_TXT,leading=12)),
        ]],colWidths=[28*mm,CW-40*mm],style=TableStyle([
            ('BACKGROUND',(0,0),(-1,-1),GRAY_LT),
            ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
            ('VALIGN',(0,0),(-1,-1),'TOP')]))])

    inner=Table(extra_rows, colWidths=[CW-10*mm], style=TableStyle([
        ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
        ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    outer=Table([[inner]],colWidths=[CW],style=TableStyle([
        ('BOX',(0,0),(-1,-1),0.75,ulc),('LINEBEFORE',(0,0),(0,-1),ulw,ulc),
        ('BACKGROUND',(0,0),(-1,-1),WHITE),
        ('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),
        ('TOPPADDING',(0,0),(-1,-1),14),('BOTTOMPADDING',(0,0),(-1,-1),14)]))
    return KeepTogether([outer,Spacer(1,6*mm)])

def generate_pdf(leads, filename):
    doc=SimpleDocTemplate(str(filename),pagesize=A4,
        leftMargin=18*mm,rightMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm)
    W,H=A4; CW=W-36*mm
    today_str=date.today().strftime("%B %d, %Y")
    hot=[l for l in leads if l.get("urgencyScore",0)>=85]
    high=[l for l in leads if 70<=l.get("urgencyScore",0)<85]
    watch=[l for l in leads if l.get("urgencyScore",0)<70]
    story=[]

    # Cover page — with Extron logo
    # Try to fetch Extron logo
    logo_element = None
    try:
        logo_url = "https://www.extron.com/img/logo-extron.png"
        req = urllib.request.Request(logo_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            logo_data = r.read()
        from io import BytesIO as _BytesIO
        logo_img = Image(_BytesIO(logo_data), width=40*mm, height=12*mm)
        logo_element = logo_img
    except:
        logo_element = Paragraph("EXTRON", ps('logo', fontName='Helvetica-Bold', fontSize=18, textColor=WHITE, leading=22))

    cover=Table([
        [logo_element],
        [Spacer(1, 8)],
        [Paragraph("CONFIDENTIAL — EXTRON SALES INTELLIGENCE",
            ps('cl',fontName='Helvetica',fontSize=9,textColor=colors.HexColor('#aaaacc'),leading=11))],
        [Spacer(1,6)],
        [Paragraph("Lead Intelligence Report",
            ps('ct',fontName='Helvetica-Bold',fontSize=24,textColor=WHITE,leading=30))],
        [Paragraph("Startups · Funded companies · Hiring signals · Awards · Trade shows · Supply chain shifts",
            ps('cs',fontName='Helvetica',fontSize=11,textColor=colors.HexColor('#ccddee'),leading=16))],
        [Spacer(1,14)],
        [Table([[
            Table([[Paragraph("GENERATED",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(today_str,ps('mv',fontName='Helvetica-Bold',fontSize=10,textColor=WHITE,leading=13))]],colWidths=[40*mm]),
            Table([[Paragraph("TOTAL LEADS",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(str(len(leads)),ps('mn',fontName='Helvetica-Bold',fontSize=26,textColor=WHITE,leading=30))]],colWidths=[28*mm]),
            Table([[Paragraph("HOT LEADS",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(str(len(hot)),ps('mn2',fontName='Helvetica-Bold',fontSize=26,textColor=colors.HexColor('#FF9999'),leading=30))]],colWidths=[28*mm]),
            Table([[Paragraph("HIGH PRIORITY",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(str(len(high)),ps('mn3',fontName='Helvetica-Bold',fontSize=26,textColor=colors.HexColor('#FFD580'),leading=30))]],colWidths=[28*mm]),
            Table([[Paragraph("WATCH LIST",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(str(len(watch)),ps('mn4',fontName='Helvetica-Bold',fontSize=26,textColor=WHITE,leading=30))]],colWidths=[28*mm]),
        ]],colWidths=[40*mm,28*mm,28*mm,28*mm,28*mm],style=TableStyle([
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))]
    ],colWidths=[CW],style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),NAVY),
        ('LEFTPADDING',(0,0),(-1,-1),20),('RIGHTPADDING',(0,0),(-1,-1),20),
        ('TOPPADDING',(0,0),(-1,-1),20),('BOTTOMPADDING',(0,0),(-1,-1),24)]))
    story+=[cover,Spacer(1,8*mm)]

    # Summary table of all leads
    story.append(Paragraph("ALL LEADS AT A GLANCE",
        ps('h2',fontName='Helvetica-Bold',fontSize=10,textColor=TEXT2,leading=13,spaceAfter=6)))
    story.append(HRFlowable(width=CW,thickness=0.5,color=BORDER,spaceAfter=8))

    summary_header = [
        Paragraph("Company", ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Category", ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Signal", ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Stage", ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
        Paragraph("Urgency", ps('th',fontName='Helvetica-Bold',fontSize=9,textColor=TEXT2,leading=11)),
    ]
    summary_rows = [summary_header]
    for l in sorted(leads, key=lambda x: -x.get("urgencyScore",0)):
        u = l.get("urgencyScore",0)
        urg_color = RED if u>=85 else AMBER if u>=70 else GRAY_TXT
        summary_rows.append([
            Paragraph(l.get("name",""), ps('td',fontName='Helvetica-Bold',fontSize=9,textColor=INK,leading=12)),
            Paragraph(l.get("category",""), ps('td',fontName='Helvetica',fontSize=9,textColor=TEXT2,leading=12)),
            Paragraph(l.get("signalType",""), ps('td',fontName='Helvetica',fontSize=9,textColor=TEXT2,leading=12)),
            Paragraph(l.get("stage",""), ps('td',fontName='Helvetica',fontSize=9,textColor=TEXT2,leading=12)),
            Paragraph(f"{u}%", ps('td',fontName='Helvetica-Bold',fontSize=9,textColor=urg_color,leading=12)),
        ])

    summary_table = Table(summary_rows,
        colWidths=[50*mm, 30*mm, 45*mm, 35*mm, 18*mm],
        style=TableStyle([
            ('BACKGROUND',(0,0),(-1,0),GRAY_LT),
            ('LINEBELOW',(0,0),(-1,0),0.5,BORDER),
            ('LINEBELOW',(0,1),(-1,-1),0.25,BORDER),
            ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),6),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,GRAY_LT]),
        ]))
    story.append(summary_table)
    story.append(Spacer(1,10*mm))

    # Signal legend
    story.append(Paragraph("SIGNAL LEGEND",
        ps('h2',fontName='Helvetica-Bold',fontSize=10,textColor=TEXT2,leading=13,spaceAfter=6)))
    story.append(HRFlowable(width=CW,thickness=0.5,color=BORDER,spaceAfter=8))
    legend=[
        (PURPLE_BG,PURPLE,   "Series A/B/C/D Funding",        "Institutional VC — company actively scaling, needs supply chain"),
        (PURPLE_BG,PURPLE,   "Angel / Seed Funding",           "Early stage — building from scratch, early vendor relationships matter most"),
        (GREEN_BG, GREEN_MID,"Hiring Hardware Engineers",      "Growing tech headcount — product scaling means supply chain decisions soon"),
        (TEAL_BG,  TEAL,     "Hiring Operations Talent",       "Building ops/supply chain team — procurement decisions are imminent"),
        (colors.HexColor('#E3F2FD'),colors.HexColor('#0D47A1'),"Bay Area Expansion","Moving near Extron's key market — relationship timing is ideal"),
        (colors.HexColor('#FFFDE7'),colors.HexColor('#F57F17'),"CES / Industry Award","Award winners are investing in growth and visibility"),
        (colors.HexColor('#FCE4EC'),colors.HexColor('#880E4F'),"Trade Show Exhibitor","Exhibiting at InfoComm, Interop, HIMSS, NAB, ISC West, etc."),
        (TEAL_BG,  TEAL,     "Supply Chain Onshoring",         "Actively changing suppliers — direct opening for Extron"),
        (colors.HexColor('#E8F5E9'),colors.HexColor('#1B5E20'),"New Company / Startup","Early relationship = long-term customer"),
        (colors.HexColor('#E3F2FD'),colors.HexColor('#0D47A1'),"New CEO / Leadership","New leaders audit all vendor relationships in first 90 days"),
        (ORANGE_BG,ORANGE,   "M&A / Acquisition",              "Post-merger supply chain consolidation creates vendor openings"),
        (RED_BG,   RED,      "Layoffs / Restructuring",        "Cost-cutting leads to renegotiating all supplier contracts"),
    ]
    for bg,fg,lbl,desc in legend:
        story.append(Table([[
            Table([[Paragraph(lbl,ps('ll',fontName='Helvetica-Bold',fontSize=8,textColor=fg,leading=10))]],
                colWidths=[52*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),
                ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
                ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])),
            Paragraph(desc,ps('ld',fontName='Helvetica',fontSize=9,textColor=TEXT2,leading=12)),
        ]],colWidths=[54*mm,CW-58*mm],style=TableStyle([
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('LEFTPADDING',(0,1),(0,1),8),
            ('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2)])))
    story.append(Spacer(1,10*mm))

    # Detailed lead cards
    def section(text,color):
        return [
            Paragraph(text,ps('sh',fontName='Helvetica-Bold',fontSize=12,textColor=color,leading=15,spaceBefore=8,spaceAfter=6)),
            HRFlowable(width=CW,thickness=0.5,color=color,spaceAfter=8)]

    if hot:
        story+=section("HOT LEADS — Act Immediately",RED)
        for l in sorted(hot,key=lambda x:-x.get("urgencyScore",0)):
            story.append(make_card(l,CW))
    if high:
        story+=section("HIGH PRIORITY",AMBER)
        for l in sorted(high,key=lambda x:-x.get("urgencyScore",0)):
            story.append(make_card(l,CW))
    if watch:
        story+=section("WATCH LIST",GRAY_TXT)
        for l in sorted(watch,key=lambda x:-x.get("urgencyScore",0)):
            story.append(make_card(l,CW))
    if not leads:
        story.append(Spacer(1,10*mm))
        story.append(Paragraph("No new qualifying leads found in this scan.",
            ps('e',fontName='Helvetica',fontSize=12,textColor=TEXT2,leading=18)))

    doc.build(story)
    log.info(f"PDF saved: {filename}")


# ════════════════════════════════════════════════════════════════════════════
# COMMUNITY SCANNER — /community routes
# Scans Reddit, Hacker News, Stack Exchange, AVS Forum, AVIXA, LinkedIn,
# Quora and other public communities for supply chain / onshoring signals.
# Completely separate state and data from the main news scanner above.
# ════════════════════════════════════════════════════════════════════════════
import hashlib, csv, io as _io, urllib.parse as _urlparse

COMM_RESULTS_FILE = REPORTS_DIR / "community_results.json"
COMM_SEEN_FILE    = REPORTS_DIR / "community_seen.json"

comm_state = {
    "running": False,
    "status": "Idle — click Run Community Scan to start",
    "last_run": None,
    "total": 0,
    "flagged": 0,
    "sources": [],
    "progress": 0,
}

# ── Community signal keywords (mirrors existing scanner logic) ────────────────
COMM_SUPPLY_SIGNALS = [
    "onshoring","reshoring","nearshoring","offshoring","friend-shoring",
    "supply chain","domestic manufacturing","us manufacturing","made in usa",
    "vendor change","supplier change","procurement","sourcing strategy",
    "supply chain disruption","tariff","import duty","manufacturing move",
    "china+1","china plus one","dual sourcing","supplier diversification",
    "supply chain risk","inventory shortage","component shortage","lead time",
]
COMM_CHANGE_SIGNALS = [
    "new ceo","chief executive","appointed ceo","named ceo","ceo change",
    "merger","acquisition","acquires","acquired","spinoff","spin-off","divest",
    "layoffs","restructuring","workforce reduction","job cuts","reorganization",
    "expands","expansion","new market","enters market",
    "ipo","series a","series b","series c","funding round","raises","raised",
    "new product","product launch","launched",
    "new vp","vp of operations","director of operations","supply chain manager",
    "procurement manager","head of operations","growing team","expanding team",
    "new office","headquarters move",
]
COMM_HARDWARE_SIGNALS = [
    "medical device","medtech","diagnostic","imaging","monitor","surgical",
    "patient monitoring","clinical","hospital","infusion","wearable","implant",
    "ev charging","charging station","charger","evse","electric vehicle",
    "audiovisual"," av ","display","projector","signal processor","switcher",
    "control system","broadcast","videoconfer","digital signage",
    "hardware","device","equipment","instrument","sensor","electronics",
    "industrial iot","embedded system","control panel",
]
COMM_EXTRON_TERMS = ["extron","extron electronics"]

REDDIT_SUBS = [
    "supplychain","manufacturing","logistics","procurement","operations",
    "AV","CommercialAV","hometheater","sysadmin",
    "medicaldevices","EVs","electricvehicles","industrialiot",
    "hardware","electronics","startups","entrepreneur","business",
]
REDDIT_SEARCHES = [
    ("supplychain",    "onshoring reshoring offshoring"),
    ("manufacturing",  "supply chain vendor change"),
    ("procurement",    "supplier hardware"),
    ("CommercialAV",   "supply chain manufacturer"),
    ("medicaldevices", "supply chain procurement"),
    ("EVs",            "supply chain hardware onshoring"),
]
AVS_TERMS   = ["supply chain","manufacturer","acquisition","onshoring","vendor","procurement"]
AVIXA_TERMS = ["supply chain","reshoring","new product","manufacturer","procurement"]

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
        if "av" in c: return "#0C447C","#E6F1FB"
        if "ev" in c or "charg" in c: return "#27500A","#EAF3DE"
        return "#791F1F","#FCEBEB"

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
            f'<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">'
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
<nav class="nav"><a href="/" class="active">Dashboard</a><a href="/history">Lead history</a><a href="/settings">Settings</a><a href="/community">Community scanner</a></nav></div>
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
<select id="fCat" onchange="filterCards()"><option value="">All categories</option><option value="av hardware">AV Hardware</option><option value="ev charging">EV Charging</option><option value="medical">Medical Devices</option></select>
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
        if "av" in c: return "#0C447C","#E6F1FB"
        if "ev" in c or "charg" in c: return "#27500A","#EAF3DE"
        return "#791F1F","#FCEBEB"

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
<nav class="nav"><a href="/">Dashboard</a><a href="/history" class="active">Lead history</a><a href="/settings">Settings</a><a href="/community">Community</a></nav></div>
<div class="filter-bar">
<select id="hCat" onchange="filterHist()"><option value="">All categories</option><option value="av">AV Hardware</option><option value="ev">EV Charging</option><option value="medical">Medical Devices</option></select>
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
<nav class="nav"><a href="/">Dashboard</a><a href="/history">History</a><a href="/settings" class="active">Settings</a><a href="/community">Community</a></nav></div>
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
        scan_state["status"]="Fetching articles from 40+ sources..."
        log.info("="*55)
        log.info("Starting Extron lead intelligence scan...")
        all_articles=[]
        for feed in RSS_FEEDS:
            all_articles.extend(fetch_rss(feed))

        seen_set,unique=set(),[]
        for a in all_articles:
            t=a.get("title","").lower().strip()
            if t and t not in seen_set and within_90_days(a.get("pubDate","")):
                unique.append(a); seen_set.add(t)
        scan_state["articles"]=unique
        log.info(f"Unique articles within 90 days: {len(unique)}")

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

        scan_state["last_run"]=date.today().strftime("%B %d, %Y %H:%M")
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
