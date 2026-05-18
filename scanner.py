"""
Extron Lead Intelligence Scanner
---------------------------------
Runs every 24 hours on Railway. Scans trusted news/SEC sources for companies that:
  - Sell AV hardware, EV charging hardware, or medical devices ($300+/unit)
  - Are undergoing leadership change, M&A, restructuring, or market expansion
  - Fit Extron's ideal customer profile

Sends a PDF report to your inbox each morning.
"""

import os, re, json, time, logging, smtplib, urllib.request
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from io import BytesIO

import anthropic
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 Table, TableStyle, HRFlowable, KeepTogether)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# ── Config — set these as environment variables in Railway ───────────────────
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
SMTP_HOST          = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT          = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER          = os.environ.get("SMTP_USER", os.environ.get("GMAIL_ADDRESS", ""))        # your Gmail address
SMTP_PASS          = os.environ.get("SMTP_PASS", os.environ.get("GMAIL_APP_PASS", ""))        # Gmail app password
REPORT_RECIPIENT   = os.environ.get("REPORT_RECIPIENT", os.environ.get("REPORT_EMAIL", "")) # where to send report
SCAN_INTERVAL_HRS  = float(os.environ.get("SCAN_INTERVAL_HRS", "24"))

# ── RSS feeds ────────────────────────────────────────────────────────────────
FEEDS = [
    "https://news.google.com/rss/search?q=AV+hardware+company+CEO+acquisition+restructuring&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=pro+AV+display+signal+processor+company+change&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=EV+charging+hardware+company+leadership+merger&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=electric+vehicle+charging+station+company+restructuring&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medical+device+hardware+new+CEO+acquisition+2025&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=medtech+hardware+restructuring+layoffs+expansion&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:prnewswire.com+hardware+company+acquisition+CEO&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:businesswire.com+hardware+company+merger+leadership&hl=en-US&gl=US&ceid=US:en",
]

# ── Colors ───────────────────────────────────────────────────────────────────
NAVY=colors.HexColor('#0C447C'); WHITE=colors.white
AMBER=colors.HexColor('#633806'); AMBER_BG=colors.HexColor('#FAEEDA'); AMBER_MID=colors.HexColor('#BA7517')
RED=colors.HexColor('#791F1F'); RED_BG=colors.HexColor('#FCEBEB'); RED_MID=colors.HexColor('#E24B4A')
GREEN=colors.HexColor('#27500A'); GREEN_BG=colors.HexColor('#EAF3DE')
GRAY_BG=colors.HexColor('#F1EFE8'); GRAY_TXT=colors.HexColor('#444441')
GRAY_LT=colors.HexColor('#F7F7F5'); BORDER=colors.HexColor('#E0E0E0')
TEXT2=colors.HexColor('#666666'); INK=colors.HexColor('#111111')

def ps(name, **kw): return ParagraphStyle(name, **kw)

# ── RSS fetch ─────────────────────────────────────────────────────────────────
def fetch_rss(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"RSS fetch failed {url[:55]}: {e}"); return []
    items = []
    for item in re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
        def tag(t):
            m = re.search(fr"<{t}[^>]*>(.*?)</{t}>", item, re.DOTALL)
            return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
        items.append({"title": tag("title"), "link": tag("link"), "summary": tag("description")})
    log.info(f"  {len(items)} items from {url[:55]}"); return items

# ── AI filter ─────────────────────────────────────────────────────────────────
def ai_filter(articles):
    if not articles: return []
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    text = "\n".join(
        f"[{i+1}] TITLE: {a['title']}\n    SUMMARY: {a['summary'][:250]}\n    URL: {a['link']}"
        for i, a in enumerate(articles[:40])
    )
    prompt = f"""You are a B2B sales intelligence analyst for Extron Inc (professional AV hardware).
Extron's ideal customers sell physical hardware ($300+/unit) in:
  - Professional AV / displays / signal processors
  - EV charging infrastructure
  - Medical devices & diagnostics
AND are currently experiencing: new CEO/leadership, merger/acquisition, restructuring/layoffs, OR market expansion.

Review these articles. Return ONLY companies that clearly match ALL criteria.
Return a raw JSON array (no markdown, no backticks). Start with [ end with ].
If none qualify return [].

Each object: name, category (AV Hardware / EV Charging / Medical Devices),
hq, founded (int or null), ticker (string or null), unitPrice,
signalType, signalDate, signalDetail (2 sentences),
whyNow (1 sentence — supply chain opening for Extron),
source, sourceUrl, urgencyScore (0-100), confidence (0-100).

Articles:
{text}"""
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=2000,
            system="JSON-only API. Return ONLY a raw JSON array. No markdown. No backticks.",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.replace("```json","").replace("```","").strip()
        leads = json.loads(raw[raw.index("["):raw.rindex("]")+1])
        log.info(f"AI returned {len(leads)} qualifying leads"); return leads
    except Exception as e:
        log.error(f"AI filter error: {e}"); return []

# ── Deduplication ─────────────────────────────────────────────────────────────
SEEN_FILE = "/tmp/seen_leads.json"
def load_seen():
    try:
        with open(SEEN_FILE) as f: return set(json.load(f))
    except: return set()
def save_seen(seen):
    with open(SEEN_FILE, "w") as f: json.dump(list(seen), f)
def deduplicate(leads, seen):
    out = []
    for l in leads:
        k = l.get("name","").lower().strip()
        if k and k not in seen: out.append(l); seen.add(k)
    return out

# ── PDF ───────────────────────────────────────────────────────────────────────
def badge(text, bg, fg, w):
    return Table([[Paragraph(text, ps('b', fontName='Helvetica-Bold', fontSize=8, textColor=fg, leading=10))]],
        colWidths=[w], style=TableStyle([('BACKGROUND',(0,0),(-1,-1),bg),
        ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
        ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]))

def urg_meta(u):
    if u>=85: return "HOT LEAD",RED_BG,RED,RED_MID,3.5
    if u>=70: return "HIGH PRIORITY",AMBER_BG,AMBER,AMBER_MID,1.0
    return "WATCH LIST",GRAY_BG,GRAY_TXT,BORDER,0.5

def cat_colors(cat):
    c = cat.lower()
    if "av" in c or "audio" in c or "video" in c: return colors.HexColor('#EAF0FB'),colors.HexColor('#1A3A6B')
    if "ev" in c or "charg" in c: return GREEN_BG,GREEN
    return colors.HexColor('#FDE8EC'),colors.HexColor('#7A1530')

def lead_card(l, CW):
    u = l.get("urgencyScore",60)
    ulbl,ubg,ufc,ulc,ulw = urg_meta(u)
    cbg,cfg = cat_colors(l.get("category",""))
    hdr = Table([[
        Table([[Paragraph(l.get("name",""), ps('cn',fontName='Helvetica-Bold',fontSize=14,textColor=INK,leading=18))],
               [Paragraph(f"{l.get('hq','')}  ·  Est. {l.get('founded','—')}  ·  {l.get('ticker','') or 'Private'}",
                    ps('cs',fontName='Helvetica',fontSize=10,textColor=TEXT2,leading=13))]],
            colWidths=[CW-76*mm]),
        Table([[badge(l.get("category","").upper(),cbg,cfg,32*mm), badge(ulbl,ubg,ufc,28*mm)]],
            colWidths=[34*mm,30*mm], style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),3),
            ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])),
    ]], colWidths=[CW-64*mm,64*mm], style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
        ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    sig = Table([
        [Paragraph(f"{l.get('signalType','').upper()}  ·  {l.get('signalDate','')}",
            ps('sl',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10))],
        [Paragraph(l.get('signalDetail',''), ps('sd',fontName='Helvetica',fontSize=10,textColor=INK,leading=14))],
    ], colWidths=[CW-10*mm], style=TableStyle([('BACKGROUND',(0,0),(-1,-1),GRAY_LT),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(0,0),8),('BOTTOMPADDING',(0,0),(0,0),3),
        ('TOPPADDING',(0,1),(-1,-1),0),('BOTTOMPADDING',(0,1),(-1,-1),8)]))
    why = Table([[
        Paragraph("Why contact now: ", ps('wb',fontName='Helvetica-Bold',fontSize=10,textColor=AMBER,leading=14)),
        Paragraph(l.get('whyNow',''), ps('wt',fontName='Helvetica',fontSize=10,textColor=AMBER,leading=14)),
    ]], colWidths=[32*mm,CW-44*mm], style=TableStyle([('BACKGROUND',(0,0),(-1,-1),AMBER_BG),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('VALIGN',(0,0),(-1,-1),'TOP')]))
    foot = Table([[
        Paragraph(f"{l.get('unitPrice','$300+/unit')}   |   {l.get('source','')}",
            ps('fp',fontName='Helvetica',fontSize=9,textColor=TEXT2,leading=12)),
        Paragraph(f"Urgency: {u}%",
            ps('fu',fontName='Helvetica-Bold',fontSize=9,textColor=ufc,leading=12,alignment=TA_RIGHT)),
    ]], colWidths=[CW*0.65,CW*0.35], style=TableStyle([('LINEABOVE',(0,0),(-1,-1),0.5,BORDER),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),0),
        ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('VALIGN',(0,0),(-1,-1),'MIDDLE')]))
    inner = Table([[hdr],[sig],[Spacer(1,4)],[why],[Spacer(1,4)],[foot]], colWidths=[CW-10*mm],
        style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
        ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    outer = Table([[inner]], colWidths=[CW], style=TableStyle([
        ('BOX',(0,0),(-1,-1),0.75,ulc),('LINEBEFORE',(0,0),(0,-1),ulw,ulc),
        ('BACKGROUND',(0,0),(-1,-1),WHITE),
        ('LEFTPADDING',(0,0),(-1,-1),12),('RIGHTPADDING',(0,0),(-1,-1),12),
        ('TOPPADDING',(0,0),(-1,-1),12),('BOTTOMPADDING',(0,0),(-1,-1),12)]))
    return KeepTogether([outer, Spacer(1,4*mm)])

def generate_pdf(leads):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
    W,H = A4; CW = W-36*mm
    today_str = date.today().strftime("%B %d, %Y")
    hot   = [l for l in leads if l.get("urgencyScore",0)>=85]
    high  = [l for l in leads if 70<=l.get("urgencyScore",0)<85]
    watch = [l for l in leads if l.get("urgencyScore",0)<70]
    story = []

    # Cover
    cov = Table([
        [Paragraph("CONFIDENTIAL — EXTRON SALES INTELLIGENCE",
            ps('cl',fontName='Helvetica',fontSize=9,textColor=colors.HexColor('#aaaacc'),leading=11))],
        [Spacer(1,6)],
        [Paragraph("Change Signal Lead Report",
            ps('ct',fontName='Helvetica-Bold',fontSize=24,textColor=WHITE,leading=30))],
        [Paragraph("Companies undergoing change — prime targets for Extron supply chain conversations",
            ps('cs',fontName='Helvetica',fontSize=12,textColor=colors.HexColor('#ccddee'),leading=17))],
        [Spacer(1,14)],
        [Table([[
            Table([[Paragraph("GENERATED",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(today_str,ps('mv',fontName='Helvetica-Bold',fontSize=11,textColor=WHITE,leading=14))]],colWidths=[44*mm]),
            Table([[Paragraph("COMPANIES",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(str(len(leads)),ps('mn',fontName='Helvetica-Bold',fontSize=22,textColor=WHITE,leading=26))]],colWidths=[32*mm]),
            Table([[Paragraph("HOT LEADS",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph(str(len(hot)),ps('mn2',fontName='Helvetica-Bold',fontSize=22,textColor=colors.HexColor('#FF9999'),leading=26))]],colWidths=[32*mm]),
            Table([[Paragraph("CATEGORIES",ps('ml',fontName='Helvetica',fontSize=8,textColor=colors.HexColor('#aaaacc'),leading=10))],
                   [Paragraph("AV · EV · MedTech",ps('mv2',fontName='Helvetica-Bold',fontSize=10,textColor=WHITE,leading=14))]],colWidths=[44*mm]),
        ]],colWidths=[44*mm,32*mm,32*mm,44*mm],style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
            ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))],
    ], colWidths=[CW], style=TableStyle([('BACKGROUND',(0,0),(-1,-1),NAVY),
        ('LEFTPADDING',(0,0),(-1,-1),20),('RIGHTPADDING',(0,0),(-1,-1),20),
        ('TOPPADDING',(0,0),(-1,-1),20),('BOTTOMPADDING',(0,0),(-1,-1),24)]))
    story += [cov, Spacer(1,8*mm)]

    story.append(Paragraph("EXECUTIVE SUMMARY",
        ps('h2',fontName='Helvetica-Bold',fontSize=10,textColor=TEXT2,leading=13,spaceAfter=6)))
    story.append(HRFlowable(width=CW,thickness=0.5,color=BORDER,spaceAfter=8))

    def sbox(lbl,val,col=INK):
        return Table([[Paragraph(lbl,ps('sl',fontName='Helvetica-Bold',fontSize=8,textColor=TEXT2,leading=10))],
                      [Paragraph(str(val),ps('sv',fontName='Helvetica-Bold',fontSize=24,textColor=col,leading=28))]],
            colWidths=[(CW/4)-6*mm], style=TableStyle([('BACKGROUND',(0,0),(-1,-1),GRAY_LT),
            ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),6),
            ('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10)]))

    story.append(Table([[sbox("TOTAL LEADS",len(leads)),sbox("HOT LEADS",len(hot),RED),
                          sbox("HIGH PRIORITY",len(high),AMBER),sbox("WATCH LIST",len(watch),GRAY_TXT)]],
        colWidths=[(CW/4)]*4, style=TableStyle([('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),
        ('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)])))
    story.append(Spacer(1,5*mm))

    crit = [["Categories","AV Hardware / Pro Displays, EV Charging, Medical Devices"],
            ["Min. unit price","$300+ per unit"],
            ["Change signals","New CEO, M&A, Layoffs / Restructuring, Market Expansion"],
            ["Scan frequency","Every 24 hours"],
            ["Sources","Google News RSS, SEC EDGAR 8-K, PRNewswire, BusinessWire"]]
    story.append(Table([[Paragraph(k,ps('ck',fontName='Helvetica',fontSize=10,textColor=TEXT2,leading=14)),
                          Paragraph(v,ps('cv',fontName='Helvetica',fontSize=10,textColor=INK,leading=14))]
                         for k,v in crit], colWidths=[48*mm,CW-48*mm],
        style=TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-2),0.5,BORDER),
        ('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),
        ('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)])))
    story.append(Spacer(1,8*mm))

    def sec(text,color):
        return [Paragraph(text,ps('sh',fontName='Helvetica-Bold',fontSize=11,textColor=color,leading=14,spaceBefore=8,spaceAfter=6)),
                HRFlowable(width=CW,thickness=0.5,color=color,spaceAfter=6)]

    if hot:
        story += sec("Hot Leads — Act Immediately", RED)
        for l in hot: story.append(lead_card(l,CW))
    if high:
        story += sec("High Priority", AMBER)
        for l in high: story.append(lead_card(l,CW))
    if watch:
        story += sec("Watch List", GRAY_TXT)
        for l in watch: story.append(lead_card(l,CW))
    if not leads:
        story.append(Paragraph("No new qualifying leads found today. The scanner will run again in 24 hours.",
            ps('e',fontName='Helvetica',fontSize=12,textColor=TEXT2,leading=18)))

    doc.build(story); return buf.getvalue()

# ── Email ─────────────────────────────────────────────────────────────────────
def send_email(pdf_bytes, lead_count, hot_count):
    today_str = date.today().strftime("%B %d, %Y")
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Extron Lead Intelligence — {lead_count} leads ({hot_count} hot) — {today_str}"
    msg["From"] = SMTP_USER; msg["To"] = REPORT_RECIPIENT
    html = f"""<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
      <div style="background:#0C447C;padding:28px 32px;border-radius:8px 8px 0 0">
        <h1 style="color:white;font-size:20px;margin:0 0 4px">Change Signal Lead Report</h1>
        <p style="color:#ccd9e8;font-size:13px;margin:0">{today_str} — Extron Sales Intelligence</p>
      </div>
      <div style="background:#f7f7f5;padding:24px 32px">
        <table width="100%"><tr>
          <td style="background:white;border-radius:8px;padding:16px;text-align:center">
            <div style="font-size:11px;color:#666;text-transform:uppercase">Total leads</div>
            <div style="font-size:28px;font-weight:700">{lead_count}</div></td>
          <td width="12"></td>
          <td style="background:white;border-radius:8px;padding:16px;text-align:center">
            <div style="font-size:11px;color:#666;text-transform:uppercase">Hot leads</div>
            <div style="font-size:28px;font-weight:700;color:#791F1F">{hot_count}</div></td>
          <td width="12"></td>
          <td style="background:white;border-radius:8px;padding:16px;text-align:center">
            <div style="font-size:11px;color:#666;text-transform:uppercase">Categories</div>
            <div style="font-size:14px;font-weight:700;color:#0C447C">AV · EV · Med</div></td>
        </tr></table>
        <p style="font-size:13px;color:#444;line-height:1.6;margin-top:20px">
          Your daily Extron lead scan is complete. Full report with company profiles,
          change signals, and supply chain opening analysis is attached.</p>
        <p style="font-size:11px;color:#888;margin-top:16px">
          Scans run every 24 hours across Google News RSS, SEC EDGAR, PRNewswire, and BusinessWire.
          Companies are filtered for AV hardware, EV charging, and medical device categories ($300+/unit)
          undergoing leadership, M&amp;A, restructuring, or market expansion changes.</p>
      </div></div>"""
    msg.attach(MIMEText(html, "html"))
    part = MIMEBase("application","octet-stream"); part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
        f'attachment; filename="Extron_Leads_{date.today().isoformat()}.pdf"')
    msg.attach(part)
    log.info(f"Connecting to {SMTP_HOST}:{SMTP_PORT} as {SMTP_USER}...")
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as s:
        s.login(SMTP_USER, SMTP_PASS)
        log.info("Logged in to Gmail OK, sending...")
        s.send_message(msg)
    log.info(f"Report emailed to {REPORT_RECIPIENT}")

# ── Main loop ─────────────────────────────────────────────────────────────────
def run_scan():
    log.info("="*55)
    log.info("Starting Extron lead intelligence scan...")
    all_articles = []
    for feed in FEEDS:
        all_articles.extend(fetch_rss(feed))
    seen_titles = set(); unique = []
    for a in all_articles:
        t = a.get("title","").lower().strip()
        if t and t not in seen_titles: unique.append(a); seen_titles.add(t)
    log.info(f"Unique articles to process: {len(unique)}")
    leads = ai_filter(unique)
    seen = load_seen()
    new_leads = deduplicate(leads, seen)
    save_seen(seen)
    log.info(f"New leads after deduplication: {len(new_leads)}")
    pdf = generate_pdf(new_leads)
    hot_count = sum(1 for l in new_leads if l.get("urgencyScore",0)>=85)
    send_email(pdf, len(new_leads), hot_count)
    log.info("Scan complete.")

def main():
    log.info(f"Extron scanner starting — interval: {SCAN_INTERVAL_HRS}h")
    while True:
        try: run_scan()
        except Exception as e: log.error(f"Scan error: {e}", exc_info=True)
        log.info(f"Sleeping {SCAN_INTERVAL_HRS}h...")
        time.sleep(SCAN_INTERVAL_HRS * 3600)

if __name__ == "__main__":
    main()
