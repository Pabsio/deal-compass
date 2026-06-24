"""
VoyagesPirates.fr — Scraper Trivago + Airbnb
=============================================
Lógica:
  1. Abrir el feed de /vacances/hotels con Playwright
  2. Leer cada deal de la lista (título, URL, precio, fecha)
  3. Para cada deal, abrir la página individual y leer la URL del botón CTA
       → apunta a trivago.com  = fuente Trivago
       → apunta a airbnb.com   = fuente Airbnb
       → apunta a booking.com  = fuente Booking
  4. Filtrar por fuente y últimos N días
  5. Exportar CSV + JSON

Requisitos:
    pip install playwright
    playwright install chromium

Uso:
    python voyagespirates_scraper.py
    python voyagespirates_scraper.py --days 10 --sources trivago airbnb
    python voyagespirates_scraper.py --days 30 --sources airbnb --max 100
"""

import argparse
import asyncio
import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, TimeoutError as PWTimeout


FEED_URL  = "https://www.voyagespirates.fr/vacances/hotels"
BASE_URL  = "https://www.voyagespirates.fr"
PAGE_WAIT = 1.0   # segundos entre páginas individuales


def detect_source(cta_href: str) -> str:
    """Determina la fuente a partir del dominio de la URL del CTA."""
    if not cta_href:
        return "unknown"
    host = urlparse(cta_href).netloc.lower()
    if "airbnb"   in host: return "airbnb"
    if "trivago"  in host: return "trivago"
    if "booking"  in host: return "booking"
    if "expedia"  in host: return "expedia"
    if "hotels.com" in host: return "hotels.com"
    return "other"


@dataclass
class Deal:
    title:            str
    url:              str
    source:           str
    cta_href:         str
    published_date:   Optional[str]
    price_per_person: Optional[str]
    description:      str           = ""
    scraped_at:       str           = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self):
        return asdict(self)


async def get_deals_from_feed(page, max_deals: int) -> list[dict]:
    """
    Carga el feed /vacances/hotels y extrae los deals listados
    (título, URL relativa, precio, fecha visible).
    """
    print(f"  Cargando feed: {FEED_URL}")
    await page.goto(FEED_URL, wait_until="networkidle", timeout=30000)

    # Aceptar cookies
    try:
        btn = page.locator("button:has-text('Tout accepter'), button:has-text('Accepter')")
        if await btn.count() > 0:
            await btn.first.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    # Scroll para cargar más deals (lazy loading)
    for _ in range(5):
        await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
        await page.wait_for_timeout(800)

    # Extraer tarjetas de deals — buscar todos los links a /hotels/<slug>
    deals_raw = await page.evaluate("""() => {
        const results = [];
        const seen = new Set();

        // Buscar tarjetas de deal — cada una tiene un link a /hotels/...
        const cards = document.querySelectorAll('a[href*="/hotels/"]');
        for (const card of cards) {
            const href = card.getAttribute('href');
            if (!href || seen.has(href)) continue;
            if (href === '/hotels' || href === '/vacances/hotels') continue;
            if (!href.match(/\\/hotels\\/[a-z0-9\\-]+$/)) continue;
            seen.add(href);

            // Subir al contenedor padre para extraer más datos
            const container = card.closest('article') || card.closest('[class*="deal"]') || card.closest('[class*="card"]') || card;

            const titleEl = container.querySelector('h2, h3, h4, [class*="title"], [class*="Title"]');
            const priceEl = container.querySelector('[class*="price"], [class*="Price"], strong');
            const dateEl  = container.querySelector('time, [class*="date"], [class*="Date"]');

            results.push({
                href:  href,
                title: titleEl ? titleEl.innerText.trim() : card.innerText.trim().slice(0, 120),
                price: priceEl ? priceEl.innerText.trim() : '',
                date:  dateEl  ? (dateEl.getAttribute('datetime') || dateEl.innerText.trim()) : '',
            });

            if (results.length >= """ + str(max_deals) + """) break;
        }
        return results;
    }""")

    print(f"  → {len(deals_raw)} deals encontrados en el feed")
    return deals_raw


async def get_cta_from_deal_page(page, url: str) -> tuple[str, str, str, str]:
    """
    Abre la página individual del deal y extrae:
    - href del botón CTA principal
    - fecha de publicación (metadato)
    - descripción (og:description)
    - precio (si no se obtuvo del feed)
    Devuelve (cta_href, published_date, description, price)
    """
    full_url = url if url.startswith("http") else BASE_URL + url
    try:
        await page.goto(full_url, wait_until="networkidle", timeout=25000)
    except PWTimeout:
        return "", "", "", ""

    data = await page.evaluate("""() => {
        // 1. CTA: buscar el botón/link de reserva principal
        //    Suele ser el link más prominente con clase "cta", "button", "BookingButton"
        //    o el primer link externo que apunta a trivago/airbnb/booking
        let ctaHref = '';

        // Primero: buscar por data-attributes o clases específicas
        const ctaSelectors = [
            '[data-testid*="cta"]',
            '[class*="BookingButton"] a',
            '[class*="cta-button"]',
            '[class*="CTA"] a',
            'a[class*="button"][href*="trivago"]',
            'a[class*="button"][href*="airbnb"]',
            'a[class*="button"][href*="booking"]',
        ];
        for (const sel of ctaSelectors) {
            const el = document.querySelector(sel);
            if (el) { ctaHref = el.href || el.getAttribute('href') || ''; break; }
        }

        // Segundo: cualquier link externo a trivago, airbnb o booking
        if (!ctaHref) {
            const links = document.querySelectorAll('a[href]');
            for (const a of links) {
                const h = a.href || '';
                if (h.includes('trivago.com') || h.includes('airbnb.com') || h.includes('booking.com')) {
                    ctaHref = h;
                    break;
                }
            }
        }

        // 2. Fecha de publicación
        const pubMeta = document.querySelector(
            "meta[property='article:published_time'], meta[property='og:article:published_time']"
        );
        const pubDate = pubMeta ? pubMeta.content : '';

        // 3. Descripción
        const descMeta = document.querySelector("meta[property='og:description'], meta[name='description']");
        const desc = descMeta ? descMeta.content : '';

        // 4. Precio (búsqueda en texto si no vino del feed)
        const bodyText = document.body.innerText;
        const priceMatch = bodyText.match(/(\\d+[\\.,]?\\d*)\\s*€\\s*(?:p\\.p|par personne)/i)
                        || bodyText.match(/(?:à partir de|seulement|dès)\\s*(\\d+[\\.,]?\\d*)\\s*€/i)
                        || bodyText.match(/(\\d+)\\s*€\\s*par\\s*personne/i);
        const price = priceMatch ? priceMatch[1] + ' €' : '';

        return { ctaHref, pubDate, desc, price };
    }""")

    cta_href  = data.get("ctaHref", "")
    pub_date  = data.get("pubDate", "")
    desc      = data.get("desc", "")
    price     = data.get("price", "")

    # Normalizar fecha
    if pub_date:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', pub_date)
        pub_date = m.group(1) if m else ""

    return cta_href, pub_date, desc[:400], price


async def run_scraper(sources: list[str], days: int, max_deals: int) -> list[Deal]:
    cutoff = datetime.now() - timedelta(days=days)
    deals  = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
        )
        page = await context.new_page()

        # Paso 1: feed
        print("\n── PASO 1 · Feed de hôtels ────────────────────")
        feed_items = await get_deals_from_feed(page, max_deals)

        # Paso 2: página individual de cada deal
        print(f"\n── PASO 2 · Revisando {len(feed_items)} páginas ───────────")
        for i, item in enumerate(feed_items, 1):
            slug = item["href"].split("/")[-1][:55]
            print(f"  [{i:>3}/{len(feed_items)}] {slug}")

            cta_href, pub_date, desc, price_fallback = await get_cta_from_deal_page(page, item["href"])

            source = detect_source(cta_href)
            price  = item.get("price") or (price_fallback + " p.p." if price_fallback else "")

            print(f"         source={source:10} | cta={cta_href[:60]}")

            # Filtro por fuente
            if sources and source not in sources:
                continue

            # Filtro por fecha
            if pub_date:
                try:
                    if datetime.strptime(pub_date, "%Y-%m-%d") < cutoff:
                        print(f"         ↳ fuera de período ({pub_date}), saltando")
                        continue
                except ValueError:
                    pass

            deals.append(Deal(
                title=item.get("title", slug),
                url=BASE_URL + item["href"] if not item["href"].startswith("http") else item["href"],
                source=source,
                cta_href=cta_href,
                published_date=pub_date or None,
                price_per_person=price or None,
                description=desc,
            ))

            await asyncio.sleep(PAGE_WAIT)

        await browser.close()

    return deals


def export(deals: list[Deal], prefix: str):
    if not deals:
        print("  ⚠️  Sin resultados para exportar.")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path  = f"{prefix}_{ts}.csv"
    json_path = f"{prefix}_{ts}.json"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=deals[0].to_dict().keys())
        writer.writeheader()
        writer.writerows(d.to_dict() for d in deals)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(d) for d in deals], f, ensure_ascii=False, indent=2)

    print(f"  💾 CSV  → {csv_path}")
    print(f"  💾 JSON → {json_path}")


def main():
    parser = argparse.ArgumentParser(description="Scraper VoyagesPirates — Trivago + Airbnb")
    parser.add_argument("--days",    type=int,  default=10,
                        help="Días hacia atrás (default: 10)")
    parser.add_argument("--sources", nargs="+", default=["trivago", "airbnb"],
                        choices=["trivago", "airbnb", "booking", "expedia", "other"],
                        help="Fuentes a incluir (default: trivago airbnb)")
    parser.add_argument("--max",     type=int,  default=50,
                        help="Máximo de deals a revisar del feed (default: 50)")
    parser.add_argument("--output",  default="voyagespirates_deals",
                        help="Prefijo de los archivos de salida")
    args = parser.parse_args()

    print(f"\n🏴‍☠️  VoyagesPirates Scraper")
    print(f"   Fuentes : {', '.join(args.sources)}")
    print(f"   Período : últimos {args.days} días")
    print(f"   Max     : {args.max} deals del feed")

    deals = asyncio.run(run_scraper(
        sources=args.sources,
        days=args.days,
        max_deals=args.max,
    ))

    print(f"\n── RESULTADO ──────────────────────────────────")
    print(f"  ✅ {len(deals)} deals encontrados\n")
    for d in deals:
        print(f"  📌 {d.title}")
        print(f"     {d.source.upper()} · {d.published_date or 'sin fecha'} · {d.price_per_person or 'ver página'}")
        print(f"     {d.url}")
        print()

    print("── EXPORTANDO ─────────────────────────────────")
    export(deals, args.output)


if __name__ == "__main__":
    main()
