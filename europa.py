"""
europa.py - warstwa pobierania danych EUROPEJSKICH dla zadania "EV Polska - przeglad tygodniowy".

WERSJA 0.1 (2026-09-29) = SONDA. Zapisuje SUROWE pliki i ich tekst; parsery tabel powstana po obejrzeniu.
Zrodla:
- ACEA: miesieczny komunikat PDF o rejestracjach nowych samochodow (UE + EFTA + UK, per kraj, BEV).
  Wzor adresu zaobserwowany 29.09.2026 w wynikach wyszukiwania:
  https://www.acea.auto/files/Press_release_car_registrations_August_2026.pdf (i March_2026)
  -> skrypt probuje 3 ostatnich miesiecy wg tego wzoru; kod HTTP rozstrzyga, czy plik istnieje.
- KBA (Niemcy): strony komunikatow o nowych rejestracjach wg marek i napedow alternatywnych.
  Adresy startowe z wynikow wyszukiwania 29.09.2026; skrypt szuka na nich linkow do nowszych komunikatow.

Zasady jak w polska.py: zadnych liczb wpisanych recznie; kazdy plik z adresem, czasem UTC, kodem HTTP,
rozmiarem i sha256 w data/europa/status_europa.json; blad jednego zrodla nie przerywa reszty;
standardowe ustawienia bezpieczenstwa TLS; skrypt zawsze konczy sie kodem 0.
"""

import csv
import hashlib
import io
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

OUT = Path("data") / "europa"
UA = "ev-dane-pl/0.1-europa (+https://github.com/Kozienice/ev-dane-pl)"
TIMEOUT = 60
MIESIACE_EN = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

status = {
    "uruchomienie_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "wersja_skryptu": "europa-0.1-sonda",
    "zrodla": {},
}


def sesja():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def notuj(z, k, v):
    status["zrodla"].setdefault(z, {})[k] = v


def get(s, url, z):
    try:
        r = s.get(url, timeout=TIMEOUT)
        notuj(z, f"GET {url}", {"http": r.status_code, "bajty": len(r.content),
                                "content_type": r.headers.get("Content-Type")})
        return r
    except Exception as e:
        notuj(z, f"GET {url}", {"blad": f"{type(e).__name__}: {e}"[:500]})
        return None


def zapisz(sciezka: Path, tresc: bytes, z: str, url: str, kod: int):
    sciezka.parent.mkdir(parents=True, exist_ok=True)
    sciezka.write_bytes(tresc)
    status["zrodla"].setdefault(z, {}).setdefault("pliki", []).append({
        "plik": str(sciezka), "url": url, "http": kod, "bajty": len(tresc),
        "sha256": hashlib.sha256(tresc).hexdigest(),
        "pobrano_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


def ostatnie_miesiace(n=3):
    teraz = datetime.now(timezone.utc)
    r, m = teraz.year, teraz.month
    wynik = []
    for _ in range(n):
        m -= 1
        if m == 0:
            r, m = r - 1, 12
        wynik.append((r, m))
    return wynik


# ---------------------------------------------------------------- ACEA

def pdf_do_tekstu(tresc: bytes, baza: Path, z: str):
    """Tekst calego PDF + tabele z kazdej strony jako CSV (1:1, bez interpretacji)."""
    import pdfplumber
    baza.parent.mkdir(parents=True, exist_ok=True)
    try:
        teksty, n_tab = [], 0
        with pdfplumber.open(io.BytesIO(tresc)) as pdf:
            for i, strona in enumerate(pdf.pages, start=1):
                teksty.append(f"===== STRONA {i} =====\n{strona.extract_text() or ''}")
                for j, tab in enumerate(strona.extract_tables(), start=1):
                    p = baza.parent / f"{baza.name}_s{i}_t{j}.csv"
                    with p.open("w", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerows([["" if c is None else c for c in w] for w in tab])
                    n_tab += 1
        txt = "\n".join(teksty)
        baza.with_suffix(".txt").write_text(txt, encoding="utf-8")
        polska = [l for l in txt.splitlines() if re.search(r"\bPoland\b", l)]
        notuj(z, f"tekst_{baza.name}", {"strony": len(teksty), "tabele_csv": n_tab,
                                        "linie_z_Poland": polska[:10]})
    except Exception as e:
        notuj(z, f"blad_pdf_{baza.name}", f"{type(e).__name__}: {e}"[:500])


def acea():
    z = "acea"
    s = sesja()
    for r, m in ostatnie_miesiace(3):
        url = f"https://www.acea.auto/files/Press_release_car_registrations_{MIESIACE_EN[m-1]}_{r}.pdf"
        odp = get(s, url, z)
        if odp is not None and odp.status_code == 200 and odp.content[:4] == b"%PDF":
            baza = OUT / "acea" / f"acea_{r}-{m:02d}"
            zapisz(baza.with_suffix(".pdf"), odp.content, z, url, odp.status_code)
            pdf_do_tekstu(odp.content, baza, z)


# ---------------------------------------------------------------- KBA

KBA_START = [
    "https://www.kba.de/DE/Presse/Pressemitteilungen/Fahrzeugzulassungen/2026/pm35_2026_n_08_26_pm_komplett.html",
    "https://www.kba.de/DE/Presse/Pressemitteilungen/AlternativeAntriebe/2026/pm34_2026_Antriebe_07_26_komplett.html",
]


def html_tabele_do_csv(html: str, baza: Path, z: str):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    n = 0
    for i, t in enumerate(soup.find_all("table"), start=1):
        wiersze = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in t.find_all("tr")]
        if not wiersze:
            continue
        p = baza.parent / f"{baza.name}_t{i}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(wiersze)
        n += 1
    notuj(z, f"tabele_{baza.name}", n)


def kba():
    z = "kba"
    s = sesja()
    do_pobrania, widziane = list(KBA_START), set()
    # z pobranych stron dobierz linki do nowszych komunikatow tego samego typu (maks. 6 stron)
    while do_pobrania and len(widziane) < 6:
        url = do_pobrania.pop(0)
        if url in widziane:
            continue
        widziane.add(url)
        odp = get(s, url, z)
        if odp is None or odp.status_code != 200:
            continue
        nazwa = re.sub(r"[^\w\-]+", "_", url.rsplit("/", 1)[-1].replace(".html", ""))[:80]
        baza = OUT / "kba" / nazwa
        zapisz(baza.with_suffix(".html"), odp.content, z, url, odp.status_code)
        html_tabele_do_csv(odp.text, baza, z)
        for h in re.findall(r'href="([^"]+(?:Antriebe_|_n_)\d\d_\d\d[^"]*komplett\.html)"', odp.text):
            pelny = urljoin(url, h.split(";")[0])
            if pelny not in widziane and pelny not in do_pobrania:
                do_pobrania.append(pelny)
    notuj(z, "strony_odwiedzone", sorted(widziane))


# ---------------------------------------------------------------- main

def main():
    for nazwa, f in (("acea", acea), ("kba", kba)):
        try:
            f()
        except Exception:
            notuj(nazwa, "wyjatek", traceback.format_exc()[-1500:])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "status_europa.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2)[:5000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
