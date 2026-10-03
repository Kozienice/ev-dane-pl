#!/usr/bin/env python3
"""europa_marki.py v0.1 — BEV osobowe per marka, Holandia (RDW Open Data).

Źródło: RDW Open Data (Socrata), licencja CC0:
  - pojazdy:  https://opendata.rdw.nl/resource/m9d7-ebf2.json  (merk, voertuigsoort, daty)
  - paliwa:   https://opendata.rdw.nl/resource/8ys7-d773.json  (kenteken, brandstof_omschrijving)

Definicje (jawne, do PATCHLOG):
  - NOWE AUTO w miesiącu M: voertuigsoort = 'Personenauto' ORAZ datum_eerste_toelating w M
    ORAZ datum_eerste_tenaamstelling_in_nederland w M (wyklucza import używanych).
  - BEV: zbiór paliw pojazdu == {'Elektriciteit'} (PHEV/HEV mają też Benzine/Diesel,
    FCEV ma Waterstof -> wykluczone).
  - RDW to rejestr bieżący: auto wyrejestrowane/eksportowane później może zniknąć,
    więc liczby starszych miesięcy mogą się lekko zmieniać -> ostatnie 2 miesiące
    są przeliczane co przebieg, starsze tylko gdy brak pliku.

Wyjście:
  data/europa/rdw_nl_bev_marki_{RRRR-MM}.csv   marka,bev,udzial_bev
  data/europa/rdw_nl_bev_marki_ytd_{RRRR}.csv  marka,bev_ytd,udzial,miesiace
  data/europa/status_rdw.json                   adresy, liczby wierszy, kontrole
"""
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://opendata.rdw.nl/resource"
POJAZDY = f"{BASE}/m9d7-ebf2.json"
PALIWA = f"{BASE}/8ys7-d773.json"
OUT = os.path.join("data", "europa")
STRONA = 50000          # limit wierszy na stronę Socrata
PACZKA = 300            # kentekens w jednym zapytaniu o paliwa
UA = "ev-dane-pl/0.1 (github.com/Kozienice/ev-dane-pl)"
APP_TOKEN = os.environ.get("RDW_APP_TOKEN")  # opcjonalny, podnosi limity Socrata


def pobierz_json(url, params, proby=4):
    q = urllib.parse.urlencode(params)
    full = f"{url}?{q}"
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if APP_TOKEN:
        headers["X-App-Token"] = APP_TOKEN
    ostatni = None
    for i in range(proby):
        try:
            req = urllib.request.Request(full, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                return json.loads(body), r.status, len(body)
        except Exception as e:  # noqa: BLE001 — logujemy i ponawiamy
            ostatni = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"GET {full[:200]} nieudane po {proby} próbach: {ostatni}")


def zakres_miesiaca(rok, mies):
    pocz = dt.date(rok, mies, 1)
    nast = dt.date(rok + (mies == 12), mies % 12 + 1, 1)
    return pocz, nast


def nowe_auta(rok, mies, log):
    """Zwraca listę (kenteken, merk) nowych aut osobowych z miesiąca."""
    pocz, nast = zakres_miesiaca(rok, mies)
    gdzie = (
        "voertuigsoort='Personenauto' "
        f"AND datum_eerste_toelating_dt >= '{pocz}T00:00:00' "
        f"AND datum_eerste_toelating_dt < '{nast}T00:00:00' "
        f"AND datum_eerste_tenaamstelling_in_nederland_dt >= '{pocz}T00:00:00' "
        f"AND datum_eerste_tenaamstelling_in_nederland_dt < '{nast}T00:00:00'"
    )
    wynik, offset = [], 0
    while True:
        dane, kod, bajty = pobierz_json(POJAZDY, {
            "$select": "kenteken,merk", "$where": gdzie,
            "$order": "kenteken", "$limit": STRONA, "$offset": offset})
        log.append({"zrodlo": "pojazdy", "okres": f"{rok}-{mies:02d}", "offset": offset,
                    "http": kod, "bajty": bajty, "wiersze": len(dane)})
        wynik += [(d["kenteken"], d.get("merk", "?").strip().upper()) for d in dane]
        if len(dane) < STRONA:
            return wynik
        offset += STRONA


def paliwa(kentekens, log):
    """kenteken -> zbiór paliw."""
    out = {}
    for i in range(0, len(kentekens), PACZKA):
        paczka = kentekens[i:i + PACZKA]
        lista = ",".join("'" + k.replace("'", "") + "'" for k in paczka)
        dane, kod, bajty = pobierz_json(PALIWA, {
            "$select": "kenteken,brandstof_omschrijving",
            "$where": f"kenteken in({lista})", "$limit": PACZKA * 4})
        for d in dane:
            out.setdefault(d["kenteken"], set()).add(d.get("brandstof_omschrijving", "?"))
        if i == 0 or kod != 200:
            log.append({"zrodlo": "paliwa", "paczka_od": i, "http": kod, "bajty": bajty})
    return out


def policz_miesiac(rok, mies, log):
    auta = nowe_auta(rok, mies, log)
    pal = paliwa([k for k, _ in auta], log)
    bez_paliwa = sum(1 for k, _ in auta if k not in pal)
    licz = {}
    for k, merk in auta:
        if pal.get(k) == {"Elektriciteit"}:
            licz[merk] = licz.get(merk, 0) + 1
    bev = sum(licz.values())
    kontrola = {
        "okres": f"{rok}-{mies:02d}",
        "nowe_osobowe": len(auta),
        "bev": bev,
        "udzial_bev": round(bev / len(auta), 4) if auta else None,
        "bez_rekordu_paliwa": bez_paliwa,
        # kontrole spójności (wymóg zadania: brak -> nie używać pliku)
        "suma_marek_rowna_bev": sum(licz.values()) == bev,
        "marka_nie_wieksza_niz_rynek": all(v <= bev for v in licz.values()),
        "pokrycie_paliw_ok": (bez_paliwa / len(auta) < 0.01) if auta else False,
    }
    kontrola["zgodnosc"] = all([kontrola["suma_marek_rowna_bev"],
                                kontrola["marka_nie_wieksza_niz_rynek"],
                                kontrola["pokrycie_paliw_ok"]])
    return licz, kontrola


def zapisz_csv(sciezka, naglowek, wiersze):
    with open(sciezka, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(naglowek)
        w.writerows(wiersze)
    with open(sciezka, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main(dzis=None):
    dzis = dzis or dt.date.today()
    os.makedirs(OUT, exist_ok=True)
    rok = dzis.year if dzis.month > 1 else dzis.year - 1
    ostatni = dzis.month - 1 if dzis.month > 1 else 12
    status = {"uruchomienie_utc": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
              "wersja_skryptu": "europa_marki 0.1", "kraj": "NL", "zrodlo": "RDW Open Data",
              "definicje": "nowe = pierwsza rejestracja i pierwsza tenaamstelling w NL w tym samym miesiącu; BEV = jedyne paliwo 'Elektriciteit'",
              "miesiace": {}, "log": [], "pliki": []}
    ytd = {}
    uzyte = []
    for m in range(1, ostatni + 1):
        sciezka = os.path.join(OUT, f"rdw_nl_bev_marki_{rok}-{m:02d}.csv")
        przelicz = (m >= ostatni - 1) or not os.path.exists(sciezka)
        if przelicz:
            try:
                licz, kontrola = policz_miesiac(rok, m, status["log"])
            except Exception as e:  # noqa: BLE001
                status["miesiace"][f"{rok}-{m:02d}"] = {"blad": str(e)[:300],
                                                       "uzyto_cache": os.path.exists(sciezka)}
                if not os.path.exists(sciezka):
                    continue
                licz = None
            if licz is not None:
                bev = kontrola["bev"]
                wiersze = sorted(((mk, n, round(n / bev, 4) if bev else 0)
                                  for mk, n in licz.items()), key=lambda x: -x[1])
                if kontrola["zgodnosc"] or not os.path.exists(sciezka):
                    sha = zapisz_csv(sciezka, ["marka", "bev", "udzial_bev"], wiersze)
                    status["pliki"].append({"plik": sciezka, "sha256": sha})
                else:  # niespójny przebieg nie nadpisuje poprawnego pliku
                    kontrola["nie_nadpisano"] = True
                status["miesiace"][f"{rok}-{m:02d}"] = kontrola
        else:
            status["miesiace"][f"{rok}-{m:02d}"] = {"z_cache": True}
        with open(sciezka, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ytd[r["marka"]] = ytd.get(r["marka"], 0) + int(r["bev"])
        uzyte.append(f"{rok}-{m:02d}")
    razem = sum(ytd.values())
    wiersze = sorted(((mk, n, round(n / razem, 4) if razem else 0, len(uzyte))
                      for mk, n in ytd.items()), key=lambda x: -x[1])
    sciezka = os.path.join(OUT, f"rdw_nl_bev_marki_ytd_{rok}.csv")
    sha = zapisz_csv(sciezka, ["marka", "bev_ytd", "udzial", "miesiace"], wiersze)
    status["pliki"].append({"plik": sciezka, "sha256": sha})
    status["ytd"] = {"rok": rok, "miesiace": uzyte, "bev_razem": razem,
                     "top10": [w[0] for w in wiersze[:10]]}
    with open(os.path.join(OUT, "status_rdw.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)
    bledy = [k for k, v in status["miesiace"].items() if "blad" in v]
    print(f"RDW NL: {len(uzyte)} mies., BEV YTD {razem}, błędy: {bledy}")
    return 0  # status czytać z status_rdw.json (continue-on-error w workflow)


if __name__ == "__main__":
    sys.exit(main())
