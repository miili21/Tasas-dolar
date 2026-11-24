"""
Actualiza las tarjetas "DÓLAR BINANCE (P2P)" y "TASA BCV" de la página
FINANZAS FÁCILES en Notion con los precios actuales.
"""
import datetime
import os
import statistics
import sys

import requests
import urllib3
from bs4 import BeautifulSoup

# La web del BCV suele tener problemas con su certificado SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
PAGE_ID = os.environ.get("NOTION_PAGE_ID", "ee5ea6693bf782bf8ebb81005b84ae28")
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
HORA_VE = datetime.timezone(datetime.timedelta(hours=-4))

URL_BINANCE = "https://p2p.binance.com/es/trade/all-payments/USDT?fiat=VES"
URL_BCV = "https://www.bcv.org.ve/"


# ---------- Utilidades ----------

def formato_bs(valor: float) -> str:
    """1096.41 -> '1.096,41'"""
    s = f"{valor:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def ahora_ve() -> str:
    return datetime.datetime.now(HORA_VE).strftime("%d/%m/%Y %I:%M %p")


def texto(contenido, link=None, negrita=False):
    obj = {"type": "text", "text": {"content": contenido}}
    if link:
        obj["text"]["link"] = {"url": link}
    if negrita:
        obj["annotations"] = {"bold": True}
    return obj


# ---------- Fuentes de datos ----------

def precio_binance(tipo: str) -> float:
    """tipo='BUY' (lo que pagas por 1 USDT) o 'SELL' (lo que te pagan).
    Devuelve la mediana de los 5 primeros anuncios del P2P."""
    r = requests.post(
        "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search",
        json={
            "fiat": "VES", "asset": "USDT", "tradeType": tipo,
            "page": 1, "rows": 10, "payTypes": [], "countries": [],
            "publisherType": None, "proMerchantAds": False,
            "shieldMerchantAds": False,
        },
        headers={**NAVEGADOR, "Content-Type": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    anuncios = r.json().get("data") or []
    precios = [float(a["adv"]["price"]) for a in anuncios[:5]]
    if not precios:
        raise RuntimeError(f"Binance no devolvió anuncios ({tipo})")
    return statistics.median(precios)


def tasa_bcv():
    """Devuelve (tasa, fecha_valor) leídas de la portada del BCV."""
    r = requests.get(URL_BCV, headers=NAVEGADOR, timeout=60, verify=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    nodo = soup.select_one("#dolar strong")
    if nodo is None:
        raise RuntimeError("No encontré la tasa del dólar en la web del BCV")
    tasa = float(nodo.get_text(strip=True).replace(".", "").replace(",", "."))

    fecha = "sin fecha"
    nodo_fecha = soup.select_one("span.date-display-single")
    if nodo_fecha is not None:
        iso = (nodo_fecha.get("content") or "")[:10]
        try:
            fecha = datetime.date.fromisoformat(iso).strftime("%d/%m/%Y")
        except ValueError:
            fecha = nodo_fecha.get_text(strip=True)
    return tasa, fecha


# ---------- Notion ----------

def hijos(block_id):
    resultados, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = requests.get(f"https://api.notion.com/v1/blocks/{block_id}/children",
                         headers=NOTION_HEADERS, params=params, timeout=30)
        r.raise_for_status()
        datos = r.json()
        resultados += datos["results"]
        if not datos.get("has_more"):
            return resultados
        cursor = datos["next_cursor"]


def buscar_tarjetas(block_id, nivel=0):
    """Busca los callouts por su primera línea, aunque estén dentro de columnas."""
    encontrados = {}
    for b in hijos(block_id):
        if b["type"] == "callout":
            primera = "".join(t["plain_text"] for t in b["callout"]["rich_text"]).upper()
            if primera.startswith("DÓLAR BINANCE"):
                encontrados["binance"] = b["id"]
            elif primera.startswith("TASA BCV"):
                encontrados["bcv"] = b["id"]
        elif b.get("has_children") and b["type"] in ("column_list", "column") and nivel < 3:
            encontrados.update(buscar_tarjetas(b["id"], nivel + 1))
    return encontrados


def actualizar_tarjeta(block_id, rich_text):
    r = requests.patch(f"https://api.notion.com/v1/blocks/{block_id}",
                       headers=NOTION_HEADERS,
                       json={"callout": {"rich_text": rich_text}}, timeout=30)
    r.raise_for_status()


# ---------- Programa principal ----------

def main():
    tarjetas = buscar_tarjetas(PAGE_ID)
    print("Tarjetas encontradas:", tarjetas)
    exitos = 0

    if "binance" in tarjetas:
        try:
            compra, venta = precio_binance("BUY"), precio_binance("SELL")
            actualizar_tarjeta(tarjetas["binance"], [
                texto("DÓLAR BINANCE (P2P)\n"),
                texto(f"Compra 1 USDT: Bs. {formato_bs(compra)}\n", negrita=True),
                texto(f"Venta 1 USDT: Bs. {formato_bs(venta)}\n", negrita=True),
                texto(f"Actualizado: {ahora_ve()} (hora de Venezuela) · mediana de los 5 primeros anuncios\n"),
                texto("Fuente: "), texto("Binance P2P", link=URL_BINANCE),
            ])
            print(f"Binance OK: compra {compra} / venta {venta}")
            exitos += 1
        except Exception as e:
            print("⚠️ No se pudo actualizar Binance:", e)
    else:
        print("⚠️ No encontré la tarjeta de Binance en la página")

    if "bcv" in tarjetas:
        try:
            tasa, fecha = tasa_bcv()
            actualizar_tarjeta(tarjetas["bcv"], [
                texto("TASA BCV\n"),
                texto(f"1 USD: Bs. {formato_bs(tasa)}\n", negrita=True),
                texto(f"Fecha valor: {fecha}\n"),
                texto(f"Revisado: {ahora_ve()} (hora de Venezuela)\n"),
                texto("Fuente: "), texto("BCV oficial", link=URL_BCV),
            ])
            print(f"BCV OK: {tasa} (fecha valor {fecha})")
            exitos += 1
        except Exception as e:
            print("⚠️ No se pudo actualizar el BCV:", e)
    else:
        print("⚠️ No encontré la tarjeta del BCV en la página")

    # Solo marca error si no se pudo actualizar nada
    if exitos == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
