"""
HolidayPirates Group — Scraper multi-sitio (RSS + Playwright)
=============================================================
Estrategia:
  1. Leer RSS feed (/feed) de cada sitio → título, fecha, imagen, descripción
  2. Filtrar solo items de /hotels/ (o /hoteles/ para ES)
  3. Para cada deal, abrir con Playwright y leer la URL del botón CTA
     (clase hp_22_cta_button) → detectar trivago / airbnb / booking
  4. Guardar en SQLite

Requisitos:
    pip3 install playwright requests
    python3 -m playwright install chromium

Uso:
    python3 holidaypirates_scraper.py
    python3 holidaypirates_scraper.py --sites viajerospiratas --max 50
    python3 holidaypirates_scraper.py --sources trivago airbnb --days 7
"""

import argparse
import asyncio
import csv
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from playwright.async_api import async_playwright, TimeoutError as PWTimeout


# ──────────────────────────────────────────────────────────
# Configuración de los 10 sitios
# ──────────────────────────────────────────────────────────

SITES = {
    "urlaubspiraten_de": {
        "name":        "Urlaubspiraten (DE)",
        "base_url":    "https://www.urlaubspiraten.de",
        "feed_url":    "https://www.urlaubspiraten.de/feed",
        "deal_path":   "/hotels/",
        "locale":      "de-DE",
    },
    "urlaubspiraten_at": {
        "name":        "Urlaubspiraten (AT)",
        "base_url":    "https://www.urlaubspiraten.at",
        "feed_url":    "https://www.urlaubspiraten.at/feed",
        "deal_path":   "/hotels/",
        "locale":      "de-AT",
    },
    "ferienpiraten": {
        "name":        "Ferienpiraten (CH)",
        "base_url":    "https://www.ferienpiraten.ch",
        "feed_url":    "https://www.ferienpiraten.ch/feed",
        "deal_path":   "/hotels/",
        "locale":      "de-CH",
    },
    "voyagespirates": {
        "name":        "VoyagesPirates (FR)",
        "base_url":    "https://www.voyagespirates.fr",
        "feed_url":    "https://www.voyagespirates.fr/feed",
        "deal_path":   "/hotels/",
        "locale":      "fr-FR",
    },
    "piratinviaggio": {
        "name":        "PiratinViaggio (IT)",
        "base_url":    "https://www.piratinviaggio.it",
        "feed_url":    "https://www.piratinviaggio.it/feed",
        "deal_path":   "/hotel/",          # ← italiano singular
        "locale":      "it-IT",
    },
    "viajerospiratas": {
        "name":        "ViajerosPiratas (ES)",
        "base_url":    "https://www.viajerospiratas.es",
        "feed_url":    "https://www.viajerospiratas.es/feed",
        "deal_path":   "/hoteles/",          # ← español
        "locale":      "es-ES",
    },
    "holidaypirates": {
        "name":        "HolidayPirates (UK)",
        "base_url":    "https://www.holidaypirates.com",
        "feed_url":    "https://www.holidaypirates.com/feed",
        "deal_path":   "/hotels/",
        "locale":      "en-GB",
    },
    "travelpirates": {
        "name":        "TravelPirates (US)",
        "base_url":    "https://www.travelpirates.com",
        "feed_url":    "https://www.travelpirates.com/feed",
        "deal_path":   "/hotels/",
        "locale":      "en-US",
    },
    "vakantiepiraten": {
        "name":        "VakantiePiraten (NL)",
        "base_url":    "https://www.vakantiepiraten.nl",
        "feed_url":    "https://www.vakantiepiraten.nl/feed",
        "deal_path":   "/hotels/",
        "locale":      "nl-NL",
    },
    "wakacyjnipiraci": {
        "name":        "WakacyjniPiraci (PL)",
        "base_url":    "https://www.wakacyjnipiraci.pl",
        "feed_url":    "https://www.wakacyjnipiraci.pl/feed",
        "deal_path":   "/hotele/",         # ← polaco plural
        "locale":      "pl-PL",
    },
}

DB_FILE    = "holidaypirates_deals.db"
PAGE_WAIT  = 1.0   # segundos entre páginas en Playwright
RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HolidayPiratesBot/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

ALLOWED_SOURCES = {"trivago", "airbnb", "booking"}

PLATFORM_MAP = {
    "airbnb":   ["airbnb.com", "airbnb.", "7eer.net/c/462462"],
    "trivago":  ["trivago."],
    "booking":  ["booking.com"],
    "expedia":  ["expedia."],
    "logitravel": ["logitravel."],
    "travelcircus": ["travelcircus."],
}

CONTENTFUL_SPACE = "24j3e0idq43b"


# ──────────────────────────────────────────────────────────
# SQLite
# ──────────────────────────────────────────────────────────

def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            site_key         TEXT NOT NULL,
            site_name        TEXT NOT NULL,
            title            TEXT,
            url              TEXT UNIQUE,
            source           TEXT,
            cta_href         TEXT,
            contentful_id    TEXT,
            contentful_url   TEXT,
            published_date   TEXT,
            image_url        TEXT,
            description      TEXT,
            scraped_at       TEXT
        )
    """)
    # Migraciones por si la tabla ya existía sin las columnas nuevas
    for col, typedef in [
        ("contentful_id",  "TEXT"),
        ("contentful_url", "TEXT"),
        ("image_url",      "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE deals ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON deals(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_site   ON deals(site_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date   ON deals(published_date)")
    conn.commit()
    return conn


def upsert(conn: sqlite3.Connection, row: dict):
    conn.execute("""
        INSERT INTO deals
            (site_key, site_name, title, url, source, cta_href,
             contentful_id, contentful_url,
             published_date, image_url, description, scraped_at)
        VALUES
            (:site_key, :site_name, :title, :url, :source, :cta_href,
             :contentful_id, :contentful_url,
             :published_date, :image_url, :description, :scraped_at)
        ON CONFLICT(url) DO UPDATE SET
            source         = excluded.source,
            cta_href       = excluded.cta_href,
            contentful_id  = excluded.contentful_id,
            contentful_url = excluded.contentful_url,
            published_date = COALESCE(deals.published_date, excluded.published_date),
            image_url      = COALESCE(excluded.image_url, deals.image_url),
            description    = COALESCE(excluded.description, deals.description),
            scraped_at     = excluded.scraped_at
    """, row)
    conn.commit()


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────

def detect_source(href: str) -> str:
    if not href:
        return "unknown"
    href_lower = href.lower()
    for name, domains in PLATFORM_MAP.items():
        if any(d in href_lower for d in domains):
            return name
    return "other"


def extract_contentful_id(cta_href: str) -> Optional[str]:
    """Extrae el ID de Contentful del parámetro cip_tc de la URL de Trivago.
    El ID es la parte alfanumérica que tiene dígitos Y letras mezcladas, >= 15 chars.
    Ejemplos:
      Hotel_7cwyn97XQc4r5Bm9kLarFd        → 7cwyn97XQc4r5Bm9kLarFd
      CDSHotels_Sizilien_18I0F0xMnkZb...  → 18I0F0xMnkZb...
      3kYS5MOyS9Af0v_EstesPark_Stanley    → 3kYS5MOyS9Af0v
    """
    if not cta_href:
        return None
    m = re.search(r'cip_tc=([^&]+)', cta_href)
    if not m:
        return None
    raw = m.group(1)
    parts = raw.split('_')
    for part in parts:
        if (len(part) >= 15
                and re.search(r'[0-9]', part)
                and re.search(r'[a-z]', part)
                and re.search(r'[A-Z]', part)):
            return part
    return None


def contentful_url(cid: str) -> Optional[str]:
    if not cid:
        return None
    return f"https://app.contentful.com/spaces/{CONTENTFUL_SPACE}/entries/{cid}"


# ──────────────────────────────────────────────────────────
# 1. Leer RSS feed
# ──────────────────────────────────────────────────────────

def fetch_rss(site: dict) -> list[dict]:
    """Lee el feed RSS y devuelve todos los items de /hotels/."""
    deal_path = site["deal_path"]

    print(f"    📡 RSS: {site['feed_url']}")
    try:
        r = requests.get(site["feed_url"], headers=RSS_HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"    ⚠️  Error RSS: {e}")
        return []

    try:
        root = ET.fromstring(r.content)
    except ET.ParseError as e:
        print(f"    ⚠️  Error XML: {e}")
        return []

    items = []
    for item in root.iter("item"):
        link  = item.findtext("link", "").strip()
        title = item.findtext("title", "").strip()
        desc  = item.findtext("description", "").strip()
        pub   = item.findtext("pubDate", "").strip()

        # Solo deals de /hotels/ (o /hoteles/)
        if deal_path not in link:
            continue

        # Imagen del enclosure
        enclosure = item.find("enclosure")
        image_url = enclosure.get("url", "") if enclosure is not None else ""

        # Parsear fecha
        pub_date = None
        if pub:
            try:
                pub_date = parsedate_to_datetime(pub).strftime("%Y-%m-%d")
            except Exception:
                pass

        items.append({
            "url":            link,
            "title":          title,
            "description":    desc[:400],
            "published_date": pub_date,
            "image_url":      image_url,
        })

    print(f"    → {len(items)} deals en /hotels/ en el feed")
    return items


# ──────────────────────────────────────────────────────────
# 2. Extraer CTA con Playwright
# ──────────────────────────────────────────────────────────

async def get_cta(page, url: str) -> str:
    """Abre la página del deal y extrae la URL del botón CTA principal."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=25000)
    except PWTimeout:
        # Intentar con domcontentloaded si networkidle falla
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)
        except Exception:
            return ""
    except Exception:
        return ""

    cta_href = await page.evaluate(r"""() => {
        const PLATFORMS = [
            'trivago.', 'airbnb.com', 'airbnb.', '7eer.net',
            'booking.com', 'expedia.', 'logitravel.', 'travelcircus.'
        ];

        // 1. Botón CTA principal por clase estable del grupo
        const ctaBtn = document.querySelector('a.hp_22_cta_button, [class*="hp_22_cta"]');
        if (ctaBtn && ctaBtn.href) return ctaBtn.href;

        // 2. Cualquier link externo a plataformas conocidas
        for (const a of document.querySelectorAll('a[href]')) {
            const h = a.href || '';
            if (PLATFORMS.some(p => h.includes(p))) return h;
        }
        return '';
    }""")

    return cta_href or ""


# ──────────────────────────────────────────────────────────
# 3. Pipeline por sitio
# ──────────────────────────────────────────────────────────

async def scrape_site(site_key: str, site: dict, sources: list,
                      conn: sqlite3.Connection) -> int:
    print(f"\n  🏴‍☠️  {site['name']}")

    # Paso 1: RSS
    rss_items = fetch_rss(site)
    if not rss_items:
        return 0

    saved = skipped = 0

    # Paso 2: Playwright para los CTAs
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale=site["locale"],
        )
        page = await context.new_page()

        for i, item in enumerate(rss_items, 1):
            slug = item["url"].split("/")[-1][:55]
            print(f"    [{i:>3}/{len(rss_items)}] {slug}")

            cta_href = await get_cta(page, item["url"])
            source   = detect_source(cta_href)

            # Contentful ID (solo para Trivago)
            cid = extract_contentful_id(cta_href) if source == "trivago" else None
            ctf_url = contentful_url(cid)

            print(f"           {source:12} │ {cta_href[:60]}")

            # Filtrar por fuente si se especificó
            if sources and source not in sources:
                skipped += 1
                await asyncio.sleep(PAGE_WAIT)
                continue

            upsert(conn, {
                "site_key":       site_key,
                "site_name":      site["name"],
                "title":          item["title"],
                "url":            item["url"],
                "source":         source,
                "cta_href":       cta_href or None,
                "contentful_id":  cid,
                "contentful_url": ctf_url,
                "published_date": item["published_date"],
                "image_url":      item["image_url"] or None,
                "description":    item["description"],
                "scraped_at":     datetime.now().isoformat(),
            })
            saved += 1
            await asyncio.sleep(PAGE_WAIT)

        await browser.close()

    print(f"    ✅ {saved} guardados · {skipped} descartados")
    return saved


# ──────────────────────────────────────────────────────────
# 4. Exportar CSV
# ──────────────────────────────────────────────────────────

def export_csv(conn: sqlite3.Connection, output_dir: str):
    Path(output_dir).mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(deals)")]

    rows = conn.execute("SELECT * FROM deals ORDER BY published_date DESC").fetchall()
    if rows:
        path = f"{output_dir}/all_deals_{ts}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            import csv as csv_mod
            w = csv_mod.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        print(f"  💾 CSV → {path}  ({len(rows)} filas)")


# ──────────────────────────────────────────────────────────
# 5. Main
# ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HolidayPirates Group — Scraper RSS + Playwright"
    )
    parser.add_argument("--sources", nargs="+", default=[],
                        help="Fuentes a incluir: trivago airbnb booking (default: todas)")
    parser.add_argument("--sites",   nargs="+", default=list(SITES.keys()),
                        choices=list(SITES.keys()),
                        help="Sitios a scrapear (default: todos)")
    parser.add_argument("--db",      default=DB_FILE,
                        help=f"Archivo SQLite (default: {DB_FILE})")
    parser.add_argument("--csv-dir", default="output",
                        help="Carpeta para CSVs (default: output/)")
    args = parser.parse_args()

    print(f"\n🏴‍☠️  HolidayPirates Group Scraper  (RSS + Playwright)")
    print(f"   Sitios  : {', '.join(args.sites)}")
    print(f"   Fuentes : {', '.join(args.sources) if args.sources else 'todas'}")
    print(f"   DB      : {args.db}\n")

    conn  = init_db(args.db)
    total = 0

    for key in args.sites:
        saved = asyncio.run(scrape_site(
            site_key=key,
            site=SITES[key],
            sources=args.sources,
            conn=conn,
        ))
        total += saved

    # Resumen
    print(f"\n{'─'*50}")
    print(f"  Total guardados : {total}")
    print(f"\n  Por fuente:")
    for row in conn.execute("SELECT source, COUNT(*) n FROM deals GROUP BY source ORDER BY n DESC"):
        print(f"    {row[0]:<15} {row[1]}")
    print(f"\n  Por sitio:")
    for row in conn.execute("SELECT site_name, COUNT(*) n FROM deals GROUP BY site_name ORDER BY n DESC"):
        print(f"    {row[0]:<30} {row[1]}")

    print(f"\n── Exportando CSV ─────────────────────────────")
    export_csv(conn, args.csv_dir)
    conn.close()
    print(f"\n✅ Listo. DB: {args.db}")


if __name__ == "__main__":
    main()
