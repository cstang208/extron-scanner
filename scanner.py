"""
Extron Lead Intelligence Scanner
Scans news sources daily for companies matching Extron's customer profile
that are undergoing organizational change. Emails a PDF report every day.
"""

import os, re, json, time, logging, urllib.request, base64
from datetime import date
from io import BytesIO

import anthropic
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_RIGHT

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# ── These come from Railway environment variables ─────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
RESEND_API_KEY    = os.environ["RESEND_API_KEY"]
REPORT_EMAIL      = os.environ["REPORT_EMAIL"]

# ── RSS feeds to scan ─────────────────────────────────────────────────────────
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

# ── Colors for PDF ────────────────────────────────────────────────────────────
NAVY     = colors.HexColor('#0C447C')
WHITE    = colors.white
AMBER    = colors.HexColor('#633806')
AMBER_BG = colors.HexColor('#FAEEDA')
AMBER_MID= colors.HexColor('#BA7517')
RED      = colors.HexColor('#791F1F')
RED_BG   = colors.HexColor('#FCEBEB')
RED_MID  = colors.HexColor('#E24B4A')
GREEN    = colors.HexColor('#27500A')
GREEN_BG = colors.HexColor('#EAF3DE')
GRAY_BG  = colors.HexColor('#F1EFE8')
GRAY_TXT = colors.HexColor('#444441')
GRAY_LT  = colors.HexColor('#F7F7F5')
BORDER   = colors.HexColor('#E0E0E0')
TEXT2    = colors.HexColor('#666666')
INK      = colors.HexColor('#111111')

def ps(name, **kw):
    return ParagraphStyle(name, **kw)

# ── Fetch RSS feeds ───────────────────────────────────────────────────────────
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
            items.append({"title": tag("title"), "link": tag("link"), "summary": tag("description")})
        log.info(f"  {len(items)} items from {url[:60]}")
        return items
    except Exception as e:
        log.warning(f"Feed failed {url[:60]}: {e}")
        return []

# ── Filter articles with Claude AI ───────────────────────────────────────────
def ai_filter(articles):
    if not articles:
        return []
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    text = "\n".join(
        f"[{i+1}] TITLE: {a['title']}\n    SUMMARY: {a['summary'][:250]}\n    URL: {a['link']}"
        for i, a in enumerate(articles[:40])
    )
    prompt = f"""You are a B2B sales intelligence analyst for Extron Inc (professional AV hardware manufacturer).

Extron's ideal customers sell physical hardware ($300+/unit) in:
- Professional AV / displays / signal processors / control systems
- EV charging infrastructure hardware
- Medical devices and diagnostics equipment

AND are currently experiencing ONE OR MORE of:
- New CEO or leadership change
- Merger or acquisition
- Layoffs or restructuring
- Expansion to new markets

Review these articles and return ONLY companies that clearly match ALL criteria.
Return a raw JSON array. No markdown. No backticks. Start with [ end with ]. If none qualify return [].

Each object must have these exact fields:
- name: company name
- category: one of "AV Hardware", "EV Charging", "Medical Devices"
- hq: city, state
- founded: year as number or null
- ticker: stock ticker or null
- unitPrice: estimated price per unit e.g. "$500-$2,000/unit"
- signalType: e.g. "New CEO", "M&A / Acquisition", "Layoffs / Restructuring", "Market Expansion"
- signalDate: e.g. "May 2025"
- signalDetail: 2 factual sentences about what happened
- whyNow: 1 sentence on the supply chain opening this creates for Extron
- source: source name
- sourceUrl: URL
- urgencyScore: 0-100
- confidence: 0-100

Only include companies with confidence >= 70.

Articles to analyze:
{text}"""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            system="You are a JSON-only API. Return ONLY a raw JSON array. No markdown. No backticks. Start with [ and end with ].",
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.replace("```json", "").replace("```", "").strip()
        leads = json.loads(raw[raw.index("["):raw.rindex("]") + 1])
        log.info(f"AI returned {len(leads)} qualifying leads")
        return leads
    except Exception as e:
        log.error(f"AI filter error: {e}")
        return []

# ── Remove companies already reported ────────────────────────────────────────
SEEN_FILE = "/tmp/seen_leads.json"

def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def deduplicate(leads, seen):
    fresh = []
    for l in leads:
        key = l.get("name", "").lower().strip()
        if key and key not in seen:
            fresh.append(l)
            seen.add(key)
    return fresh

# ── Build PDF report ──────────────────────────────────────────────────────────
def generate_pdf(leads):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
    W, H = A4
    CW = W - 36*mm
    today_str = date.today().strftime("%B %d, %Y")
    hot   = [l for l in leads if l.get("urgencyScore", 0) >= 85]
    high  = [l for l in leads if 70 <= l.get("urgencyScore", 0) < 85]
    watch = [l for l in leads if l.get("urgencyScore", 0) < 70]
    story = []

    # Cover block
    cover = Table([
        [Paragraph("CONFIDENTIAL — EXTRON SALES INTELLIGENCE",
            ps('cl', fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#aaaacc'), leading=11))],
        [Spacer(1, 6)],
        [Paragraph("Change Signal Lead Report",
            ps('ct', fontName='Helvetica-Bold', fontSize=24, textColor=WHITE, leading=30))],
        [Paragraph("Companies undergoing organizational change — prime targets for Extron supply chain conversations",
            ps('cs', fontName='Helvetica', fontSize=12, textColor=colors.HexColor('#ccddee'), leading=17))],
        [Spacer(1, 14)],
        [Table([[
            Table([[Paragraph("GENERATED", ps('ml', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#aaaacc'), leading=10))],
                   [Paragraph(today_str, ps('mv', fontName='Helvetica-Bold', fontSize=11, textColor=WHITE, leading=14))]], colWidths=[44*mm]),
            Table([[Paragraph("COMPANIES", ps('ml', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#aaaacc'), leading=10))],
                   [Paragraph(str(len(leads)), ps('mn', fontName='Helvetica-Bold', fontSize=22, textColor=WHITE, leading=26))]], colWidths=[32*mm]),
            Table([[Paragraph("HOT LEADS", ps('ml', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#aaaacc'), leading=10))],
                   [Paragraph(str(len(hot)), ps('mn2', fontName='Helvetica-Bold', fontSize=22, textColor=colors.HexColor('#FF9999'), leading=26))]], colWidths=[32*mm]),
            Table([[Paragraph("CATEGORIES", ps('ml', fontName='Helvetica', fontSize=8, textColor=colors.HexColor('#aaaacc'), leading=10))],
                   [Paragraph("AV · EV · MedTech", ps('mv2', fontName='Helvetica-Bold', fontSize=10, textColor=WHITE, leading=14))]], colWidths=[44*mm]),
        ]], colWidths=[44*mm, 32*mm, 32*mm, 44*mm], style=TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 0), ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0), ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ]))],
    ], colWidths=[CW], style=TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY),
        ('LEFTPADDING', (0,0), (-1,-1), 20), ('RIGHTPADDING', (0,0), (-1,-1), 20),
        ('TOPPADDING', (0,0), (-1,-1), 20), ('BOTTOMPADDING', (0,0), (-1,-1), 24),
    ]))
    story += [cover, Spacer(1, 8*mm)]

    # Summary header
    story.append(Paragraph("EXECUTIVE SUMMARY",
        ps('h2', fontName='Helvetica-Bold', fontSize=10, textColor=TEXT2, leading=13, spaceAfter=6)))
    story.append(HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=8))

    def sbox(lbl, val, col=INK):
        return Table([
            [Paragraph(lbl, ps('sl', fontName='Helvetica-Bold', fontSize=8, textColor=TEXT2, leading=10))],
            [Paragraph(str(val), ps('sv', fontName='Helvetica-Bold', fontSize=24, textColor=col, leading=28))],
        ], colWidths=[(CW/4) - 6*mm], style=TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), GRAY_LT),
            ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 10), ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ]))

    story.append(Table([[
        sbox("TOTAL LEADS", len(leads)),
        sbox("HOT LEADS", len(hot), RED),
        sbox("HIGH PRIORITY", len(high), AMBER),
        sbox("WATCH LIST", len(watch), GRAY_TXT),
    ]], colWidths=[(CW/4)] * 4, style=TableStyle([
        ('LEFTPADDING', (0,0), (-1,-1), 3), ('RIGHTPADDING', (0,0), (-1,-1), 3),
        ('TOPPADDING', (0,0), (-1,-1), 0), ('BOTTOMPADDING', (0,0), (-1,-1), 0),
    ])))
    story.append(Spacer(1, 8*mm))

    # Lead cards
    def urg_meta(u):
        if u >= 85: return "HOT LEAD", RED_BG, RED, RED_MID, 3.5
        if u >= 70: return "HIGH PRIORITY", AMBER_BG, AMBER, AMBER_MID, 1.0
        return "WATCH LIST", GRAY_BG, GRAY_TXT, BORDER, 0.5

    def cat_col(cat):
        c = (cat or "").lower()
        if "av" in c or "audio" in c: return colors.HexColor('#EAF0FB'), colors.HexColor('#1A3A6B')
        if "ev" in c or "charg" in c: return GREEN_BG, GREEN
        return colors.HexColor('#FDE8EC'), colors.HexColor('#7A1530')

    def badge(text, bg, fg, w):
        return Table([[Paragraph(text, ps('b', fontName='Helvetica-Bold', fontSize=8, textColor=fg, leading=10))]],
            colWidths=[w], style=TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), bg),
                ('LEFTPADDING', (0,0), (-1,-1), 7), ('RIGHTPADDING', (0,0), (-1,-1), 7),
                ('TOPPADDING', (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ]))

    def make_card(l):
        u = l.get("urgencyScore", 60)
        ulbl, ubg, ufc, ulc, ulw = urg_meta(u)
        cbg, cfg = cat_col(l.get("category", ""))

        hdr = Table([[
            Table([
                [Paragraph(l.get("name", ""), ps('cn', fontName='Helvetica-Bold', fontSize=14, textColor=INK, leading=18))],
                [Paragraph(f"{l.get('hq', '')}  ·  Est. {l.get('founded', '—')}  ·  {l.get('ticker') or 'Private'}",
                    ps('cs2', fontName='Helvetica', fontSize=10, textColor=TEXT2, leading=13))],
            ], colWidths=[CW - 76*mm]),
            Table([[badge(l.get("category", "").upper(), cbg, cfg, 32*mm),
                    badge(ulbl, ubg, ufc, 28*mm)]],
                colWidths=[34*mm, 30*mm], style=TableStyle([
                    ('LEFTPADDING', (0,0), (-1,-1), 3), ('RIGHTPADDING', (0,0), (-1,-1), 0),
                    ('TOPPADDING', (0,0), (-1,-1), 0), ('BOTTOMPADDING', (0,0), (-1,-1), 0),
                ])),
        ]], colWidths=[CW - 64*mm, 64*mm], style=TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 0), ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0), ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ]))

        sig = Table([
            [Paragraph(f"{l.get('signalType', '').upper()}  ·  {l.get('signalDate', '')}",
                ps('sigl', fontName='Helvetica-Bold', fontSize=8, textColor=TEXT2, leading=10))],
            [Paragraph(l.get("signalDetail", ""), ps('sigd', fontName='Helvetica', fontSize=10, textColor=INK, leading=14))],
        ], colWidths=[CW - 10*mm], style=TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), GRAY_LT),
            ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('TOPPADDING', (0,0), (0,0), 8), ('BOTTOMPADDING', (0,0), (0,0), 3),
            ('TOPPADDING', (0,1), (-1,-1), 0), ('BOTTOMPADDING', (0,1), (-1,-1), 8),
        ]))

        why = Table([[
            Paragraph("Why contact now: ", ps('wb', fontName='Helvetica-Bold', fontSize=10, textColor=AMBER, leading=14)),
            Paragraph(l.get("whyNow", ""), ps('wt', fontName='Helvetica', fontSize=10, textColor=AMBER, leading=14)),
        ]], colWidths=[32*mm, CW - 44*mm], style=TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), AMBER_BG),
            ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 7),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ]))

        foot = Table([[
            Paragraph(f"{l.get('unitPrice', '$300+/unit')}   |   {l.get('source', '')}",
                ps('fp', fontName='Helvetica', fontSize=9, textColor=TEXT2, leading=12)),
            Paragraph(f"Urgency: {u}%",
                ps('fu', fontName='Helvetica-Bold', fontSize=9, textColor=ufc, leading=12, alignment=TA_RIGHT)),
        ]], colWidths=[CW * 0.65, CW * 0.35], style=TableStyle([
            ('LINEABOVE', (0,0), (-1,-1), 0.5, BORDER),
            ('TOPPADDING', (0,0), (-1,-1), 7), ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ('LEFTPADDING', (0,0), (-1,-1), 0), ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ]))

        inner = Table([[hdr], [sig], [Spacer(1, 4)], [why], [Spacer(1, 4)], [foot]],
            colWidths=[CW - 10*mm], style=TableStyle([
                ('LEFTPADDING', (0,0), (-1,-1), 0), ('RIGHTPADDING', (0,0), (-1,-1), 0),
                ('TOPPADDING', (0,0), (-1,-1), 0), ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ]))

        outer = Table([[inner]], colWidths=[CW], style=TableStyle([
            ('BOX', (0,0), (-1,-1), 0.75, ulc),
            ('LINEBEFORE', (0,0), (0,-1), ulw, ulc),
            ('BACKGROUND', (0,0), (-1,-1), WHITE),
            ('LEFTPADDING', (0,0), (-1,-1), 12), ('RIGHTPADDING', (0,0), (-1,-1), 12),
            ('TOPPADDING', (0,0), (-1,-1), 12), ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ]))
        return KeepTogether([outer, Spacer(1, 4*mm)])

    def section(text, color):
        return [
            Paragraph(text, ps('sh', fontName='Helvetica-Bold', fontSize=11, textColor=color, leading=14, spaceBefore=8, spaceAfter=6)),
            HRFlowable(width=CW, thickness=0.5, color=color, spaceAfter=6),
        ]

    if hot:
        story += section("Hot Leads — Act Immediately", RED)
        for l in hot: story.append(make_card(l))
    if high:
        story += section("High Priority", AMBER)
        for l in high: story.append(make_card(l))
    if watch:
        story += section("Watch List", GRAY_TXT)
        for l in watch: story.append(make_card(l))
    if not leads:
        story.append(Spacer(1, 10*mm))
        story.append(Paragraph("No new qualifying leads found today. The scanner will run again tomorrow.",
            ps('e', fontName='Helvetica', fontSize=12, textColor=TEXT2, leading=18)))

    doc.build(story)
    return buf.getvalue()

# ── Send email via Resend ─────────────────────────────────────────────────────
def send_email(pdf_bytes, lead_count, hot_count):
    today_str = date.today().strftime("%B %d, %Y")
    subject = f"Extron Lead Intelligence — {today_str} — {lead_count} leads, {hot_count} hot"

    rows = ""
    html = f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:620px;margin:0 auto;background:#f5f5f3;padding:20px">
  <div style="background:#0C447C;border-radius:10px 10px 0 0;padding:28px 32px">
    <h1 style="color:white;font-size:20px;margin:0 0 4px;font-weight:600">Extron Change Signal Lead Report</h1>
    <p style="color:#ccd9e8;font-size:13px;margin:0">{today_str} · Daily Intelligence Scan</p>
  </div>
  <div style="background:white;border-radius:0 0 10px 10px;padding:24px 32px;border:1px solid #e0e0e0;border-top:none">
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:20px">
      <tr>
        <td style="background:#f7f7f5;border-radius:8px;padding:14px;text-align:center;width:33%">
          <div style="font-size:10px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">Total leads</div>
          <div style="font-size:28px;font-weight:700;color:#111">{lead_count}</div>
        </td>
        <td width="12"></td>
        <td style="background:#f7f7f5;border-radius:8px;padding:14px;text-align:center;width:33%">
          <div style="font-size:10px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">Hot leads</div>
          <div style="font-size:28px;font-weight:700;color:#791F1F">{hot_count}</div>
        </td>
        <td width="12"></td>
        <td style="background:#f7f7f5;border-radius:8px;padding:14px;text-align:center;width:33%">
          <div style="font-size:10px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">Categories</div>
          <div style="font-size:14px;font-weight:700;color:#0C447C">AV · EV · Med</div>
        </td>
      </tr>
    </table>
    <p style="font-size:13px;color:#444;line-height:1.6;margin:0 0 8px">
      Your daily Extron lead scan is complete. The full report with company profiles,
      change signals, and supply chain opportunity analysis is attached as a PDF.</p>
    <p style="font-size:11px;color:#999;margin:0">
      Scans run every 24 hours · Sources: Google News, PRNewswire, BusinessWire, SEC EDGAR</p>
  </div>
</div>"""

    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    filename = f"Extron_Leads_{date.today().isoformat()}.pdf"

    payload = json.dumps({
        "from": "Extron Scanner <onboarding@resend.dev>",
        "to": [REPORT_EMAIL],
        "subject": subject,
        "html": html,
        "attachments": [{"filename": filename, "content": pdf_b64}]
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        }
    )
    try:

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            log.info(f"Email sent — Resend ID: {result.get('id', '?')}")
    except urllib.error.HTTPError as e:
        log.error(f"Email failed {e.code}: {e.read().decode()}")
    except Exception as e:
        log.error(f"Email failed: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    log.info("=" * 55)
    log.info("Starting Extron lead intelligence scan...")

    all_articles = []
    for feed in FEEDS:
        all_articles.extend(fetch_rss(feed))

    # Deduplicate by title
    seen_titles, unique = set(), []
    for a in all_articles:
        t = a.get("title", "").lower().strip()
        if t and t not in seen_titles:
            unique.append(a)
            seen_titles.add(t)
    log.info(f"Unique articles to analyze: {len(unique)}")

    leads = ai_filter(unique)

    seen = load_seen()
    fresh = deduplicate(leads, seen)
    save_seen(seen)
    log.info(f"Fresh leads after deduplication: {len(fresh)}")

    pdf = generate_pdf(fresh)
    hot = sum(1 for l in fresh if l.get("urgencyScore", 0) >= 85)
    send_email(pdf, len(fresh), hot)
    log.info("Scan complete.")

if __name__ == "__main__":
    log.info("Extron Scanner starting...")
    while True:
        try:
            run()
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)
        log.info("Sleeping 24 hours...")
        time.sleep(86400)

