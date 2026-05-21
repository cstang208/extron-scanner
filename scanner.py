# v6
import os, re, json, time, logging, urllib.request, threading
from datetime import date, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

import anthropic
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
REPORTS_DIR = Path("/app/reports")
REPORTS_DIR.mkdir(exist_ok=True)

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
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    top = articles[:25]
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
Include companies with confidence >= 30. Be generous — partial matches count.
Aim for at least 5-10 leads combining both jobs.

Each object: name, category (AV Hardware/EV Charging/Medical Devices),
stage (Startup/Early Stage/Growth Stage/Established SMB/Public Company),
hq, founded (int or null), ticker (or null), unitPrice,
signalType (e.g. "Series B Funding", "Hiring Operations Talent", "CES Award", "Trade Show — InfoComm", "Bay Area Expansion", "New CEO/Leadership", "M&A / Acquisition", "Supply Chain Onshoring", "New Product Launch", "New Company / Startup", "Layoffs / Restructuring", "Market Expansion", "New Partnership", "IPO / SPAC", "Angel / Seed Funding"),
signalDate, signalDetail (2 sentences), whyNow (1 sentence), source, sourceUrl,
urgencyScore (0-100), confidence (0-100).

Articles to analyze:
{text}"""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=4000,
            system="Return ONLY a raw JSON array. No markdown. No backticks. Start with [ and end with ].",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.replace("```json","").replace("```","").strip()
        leads = json.loads(raw[raw.index("["):raw.rindex("]")+1])
        log.info(f"AI found {len(leads)} qualifying leads")
        return leads
    except Exception as e:
        log.error(f"AI error: {e}")
        return []

SEEN_FILE = "/tmp/seen_leads.json"
def load_seen():
    try:
        with open(SEEN_FILE) as f: return set(json.load(f))
    except: return set()
def save_seen(seen):
    with open(SEEN_FILE, "w") as f: json.dump(list(seen), f)
def deduplicate(leads, seen):
    fresh = []
    for l in leads:
        key = l.get("name","").lower().strip()
        if key and key not in seen:
            fresh.append(l); seen.add(key)
    return fresh

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

    hdr=Table([[
        Table([
            [Paragraph(l.get("name",""),ps('cn',fontName='Helvetica-Bold',fontSize=14,textColor=INK,leading=18))],
            [Paragraph(f"{l.get('hq','')}  ·  {stage}  ·  {l.get('ticker') or 'Private'}",ps('cs',fontName='Helvetica',fontSize=10,textColor=TEXT2,leading=13))],
        ],colWidths=[CW-80*mm]),
        Table([[badge(l.get("category","").upper(),cbg,cfg,30*mm), badge(ulbl,ubg,ufc,28*mm)]],
            colWidths=[32*mm,30*mm],style=TableStyle([
                ('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),0),
                ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])),
    ]],colWidths=[CW-68*mm,68*mm],style=TableStyle([
        ('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
        ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),7)]))

    sig=Table([
        [Paragraph(f"{l.get('signalType','').upper()}  ·  {l.get('signalDate','')}",
            ps('sigl',fontName='Helvetica-Bold',fontSize=8,textColor=sfc,leading=10))],
        [Paragraph(l.get('signalDetail',''),ps('sigd',fontName='Helvetica',fontSize=10,textColor=INK,leading=14))],
    ],colWidths=[CW-10*mm],style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),sbg),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(0,0),8),('BOTTOMPADDING',(0,0),(0,0),3),
        ('TOPPADDING',(0,1),(-1,-1),0),('BOTTOMPADDING',(0,1),(-1,-1),8)]))

    why=Table([[
        Paragraph("Why reach out now: ",ps('wb',fontName='Helvetica-Bold',fontSize=10,textColor=AMBER,leading=14)),
        Paragraph(l.get('whyNow',''),ps('wt',fontName='Helvetica',fontSize=10,textColor=AMBER,leading=14)),
    ]],colWidths=[34*mm,CW-46*mm],style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),AMBER_BG),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('VALIGN',(0,0),(-1,-1),'TOP')]))

    foot=Table([[
        Paragraph(f"{l.get('unitPrice','$300+/unit')}   |   {l.get('source','')}",
            ps('fp',fontName='Helvetica',fontSize=9,textColor=TEXT2,leading=12)),
        Paragraph(f"Urgency: {u}%  ·  Confidence: {l.get('confidence',50)}%",
            ps('fu',fontName='Helvetica-Bold',fontSize=9,textColor=ufc,leading=12,alignment=TA_RIGHT)),
    ]],colWidths=[CW*0.6,CW*0.4],style=TableStyle([
        ('LINEABOVE',(0,0),(-1,-1),0.5,BORDER),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),0),
        ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE')]))

    inner=Table([[hdr],[sig],[Spacer(1,4)],[why],[Spacer(1,4)],[foot]],
        colWidths=[CW-10*mm],style=TableStyle([
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    outer=Table([[inner]],colWidths=[CW],style=TableStyle([
        ('BOX',(0,0),(-1,-1),0.75,ulc),('LINEBEFORE',(0,0),(0,-1),ulw,ulc),
        ('BACKGROUND',(0,0),(-1,-1),WHITE),
        ('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),
        ('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)]))
    return KeepTogether([outer,Spacer(1,4*mm)])

def generate_pdf(leads, filename):
    doc=SimpleDocTemplate(str(filename),pagesize=A4,
        leftMargin=18*mm,rightMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm)
    W,H=A4; CW=W-36*mm
    today_str=date.today().strftime("%B %d, %Y")
    hot=[l for l in leads if l.get("urgencyScore",0)>=85]
    high=[l for l in leads if 70<=l.get("urgencyScore",0)<85]
    watch=[l for l in leads if l.get("urgencyScore",0)<70]
    story=[]

    cover=Table([
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
                   [Paragraph(today_str,ps('mv',fontName='Helvetica-Bold',fontSize=10,textColor=WHITE,leading=13))]],colWidths=[38*mm]),
            Table([[Paragraph("TOTAL LEADS",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(str(len(leads)),ps('mn',fontName='Helvetica-Bold',fontSize=22,textColor=WHITE,leading=26))]],colWidths=[28*mm]),
            Table([[Paragraph("HOT LEADS",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(str(len(hot)),ps('mn2',fontName='Helvetica-Bold',fontSize=22,textColor=colors.HexColor('#FF9999'),leading=26))]],colWidths=[28*mm]),
            Table([[Paragraph("SOURCES",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph("40+",ps('mv2',fontName='Helvetica-Bold',fontSize=22,textColor=WHITE,leading=26))]],colWidths=[28*mm]),
            Table([[Paragraph("LOOKBACK",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph("90 days",ps('mv3',fontName='Helvetica-Bold',fontSize=10,textColor=WHITE,leading=13))]],colWidths=[32*mm]),
        ]],colWidths=[38*mm,28*mm,28*mm,28*mm,32*mm],style=TableStyle([
            ('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))]
    ],colWidths=[CW],style=TableStyle([
        ('BACKGROUND',(0,0),(-1,-1),NAVY),
        ('LEFTPADDING',(0,0),(-1,-1),20),('RIGHTPADDING',(0,0),(-1,-1),20),
        ('TOPPADDING',(0,0),(-1,-1),20),('BOTTOMPADDING',(0,0),(-1,-1),24)]))
    story+=[cover,Spacer(1,8*mm)]

    # Signal legend
    story.append(Paragraph("SIGNAL TYPES",
        ps('h2',fontName='Helvetica-Bold',fontSize=10,textColor=TEXT2,leading=13,spaceAfter=6)))
    story.append(HRFlowable(width=CW,thickness=0.5,color=BORDER,spaceAfter=8))
    legend=[
        (PURPLE_BG,PURPLE,   "Series A/B/C/D Funding",        "Institutional VC funding — company is actively scaling"),
        (PURPLE_BG,PURPLE,   "Angel / Seed Funding",           "Early stage funding — company is brand new and building"),
        (GREEN_BG, GREEN_MID,"Hiring Hardware Engineers",      "Growing technical headcount — product is scaling"),
        (TEAL_BG,  TEAL,     "Hiring Operations Talent",       "Building supply chain / ops team — procurement decisions incoming"),
        (colors.HexColor('#E3F2FD'),colors.HexColor('#0D47A1'),"Bay Area Expansion","Moving to or expanding in the Bay Area — Extron's backyard"),
        (colors.HexColor('#FFFDE7'),colors.HexColor('#F57F17'),"CES / Industry Award","Award winners are investing in growth and visibility"),
        (colors.HexColor('#FCE4EC'),colors.HexColor('#880E4F'),"Trade Show Exhibitor","Exhibiting at InfoComm, Interop, HIMSS, NAB, ISC West, etc."),
        (TEAL_BG,  TEAL,     "Supply Chain Onshoring",         "Actively changing suppliers — direct opening for Extron"),
        (colors.HexColor('#E8F5E9'),colors.HexColor('#1B5E20'),"New Company / Startup","Early relationship = long-term customer"),
        (colors.HexColor('#E3F2FD'),colors.HexColor('#0D47A1'),"New CEO / Leadership","New leadership audits all vendor relationships"),
        (ORANGE_BG,ORANGE,   "M&A / Acquisition",              "Post-merger supply chain consolidation creates openings"),
        (RED_BG,   RED,      "Layoffs / Restructuring",        "Cost-cutting leads to renegotiating supplier contracts"),
    ]
    for bg,fg,lbl,desc in legend:
        story.append(Table([[
            Table([[Paragraph(lbl,ps('ll',fontName='Helvetica-Bold',fontSize=8,textColor=fg,leading=10))]],
                colWidths=[50*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),
                ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
                ('TOPPADDING',(0,0),(-1,-1),4),('BOTTOMPADDING',(0,0),(-1,-1),4)])),
            Paragraph(desc,ps('ld',fontName='Helvetica',fontSize=9,textColor=TEXT2,leading=12)),
        ]],colWidths=[52*mm,CW-56*mm],style=TableStyle([
            ('VALIGN',(0,0),(-1,-1),'MIDDLE'),
            ('LEFTPADDING',(0,1),(0,1),8),
            ('LEFTPADDING',(0,0),(0,0),0),('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2)])))
    story.append(Spacer(1,8*mm))

    def section(text,color):
        return [
            Paragraph(text,ps('sh',fontName='Helvetica-Bold',fontSize=11,textColor=color,leading=14,spaceBefore=8,spaceAfter=6)),
            HRFlowable(width=CW,thickness=0.5,color=color,spaceAfter=6)]

    if hot:
        story+=section("Hot Leads — Act Immediately",RED)
        for l in sorted(hot,key=lambda x:-x.get("urgencyScore",0)): story.append(make_card(l,CW))
    if high:
        story+=section("High Priority",AMBER)
        for l in sorted(high,key=lambda x:-x.get("urgencyScore",0)): story.append(make_card(l,CW))
    if watch:
        story+=section("Watch List",GRAY_TXT)
        for l in sorted(watch,key=lambda x:-x.get("urgencyScore",0)): story.append(make_card(l,CW))
    if not leads:
        story.append(Spacer(1,10*mm))
        story.append(Paragraph("No new qualifying leads found in this scan.",
            ps('e',fontName='Helvetica',fontSize=12,textColor=TEXT2,leading=18)))

    doc.build(story)
    log.info(f"PDF saved: {filename}")

# ── Web UI ────────────────────────────────────────────────────────────────────
def build_page():
    s = scan_state
    status_html = ""
    if s["running"]:
        status_html = f'<div style="background:#E6F1FB;border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:13px;color:#0C447C;border:1px solid #B5D4F4">⏳ {s["status"]}</div>'
    elif s["last_run"]:
        leads=s.get("leads",[]); hot=sum(1 for l in leads if l.get("urgencyScore",0)>=85)
        status_html=f'<div style="background:#EAF3DE;border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:13px;color:#27500A;border:1px solid #A5D6A7">✅ Scan complete — {len(leads)} leads found ({hot} hot). Download the PDF below.</div>'

    files=sorted(REPORTS_DIR.glob("*.pdf"),reverse=True)
    pdf_links="".join(
        f'<li style="margin:10px 0"><a href="/download/{f.name}" style="color:#0C447C;font-size:15px;text-decoration:none">📄 {f.name}</a> <span style="color:#999;font-size:12px">{f.stat().st_size//1024} KB</span></li>'
        for f in files
    ) or '<li style="color:#999;font-size:13px">No reports yet — run a scan first.</li>'

    articles=s.get("filtered",[])
    article_rows="".join(
        f'<tr style="border-bottom:1px solid #f0f0f0"><td style="padding:9px 8px;font-size:12px"><a href="{a.get("link","#")}" target="_blank" style="color:#0C447C;text-decoration:none">{a.get("title","")[:115]}</a></td><td style="padding:9px 8px;font-size:11px;color:#999;white-space:nowrap">{a.get("pubDate","")[:16]}</td></tr>'
        for a in articles
    ) or '<tr><td colspan="2" style="padding:20px;color:#999;font-size:13px;text-align:center">Run a scan to see articles</td></tr>'

    signals_covered = [
        "Series A / B / C / D funding","Angel & seed funding","Hiring hardware engineers",
        "Hiring operations & supply chain talent","Bay Area expansion / relocation",
        "CES awards & industry awards","InfoComm, Interop, HIMSS, NAB, ISC West exhibitors",
        "Supply chain onshoring / reshoring discussions","New startups & early-stage companies",
        "New CEO / leadership changes","M&A / acquisitions","Layoffs & restructuring",
        "New product launches","New strategic partnerships",
    ]
    sig_pills="".join(f'<span style="display:inline-block;background:#f0f0f0;border-radius:99px;padding:3px 10px;font-size:11px;color:#444;margin:3px">{s}</span>' for s in signals_covered)

    last=f"Last scan: {s['last_run']}" if s['last_run'] else "No scans run yet"
    return f"""<!DOCTYPE html><html><head><title>Extron Lead Intelligence</title>
<meta http-equiv="refresh" content="8">
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f3;margin:0;padding:24px}}
  .wrap{{max-width:860px;margin:0 auto}}
  .card{{background:white;border-radius:10px;padding:20px 24px;margin-bottom:16px;border:1px solid #e0e0e0}}
  h2{{font-size:15px;margin:0 0 10px;color:#111}}
</style>
</head><body><div class="wrap">
  <div style="background:#0C447C;color:white;padding:24px 28px;border-radius:12px;margin-bottom:20px">
    <h1 style="margin:0 0 4px;font-size:22px">Extron Lead Intelligence Scanner</h1>
    <p style="margin:0;opacity:.75;font-size:13px">{last}</p>
  </div>

  {status_html}

  <div class="card">
    <h2>🔍 Run a scan</h2>
    <p style="font-size:13px;color:#666;margin:0 0 10px">Searches 40+ sources across news, press releases, and trade publications. Covers all the signals below. Takes 1–2 minutes.</p>
    <div style="margin-bottom:14px">{sig_pills}</div>
    <form method="POST" action="/run">
      <button type="submit" {"disabled" if s["running"] else ""}
        style="background:{'#aaa' if s['running'] else '#0C447C'};color:white;border:none;padding:11px 28px;border-radius:8px;font-size:14px;font-weight:600;cursor:{'not-allowed' if s['running'] else 'pointer'}">
        {'⏳ Scan running...' if s['running'] else '🔍 Run Scan Now'}
      </button>
    </form>
  </div>

  <div class="card">
    <h2>📄 PDF Reports ({len(files)} available)</h2>
    <ul style="list-style:none;padding:0;margin:0">{pdf_links}</ul>
  </div>

  <div class="card">
    <h2>📰 Articles analyzed in last scan ({len(articles)} articles)</h2>
    <p style="font-size:12px;color:#999;margin:0 0 10px">Articles that matched keyword filters and were sent to AI for analysis. Click any to read the full article.</p>
    <div style="max-height:480px;overflow-y:auto;border:1px solid #eee;border-radius:6px">
      <table width="100%" cellpadding="0" cellspacing="0">
        <thead><tr style="background:#f7f7f5">
          <th style="padding:9px 8px;text-align:left;font-size:11px;color:#666;font-weight:600">Article title</th>
          <th style="padding:9px 8px;text-align:left;font-size:11px;color:#666;font-weight:600;white-space:nowrap">Date</th>
        </tr></thead>
        <tbody>{article_rows}</tbody>
      </table>
    </div>
  </div>
</div></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def do_GET(self):
        if self.path in ["/","reports"]:
            self.send_response(200); self.send_header("Content-Type","text/html"); self.end_headers()
            self.wfile.write(build_page().encode())
        elif self.path.startswith("/download/"):
            fname=self.path.replace("/download/",""); fpath=REPORTS_DIR/fname
            if fpath.exists() and fpath.suffix==".pdf":
                self.send_response(200); self.send_header("Content-Type","application/pdf")
                self.send_header("Content-Disposition",f'attachment; filename="{fname}"'); self.end_headers()
                self.wfile.write(fpath.read_bytes())
            else:
                self.send_response(404); self.end_headers(); self.wfile.write(b"Not found")
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        if self.path=="/run":
            if not scan_state["running"]:
                threading.Thread(target=run_scan,daemon=True).start()
            self.send_response(303); self.send_header("Location","/"); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

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

        scan_state["status"]=f"AI analyzing top {min(len(filtered),25)} articles..."
        leads=ai_filter(filtered)

        seen=load_seen()
        fresh=deduplicate(leads,seen)
        save_seen(seen)
        scan_state["leads"]=fresh

        scan_state["status"]="Generating PDF..."
        filename=REPORTS_DIR/f"Extron_Leads_{date.today().isoformat()}.pdf"
        generate_pdf(fresh,filename)

        scan_state["last_run"]=date.today().strftime("%B %d, %Y %H:%M")
        scan_state["status"]=f"Done — {len(fresh)} leads found."
        log.info("Scan complete.")
    except Exception as e:
        scan_state["status"]=f"Error: {e}"
        log.error(f"Scan error: {e}",exc_info=True)
    finally:
        scan_state["running"]=False

if __name__=="__main__":
    log.info("Extron Scanner starting...")
    threading.Thread(target=start_web_server,daemon=True).start()
    threading.Thread(target=run_scan,daemon=True).start()
    while True:
        time.sleep(60)
