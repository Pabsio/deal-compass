"""
build_board.py — Lee la DB y genera el board HTML
Uso: python build_board.py
     python build_board.py --no-open   (sin abrir el navegador, para CI)
     python build_board.py --db holidaypirates_deals.db
"""
import argparse, json, re, sqlite3, webbrowser
from pathlib import Path

HERE     = Path(__file__).parent
TEMPLATE = HERE / "board_template.html"
DB_DEFAULT = HERE / "holidaypirates_deals.db"
OUTPUT   = HERE / "holidaypirates_board.html"

def load_deals(db_path):
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(deals)")]
    rows = conn.execute("SELECT * FROM deals ORDER BY published_date DESC, id DESC").fetchall()
    conn.close()
    deals = []
    for r in rows:
        d = dict(zip(cols, r))
        t = d["title"] or ""
        t = re.sub(r"^[A-ZÁÉÍÓÚÀÈÙÂÊÎÔÛÄËÏÖÜ\s]{3,20}\n", "", t)
        t = t.replace("\xa0", " ").replace("\n", " — ").strip()
        d["title"] = t[:200]
        d["description"] = (d["description"] or "").replace("\xa0", " ").strip()[:300]
        for k in ["image_url", "contentful_id", "contentful_url"]:
            d.setdefault(k, None)
        deals.append(d)
    return deals

def build_board(deals):
    html = TEMPLATE.read_text(encoding="utf-8")
    deals_json = json.dumps(deals, ensure_ascii=False, default=str)
    lines = html.split("\n")
    result = "\n".join([
        f"const DEALS = {deals_json};"
        if l.strip().startswith("const DEALS =") else l
        for l in lines
    ])
    OUTPUT.write_text(result, encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"❌ DB no encontrada: {db}")
        return

    deals = load_deals(db)
    print(f"📦 {len(deals)} deals cargados")
    build_board(deals)
    print(f"✅ Board generado: {OUTPUT}")

    if not args.no_open:
        webbrowser.open(f"file://{OUTPUT.resolve()}")

if __name__ == "__main__":
    main()
