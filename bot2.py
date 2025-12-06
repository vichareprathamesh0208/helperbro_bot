# ================================
#   PERFECT CODE + DAILY ALERTS
#   helperbro_ipo_bot.py
# ================================

import os
import re
import asyncio
import aiohttp
import datetime
import pytz
from bs4 import BeautifulSoup
from datetime import datetime as dt

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# ------------------------------------------------
# Environment
# ------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not ADMIN_CHAT_ID:
    raise RuntimeError("ADMIN_CHAT_ID missing")
ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

# ------------------------------------------------
# URLs
# ------------------------------------------------
IPOWATCH_GMP_URL = "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"
IPOWATCH_SUB_URL = "https://ipowatch.in/ipo-subscription-status-today/"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Batching config
BATCH_SIZE = 6
BATCH_DELAY = 1.0

# ------------------------------------------------
# Helpers
# ------------------------------------------------
def clean(t):
    return re.sub(r"\s+", " ", t).strip() if t else ""

def normalize_money(s):
    if not s or s in ("-", "—", "₹-", "₹ –", ""):
        return "N/A"
    return s.strip()

# ------------------------------------------------
# DATE PARSER for IPO dates (8-10 Dec, 28-2 Dec)
# ------------------------------------------------
MONTH_MAP = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12
}

def parse_start_close(datestr):
    """
    Handles:
        "8-10 Dec"
        "28-2 Dec"
    """
    if not datestr:
        return None, None

    ds = datestr.lower().replace("to", "-")
    ds = re.sub(r"\s+", "", ds)
    parts = ds.split("-")
    if len(parts) != 2:
        return None, None

    left, right = parts

    # detect month
    m = re.search(r"([a-z]{3})", ds)
    if not m:
        return None, None
    month = MONTH_MAP.get(m.group(1))
    if not month:
        return None, None

    # extract days
    def getday(x):
        m = re.search(r"(\d{1,2})", x)
        return int(m.group(1)) if m else None

    sd = getday(left)
    cd = getday(right)
    if not sd or not cd:
        return None, None

    # year logic
    year = dt.now().year
    # if month is Jan but current month is Dec → next year
    if month == 1 and dt.now().month == 12:
        year += 1

    try:
        start = dt(year, month, sd)
        close = dt(year, month, cd)
        return start, close
    except:
        return None, None


# ------------------------------------------------
# FETCH HTML
# ------------------------------------------------
async def fetch_html(session, url):
    try:
        async with session.get(url, headers=HEADERS, timeout=20) as r:
            if r.status == 200:
                return await r.text()
    except Exception as e:
        print("Fetch error:", e)
    return None

# ------------------------------------------------
# HEADER MAPPING
# ------------------------------------------------
def build_header_map(headers):
    m = {}
    for i, h in enumerate(headers):
        H = h.lower()
        if "ipo" in H or "company" in H:
            m["name"] = i
        elif "type" in H:
            m["type"] = i
        elif "date" in H:
            m["date"] = i
        elif "price" in H:
            m["price"] = i
        elif "gmp" in H or "grey" in H:
            m["gmp"] = i
        elif "size" in H:
            m["size"] = i
        elif "lot" in H:
            m["lot"] = i
        elif "listing" in H:
            m["listing"] = i
    return m

# ------------------------------------------------
# GMP Strength Detector
# ------------------------------------------------
def classify_gmp_strength(gmp):
    """
    Strong = GMP rising or > 50
    Stable = GMP between 5 and 50
    Weak   = Everything else
    """
    if not gmp or gmp == "N/A":
        return "weak"

    # extract numeric
    m = re.search(r"(\d+)", gmp)
    if not m:
        return "weak"

    val = int(m.group(1))

    if val >= 50:
        return "strong"
    if 5 <= val < 50:
        return "stable"
    return "weak"

# ------------------------------------------------
# PARSE IPO TABLES (GMP TABLE)
# ------------------------------------------------
def parse_gmp_page(html):
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    results = []

    for tbl in tables:
        headers = [clean(th.get_text()) for th in tbl.find_all("th")]
        if not headers:
            continue

        hmap = build_header_map(headers)
        if "name" not in hmap:
            continue

        for r in tbl.find_all("tr")[1:]:
            cols = [clean(td.get_text(" ", strip=True)) for td in r.find_all("td")]
            if not cols:
                continue

            def get(k):
                if k in hmap and hmap[k] < len(cols):
                    return cols[hmap[k]]
                return "N/A"

            record = {
                "name": get("name"),
                "type": get("type"),
                "date_raw": get("date"),
                "gmp": normalize_money(get("gmp")),
                "price": normalize_money(get("price")),
                "lot": get("lot"),
                "size": get("size"),
                "listing": get("listing"),
            }

            s, c = parse_start_close(record["date_raw"])
            record["start"] = s
            record["close"] = c

            results.append(record)

    # dedupe
    final = []
    seen = set()
    for r in results:
        k = r["name"].lower()
        if k not in seen:
            seen.add(k)
            final.append(r)
    return final

# ------------------------------------------------
# PARSE SUBSCRIPTION PAGE (TOTAL ONLY)
# ------------------------------------------------
def parse_subscription_page(html):
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    out = []

    for tbl in tables:
        headers = [clean(h.get_text()) for h in tbl.find_all("th")]
        if not headers:
            continue

        idx_name = idx_date = idx_total = None
        for i, h in enumerate(headers):
            H = h.lower()
            if "ipo" in H or "company" in H:
                idx_name = i
            elif "date" in H:
                idx_date = i
            elif "total" in H:
                idx_total = i

        if idx_name is None or idx_total is None:
            continue

        for r in tbl.find_all("tr")[1:]:
            tds = r.find_all("td")
            if len(tds) < 2:
                continue

            name = clean(tds[idx_name].get_text())
            total = clean(tds[idx_total].get_text())
            date_str = clean(tds[idx_date].get_text()) if idx_date is not None else ""

            # only accept DD MMM or DD MMM YYYY
            if not re.match(r"^\d{1,2}\s+[A-Za-z]{3}(?:\s+\d{4})?$", date_str):
                continue

            out.append({
                "name": name,
                "total": total,
                "date": date_str,
            })

    final = []
    seen = set()
    for x in out:
        k = x["name"].lower()
        if k not in seen:
            seen.add(k)
            final.append(x)
    return final

# ------------------------------------------------
# FETCH ALL IPO + SUBSCRIPTION
# ------------------------------------------------
async def fetch_ipos():
    async with aiohttp.ClientSession() as s:
        h = await fetch_html(s, IPOWATCH_GMP_URL)
        if not h:
            return []
        return parse_gmp_page(h)

async def fetch_subs():
    async with aiohttp.ClientSession() as s:
        h = await fetch_html(s, IPOWATCH_SUB_URL)
        if not h:
            return []
        return parse_subscription_page(h)

# ------------------------------------------------
# BATCH SENDER
# ------------------------------------------------
async def send_batches(update, context, recs):
    if not recs:
        await update.message.reply_text("No IPOs matched.")
        return

    for i in range(0, len(recs), BATCH_SIZE):
        batch = recs[i:i+BATCH_SIZE]
        tasks = []
        for r in batch:

            gmp_icon = "🟢" if r["gmp_strength"] == "strong" else "🟡" if r["gmp_strength"]=="stable" else "🔴"

            msg = (
                f"📌 <b>{r['name']}</b> ({r['type']})\n"
                f"{gmp_icon} GMP: <code>{r['gmp']}</code>\n"
                f"🏦 Price: <code>{r['price']}</code>\n"
                f"📦 Lot Size: <code>{r['lot']}</code>\n"
                f"💼 Issue Size: <code>{r['size']}</code>\n"
                f"📆 {r['start_str']} → {r['close_str']}\n"
                f"🧮 Subscription: <b>{r['sub_total']}</b> (as of {r['sub_date']})\n"
                f"📅 Listing: <code>{r['listing']}</code>"
            )

            tasks.append(
                context.bot.send_message(
                    update.effective_chat.id,
                    msg,
                    parse_mode="HTML"
                )
            )
        await asyncio.gather(*tasks)
        await asyncio.sleep(BATCH_DELAY)

# ------------------------------------------------
# /ipos COMMAND
# ------------------------------------------------
async def cmd_ipos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wait = await update.message.reply_text("⏳ Fetching IPO data...")

    ipos = await fetch_ipos()
    subs = await fetch_subs()
    sub_map = {x["name"].lower(): x for x in subs}

    today = dt.now()
    final = []

    for r in ipos:
        if not r["start"] or not r["close"]:
            continue

        if today <= r["close"]:  # active or upcoming
            key = r["name"].lower()
            S = sub_map.get(key)

            r["start_str"] = r["start"].strftime("%d %b")
            r["close_str"] = r["close"].strftime("%d %b")
            r["sub_total"] = S["total"] if S else "N/A"
            r["sub_date"] = S["date"] if S else "N/A"
            r["gmp_strength"] = classify_gmp_strength(r["gmp"])

            final.append(r)

    final.sort(key=lambda x: x["start"])

    await wait.edit_text(f"Found {len(final)} IPOs.")
    await send_batches(update, context, final)

# ------------------------------------------------
# DAILY JOB — 9:30 AM IST
# ------------------------------------------------
async def daily_gmp_alert(context: ContextTypes.DEFAULT_TYPE):
    """Send daily alert listing strong/stable GMP IPOs."""
    try:
        ipos = await fetch_ipos()
        alerts = []

        for r in ipos:
            strength = classify_gmp_strength(r["gmp"])
            if strength in ("strong", "stable"):
                icon = "🟢" if strength == "strong" else "🟡"
                alerts.append(f"{icon} <b>{r['name']}</b> — GMP {r['gmp']}")

        if not alerts:
            return

        msg = "📢 <b>Daily IPO GMP Alert (Strong/Stable)</b>\n\n"
        msg += "\n".join(alerts)

        await context.bot.send_message(
            ADMIN_CHAT_ID,
            msg,
            parse_mode="HTML"
        )
    except Exception as e:
        print("Daily Alert Error:", e)


# ------------------------------------------------
# /start
# ------------------------------------------------
async def cmd_start(update: Update, context):
    await update.message.reply_text(
        "👋 IPO HelperBro is LIVE!\nUse /ipos anytime."
    )

# ------------------------------------------------
# MAIN (WITH DAILY JOB)
# ------------------------------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ipos", cmd_ipos))

    # DAILY 9:30 IST ALERT
    ist = pytz.timezone("Asia/Kolkata")
    app.job_queue.run_daily(
        daily_gmp_alert,
        time=datetime.time(hour=9, minute=30, tzinfo=ist)
    )

    print("BOT RUNNING 24/7...")
    app.run_polling()

if __name__ == "__main__":
    main()
