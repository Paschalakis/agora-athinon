"""
Ενημέρωση τιμών για το site «Αγορά Αθηνών».

Διαβάζει:
  - capital.gr  -> σύμβολο, τιμή, μεταβολή %  (καθυστέρηση 15')
  - euro2day.gr -> όνομα εταιρείας, κεφαλαιοποίηση
και γράφει το data.json στη ρίζα του repository.

Ασφάλεια: αν κάτι πάει στραβά (π.χ. άλλαξε η σελίδα της πηγής και
διαβάζονται ελάχιστες μετοχές), ΔΕΝ γράφει νέο data.json, ώστε το site
να συνεχίσει να δείχνει τα τελευταία σωστά στοιχεία. Αποθηκεύει τότε το
HTML της πηγής στον φάκελο debug/ για διάγνωση.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data.json")
DEBUG = os.path.join(ROOT, "debug")

CAPITAL_URL = "https://www.capital.gr/finance/allstocks"
EURO2DAY_URL = "https://www.euro2day.gr/AseQuotesCapitalization.aspx"

MIN_PRICES = 150   # λιγότερες μετοχές από αυτό = κάτι χάλασε
MIN_CAPS = 60

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "el-GR,el;q=0.9,en;q=0.8",
}

# Κωδικοί του euro2day που διαφέρουν από τα σύμβολα του capital.gr
ALIAS = {
    "ΓΕΚΤΕΡΝΑ": "ΓΕΚΤΕΡ", "ΕΛΛΑΚΤΩΡ": "ΕΛΛΑΚΤ", "ΠΡΟΝΤΕΑ": "ΠΡΟΝΤΕ",
    "REALCONS": "REALCO", "ΤΡΑΣΤΟΡ": "ΤΡΑΣΤΟ", "ΤΡΕΣΤΑΤΕΣ": "ΤΡΕΣΤΑ",
    "ΜΠΛΕΚΕΔΡΟΣ": "ΜΠΛΕΚΕ", "ΟΡΙΛΙΝΑ": "ΟΡΙΛΙΝ", "ΝΤΟΤΣΟΦΤ": "ΝΤΟΤΣΟ",
    "ΚΑΙΡΟΜΕΖ": "ΚΑΙΡΟΜ", "ΣΑΝΜΕΖΖ": "ΣΑΝΜΕΖ",
    "ΜΑΣΤΙΧΑ": "ΜΑΣΤΙΧ", "ΝΤΟΠΛΕΡ": "ΝΤΟΠΛΕ",
}

SYM_RE = re.compile(r"^[A-ZΑ-Ω0-9][A-ZΑ-Ω0-9.\-&/]{0,11}$")
PRICE_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{1,6}$")
PCT_RE = re.compile(r"^[-+−]?\d{1,3},\d{1,4}\s*%$")
CAP_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d{2}$")
INT_RE = re.compile(r"^\d{1,4}$")
NUM_RE = re.compile(r"^[-+−]?\d{1,3},\d{1,4}$")


def gr_num(s):
    """'1.234,56' -> 1234.56"""
    s = s.replace("%", "").replace("−", "-").replace("+", "").strip()
    return float(s.replace(".", "").replace(",", "."))


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding
    return r.text


def tokens(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    text = soup.get_text("\n")
    return [t.strip() for t in text.split("\n") if t.strip()]


def parse_capital(html):
    """Βρίσκει: ΣΥΜΒΟΛΟ, ΤΙΜΗ, ΜΕΤΑΒΟΛΗ, «%» (το % μπορεί να είναι
    χωριστό στοιχείο ή κολλημένο στη μεταβολή)."""
    tok = tokens(html)
    # Ξεκινάμε από την επικεφαλίδα του κύριου πίνακα (… ΤΙΜΗ, ΜΕΤ.%), ώστε να
    # μη διαβαστούν οι δείκτες της πάνω μπάρας ως μετοχές, και σταματάμε στον
    # πίνακα «ΑΝΟΔΙΚΕΣ / ΠΤΩΤΙΚΕΣ ΜΕΤΟΧΕΣ».
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
                out.setdefault(a, {"price": gr_num(b), "chg": gr_num(c)})
                i += 3
                continue
            if NUM_RE.match(c) and d == "%":
                out.setdefault(a, {"price": gr_num(b), "chg": gr_num(c)})
                i += 4
                continue
        i += 1
    return out


def parse_euro2day(html):
    """Βρίσκει τετράδες: Α/Α, ΟΝΟΜΑ, ΚΩΔΙΚΟΣ, ΚΕΦΑΛΑΙΟΠΟΙΗΣΗ."""
    tok = tokens(html)
    out = {}
    for i in range(len(tok) - 3):
        n, name, code, cap = tok[i], tok[i + 1], tok[i + 2], tok[i + 3]
        if INT_RE.match(n) and SYM_RE.match(code) and CAP_RE.match(cap) and not CAP_RE.match(name):
            code = ALIAS.get(code, code)
            name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
            if code not in out:
                out[code] = {"name": name, "mcap": gr_num(cap)}
    return out


def save_debug(name, html):
    os.makedirs(DEBUG, exist_ok=True)
    with open(os.path.join(DEBUG, name), "w", encoding="utf-8") as f:
        f.write(html)


def load_previous():
    try:
        with open(OUT, encoding="utf-8") as f:
            return {s["sym"]: s for s in json.load(f).get("stocks", [])}
    except Exception:
        return {}


def main():
    prev = load_previous()

    # 1. Τιμές (απαραίτητες)
    try:
        cap_html = get(CAPITAL_URL)
    except Exception as e:
        print("ΣΦΑΛΜΑ: δεν φορτώθηκε το capital.gr:", e)
        sys.exit(1)
    prices = parse_capital(cap_html)
    print("capital.gr: βρέθηκαν", len(prices), "μετοχές")
    if len(prices) < MIN_PRICES:
        save_debug("capital.html", cap_html)
        print("ΣΦΑΛΜΑ: πολύ λίγες μετοχές. Το data.json ΔΕΝ άλλαξε. Δες debug/capital.html")
        sys.exit(1)

    # 2. Κεφαλαιοποίηση (προαιρετική: αν αποτύχει κρατάμε τα προηγούμενα ονόματα)
    caps = {}
    try:
        e2d_html = get(EURO2DAY_URL)
        caps = parse_euro2day(e2d_html)
        print("euro2day: βρέθηκαν", len(caps), "εταιρείες")
        if len(caps) < MIN_CAPS:
            save_debug("euro2day.html", e2d_html)
            print("ΠΡΟΣΟΧΗ: λίγες εταιρείες από euro2day, κρατάω τα παλιά ονόματα")
            caps = {}
    except Exception as e:
        print("ΠΡΟΣΟΧΗ: δεν φορτώθηκε το euro2day:", e)

    # 3. Συνδυασμός
    stocks = []
    for sym, p in prices.items():
        s = {"sym": sym, "price": p["price"], "chg": p["chg"]}
        old = prev.get(sym, {})
        if sym in caps:
            s["name"] = caps[sym]["name"]
            # Η κεφαλαιοποίηση του euro2day αντιστοιχεί στη δική του τιμή·
            # είναι αρκετά κοντά στην τιμή του capital.gr για εμφάνιση.
            s["mcap"] = round(caps[sym]["mcap"], 2)
        elif old.get("mcap") and old.get("price"):
            # Παλιά κεφαλαιοποίηση, αναπροσαρμοσμένη στη νέα τιμή
            s["name"] = old.get("name", "")
            s["mcap"] = round(old["mcap"] * p["price"] / old["price"], 2)
        elif old.get("name"):
            s["name"] = old["name"]
        stocks.append(s)

    data = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": ["capital.gr", "euro2day.gr"],
        "delay_minutes": 15,
        "count": len(stocks),
        "stocks": stocks,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("ΟΚ: γράφτηκε data.json με", len(stocks), "μετοχές")


if __name__ == "__main__":
    main()
