# HolidayPirates Deals Compass

Board interno para monitorizar ofertas de Trivago, Airbnb y Booking en los 10 mercados del grupo HolidayPirates.

## Stack

- **Scraper**: Python + Playwright (lee RSS feeds + extrae CTA con headless browser)
- **Board**: HTML estático generado automáticamente
- **CI**: GitHub Actions (cada 3h entre 08:00-23:00 CET)
- **Hosting**: Netlify (autodeploy desde `main`)

## Setup

### 1. GitHub

```bash
git clone https://github.com/TU_ORG/deals-compass
cd deals-compass
```

En **Settings → Actions → General** asegúrate de que _Workflow permissions_ tiene permiso de escritura (`Read and write permissions`).

### 2. Netlify

1. New site → Import from GitHub → selecciona este repo
2. Build command: `echo ok`
3. Publish directory: `.` (raíz)
4. Deploy

Netlify se redesplegará automáticamente cada vez que GitHub Actions haga push del board actualizado.

### 3. Ejecución local

```bash
pip install playwright requests
python -m playwright install chromium

# Scrapear todos los mercados
python scraper.py

# Scrapear solo España, solo Trivago y Airbnb
python scraper.py --sites viajerospiratas --sources trivago airbnb

# Generar y abrir el board
python build_board.py
```

## Archivos

| Archivo | Descripción |
|---|---|
| `scraper.py` | Scraper principal (RSS + Playwright) |
| `build_board.py` | Genera `holidaypirates_board.html` desde la DB |
| `board_template.html` | Template del board (sin datos) |
| `holidaypirates_board.html` | Board generado (actualizado por CI) |
| `netlify.toml` | Configuración de Netlify |
| `.github/workflows/scrape.yml` | GitHub Actions workflow |
