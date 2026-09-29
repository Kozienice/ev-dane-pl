"""
pobierz.py - warstwa pobierania danych dla zadania "EV Polska - przeglad tygodniowy".

WERSJA 0.1 = PRZEBIEG ROZPOZNAWCZY (sonda).
Cel: sprawdzic, co faktycznie zwracaja zrodla z serwerow GitHuba, i zapisac SUROWE odpowiedzi.
Parsery (tabela marek PZPM, agregacja CEPiK) powstana dopiero po obejrzeniu surowych plikow.

Zasady:
- zadnych liczb wpisanych recznie; wszystko, co trafia do data/, pochodzi z odpowiedzi zrodla
- kazdy zapisany plik ma w data/status.json: adres, czas pobrania UTC, kod HTTP, rozmiar, sha256
- blad jednego zrodla nie przerywa pozostalych; skrypt zawsze konczy sie zapisem status.json
- adresy endpointow CEPiK ponizej to HIPOTEZY do sprawdzenia (nie potwierdzone dokumentacja)
- polaczenia wylacznie ze standardowymi ustawieniami bezpieczenstwa TLS (bez obnizania poziomu)
"""

import hashlib
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

DATA = Path("data")
RAW = DATA / "raw"
CSV = DATA / "csv"
UA = "ev-dane-pl/0.1 (+https://github.com/Kozienice/ev-dane-pl)"
TIMEOUT = 60

status = {
    "uruchomienie_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "wersja_skryptu": "0.1-sonda",
    "zrodla": {},
}


# ---------------------------------------------------------------- narzedzia

def sesja():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def zapisz(sciezka: Path, tresc: bytes, zrodlo: str, url: str, kod: int):
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_bytes(tresc)
    status["zrodla"].setdefault(zrodlo, {}).setdefault("pliki", []).append({
        "plik": str(sciezka),
        "url": url,
        "http": kod,
        "bajty": len(tresc),
        "sha256": hashlib.sha256(tresc).hexdigest(),
        "pobrano_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


def notuj(zrodlo: str, klucz: str, wartosc):
    status["zrodla"].setdefault(zrodlo, {})[klucz] = wartosc


def get(s, url, zrodlo, **kw):
    try:
        r = s.get(url, timeout=TIMEOUT, **kw)
        notuj(zrodlo, f"GET {url}", {"http": r.status_code, "bajty": len(r.content),
                                     "content_type": r.headers.get("Content-Type")})
        return r
    except Exception as e:
        notuj(zrodlo, f"GET {url}", {"blad": f"{type(e).__name__}: {e}"[:500]})
        return None


# ---------------------------------------------------------------- PZPM eRejestracje

MIESIACE = ["STYCZEN", "LUTY", "MARZEC", "KWIECIEN", "MAJ", "CZERWIEC",
            "LIPIEC", "SIERPIEN", "WRZESIEN", "PAZDZIERNIK", "LISTOPAD", "GRUDZIEN"]


def pzpm():
    z = "pzpm"
    s = sesja()
    indeks = "https://www1.pzpm.org.pl/pl/Elektromobilnosc/eRejestracje"
    r = get(s, indeks, z)
    if r is None or r.status_code != 200:
        return
    zapisz(RAW / "pzpm" / "indeks_eRejestracje.html", r.content, z, indeks, r.status_code)

    # linki do stron miesiecznych: slug zawiera nazwe miesiaca i rok (bywaja literowki, np. STYCZEN-20262)
    linki = set(re.findall(r'href="([^"]*eRejestracje/[^"]*)"', r.text))
    rok = datetime.now(timezone.utc).year
    miesieczne = []
    for href in linki:
        up = href.upper()
        for i, m in enumerate(MIESIACE, start=1):
            if f"/{m}-{rok}" in up:
                miesieczne.append((i, urljoin(indeks + "/", href)))
    miesieczne.sort()
    notuj(z, "strony_miesieczne_znalezione", [u for _, u in miesieczne])
    if not miesieczne:
        return

    # najnowszy miesiac + jeden wczesniejszy (kontrola spojnosci YTD)
    for nr, url in miesieczne[-2:]:
        rs = get(s, url, z)
        if rs is None or rs.status_code != 200:
            continue
        xlsx = [urljoin(url, h) for h in re.findall(r'href="([^"]+\.xlsx)"', rs.text)]
        pdf = [urljoin(url, h) for h in re.findall(r'href="([^"]+\.pdf)"', rs.text)]
        notuj(z, f"pliki_{rok}-{nr:02d}", {"xlsx": xlsx, "pdf": pdf})
        for u in xlsx:
            rx = get(s, u, z)
            if rx is not None and rx.status_code == 200:
                p = RAW / "pzpm" / f"{rok}-{nr:02d}_tabele.xlsx"
                zapisz(p, rx.content, z, u, rx.status_code)
                xlsx_do_csv(p, CSV / "pzpm" / f"{rok}-{nr:02d}", z)
        for u in pdf:
            if "informacja" in u.lower():
                rp = get(s, u, z)
                if rp is not None and rp.status_code == 200:
                    zapisz(RAW / "pzpm" / f"{rok}-{nr:02d}_informacja.pdf", rp.content, z, u, rp.status_code)


def xlsx_do_csv(sciezka: Path, katalog: Path, z: str):
    """Zrzut KAZDEGO arkusza do CSV 1:1 (bez interpretacji) - parser marek powstanie po obejrzeniu."""
    import csv
    from openpyxl import load_workbook
    try:
        wb = load_workbook(sciezka, data_only=True, read_only=True)
        katalog.mkdir(parents=True, exist_ok=True)
        arkusze = []
        for ws in wb.worksheets:
            nazwa = re.sub(r"[^\w\-]+", "_", ws.title)[:60]
            out = katalog / f"{nazwa}.csv"
            with out.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                n = 0
                for row in ws.iter_rows(values_only=True):
                    w.writerow(["" if v is None else v for v in row])
                    n += 1
            arkusze.append({"arkusz": ws.title, "wiersze": n, "plik": str(out)})
        notuj(z, f"arkusze_{sciezka.stem}", arkusze)
    except Exception as e:
        notuj(z, f"blad_xlsx_{sciezka.stem}", f"{type(e).__name__}: {e}"[:500])


# ---------------------------------------------------------------- CEPiK API (sonda)

def cepik():
    z = "cepik"
    baza = "https://api.cepik.gov.pl"
    # standardowe TLS; znany blad 27.09 z innej infrastruktury: DH_KEY_TOO_SMALL -> sonda to potwierdzi lub obali
    s = sesja()
    r = get(s, baza + "/slowniki", z)
    if r is None:
        return
    zapisz(RAW / "cepik" / "slowniki.json", r.content, z, baza + "/slowniki", r.status_code)

    # HIPOTEZY endpointow - sonda zapisuje odpowiedz, cokolwiek przyjdzie
    proby = {
        "slownik_rodzaj_paliwa": "/slowniki/rodzaj-paliwa",
        "slownik_wojewodztwa": "/slowniki/wojewodztwa",
        "pojazdy_probka": "/pojazdy?wojewodztwo=14&data-od=20260801&data-do=20260807"
                          "&typ-daty=1&tylko-zarejestrowane=true&pokaz-wszystkie-pola=true&limit=5&page=1",
    }
    for nazwa, sciezka in proby.items():
        rr = get(s, baza + sciezka, z)
        if rr is not None:
            zapisz(RAW / "cepik" / f"{nazwa}.json", rr.content[:2_000_000], z, baza + sciezka, rr.status_code)


# ---------------------------------------------------------------- CPCA (test dostepu)

def cpca():
    z = "cpca"
    s = sesja()
    for url in ("http://www.cpcaauto.com/", "https://www.cpcaauto.com/"):
        r = get(s, url, z)
        if r is not None and r.status_code == 200:
            zapisz(RAW / "cpca" / "strona_glowna.html", r.content, z, url, r.status_code)
            break


# ---------------------------------------------------------------- main

def main():
    for nazwa, f in (("pzpm", pzpm), ("cepik", cepik), ("cpca", cpca)):
        try:
            f()
        except Exception:
            notuj(nazwa, "wyjatek", traceback.format_exc()[-1500:])
    DATA.mkdir(exist_ok=True)
    (DATA / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
