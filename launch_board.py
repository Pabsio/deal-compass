"""
launch_board.py — Genera el board desde la DB y lo abre en el navegador
Uso: python3 launch_board.py
     python3 launch_board.py --db holidaypirates_deals.db
"""
import argparse, json, re, sqlite3, webbrowser
from pathlib import Path

TEMPLATE   = Path(__file__).parent / "holidaypirates_board.html"
DB_DEFAULT = Path(__file__).parent / "holidaypirates_deals.db"

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
        if not d["price_per_person"]:
            pm = re.search(r"(?:Ab|From|Desde|Dès|Da|Von)\s*([\d,\.]+\s*(?:€|CHF|£|\$|PLN)(?:\s*p\.\s*P\.?)?)", t, re.IGNORECASE)
            if pm:
                d["price_per_person"] = pm.group(0).strip()
        d["title"] = t[:200]
        d["description"] = (d["description"] or "").replace("\xa0", " ").strip()[:300]
        deals.append(d)
    return deals

def build_board(deals, out_path):
    html = TEMPLATE.read_text(encoding="utf-8")
    deals_json = json.dumps(deals, ensure_ascii=False, default=str)
    # Reemplazar línea exacta — más robusto que regex con DOTALL
    lines = html.split("\n")
    new_lines = []
    for line in lines:
        if line.strip().startswith("const DEALS ="):
            new_lines.append(f"const DEALS = {deals_json};")
        else:
            new_lines.append(line)
    out_path.write_text("\n".join(new_lines), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"❌ No se encuentra: {db}")
        return

    print(f"📦 Cargando deals de {db.name}...")
    deals = load_deals(db)
    print(f"   {len(deals)} deals encontrados")

    # Salida al lado del .db
    out = db.parent / "holidaypirates_board.html"
    build_board(deals, out)
    print(f"✅ Board generado: {out}")

    if not args.no_open:
        webbrowser.open(f"file://{out.resolve()}")

if __name__ == "__main__":
    main()
