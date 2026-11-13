"""
europa.py - warstwa pobierania danych EUROPEJSKICH dla zadania "EV Polska - przeglad tygodniowy".

WERSJA 0.2 (2026-11-13)
- KBA: koniec z adresami zaszytymi na sztywno (pm35 sierpien / pm34 lipiec - starsze komunikaty nie linkuja
  do nowszych, wiec od wrzesnia nic nie przybywalo). Teraz dla 3 ostatnich miesiecy:
  (1) strona statystyki 'Monatliche Neuzulassungen' z filtrem rok/miesiac -> linki do FZ10 i komunikatu prasowego,
  (2) gdy linku brak: wprost plik FZ10 wg wzoru fz10_RRRR_MM.xlsx (kod HTTP rozstrzyga).
      Pomiar 13.11.2026 (przebieg na GitHubie): strona z filtrem zwraca 200, ale bez linkow (tresc dociagana
      skryptem); wzor bezposredni dziala dla 08, 09 i 10.2026 -> to on jest dzis droga glowna.
  FZ10 ma kolumny wg napedu (m.in. elektryczne), wiec daje tez rozbicie BEV per marka i model.
  FZ10 = Pkw nach Marken und Modellreihen -> zrzut arkuszy do CSV + wyciag wierszy obserwowanych marek
  do data/europa/kba/kba_marki_obserwowane_<okres>.csv.
  Nie uzywamy wyszukiwarki komunikatow KBA (SiteGlobals/Forms/...): robots.txt jej zabrania.

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
from urllib.parse import urlencode, urljoin

import requests

OUT = Path("data") / "europa"
UA = "ev-dane-pl/0.2-europa (+https://github.com/Kozienice/ev-dane-pl)"
TIMEOUT = 60
MIESIACE_EN = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
MIESIACE_DE = ["Januar", "Februar", "März", "April", "Mai", "Juni",
               "Juli", "August", "September", "Oktober", "November", "Dezember"]

# marki wyciagane z FZ10 (KBA); dopisz kolejna marke tutaj
MARKI_OBSERWOWANE = ["XPENG", "BYD", "LEAPMOTOR", "MG", "OMODA", "JAECOO", "CHERY", "ZEEKR",
                     "GEELY", "LYNK & CO", "NIO", "POLESTAR", "TESLA"]

status = {
    "uruchomienie_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "wersja_skryptu": "europa-0.2",
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

KBA_BAZA = "https://www.kba.de"
KBA_MONATLICH = KBA_BAZA + "/DE/Statistik/Fahrzeuge/Neuzulassungen/MonatlicheNeuzulassungen/monatl_neuzulassungen_node.html"
KBA_FZ10 = KBA_BAZA + "/SharedDocs/Downloads/DE/Statistik/Fahrzeuge/FZ10/fz10_{r}_{m:02d}.xlsx?__blob=publicationFile"


def czysty_href(h: str, baza: str) -> str:
    """&amp; -> &, bez ';jsessionid', bez kotwicy; parametry ?nn=... odciete dla stron HTML (nie dla plikow)."""
    h = h.replace("&amp;", "&").split(";")[0].split("#")[0]
    if ".html" in h:
        h = h.split("?")[0]
    return urljoin(baza, h)


def linki_kba(html: str, r: int, m: int):
    """Z HTML wybiera linki TEGO miesiaca: FZ10 (fz10_RRRR_MM.xlsx), komunikat o rejestracjach
    (pmNN_RRRR_n_MM_RR_pm_komplett.html) i komunikat o napedach alternatywnych (..._Antriebe_MM_RR_komplett.html)."""
    rr, mm = f"{r % 100:02d}", f"{m:02d}"
    wynik = {"fz10": [], "komunikaty": []}
    for h in re.findall(r'href="([^"]+)"', html):
        if re.search(rf"fz10_{r}_{mm}\.xlsx", h, re.IGNORECASE):
            wynik["fz10"].append(czysty_href(h, KBA_BAZA))
        elif re.search(rf"pm\d+_{r}_n_{mm}_{rr}_pm_komplett\.html|_Antriebe_{mm}_{rr}_komplett\.html", h):
            wynik["komunikaty"].append(czysty_href(h, KBA_BAZA))
    return {k: sorted(set(v)) for k, v in wynik.items()}


def xlsx_do_csv(sciezka: Path, katalog: Path, z: str):
    """Zrzut KAZDEGO arkusza do CSV 1:1 (bez interpretacji)."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(sciezka, data_only=True, read_only=True)
        arkusze = []
        for ws in wb.worksheets:
            nazwa = re.sub(r"[^\w\-]+", "_", ws.title)[:60]
            wiersze = [["" if v is None else v for v in row] for row in ws.iter_rows(values_only=True)]
            out = katalog / f"{nazwa}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(wiersze)
            arkusze.append({"arkusz": ws.title, "wiersze": len(wiersze), "plik": str(out)})
        notuj(z, f"arkusze_{sciezka.stem}", arkusze)
    except Exception as e:
        notuj(z, f"blad_xlsx_{sciezka.stem}", f"{type(e).__name__}: {e}"[:500])


def wiersze_marek(sciezka: Path, marki):
    """Jak w polska.py: wiersze z komorka = marka (albo marka + spacja + dalszy tekst), 1:1,
    z nazwa arkusza, numerem wiersza i najblizszym naglowkiem ('Marke'/'Marka')."""
    from openpyxl import load_workbook
    wzory = [(mk, re.compile(rf"^{re.escape(mk)}(\s|$)")) for mk in marki]
    wb = load_workbook(sciezka, data_only=True, read_only=True)
    wynik = []
    for ws in wb.worksheets:
        naglowek = ""
        for nr, row in enumerate(ws.iter_rows(values_only=True), start=1):
            komorki = ["" if c is None else c for c in row]
            teksty = [str(c).strip() for c in komorki if isinstance(c, str) and c.strip()]
            if any(t.upper() in ("MARKA", "MARKE", "MARKI") for t in teksty):
                naglowek = " | ".join(str(c).strip() for c in komorki if str(c).strip())
                continue
            trafienie = next(((i, mk) for i, c in enumerate(komorki, start=1) if isinstance(c, str)
                              for mk, w in wzory if w.match(c.strip().upper())), None)
            if trafienie:
                # kolumna_marki: arkusz moze miec kilka rankingow obok siebie (np. miesiac | narastajaco)
                wynik.append([ws.title, nr, trafienie[1], trafienie[0], naglowek] + komorki)
    return wynik


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
    for r, m in ostatnie_miesiace(3):
        okres = f"{r}-{m:02d}"
        # (1) strona statystyki z filtrem miesiaca -> linki FZ10 i komunikatow tego miesiaca
        filtr = urlencode({"yearFilter": r, "monthFilter": f"{m:02d}_{MIESIACE_DE[m - 1]}"})
        odp = get(s, f"{KBA_MONATLICH}?{filtr}", z)
        linki = linki_kba(odp.text, r, m) if odp is not None and odp.status_code == 200 else {"fz10": [], "komunikaty": []}
        notuj(z, f"linki_{okres}", linki)

        # (2) FZ10: link ze strony, a gdy go brak - wprost wg wzoru (kod HTTP rozstrzyga)
        for url in linki["fz10"] + [KBA_FZ10.format(r=r, m=m)]:
            rx = get(s, url, z)
            if rx is not None and rx.status_code == 200 and rx.content[:2] == b"PK":
                p = OUT / "kba" / f"fz10_{okres}.xlsx"
                zapisz(p, rx.content, z, url, rx.status_code)
                xlsx_do_csv(p, OUT / "kba" / f"fz10_{okres}", z)
                try:
                    wiersze = wiersze_marek(p, MARKI_OBSERWOWANE)
                    szer = max((len(w) for w in wiersze), default=5)
                    p_csv = OUT / "kba" / f"kba_marki_obserwowane_{okres}.csv"
                    with p_csv.open("w", newline="", encoding="utf-8") as f:
                        w = csv.writer(f)
                        w.writerow(["arkusz", "wiersz", "marka", "kolumna_marki", "naglowek"] + [f"k{i}" for i in range(1, szer - 4)])
                        w.writerows(x + [""] * (szer - len(x)) for x in wiersze)
                    notuj(z, f"marki_{okres}", {mk: sum(1 for x in wiersze if x[2] == mk) for mk in MARKI_OBSERWOWANE})
                except Exception:
                    notuj(z, f"blad_wyciagu_{okres}", traceback.format_exc()[-1500:])
                break

        # (3) komunikaty prasowe tego miesiaca (tabele HTML 1:1, jak w wersji 0.1)
        for url in linki["komunikaty"]:
            odp = get(s, url, z)
            if odp is None or odp.status_code != 200:
                continue
            nazwa = re.sub(r"[^\w\-]+", "_", url.rsplit("/", 1)[-1].replace(".html", ""))[:80]
            baza = OUT / "kba" / nazwa
            zapisz(baza.with_suffix(".html"), odp.content, z, url, odp.status_code)
            html_tabele_do_csv(odp.text, baza, z)


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
