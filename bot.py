# helperbro_ipowatch_final_x.py
# GREAT CODE + X features:
# - Upcoming / Currently Open segments
# - Mainboard / SME grouping within each segment
# - GMP trend icons (numeric thresholds)
# - Auto-detect Issue Size, Lot Size, Listing Date columns (B1)
# - All previous behavior preserved
import os
import asyncio
import re
from datetime import datetime
import aiohttp
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ============================================================
#  ENV-BASED TOKEN LOADING (REQUIRED FOR RENDER)
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        "\nERROR: BOT_TOKEN environment variable is not set.\n"
        "Set BOT_TOKEN in Render → Environment Variables.\n"
    )
IPOWATCH_GMP_URL = "https://ipowatch.in/ipo-grey-market-premium-latest-ipo-gmp/"
IPOWATCH_SUB_URL = "https://ipowatch.in/ipo-subscription-status-today/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
BATCH_SIZE = 6
BATCH_DELAY = 1.0

# -----------------------
# Helpers
# -----------------------
def clean(text: str) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip()

def normalize_money(s: str) -> str:
    if not s:
        return "N/A"
    s = s.strip()
    if s in ("-", "—", "₹-", "₹ –", ""):
        return "N/A"
    return s

def normalize_name_for_match(name: str) -> str:
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"\b(ltd|limited|pvt|private|inc|plc|co|company|corporation)\b", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

# -----------------------
# GMP trend icon (thresholds)
# -----------------------
def gmp_trend_icon(gmp_str: str) -> str:
    """
    Determine trend by numeric value if possible.
    Thresholds:
      - Strong: >= 50 -> 🔺
      - Stable: 1..49 -> ➡️
      - Weak: <= 0 or non-numeric -> 🔻
    """
    if not gmp_str or gmp_str in ("N/A", "-", "—"):
        return "🔻"
    # remove non-digit except . and - and + and commas
    s = gmp_str.replace(",", "").replace("₹", "").strip()
    # handle formats like "+42", "42+", "42 (est)", "—"
    m = re.search(r"([+-]?\d+(\.\d+)?)", s)
    if not m:
        # if contains '+' symbol but no number, treat as stable
        if "+" in s:
            return "➡️"
        return "🔻"
    try:
        val = float(m.group(1))
    except:
        return "🔻"
    if val >= 50:
        return "🔺"
    if val >= 1:
        return "➡️"
    return "🔻"

# -----------------------
# Date parsing (unchanged)
# -----------------------
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}

def parse_ipo_dates(date_str: str):
    if not date_str:
        return None, None
    s = date_str.strip()
    if s.lower() in ("n/a", "-", "—"):
        return None, None

    s = s.replace("–", "-").replace("—", "-").replace("to", "-")
    s = re.sub(r"\s*-\s*", "-", s)

    pattern = re.compile(r"^\s*(\d{1,2})(?:\s*([A-Za-z]{3,9}))?\s*-\s*(\d{1,2})(?:\s*([A-Za-z]{3,9}))?(?:\s*(\d{4}))?\s*$", re.IGNORECASE)
    m = pattern.match(s)
    if not m:
        matches = re.findall(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", date_str)
        if matches:
            try:
                open_date = datetime.strptime(matches[0].replace("/", "-"), "%d-%m-%Y")
                close_date = datetime.strptime(matches[1].replace("/", "-"), "%d-%m-%Y") if len(matches) > 1 else open_date
                return open_date, close_date
            except Exception:
                return None, None
        return None, None

    left_day_s, left_mon_s, right_day_s, right_mon_s, explicit_year = m.groups()
    left_day = int(left_day_s)
    right_day = int(right_day_s)
    left_mon = left_mon_s.lower()[:3] if left_mon_s else ""
    right_mon = right_mon_s.lower()[:3] if right_mon_s else ""

    now = datetime.now()
    year = int(explicit_year) if explicit_year else now.year

    left_mon_num = MONTHS.get(left_mon) if left_mon else None
    right_mon_num = MONTHS.get(right_mon) if right_mon else None

    if left_mon_num is None and right_mon_num is not None:
        if left_day > right_day:
            inferred = right_mon_num - 1
            inferred_year = year
            if inferred == 0:
                inferred = 12
                inferred_year -= 1
            left_mon_num = inferred
            left_year = inferred_year
        else:
            left_mon_num = right_mon_num
            left_year = year
        right_year = year
    elif right_mon_num is None and left_mon_num is not None:
        left_year = year
        right_year = year
        right_mon_num = left_mon_num
        if right_day < left_day:
            rn = right_mon_num + 1
            ry = year
            if rn == 13:
                rn = 1
                ry += 1
            right_mon_num = rn
            right_year = ry
    elif left_mon_num is not None and right_mon_num is not None:
        left_year = year
        right_year = year
        if right_mon_num < left_mon_num:
            right_year += 1
    else:
        month_num = now.month
        left_mon_num = month_num
        right_mon_num = month_num
        left_year = year
        right_year = year
        if left_day > right_day:
            lm = month_num - 1
            ly = year
            if lm == 0:
                lm = 12
                ly -= 1
            left_mon_num = lm
            left_year = ly

    try:
        start_date = datetime(left_year, left_mon_num, left_day)
        close_date = datetime(right_year, right_mon_num, right_day)
    except Exception:
        return None, None

    return start_date, close_date

# -----------------------
# Fetch HTML
# -----------------------
async def fetch_html(session: aiohttp.ClientSession, url: str, timeout: int = 20) -> str | None:
    try:
        async with session.get(url, headers=HEADERS, timeout=timeout) as resp:
            if resp.status == 200:
                return await resp.text()
            else:
                print(f"[fetch_html] HTTP {resp.status} for {url}")
    except Exception as e:
        print(f"[fetch_html] Exception fetching {url}: {e}")
    return None

# -----------------------
# Parse GMP table with auto-detect for Issue/Lot/Listing columns
# -----------------------
def build_header_map(th_texts):
    mapping = {}
    for i, th in enumerate(th_texts):
        t = th.lower()
        if "stock" in t or ("ipo" in t and ("stock" in t or "company" in t)):
            mapping["name"] = i
        elif re.search(r"\bgmp\b", t) or "grey" in t:
            mapping["gmp"] = i
        elif "price" in t and "ipo price" not in t:
            mapping["price"] = i
        elif "listing" in t and "gain" in t:
            mapping["listing_gain"] = i
        elif "date" in t:
            mapping["date"] = i
        elif "type" in t:
            mapping["type"] = i
        elif "issue" in t and "size" in t:
            mapping["issue_size"] = i
        elif "lot" in t:
            mapping["lot_size"] = i
        elif "listing" in t or "list" in t:
            mapping["listing_date"] = i
        elif "ipo price" in t and "price" not in mapping:
            mapping["price"] = i
    return mapping

def parse_table(table_html):
    soup = BeautifulSoup(str(table_html), "lxml")
    headers = [clean(th.get_text()) for th in soup.find_all("th")]
    if not headers:
        first_row = soup.find("tr")
        if first_row:
            headers = [clean(td.get_text()) for td in first_row.find_all("td")]
    if not headers:
        return []

    header_map = build_header_map(headers)
    if "name" not in header_map:
        return []

    rows = []
    for r in soup.find_all("tr")[1:]:
        cols = [clean(td.get_text(" ", strip=True)) for td in r.find_all("td")]
        if len(cols) < 1:
            continue
        rec = {}
        rec["raw_cols"] = cols

        def get_field(key):
            idx = header_map.get(key)
            if idx is None or idx >= len(cols):
                return None
            return cols[idx].strip()

        rec["name"] = get_field("name") or cols[0]
        rec["gmp"] = normalize_money(get_field("gmp") or "")
        rec["price"] = normalize_money(get_field("price") or "")
        rec["listing_gain"] = get_field("listing_gain") or "N/A"
        rec["date"] = get_field("date") or "N/A"
        rec["type"] = get_field("type") or "N/A"

        # auto-detected extras
        rec["issue_size"] = get_field("issue_size") or "N/A"
        rec["lot_size"] = get_field("lot_size") or "N/A"
        rec["listing_date"] = get_field("listing_date") or "N/A"

        start_date, close_date = parse_ipo_dates(rec["date"])
        rec["start_date"] = start_date
        rec["close_date"] = close_date

        if rec["name"] and (rec["gmp"] != "N/A" or rec["price"] != "N/A" or rec["date"] != "N/A"):
            rows.append(rec)
    return rows

def parse_ipowatch_gmp_page(html: str):
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    results = []
    if not tables:
        article = soup.find("article") or soup.find("div", {"class":"post-body"}) or soup
        trs = article.find_all(["tr"])
        for r in trs:
            cols = [clean(td.get_text(" ", strip=True)) for td in r.find_all("td")]
            if len(cols) >= 2:
                rec = {
                    "name": cols[0],
                    "gmp": normalize_money(cols[1]),
                    "price": normalize_money(cols[2]) if len(cols) > 2 else "N/A",
                    "listing_gain": cols[3] if len(cols) > 3 else "N/A",
                    "date": cols[4] if len(cols) > 4 else "N/A",
                    "type": cols[5] if len(cols) > 5 else "N/A",
                    "issue_size": cols[6] if len(cols) > 6 else "N/A",
                    "lot_size": cols[7] if len(cols) > 7 else "N/A",
                    "listing_date": cols[8] if len(cols) > 8 else "N/A"
                }
                start_date, close_date = parse_ipo_dates(rec["date"])
                rec["start_date"] = start_date
                rec["close_date"] = close_date
                results.append(rec)
        return results

    for table in tables:
        parsed = parse_table(table)
        if parsed:
            results.extend(parsed)

    seen = set()
    unique = []
    for r in results:
        key = r["name"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique

# -----------------------
# Subscription parsing (unchanged GOOD-CODE style but skip year-only rows)
# -----------------------
def parse_subscription_page(html: str):
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    subs = {}

    if not tables:
        article = soup.find("article") or soup.find("div", {"class": "post-body"}) or soup
        rows = article.find_all("tr")
        for r in rows:
            cols = [clean(td.get_text(" ", strip=True)) for td in r.find_all("td")]
            if len(cols) >= 2:
                name = cols[0].strip()
                total = cols[-1].strip()
                date_cell = cols[1].strip() if len(cols) > 1 else ""
                if re.match(r"^\s*\d{4}\s*$", date_cell):
                    continue
                name_norm = normalize_name_for_match(name)
                subs[name_norm] = {"total": total, "date": date_cell}
        return subs

    for table in tables:
        headers = [clean(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        total_idx = None
        for i, h in enumerate(headers):
            hl = h.lower()
            if "total" in hl or "overall" in hl or "subscr" in hl or "subscription" in hl:
                total_idx = i
                break
        if total_idx is None:
            total_idx = -1

        for r in table.find_all("tr")[1:]:
            cols = [clean(td.get_text(" ", strip=True)) for td in r.find_all(["td","th"])]
            if not cols or len(cols) < 2:
                continue
            raw_name = cols[0].strip()
            if raw_name.lower() in ("name", "company", "ipo", ""):
                continue

            total_val = cols[total_idx].strip() if (total_idx != -1 and total_idx < len(cols)) else cols[-1].strip()

            date_val = ""
            date_idx = None
            for i, h in enumerate(headers):
                if "date" in h.lower():
                    date_idx = i
                    break
            if date_idx is not None and date_idx < len(cols):
                date_val = cols[date_idx].strip()
            else:
                if len(cols) > 1 and re.search(r"\d", cols[1]):
                    date_val = cols[1].strip()

            if date_val and re.match(r"^\s*\d{4}\s*$", date_val):
                continue

            name_norm = normalize_name_for_match(raw_name)
            subs[name_norm] = {"total": total_val if total_val not in ("", "-", "—") else "N/A", "date": date_val}

    return subs

async def fetch_subscription_totals():
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, IPOWATCH_SUB_URL)
        if not html:
            return {}
        return parse_subscription_page(html)

async def fetch_ipos_and_gmp():
    async with aiohttp.ClientSession() as session:
        html = await fetch_html(session, IPOWATCH_GMP_URL)
        if not html:
            return []
        return parse_ipowatch_gmp_page(html)

# -----------------------
# send_in_batches - updated message format to include extras + GMP icon
# -----------------------
async def send_in_batches(update: Update, context: ContextTypes.DEFAULT_TYPE, records: list):
    if not records:
        await update.message.reply_text("❌ No IPOs to send in this section.")
        return

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i+BATCH_SIZE]
        tasks = []
        for rec in batch:
            start_str = rec.get("start_date").strftime("%d %b") if rec.get("start_date") else "N/A"
            close_str = rec.get("close_date").strftime("%d %b") if rec.get("close_date") else "N/A"
            gmp_icon = gmp_trend_icon(rec.get("gmp", ""))
            issue_size = rec.get("issue_size", "N/A")
            lot_size = rec.get("lot_size", "N/A")
            listing_date = rec.get("listing_date", "N/A")
            text = (
                f"📌 *{rec['name']}* {gmp_icon}\n"
                f"🔹 IPO GMP: `{rec['gmp']}`\n"
                f"🔹 IPO Price: `{rec['price']}`\n"
                f"🔹 Listing Gain: {rec['listing_gain']}\n"
                f"🔹 Start Date: {start_str}\n"
                f"🔹 Close Date: {close_str}\n"
                f"🔹 Type: {rec.get('type','N/A')}\n"
                f"🔹 Issue Size: {issue_size}\n"
                f"🔹 Lot Size: {lot_size}\n"
                f"🔹 Listing Date: {listing_date}\n"
                f"🟣 Subscription (Total): `{rec.get('sub_total','N/A')}` (as of {rec.get('sub_date','N/A')})\n"
            )
            tasks.append(context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="Markdown"))
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"[send_in_batches] batch send failed: {e}. Falling back to serial send.")
            for t in tasks:
                try:
                    await t
                    await asyncio.sleep(0.3)
                except Exception as ex:
                    print(f"[send_in_batches] serial send error: {ex}")
        await asyncio.sleep(BATCH_DELAY)

# -----------------------
# Command handler (segmentation into Open vs Upcoming and Mainboard vs SME)
# -----------------------
async def cmd_ipos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Fetching IPOWatch GMP table and parsing...")
    try:
        records = await fetch_ipos_and_gmp()
    except Exception as e:
        print(f"[cmd_ipos] error fetching gmp: {e}")
        await msg.edit_text("❌ Error fetching/parsing IPO GMP data. See logs.")
        return

    if not records:
        await msg.edit_text("⚠️ No IPO rows found (page structure may have changed).")
        return

    try:
        subs = await fetch_subscription_totals()
    except Exception as e:
        print(f"[cmd_ipos] error fetching subs: {e}")
        subs = {}

    # Build subscription lookup (normalized name -> {total,date})
    sub_map = subs

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    open_ipos = []
    upcoming_ipos = []

    for r in records:
        start = r.get("start_date")
        close = r.get("close_date")
        # require both dates
        if not start or not close:
            continue
        # classify
        if start <= today <= close:
            target_list = open_ipos
        elif start > today:
            target_list = upcoming_ipos
        else:
            # already closed
            continue

        # find subscription match
        key = normalize_name_for_match(r.get("name", ""))
        sub_entry = sub_map.get(key)
        if not sub_entry:
            # token overlap heuristic
            best = None
            best_score = 0.0
            rec_tokens = set(key.split())
            for name_norm, s in sub_map.items():
                s_tokens = set(name_norm.split())
                if not s_tokens:
                    continue
                inter = len(rec_tokens & s_tokens)
                score = inter / max(1, len(s_tokens))
                if score > best_score:
                    best_score = score
                    if score >= 0.5:
                        best = s
            sub_entry = best

        # attach subscription (if found)
        if sub_entry:
            r["sub_total"] = sub_entry.get("total", "N/A")
            r["sub_date"] = sub_entry.get("date", "N/A")
        else:
            r["sub_total"] = "N/A"
            r["sub_date"] = "N/A"

        target_list.append(r)

    # sort each list ascending by start_date
    open_ipos.sort(key=lambda x: x.get("start_date") or datetime.max)
    upcoming_ipos.sort(key=lambda x: x.get("start_date") or datetime.max)

    if not open_ipos and not upcoming_ipos:
        await msg.edit_text("⚠️ No active or upcoming IPOs found currently.")
        return

    await msg.edit_text(f"✅ Found {len(open_ipos)} open IPOs and {len(upcoming_ipos)} upcoming IPOs. Sending now...")

    # Helper to split into Mainboard and SME and send
    def split_main_sme(lst):
        main = [r for r in lst if "sme" not in (r.get("type") or "").lower()]
        sme = [r for r in lst if "sme" in (r.get("type") or "").lower()]
        return main, sme

    # Send Open IPOs
    if open_ipos:
        main_open, sme_open = split_main_sme(open_ipos)
        if main_open:
            await update.message.reply_text("📈 *CURRENTLY OPEN — MAINBOARD* (sorted by start date)", parse_mode="Markdown")
            await send_in_batches(update, context, main_open)
        if sme_open:
            await update.message.reply_text("📈 *CURRENTLY OPEN — SME* (sorted by start date)", parse_mode="Markdown")
            await send_in_batches(update, context, sme_open)

    # Send Upcoming IPOs
    if upcoming_ipos:
        main_up, sme_up = split_main_sme(upcoming_ipos)
        if main_up:
            await update.message.reply_text("🟡 *UPCOMING — MAINBOARD* (sorted by start date)", parse_mode="Markdown")
            await send_in_batches(update, context, main_up)
        if sme_up:
            await update.message.reply_text("🟡 *UPCOMING — SME* (sorted by start date)", parse_mode="Markdown")
            await send_in_batches(update, context, sme_up)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 HELPERBRO (IPOWatch) ready.\nUse /ipos to fetch open + upcoming IPOs (Mainboard / SME)."
    )

# -----------------------
# Main
# -----------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ipos", cmd_ipos))
    print("HELPERBRO running...")
    app.run_polling()

if __name__ == "__main__":
    main()
