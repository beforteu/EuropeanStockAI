"""
EU Market Terminal — Data Fetcher
Eseguito ogni mattina alle 7:00 CET da GitHub Actions.
Scarica dati da Financial Modeling Prep e salva data.json.
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# ── CONFIG ──────────────────────────────────────────────────────
API_KEY  = os.environ.get("FMP_API_KEY", "")
BASE_URL = "https://financialmodelingprep.com/stable"
OUT_FILE = "data.json"
BATCH    = 8          # richieste parallele (rispetta rate limit free tier: 250/giorno)
SLEEP_S  = 0.3        # pausa tra batch (secondi)

INDICES = [
    {"name": "FTSE MIB",      "id": "ftse_mib", "cls": "exch-ftse" },
    {"name": "DAX",           "id": "dax",      "cls": "exch-dax"  },
    {"name": "CAC 40",        "id": "cac",      "cls": "exch-cac"  },
    {"name": "IBEX 35",       "id": "ibex",     "cls": "exch-ibex" },
    {"name": "EURO STOXX 50", "id": "euronext", "cls": "exch-stoxx"},
    {"name": "AEX",           "id": "aex",      "cls": "exch-aex"  },
]

TICKERS = {
    "ftse_mib": [
        "ENI.MI","ENEL.MI","ISP.MI","UCG.MI","STM.MI","TIT.MI","G.MI","MB.MI",
        "REC.MI","BAMI.MI","PST.MI","LDO.MI","CPR.MI","SRG.MI","BGN.MI","MONC.MI",
        "RACE.MI","AMP.MI","PRY.MI","CNHI.MI","HER.MI","TEN.MI","EXO.MI","BCN.MI",
        "DLG.MI","FBK.MI","BZU.MI","AZM.MI","INW.MI","RBI.MI",
    ],
    "dax": [
        "SAP.DE","SIE.DE","ALV.DE","DTE.DE","MBG.DE","BMW.DE","BAS.DE","MRK.DE",
        "ADS.DE","BAYN.DE","DB1.DE","RWE.DE","HEN3.DE","VOW3.DE","IFX.DE","ZAL.DE",
        "DHL.DE","CON.DE","EOAN.DE","VNA.DE","FRE.DE","BEI.DE","MUV2.DE","DPW.DE",
        "QIA.DE","HFG.DE","SHL.DE","SY1.DE","MTX.DE","AIR.DE",
    ],
    "cac": [
        "MC.PA","TTE.PA","SAN.PA","AIR.PA","OR.PA","SU.PA","BNP.PA","DG.PA",
        "CAP.PA","CS.PA","BN.PA","SGO.PA","RI.PA","VIE.PA","ACA.PA","GLE.PA",
        "KER.PA","STM.PA","PUB.PA","VK.PA","HO.PA","DSY.PA","EN.PA","FP.PA",
        "ORA.PA","WLN.PA","AM.PA","AC.PA","RNO.PA","ML.PA",
    ],
    "ibex": [
        "SAN.MC","IBE.MC","TEF.MC","ITX.MC","BBVA.MC","REP.MC","AMS.MC","ACS.MC",
        "FER.MC","ELE.MC","GRF.MC","MAP.MC","MEL.MC","MTS.MC","NTGY.MC","RED.MC",
        "SAB.MC","SCYR.MC","TRE.MC","UNI.MC","VIS.MC","AENA.MC","ENG.MC","ACX.MC",
        "CLNX.MC","ALM.MC","BKT.MC","LOG.MC","CIE.MC","PHM.MC",
    ],
    "euronext": [
        "ASML.AS","ADYEN.AS","INGA.AS","PHIA.AS","NN.AS","RAND.AS","WKL.AS",
        "IMCD.AS","AH.AS","HEIA.AS","BESI.AS","ASM.AS","KPN.AS","AKZA.AS",
        "AALB.AS","SBMO.AS","AGN.AS","VPK.AS","URW.AS","OCI.AS",
        "STLAM.MI","EL.PA","AI.PA","SG.PA","NOKIA.HE","NESTE.HE",
    ],
    "aex": [
        "ASML.AS","ADYEN.AS","INGA.AS","PHIA.AS","NN.AS","RAND.AS","WKL.AS",
        "IMCD.AS","AH.AS","HEIA.AS","BESI.AS","ASM.AS","KPN.AS","AKZA.AS",
        "AALB.AS","SBMO.AS","AGN.AS","VPK.AS","URW.AS","OCI.AS",
        "TKWY.AS","ABN.AS","DSM.AS","REN.AS",
    ],
}

# ── HELPERS ─────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({"Accept": "application/json"})

def fmp_get(endpoint: str) -> dict | list:
    sep = "&" if "?" in endpoint else "?"
    url = f"{BASE_URL}{endpoint}{sep}apikey={API_KEY}"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return r.json()

def safe_float(v):
    try:
        f = float(v)
        return None if (f != f) else f   # filter NaN
    except (TypeError, ValueError):
        return None

def pct(v):
    f = safe_float(v)
    return round(f * 100, 2) if f is not None else None

def m_eur(v):
    f = safe_float(v)
    return round(f / 1_000_000) if f is not None else None

def calc_score(change_pct, beta, dividend, mkt_cap_m):
    """
    Score semplificato basato solo sui dati disponibili nel piano gratuito FMP:
    variazione prezzo recente, volatilità (beta), dividend yield, dimensione azienda.
    Nota: senza P/E, ROE, margini reali, questo è un indicatore di massima —
    non un vero growth score fondamentale.
    """
    s = 50
    if change_pct is not None:
        s += 10 if change_pct > 3 else 6 if change_pct > 1 else 2 if change_pct > 0 else -6 if change_pct < -2 else -2
    if beta is not None:
        # beta moderato (0.6-1.3) preferito: troppo basso = poco dinamico, troppo alto = rischioso
        if 0.6 <= beta <= 1.3:
            s += 8
        elif beta > 1.8:
            s -= 6
    if dividend is not None:
        s += 8 if dividend > 4 else 5 if dividend > 2 else 2 if dividend > 0 else 0
    if mkt_cap_m is not None:
        # mega-cap = più stabilità/liquidità
        s += 6 if mkt_cap_m > 50000 else 3 if mkt_cap_m > 10000 else 0
    return max(0, min(100, round(s)))

# ── FETCH ONE COMPANY ────────────────────────────────────────────
def fetch_company(ticker: str, idx: dict) -> dict | None:
    try:
        profiles = fmp_get(f"/profile?symbol={ticker}")
    except Exception as e:
        print(f"  ✗ {ticker}: {e}")
        return None

    p = (profiles[0] if isinstance(profiles, list) else profiles) or {}

    if not p.get("companyName"):
        print(f"  ✗ {ticker}: no data")
        return None

    price       = safe_float(p.get("price"))
    change_pct  = safe_float(p.get("changePercentage"))
    beta        = safe_float(p.get("beta"))
    mkt_cap_m   = m_eur(p.get("marketCap"))
    last_div    = safe_float(p.get("lastDividend"))
    div_yield   = round(last_div / price * 100, 2) if last_div and price else None

    # range comes as "low-high" string, e.g. "13.584-25.015"
    low52, high52 = None, None
    range_str = p.get("range") or ""
    if "-" in range_str:
        parts = range_str.split("-")
        if len(parts) == 2:
            low52  = safe_float(parts[0])
            high52 = safe_float(parts[1])

    score = calc_score(change_pct, beta, div_yield, mkt_cap_m)

    print(f"  ✓ {ticker} ({idx['name']}) — score {score}")
    return {
        "ticker":        ticker,
        "name":          p.get("companyName", ""),
        "description":   (p.get("description") or "")[:220],
        "exchange":      idx["name"],
        "exchCls":       idx["cls"],
        "sector":        p.get("sector", ""),
        "industry":      p.get("industry", ""),
        "country":       p.get("country", ""),
        "currency":      p.get("currency", "EUR"),
        "price":         price,
        "change":        change_pct,
        "marketCap":     mkt_cap_m,
        "dividend":      div_yield,
        "beta":          beta,
        "high52":        high52,
        "low52":         low52,
        "score":         score,
    }

# ── MAIN ─────────────────────────────────────────────────────────
def main():
    if not API_KEY:
        raise ValueError("FMP_API_KEY non impostata. Aggiungila come GitHub Secret.")

    # Build deduplicated ticker → index map
    seen    = set()
    queue   = []
    for idx in INDICES:
        for t in TICKERS.get(idx["id"], []):
            if t not in seen:
                seen.add(t)
                queue.append((t, idx))

    print(f"Avvio fetch: {len(queue)} ticker su {len(INDICES)} indici")
    results = []

    for i in range(0, len(queue), BATCH):
        batch = queue[i : i + BATCH]
        for ticker, idx in batch:
            company = fetch_company(ticker, idx)
            if company:
                results.append(company)
        if i + BATCH < len(queue):
            time.sleep(SLEEP_S)

    # Timestamp CET
    cet = timezone(timedelta(hours=2))  # CEST (ora legale); usa +1 in inverno
    now = datetime.now(cet).strftime("%d/%m/%Y %H:%M")

    output = {
        "updated_at": now,
        "count":      len(results),
        "companies":  results,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    print(f"\n✅ Salvate {len(results)} aziende in {OUT_FILE} — {now}")

if __name__ == "__main__":
    main()
