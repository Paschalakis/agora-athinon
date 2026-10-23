"""
Ενημέρωση τιμών για το site «Αγορά Αθηνών».

Πηγές
  - capital.gr (αρχείο δεδομένων JSON της σελίδας «Όλες οι μετοχές»):
    σύμβολο, όνομα, τιμή, μεταβολή %, όγκος, ώρα τελευταίας συναλλαγής.
    Αν δεν απαντήσει, διαβάζεται η ίδια η σελίδα (HTML) ως εφεδρεία.
  - capital.gr: Γενικός Δείκτης και τζίρος αγοράς.
  - euro2day.gr: κεφαλαιοποίηση.

Αρχεία που γράφει (στη ρίζα του repository)
  - data.json      τρέχουσες τιμές
  - history.json   τιμή κλεισίματος κάθε ημέρας (για 7 ημέρες, γραφήματα)
  - intraday.json  τιμές της τρέχουσας ημέρας ανά εκτέλεση (γράφημα «Σήμερα»)

Ασφάλεια: αν διαβαστούν λιγότερες από 150 μετοχές δεν γράφεται τίποτα,
ώστε το site να κρατά τα τελευταία σωστά στοιχεία.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data.json")
HIST = os.path.join(ROOT, "history.json")
INTRA = os.path.join(ROOT, "intraday.json")
DEBUG = os.path.join(ROOT, "debug")
ATHENS = ZoneInfo("Europe/Athens")

CAPITAL_API = "https://www.capital.gr/api/quotes/allstocks/?langCd=el"
CAPITAL_GD = "https://www.capital.gr/api/quotes/gdm/"
CAPITAL_URL = "https://www.capital.gr/finance/allstocks"
EURO2DAY_URL = "https://www.euro2day.gr/AseQuotesCapitalization.aspx"

MIN_PRICES = 150
MIN_CAPS = 60
HISTORY_DAYS = 400

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
}
API_HEADERS = dict(HEADERS, **{
    "Accept": "application/json, text/javascript, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": CAPITAL_URL,
})

# Η σελίδα (HTML) του capital.gr κόβει τα σύμβολα στους 6 χαρακτήρες. Το
# αρχείο δεδομένων και το euro2day δίνουν τα πλήρη. Χρησιμοποιούμε τα πλήρη.
FULL = {
    "ΓΕΚΤΕΡ": "ΓΕΚΤΕΡΝΑ", "ΕΛΛΑΚΤ": "ΕΛΛΑΚΤΩΡ", "ΠΡΟΝΤΕ": "ΠΡΟΝΤΕΑ",
    "REALCO": "REALCONS", "ΤΡΑΣΤΟ": "ΤΡΑΣΤΟΡ", "ΤΡΕΣΤΑ": "ΤΡΕΣΤΑΤΕΣ",
    "ΜΠΛΕΚΕ": "ΜΠΛΕΚΕΔΡΟΣ", "ΟΡΙΛΙΝ": "ΟΡΙΛΙΝΑ", "ΝΤΟΤΣΟ": "ΝΤΟΤΣΟΦΤ",
    "ΚΑΙΡΟΜ": "ΚΑΙΡΟΜΕΖ", "ΣΑΝΜΕΖ": "ΣΑΝΜΕΖΖ", "ΜΑΣΤΙΧ": "ΜΑΣΤΙΧΑ",
    "ΝΤΟΠΛΕ": "ΝΤΟΠΛΕΡ", "ΙΝΤΕΡΚ": "ΙΝΤΕΡΚΟ", "ΝΤΡΙΑΡ": "ΝΤΡΙΑΡΤ",
}

SYM_RE = re.compile(r"^[A-ZΑ-Ω0-9][A-ZΑ-Ω0-9.\-&/]{0,11}$")
PRICE_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{1,6}$")
PCT_RE = re.compile(r"^[-+−]?\d{1,3},\d{1,4}\s*%$")
CAP_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d{2}$")
INT_RE = re.compile(r"^\d{1,4}$")
NUM_RE = re.compile(r"^[-+−]?\d{1,3},\d{1,4}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def gr_num(s):
    """'1.234,56' -> 1234.56"""
    s = s.replace("%", "").replace("−", "-").replace("+", "").strip()
    return float(s.replace(".", "").replace(",", "."))


def get(url, headers=HEADERS):
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    return r.text


def save_debug(name, text):
    os.makedirs(DEBUG, exist_ok=True)
    with open(os.path.join(DEBUG, name), "w", encoding="utf-8") as f:
        f.write(text)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- capital.gr
def from_capital_api():
    """Τιμές από το αρχείο δεδομένων του capital.gr."""
    raw = get(CAPITAL_API, API_HEADERS)
    try:
        quotes = json.loads(raw)["ResultData"]["quotes"]
    except Exception:
        save_debug("capital_api.txt", raw)
        raise
    out = {}
    for q in quotes:
        sym = (q.get("s") or "").strip()
        if not sym or q.get("l") is None:
            continue
        lu = q.get("lu") or ""
        traded = (q.get("tv") or 0) > 0 or (q.get("v") or 0) > 0
        out[sym] = {
            "price": float(q["l"]),
            "chg": round(float(q.get("pc") or 0), 2),
            # «tv» = τεμάχια που άλλαξαν χέρια σήμερα
            "vol": int(q.get("tv") or 0),
            # «02:00» σημαίνει «καμία συναλλαγή σήμερα»
            "lu": lu if traded and TIME_RE.match(lu) else "",
            "cname": (q.get("sn") or q.get("n") or "").strip(),
        }
    return out


def tokens(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = soup.get_text("\n")
    return [t.strip() for t in text.split("\n") if t.strip()]


def from_capital_html():
    """Εφεδρεία: ανάγνωση της σελίδας. Δεν έχει όγκο."""
    html = get(CAPITAL_URL)
    tok = tokens(html)
    start = 0
    for i in range(len(tok) - 1):
        if tok[i].upper() == "ΤΙΜΗ" and tok[i + 1].upper().startswith("ΜΕΤ"):
            start = i + 2
            break
    end = len(tok)
    for i in range(start, len(tok)):
        if "ΑΝΟΔΙΚΕΣ" in tok[i].upper():
            end = i
            break
    tok = tok[start:end]
    out = {}
    i = 0
    while i < len(tok) - 2:
        a, b, c = tok[i], tok[i + 1], tok[i + 2]
        d = tok[i + 3] if i + 3 < len(tok) else ""
        if SYM_RE.match(a) and PRICE_RE.match(b):
            if PCT_RE.match(c):
                out.setdefault(FULL.get(a, a), {"price": gr_num(b), "chg": gr_num(c)})
                i += 3
                continue
            if NUM_RE.match(c) and d == "%":
                out.setdefault(FULL.get(a, a), {"price": gr_num(b), "chg": gr_num(c)})
                i += 4
                continue
        i += 1
    if len(out) < MIN_PRICES:
        save_debug("capital.html", html)
    return out


def general_index():
    try:
        r = json.loads(get(CAPITAL_GD, API_HEADERS))["ResultData"][0]
        return {
            "value": float(r["l"]),
            "chg": round(float(r.get("c") or 0), 2),
            "pct": round(float(r.get("pc") or 0), 2),
            "turnover": float(r.get("to") or 0),
            "time": r.get("lu") or "",
        }
    except Exception as e:
        print("ΠΡΟΣΟΧΗ: δεν βρέθηκε ο Γενικός Δείκτης:", e)
        return None


# ------------------------------------------------------------------ euro2day
def parse_euro2day(html):
    """Τετράδες: Α/Α, ΟΝΟΜΑ, ΚΩΔΙΚΟΣ, ΚΕΦΑΛΑΙΟΠΟΙΗΣΗ."""
    tok = tokens(html)
    out = {}
    for i in range(len(tok) - 3):
        n, name, code, cap = tok[i], tok[i + 1], tok[i + 2], tok[i + 3]
        if INT_RE.match(n) and SYM_RE.match(code) and CAP_RE.match(cap) and not CAP_RE.match(name):
            code = FULL.get(code, code)
            name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
            if code not in out:
                out[code] = {"name": name, "mcap": gr_num(cap)}
    return out


# ------------------------------------------------------------------- history
def update_history(stocks, now):
    """Μία στήλη ανά ημέρα συνεδρίασης. Η στήλη της ημέρας ξαναγράφεται σε
    κάθε εκτέλεση, οπότε η τελευταία εκτέλεση της ημέρας δίνει το κλείσιμο."""
    day = now.strftime("%Y-%m-%d")
    h = load_json(HIST, {"days": [], "px": {}})
    days, px = h.get("days", []), h.get("px", {})
    if not days or days[-1] != day:
        days.append(day)
        for arr in px.values():
            arr.append(None)
    n = len(days)
    for s in stocks:
        arr = px.setdefault(s["sym"], [None] * n)
        if len(arr) < n:
            arr.extend([None] * (n - len(arr)))
        arr[n - 1] = s["price"]
    if n > HISTORY_DAYS:
        cut = n - HISTORY_DAYS
        days = days[cut:]
        px = {k: v[cut:] for k, v in px.items()}
    write_json(HIST, {"days": days, "px": px})
    return len(days)


def update_intraday(stocks, index, now):
    day = now.strftime("%Y-%m-%d")
    t = now.strftime("%H:%M")
    d = load_json(INTRA, {})
    if d.get("date") != day:
        d = {"date": day, "t": [], "px": {}, "gd": []}
    if d["t"] and d["t"][-1] == t:
        return len(d["t"])
    d["t"].append(t)
    n = len(d["t"])
    for s in stocks:
        arr = d["px"].setdefault(s["sym"], [None] * (n - 1))
        if len(arr) < n - 1:
            arr.extend([None] * (n - 1 - len(arr)))
        arr.append(s["price"])
    for arr in d["px"].values():
        if len(arr) < n:
            arr.append(None)
    d["gd"].append(index["value"] if index else None)
    write_json(INTRA, d)
    return n


# ---------------------------------------------------------------------- main
def main():
    now = datetime.now(ATHENS)
    prev = {FULL.get(s["sym"], s["sym"]): s for s in load_json(OUT, {}).get("stocks", [])}

    prices, source = {}, ""
    try:
        prices = from_capital_api()
        source = "api"
    except Exception as e:
        print("ΠΡΟΣΟΧΗ: το αρχείο δεδομένων του capital.gr δεν απάντησε:", e)
    if len(prices) < MIN_PRICES:
        try:
            prices = from_capital_html()
            source = "html"
        except Exception as e:
            print("ΣΦΑΛΜΑ: δεν φορτώθηκε το capital.gr:", e)
            sys.exit(1)
    print("capital.gr (%s): %d μετοχές" % (source, len(prices)))
    if len(prices) < MIN_PRICES:
        print("ΣΦΑΛΜΑ: πολύ λίγες μετοχές. Τα αρχεία ΔΕΝ άλλαξαν.")
        sys.exit(1)

    index = general_index()

    caps = {}
    try:
        caps = parse_euro2day(get(EURO2DAY_URL))
        print("euro2day: %d εταιρείες" % len(caps))
        if len(caps) < MIN_CAPS:
            print("ΠΡΟΣΟΧΗ: λίγες εταιρείες από euro2day, κρατάω τις παλιές τιμές")
            caps = {}
    except Exception as e:
        print("ΠΡΟΣΟΧΗ: δεν φορτώθηκε το euro2day:", e)

    stocks = []
    for sym, p in prices.items():
        s = {"sym": sym, "price": p["price"], "chg": p["chg"]}
        if "vol" in p:
            s["vol"] = p["vol"]
            s["lu"] = p["lu"]
        old = prev.get(sym, {})
        if sym in caps:
            s["name"] = caps[sym]["name"]
            s["mcap"] = round(caps[sym]["mcap"], 2)
        elif old.get("mcap") and old.get("price"):
            s["name"] = old.get("name", "")
            s["mcap"] = round(old["mcap"] * p["price"] / old["price"], 2)
        else:
            s["name"] = old.get("name") or p.get("cname", "")
        if not s.get("name") and p.get("cname"):
            s["name"] = p["cname"]
        stocks.append(s)

    traded = sum(1 for s in stocks if s.get("lu"))
    data = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": ["capital.gr", "euro2day.gr"],
        "delay_minutes": 15,
        "count": len(stocks),
        "traded": traded if source == "api" else None,
        "index": index,
        "stocks": stocks,
    }
    write_json(OUT, data)
    days = update_history(stocks, now)
    points = update_intraday(stocks, index, now)
    print("ΟΚ: %d μετοχές (%s με συναλλαγές), ιστορικό %d ημέρες, %d σημεία σήμερα"
          % (len(stocks), traded if source == "api" else "?", days, points))


if __name__ == "__main__":
    main()
