import os, re, json, time, logging, urllib.request, urllib.error, threading
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

# ── All sources ───────────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Direct industry feeds
    "https://www.medtechdive.com/feeds/news/",
    "https://www.massdevice.com/feed/",
    "https://www.avnetwork.com/rss.xml",
    "https://www.electronicdesign.com/rss.xml",
    "https://electrek.co/feed/",
    "https://www.greencarcongress.com/atom.xml",
    "https://www.prnewswire.com/rss/news-releases-list.rss",
    "https://www.businesswire.com/rss/home/?rss=G7",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.fiercebiotech.com/rss.xml",
    "https://www.fierceelectronics.com/rss.xml",
    "https://www.mobihealthnews.com/feed",
    # Google News targeted searches
    "https://news.google.com/rss/search?q=medical+device+company+new+CEO+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+acquisition+merger+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medtech+layoffs+restructuring+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+startup+launch+funding+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+new+product+FDA+clearance+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+company+CEO+acquisition+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+startup+funding+launch+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+infrastructure+merger+partnership+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=professional+AV+company+acquisition+CEO+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=audiovisual+hardware+company+merger+launch+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=AV+technology+company+funding+partnership+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+new+CEO+appointed+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+acquisition+announced+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+manufacturer+layoffs+restructuring+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+new+product+launch+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=hardware+company+partnership+announced+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:prnewswire.com+medical+device+CEO+acquisition&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:prnewswire.com+EV+charging+CEO+acquisition&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:businesswire.com+hardware+company+merger+CEO&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:sec.gov+8-K+hardware+medical+device+CEO&hl=en-US&gl=US&ceid=US:en",
]

# SEC EDGAR direct search URLs for 8-K filings (leadership + M&A)
SEC_URLS = [
    "https://efts.sec.gov/LATEST/search-index?q=%22appointed%22+%22Chief+Executive%22&forms=8-K&dateRange=custom&startdt={start}&enddt={end}&_type=feed&action=getcompany",
    "https://efts.sec.gov/LATEST/search-index?q=%22merger%22+%22acquisition%22&forms=8-K&dateRange=custom&startdt={start}&enddt={end}&_type=feed&action=getcompany",
    "https://efts.sec.gov/LATEST/search-index?q=%22restructuring%22+%22workforce%22&forms=8-K&dateRange=custom&startdt={start}&enddt={end}&_type=feed&action=getcompany",
]

def fetch_url_text(url, max_chars=2000):
    """Fetch a URL and return cleaned text content."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("utf-8", errors="replace")
        # Strip HTML tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except:
        return ""

def fetch_rss(url):
    """Fetch RSS feed and return items with full text where possible."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", errors="replace")
        items = []
        for item in re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
            def tag(t):
                m = re.search(fr"<{t}[^>]*>(.*?)</{t}>", item, re.DOTALL)
                return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
            title   = tag("title")
            link    = tag("link")
            summary = tag("description")
            pubdate = tag("pubDate")
            if title:
                items.append({"title": title, "link": link, "summary": summary, "pubDate": pubdate})
        log.info(f"  {len(items)} items from {url[:70]}")
        return items
    except Exception as e:
        log.warning(f"Feed failed {url[:60]}: {e}")
        return []

def fetch_sec(url):
    """Fetch SEC EDGAR filing feed."""
    start = (date.today() - timedelta(days=90)).isoformat()
    end   = date.today().isoformat()
    url   = url.replace("{start}", start).replace("{end}", end)
    return fetch_rss(url)

def enrich_article(article):
    """Fetch full article text to give Claude more context."""
    link = article.get("link", "")
    if not link or "google.com" in link or "sec.gov" in link:
        return article
    full_text = fetch_url_text(link, max_chars=1500)
    if full_text:
        article["full_text"] = full_text
    return article

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

def ai_filter(articles):
    if not articles:
        return []
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    results = []
    batch_size = 20  # smaller batches with richer content

    for i in range(0, min(len(articles), 200), batch_size):
        batch = articles[i:i+batch_size]
        text = "\n\n".join(
            f"[{j+1}] TITLE: {a['title']}\n"
            f"    DATE: {a.get('pubDate', 'unknown')}\n"
            f"    SUMMARY: {a.get('summary', '')[:400]}\n"
            f"    FULL TEXT: {a.get('full_text', '')[:800]}\n"
            f"    URL: {a['link']}"
            for j, a in enumerate(batch)
        )

        prompt = f"""You are a B2B sales intelligence analyst for Extron Electronics.

Extron sells professional AV hardware ($300-$50,000/unit): signal processors, switchers, 
control systems, displays. Their ideal customers are companies that also sell physical 
hardware at $300+/unit in these categories:

1. PROFESSIONAL AV — displays, projectors, signal processors, switchers, control systems,
   digital signage hardware, videoconferencing hardware, broadcast equipment

2. EV CHARGING — charging stations, fleet charging hardware, EVSE equipment, 
   charging infrastructure, DC fast chargers

3. MEDICAL DEVICES — diagnostic equipment, patient monitoring, imaging systems,
   surgical hardware, wearable medical devices, lab equipment, infusion pumps

These companies are HIGH VALUE LEADS if they are experiencing ANY of:
- New CEO, President, or C-suite leadership appointment
- Merger, acquisition, spinoff, or divestiture
- Layoffs, restructuring, or workforce reduction
- Expansion to new markets, geographies, or customer segments
- Major new product launch (especially hardware)
- New strategic partnership or distribution agreement
- New company or startup just launched
- IPO, SPAC, or significant new funding round ($10M+)

Analyze ALL {len(batch)} articles carefully. Even partial matches are worth including.

Return a raw JSON array. No markdown. No backticks. Start with [ end with ].
Return [] only if absolutely nothing matches.
Include companies with confidence >= 40.

Each object must have:
- name: company name
- category: "AV Hardware" or "EV Charging" or "Medical Devices"
- hq: city, state (or "Unknown")
- founded: year as integer or null
- ticker: stock ticker string or null
- unitPrice: price per unit e.g. "$500-$2,000/unit" or "Unknown"
- signalType: one of "New CEO/Leadership", "M&A/Acquisition", "Layoffs/Restructuring", 
  "Market Expansion", "New Product Launch", "New Partnership", "New Company/Startup", "New Funding"
- signalDate: e.g. "March 2025" or "Q1 2025"
- signalDetail: 2 factual sentences about what happened
- whyNow: 1 sentence on why this creates a supply chain opportunity for Extron right now
- source: publication name
- sourceUrl: article URL
- urgencyScore: 0-100
- confidence: 0-100

Articles:
{text}"""

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=3000,
                system="You are a JSON-only API. Return ONLY a raw JSON array. No markdown. No backticks. Start with [ and end with ].",
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.replace("```json","").replace("```","").strip()
            s, e = raw.index("["), raw.rindex("]")
            batch_leads = json.loads(raw[s:e+1])
            log.info(f"Batch {i//batch_size+1}: {len(batch_leads)} leads found")
            results.extend(batch_leads)
        except Exception as ex:
            log.error(f"AI error batch {i//batch_size+1}: {ex}")
        time.sleep(2)

    return results

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
            fresh.append(l)
            seen.add(key)
    return fresh

# ── PDF colors ────────────────────────────────────────────────────────────────
NAVY=colors.HexColor('#0C447C'); WHITE=colors.white
AMBER=colors.HexColor('#633806'); AMBER_BG=colors.HexColor('#FAEEDA'); AMBER_MID=colors.HexColor('#BA7517')
RED=colors.HexColor('#791F1F'); RED_BG=colors.HexColor('#FCEBEB'); RED_MID=colors.HexColor('#E24B4A')
GREEN=colors.HexColor('#27500A'); GREEN_BG=colors.HexColor('#EAF3DE')
GRAY_BG=colors.HexColor('#F1EFE8'); GRAY_TXT=colors.HexColor('#444441')
GRAY_LT=colors.HexColor('#F7F7F5'); BORDER=colors.HexColor('#E0E0E0')
TEXT2=colors.HexColor('#666666'); INK=colors.HexColor('#111111')

def ps(name, **kw): return ParagraphStyle(name, **kw)

def badge(text, bg, fg, w):
    return Table([[Paragraph(text, ps('b', fontName='Helvetica-Bold', fontSize=8, textColor=fg, leading=10))]],
        colWidths=[w], style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),
        ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
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

def make_card(l, CW):
    u=l.get("urgencyScore",60)
    ulbl,ubg,ufc,ulc,ulw=urg_meta(u)
    cbg,cfg=cat_col(l.get("category",""))
    hdr=Table([[
        Table([[Paragraph(l.get("name",""),ps('cn',fontName='Helvetica-Bold',fontSize=14,textColor=INK,leading=18))],
               [Paragraph(f"{l.get('hq','')}  ·  Est. {l.get('founded','—')}  ·  {l.get('ticker') or 'Private'}",ps('cs',fontName='Helvetica',fontSize=10,textColor=TEXT2,leading=13))]],colWidths=[CW-76*mm]),
        Table([[badge(l.get("category","").upper(),cbg,cfg,32*mm),badge(ulbl,ubg,ufc,28*mm)]],
            colWidths=[34*mm,30*mm],style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])),
    ]],colWidths=[CW-64*mm,64*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    sig=Table([[Paragraph(f"{l.get('signalType','').upper()}  ·  {l.get('signalDate','')}",ps('sigl',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10))],
               [Paragraph(l.get('signalDetail',''),ps('sigd',fontName='Helvetica',fontSize=10,textColor=INK,leading=14))]],
        colWidths=[CW-10*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),GRAY_LT),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(0,0),8),('BOTTOMPADDING',(0,0),(0,0),3),('TOPPADDING',(0,1),(-1,-1),0),('BOTTOMPADDING',(0,1),(-1,-1),8)]))
    why=Table([[Paragraph("Why contact now: ",ps('wb',fontName='Helvetica-Bold',fontSize=10,textColor=AMBER,leading=14)),
                Paragraph(l.get('whyNow',''),ps('wt',fontName='Helvetica',fontSize=10,textColor=AMBER,leading=14))]],
        colWidths=[32*mm,CW-44*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),AMBER_BG),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('VALIGN',(0,0),(-1,-1),'TOP')]))
    foot=Table([[Paragraph(f"{l.get('unitPrice','$300+/unit')}   |   {l.get('source','')}",ps('fp',fontName='Helvetica',fontSize=9,textColor=TEXT2,leading=12)),
                 Paragraph(f"Urgency: {u}%",ps('fu',fontName='Helvetica-Bold',fontSize=9,textColor=ufc,leading=12,alignment=TA_RIGHT))]],
        colWidths=[CW*0.65,CW*0.35],style=TableStyle([('LINEABOVE',(0,0),(-1,-1),0.5,BORDER),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),0),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    inner=Table([[hdr],[sig],[Spacer(1,4)],[why],[Spacer(1,4)],[foot]],colWidths=[CW-10*mm],
        style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    outer=Table([[inner]],colWidths=[CW],style=TableStyle([('BOX',(0,0),(-1,-1),0.75,ulc),('LINEBEFORE',(0,0),(0,-1),ulw,ulc),('BACKGROUND',(0,0),(-1,-1),WHITE),('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)]))
    return KeepTogether([outer,Spacer(1,4*mm)])

def generate_pdf(leads, filename):
    doc=SimpleDocTemplate(str(filename),pagesize=A4,leftMargin=18*mm,rightMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm)
    W,H=A4; CW=W-36*mm
    today_str=date.today().strftime("%B %d, %Y")
    hot=[l for l in leads if l.get("urgencyScore",0)>=85]
    high=[l for l in leads if 70<=l.get("urgencyScore",0)<85]
    watch=[l for l in leads if l.get("urgencyScore",0)<70]
    story=[]
    cover=Table([
        [Paragraph("CONFIDENTIAL — EXTRON SALES INTELLIGENCE",ps('cl',fontName='Helvetica',fontSize=9,textColor=colors.HexColor('#aaaacc'),leading=11))],
        [Spacer(1,6)],
        [Paragraph("Change Signal Lead Report",ps('ct',fontName='Helvetica-Bold',fontSize=24,textColor=WHITE,leading=30))],
        [Paragraph("Companies undergoing change — prime targets for Extron supply chain conversations",ps('cs',fontName='Helvetica',fontSize=12,textColor=colors.HexColor('#ccddee'),leading=17))],
        [Spacer(1,14)],
        [Table([[
            Table([[Paragraph("GENERATED",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],[Paragraph(today_str,ps('mv',fontName='Helvetica-Bold',fontSize=11,textColor=WHITE,leading=14))]],colWidths=[44*mm]),
            Table([[Paragraph("COMPANIES",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],[Paragraph(str(len(leads)),ps('mn',fontName='Helvetica-Bold',fontSize=22,textColor=WHITE,leading=26))]],colWidths=[32*mm]),
            Table([[Paragraph("HOT LEADS",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],[Paragraph(str(len(hot)),ps('mn2',fontName='Helvetica-Bold',fontSize=22,textColor=colors.HexColor('#FF9999'),leading=26))]],colWidths=[32*mm]),
            Table([[Paragraph("LOOKBACK",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],[Paragraph("90 days",ps('mv2',fontName='Helvetica-Bold',fontSize=11,textColor=WHITE,leading=14))]],colWidths=[44*mm]),
        ]],colWidths=[44*mm,32*mm,32*mm,44*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))],
    ],colWidths=[CW],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),NAVY),('LEFTPADDING',(0,0),(-1,-1),20),('RIGHTPADDING',(0,0),(-1,-1),20),('TOPPADDING',(0,0),(-1,-1),20),('BOTTOMPADDING',(0,0),(-1,-1),24)]))
    story+=[cover,Spacer(1,8*mm)]
    story.append(Paragraph("EXECUTIVE SUMMARY",ps('h2',fontName='Helvetica-Bold',fontSize=10,textColor=TEXT2,leading=13,spaceAfter=6)))
    story.append(HRFlowable(width=CW,thickness=0.5,color=BORDER,spaceAfter=8))
    def sbox(lbl,val,col=INK):
        return Table([[Paragraph(lbl,ps('sl',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10))],[Paragraph(str(val),ps('sv',fontName='Helvetica-Bold',fontSize=24,textColor=col,leading=28))]],
            colWidths=[(CW/4)-6*mm],style=TableStyle([('BACKGROUND',(0,0),(-1,-1),GRAY_LT),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)]))
    story.append(Table([[sbox("TOTAL LEADS",len(leads)),sbox("HOT LEADS",len(hot),RED),sbox("HIGH PRIORITY",len(high),AMBER),sbox("WATCH LIST",len(watch),GRAY_TXT)]],
        colWidths=[(CW/4)]*4,style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])))
    story.append(Spacer(1,8*mm))
    def section(text,color):
        return [Paragraph(text,ps('sh',fontName='Helvetica-Bold',fontSize=11,textColor=color,leading=14,spaceBefore=8,spaceAfter=6)),HRFlowable(width=CW,thickness=0.5,color=color,spaceAfter=6)]
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
        story.append(Paragraph("No new qualifying leads found today. The scanner will run again tomorrow.",ps('e',fontName='Helvetica',fontSize=12,textColor=TEXT2,leading=18)))
    doc.build(story)
    log.info(f"PDF saved: {filename}")

class DownloadHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass
    def do_GET(self):
        if self.path in ["/","/reports"]:
            files=sorted(REPORTS_DIR.glob("*.pdf"),reverse=True)
            links="".join(f'<li style="margin:12px 0"><a href="/download/{f.name}" style="font-size:16px;color:#0C447C;text-decoration:none">📄 {f.name}</a> &nbsp;<span style="color:#999;font-size:12px">{f.stat().st_size//1024} KB</span></li>' for f in files)
            html=f"""<!DOCTYPE html><html><head><title>Extron Lead Reports</title></head>
<body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:20px">
  <div style="background:#0C447C;color:white;padding:24px;border-radius:10px;margin-bottom:24px">
    <h1 style="margin:0;font-size:22px">Extron Lead Intelligence Reports</h1>
    <p style="margin:8px 0 0;opacity:.8;font-size:13px">Click any report to download the PDF</p>
  </div>
  {'<ul style="list-style:none;padding:0">'+links+'</ul>' if files else '<p style="color:#666;font-size:14px">No reports yet — scanner runs daily at 1PM UTC.</p>'}
</body></html>"""
            self.send_response(200); self.send_header("Content-Type","text/html"); self.end_headers()
            self.wfile.write(html.encode())
        elif self.path.startswith("/download/"):
            fname=self.path.replace("/download/","")
            fpath=REPORTS_DIR/fname
            if fpath.exists() and fpath.suffix==".pdf":
                self.send_response(200); self.send_header("Content-Type","application/pdf")
                self.send_header("Content-Disposition",f'attachment; filename="{fname}"'); self.end_headers()
                self.wfile.write(fpath.read_bytes())
            else:
                self.send_response(404); self.end_headers(); self.wfile.write(b"Not found")
        else:
            self.send_response(404); self.end_headers()

def start_web_server():
    port=int(os.environ.get("PORT",8080))
    server=HTTPServer(("0.0.0.0",port),DownloadHandler)
    log.info(f"Download server on port {port}")
    server.serve_forever()

def run():
    log.info("="*55)
    log.info("Starting Extron lead intelligence scan...")

    # Fetch all RSS feeds
    all_articles = []
    for feed in RSS_FEEDS:
        all_articles.extend(fetch_rss(feed))

    # Fetch SEC filings
    for sec_url in SEC_URLS:
        all_articles.extend(fetch_sec(sec_url))

    # Deduplicate by title, filter to 90 days
    seen_titles, unique = set(), []
    for a in all_articles:
        t = a.get("title","").lower().strip()
        if t and t not in seen_titles and within_90_days(a.get("pubDate","")):
            unique.append(a)
            seen_titles.add(t)
    log.info(f"Unique articles within 90 days: {len(unique)}")

    # Enrich top articles with full text
    log.info("Fetching full article text for top articles...")
    enriched = []
    for a in unique[:150]:  # enrich top 150
        enriched.append(enrich_article(a))
        time.sleep(0.3)  # be polite to servers
    enriched += unique[150:]  # add rest without enrichment

    leads = ai_filter(enriched)

    seen = load_seen()
    fresh = deduplicate(leads, seen)
    save_seen(seen)
    log.info(f"Fresh leads after deduplication: {len(fresh)}")

    filename = REPORTS_DIR / f"Extron_Leads_{date.today().isoformat()}.pdf"
    generate_pdf(fresh, filename)
    log.info("Scan complete. PDF ready to download.")

if __name__ == "__main__":
    log.info("Extron Scanner starting...")
    threading.Thread(target=start_web_server, daemon=True).start()
    while True:
        try:
            run()
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)
        log.info("Sleeping 24 hours...")
        time.sleep(86400)
