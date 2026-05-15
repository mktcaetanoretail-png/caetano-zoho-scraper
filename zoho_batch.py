"""
Batch runner do Zoho scraper.
Corre fetch_zoho_data para varias (marca, ano, mes) combinacoes e
agrega tudo num so JSON no formato que o dashboard consome.

Uso:
  python zoho_batch.py --brands Renault Audi --months 2026-04 2026-03
  python zoho_batch.py --brands all --months 2026-04
  python zoho_batch.py --brands all --months 2026-01 2026-02 2026-03 2026-04 --headless
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import json
import argparse
from pathlib import Path
from datetime import datetime

from zoho_scraper import fetch_zoho_data, BRAND_TO_CONCESSIONARIOS

MO_PT = ['janeiro','fevereiro','março','abril','maio','junho',
         'julho','agosto','setembro','outubro','novembro','dezembro']

def month_label(year, month):
    """Devolve label tipo 'abril 2026' que matches com Sheets/dashboard."""
    return f"{MO_PT[month - 1]} {year}"


def parse_month(s):
    """'2026-04' -> (2026, 4)"""
    y, m = s.split("-")
    return int(y), int(m)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brands", nargs="+", required=True,
                        help="Lista de marcas. Use 'all' para todas as 13 mapeadas.")
    parser.add_argument("--months", nargs="+", required=True,
                        help="Lista de meses no formato YYYY-MM. Ex: 2026-04 2026-03")
    parser.add_argument("--output", default="zoho-data.json",
                        help="Caminho de saida do JSON agregado")
    parser.add_argument("--headless", action="store_true",
                        help="Correr sem janela visivel (mais rapido)")
    parser.add_argument("--merge", action="store_true",
                        help="Fundir com JSON existente (preserva dados que nao sao re-extraidos)")
    args = parser.parse_args()

    # Resolver marcas
    if len(args.brands) == 1 and args.brands[0].lower() == "all":
        brands = list(BRAND_TO_CONCESSIONARIOS.keys())
    else:
        for b in args.brands:
            if b not in BRAND_TO_CONCESSIONARIOS:
                print(f"ERRO: marca '{b}' nao mapeada. Disponiveis: {list(BRAND_TO_CONCESSIONARIOS.keys())}")
                sys.exit(1)
        brands = args.brands

    # Resolver meses
    months = [parse_month(m) for m in args.months]

    # Carregar JSON existente se --merge
    output_path = Path(args.output)
    if args.merge and output_path.exists():
        try:
            agg = json.loads(output_path.read_text(encoding="utf-8"))
            print(f"A fundir com {output_path} existente")
        except:
            agg = {}
    else:
        agg = {}

    # Garantir _metadata
    agg.setdefault("_metadata", {})
    agg["_metadata"]["description"] = "Dados do Zoho Analytics agregados por marca, mes e ID Landing."
    agg["_metadata"]["lead_sources"] = ["Facebook", "Instagram"]
    agg["_metadata"]["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    total_runs = len(brands) * len(months)
    print(f"\n>>> Plano: {len(brands)} marcas x {len(months)} meses = {total_runs} runs")
    print(f"    Marcas: {brands}")
    print(f"    Meses:  {[month_label(y, m) for y, m in months]}")
    print(f"    Output: {output_path}")
    print(f"    Headless: {args.headless}\n")

    run_idx = 0
    for brand in brands:
        for year, month in months:
            run_idx += 1
            label = month_label(year, month)
            print(f"\n=== [{run_idx}/{total_runs}] {brand} - {label} ===")
            try:
                data = fetch_zoho_data(
                    year, month,
                    BRAND_TO_CONCESSIONARIOS[brand],
                    headless=args.headless,
                    slow_mo=100 if args.headless else 200,
                )
                # Merge no agregado
                agg.setdefault(brand, {})
                agg[brand][label] = {
                    "totals": data["totals"],
                    "by_id_landing": data["by_id_landing"],
                }
                # Save apos cada run (caso falhe a meio nao perdemos tudo)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(agg, f, indent=2, ensure_ascii=False)
                print(f"  >> {brand}/{label}: {data['totals'].get('leads', 0)} leads em {len(data['by_id_landing'])} IDs (guardado)")
            except Exception as e:
                print(f"  >> ERRO em {brand}/{label}: {type(e).__name__}: {e}")
                continue

    print(f"\n=== Concluido ===")
    print(f"Total runs: {total_runs}")
    print(f"Output:     {output_path}")


if __name__ == "__main__":
    main()
