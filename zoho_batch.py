"""
Zoho Analytics scraper - generico e parametrizavel.

Funcao principal: fetch_zoho_data(year, month, concessionarios, lead_sources)
Devolve dict com totais e breakdown por ID Landing.

Pode ser chamado em loop para varias (marca, mes) combinacoes.
"""
import sys
import time
import json
from datetime import date

# Force UTF-8 output (corporate PowerShell uses cp1252)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from playwright.sync_api import sync_playwright

ZOHO_URL = "https://analytics.zoho.eu/open-view/38149000004701109/3d7d2c90f3b8f472b76ae7a9b1b45ae9#"

# Mapping marca -> concessionario(s) Zoho
BRAND_TO_CONCESSIONARIOS = {
    "BMW":            ["Caetano Baviera - BMW"],
    "MINI":           ["Caetano Baviera - MINI"],
    "Motorrad":       ["Caetano Baviera - Motorrad"],
    "Mercedes-Benz":  ["Caetano Star - Mercedes"],
    "Audi":           ["Caetano Sport"],
    "Volkswagen":     ["Caetano Drive"],
    "Škoda":          ["Caetano Urban"],
    "Nissan":         ["Caetano Power"],
    "Alpine":         ["Caetano Formula - Alpine"],
    "Renault":        ["Caetano Formula - Renault"],
    "Dacia":          ["Caetano Formula - Dacia"],
    "Peugeot":        ["Caetano Gamobar - Peugeot", "Caetano Motors - Peugeot"],
    "Opel":           ["Caetano Gamobar - Opel", "Caetano Motors - Opel"],
}

DEFAULT_LEAD_SOURCES = ["Facebook", "Instagram"]


# ─── Helpers de interaccao com a UI ──────────────────────────────

def _click_dropdown_option_by_text(page, text_to_find, exact=False):
    options = page.locator(".zdropdownlist__text").all()
    for opt in options:
        try:
            t = opt.inner_text(timeout=500).strip()
            match = (t.lower() == text_to_find.lower()) if exact else (text_to_find.lower() in t.lower())
            if match and opt.is_visible():
                opt.click()
                return t
        except:
            continue
    return None


def _list_dropdown_options(page):
    options = page.locator(".zdropdownlist__text").all()
    result = []
    for opt in options:
        try:
            if opt.is_visible():
                t = opt.inner_text(timeout=500).strip()
                if t:
                    result.append(t)
        except:
            continue
    return result


def _open_filter_dropdown_by_label(page, label_text):
    info = page.evaluate(f"""
        () => {{
            const labelText = {repr(label_text)};
            const all = document.querySelectorAll('*');
            for (const el of all) {{
                if (el.children.length > 0) continue;
                const t = el.textContent.trim();
                if (t.startsWith(labelText) && t.length < 50) {{
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {{
                        return {{text: t, top: Math.round(r.top), left: Math.round(r.left), bottom: Math.round(r.bottom), width: Math.round(r.width)}};
                    }}
                }}
            }}
            return null;
        }}
    """)
    if not info:
        return None
    click_x = info["left"] + min(info["width"] // 2, 60)
    click_y = info["bottom"] + 15
    page.mouse.click(click_x, click_y)
    return info


def _read_kpi(page, kpi_label):
    """Le o valor de um KPI (Leads, Oportunidades, etc) do dashboard."""
    body_text = page.evaluate("document.body.innerText")
    lines = body_text.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == kpi_label and i + 1 < len(lines):
            val = lines[i + 1].strip()
            # Remover pontos/virgulas, converter
            val_clean = val.replace(".", "").replace(",", ".")
            try:
                return float(val_clean) if "." in val_clean else int(val_clean)
            except:
                return 0
    return 0


# ─── Logica de filtro (date range parametrizavel) ───────────────

def _apply_date_filter(page, target_year, target_month):
    """Aplica filtro Mes para um ano/mes especificos."""
    today = date.today()
    months_back = (today.year - target_year) * 12 + (today.month - target_month)
    if months_back < 0:
        raise ValueError(f"Nao posso ir para o futuro: alvo={target_year}-{target_month}, hoje={today}")

    # Numero de dias no mes alvo (28-31)
    if target_month == 12:
        last_day = 31
    else:
        next_month = date(target_year, target_month + 1, 1)
        last_day_d = next_month.replace(day=1) - __import__("datetime").timedelta(days=1)
        last_day = last_day_d.day

    # Abrir date picker - tentar varios textos placeholder, fallback para click directo
    date_filter = page.locator("#ZADateRangeFilterComp")
    opened = False
    for placeholder in ["- Selecionar -", "- Select -", "Select", "Selecionar"]:
        try:
            date_filter.get_by_text(placeholder, exact=False).first.click(timeout=4000)
            opened = True
            break
        except Exception:
            continue
    if not opened:
        # Ultimo recurso: clicar directamente no componente
        date_filter.click()
    time.sleep(1)

    # Navegar meses para tras
    for _ in range(months_back):
        # Tentar PT primeiro depois EN
        clicked = False
        for btn_name in ["Previous Month", "Mes anterior", "Mês anterior"]:
            try:
                page.get_by_role("button", name=btn_name).click(timeout=2000)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("Nao consegui navegar para mes anterior no date picker")
        time.sleep(0.3)

    # Seleccionar dia 1 (start) e dia X (end)
    page.get_by_role("gridcell", name="1", exact=True).click()
    page.get_by_label(str(last_day)).get_by_text(str(last_day), exact=True).click()

    # OK
    for ok_name in ["OK", "Ok", "Aplicar", "Apply"]:
        try:
            page.get_by_role("button", name=ok_name).click(timeout=2000)
            break
        except Exception:
            continue
    time.sleep(3)


def _apply_concessionario_filter(page, concessionario_names):
    """Aplica filtro Concessionario (suporta multi-select)."""
    page.locator(".dComboDropDownLoaded.ZcompMSFilterHead").first.click()
    time.sleep(2)
    _click_dropdown_option_by_text(page, "Todos", exact=True)
    time.sleep(0.5)
    selected = []
    for nome in concessionario_names:
        s = _click_dropdown_option_by_text(page, nome, exact=True)
        if s:
            selected.append(s)
        time.sleep(0.3)
    page.get_by_role("button", name="OK").click()
    time.sleep(3)
    return selected


def _apply_lead_source_filter(page, sources):
    """Aplica filtro Lead Source (Facebook + Instagram normalmente)."""
    # Show more filters
    try:
        page.get_by_role("link", name="Mostrar mais filtros").click(timeout=5000)
        time.sleep(2)
    except:
        pass  # ja pode estar expandido

    _open_filter_dropdown_by_label(page, "Lead Source")
    time.sleep(2)
    _click_dropdown_option_by_text(page, "Todos", exact=True)
    time.sleep(0.5)
    selected = []
    for s in sources:
        sel = _click_dropdown_option_by_text(page, s, exact=True)
        if sel:
            selected.append(sel)
        time.sleep(0.3)
    page.get_by_role("button", name="OK").click()
    time.sleep(4)
    return selected


# ─── Funcao principal ────────────────────────────────────────────

def fetch_zoho_data(year, month, concessionarios, lead_sources=None, headless=False, slow_mo=200):
    """
    Aplica filtros e itera pelos IDs Landing, devolvendo dados estruturados.
    """
    if lead_sources is None:
        lead_sources = DEFAULT_LEAD_SOURCES

    result = {
        "year": year,
        "month": month,
        "concessionarios": concessionarios,
        "lead_sources": lead_sources,
        "totals": {},
        "by_id_landing": {},
        "errors": [],
    }

    with sync_playwright() as p:
        # Em modo headless usar o Chromium completo (--headless=new) em vez
        # do chrome-headless-shell (que e mais leve mas alguns sites detectam).
        launch_args = ["--headless=new"] if headless else []
        # User-Agent realista (Chrome 131 em Windows) - evita deteccao de bot
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo, args=launch_args)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=ua,
            locale="pt-PT",
            timezone_id="Europe/Lisbon",
            extra_http_headers={"Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8"},
        )
        # Anti-deteccao: esconde flag navigator.webdriver=true que Playwright define
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = context.new_page()

        print(f"[fetch] Abrir Zoho para {year}-{month:02d}, {concessionarios}, {lead_sources} (headless={headless})")
        page.goto(ZOHO_URL, timeout=60000, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=45000)
        except:
            print("  Aviso: networkidle nao alcancado em 45s, prosseguir")
        time.sleep(7 if headless else 5)

        # 3 filtros base (com screenshot de debug se falhar)
        try:
            _apply_date_filter(page, year, month)
            print(f"  Mes aplicado")
            _apply_concessionario_filter(page, concessionarios)
            print(f"  Concessionario aplicado")
            _apply_lead_source_filter(page, lead_sources)
            print(f"  Lead Source aplicado")
        except Exception as e:
            # Em caso de falha, guarda screenshot para debug
            try:
                debug_path = f"debug-{concessionarios[0].replace(' ', '_').replace('/', '_')}-{year}-{month:02d}.png"
                page.screenshot(path=debug_path, full_page=True)
                print(f"  >> Screenshot debug guardado: {debug_path}")
            except Exception:
                pass
            raise e

        # Totais com filtros base
        result["totals"]["leads"] = _read_kpi(page, "Leads")
        result["totals"]["oportunidades"] = _read_kpi(page, "Oportunidades")
        if result["totals"]["leads"] and result["totals"]["leads"] > 0:
            result["totals"]["taxa_oportunidade"] = round(
                result["totals"]["oportunidades"] / result["totals"]["leads"] * 100, 2
            )
        print(f"  Totais: {result['totals']}")

        # Enumerar IDs Landing
        _open_filter_dropdown_by_label(page, "ID Landing")
        time.sleep(2)
        opts = _list_dropdown_options(page)
        ids_landing = [o for o in opts if o.lower() != "todos" and not o.startswith("--")]
        page.keyboard.press("Escape")
        time.sleep(1)
        print(f"  {len(ids_landing)} IDs Landing: {ids_landing}")

        # Iterar
        previous_id = None
        for i, idl in enumerate(ids_landing):
            print(f"  [{i+1}/{len(ids_landing)}] ID {idl}...", end=" ")
            try:
                _open_filter_dropdown_by_label(page, "ID Landing")
                time.sleep(2)
                if previous_id is None:
                    _click_dropdown_option_by_text(page, "Todos", exact=True)
                else:
                    _click_dropdown_option_by_text(page, previous_id, exact=True)
                time.sleep(0.4)
                _click_dropdown_option_by_text(page, idl, exact=True)
                time.sleep(0.3)
                page.get_by_role("button", name="OK").click()
                time.sleep(4)

                leads = _read_kpi(page, "Leads")
                oport = _read_kpi(page, "Oportunidades")
                taxa = round(oport / leads * 100, 2) if leads and leads > 0 else 0.0

                result["by_id_landing"][str(idl)] = {
                    "leads": leads,
                    "oportunidades": oport,
                    "taxa_oportunidade": taxa,
                }
                print(f"leads={leads} oport={oport} taxa={taxa}%")
                previous_id = idl
            except Exception as e:
                err = f"ID {idl}: {type(e).__name__}: {str(e)[:100]}"
                print(f"ERRO: {err}")
                result["errors"].append(err)
                continue

        browser.close()
    return result


# ─── CLI: corre para 1 marca + 1 mes ──────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", required=True, help="Nome da marca (ex: Renault, Peugeot)")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--output", default=None, help="Caminho para JSON output")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    if args.brand not in BRAND_TO_CONCESSIONARIOS:
        print(f"Marca '{args.brand}' nao mapeada. Disponiveis: {list(BRAND_TO_CONCESSIONARIOS.keys())}")
        sys.exit(1)

    concessionarios = BRAND_TO_CONCESSIONARIOS[args.brand]
    data = fetch_zoho_data(args.year, args.month, concessionarios, headless=args.headless)
    data["brand"] = args.brand

    out = args.output or f"zoho-{args.brand.lower()}-{args.year}-{args.month:02d}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nGuardado em: {out}")
    print(json.dumps(data, indent=2, ensure_ascii=False))
