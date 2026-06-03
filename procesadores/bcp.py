import fitz
import re

PATRONES_ITF = [
    "IMPUESTO ITF"
]

PATRONES_GASTOS = [
    "MANT TD",
    "COM.MANTENIM",
    "COM MANTENIM",
    "MANTENIMIENTO",
    "COMISION",
    "COMISIÓN",
    "COM.",
    "TLC 0030TO MANT",
    "ENVIO.EST.CTA",
    "ENVIO EST CTA",
    "ENVÍO.EST.CTA",
    "ENVÍO EST CTA"
]


def extraer_texto_pdf(ruta_pdf):
    texto = ""
    with fitz.open(ruta_pdf) as doc:
        for pagina in doc:
            texto += pagina.get_text("text") + "\n"
    return texto


def limpiar_monto(monto_txt):
    monto_txt = monto_txt.strip().replace(",", "").replace("-", "")
    if monto_txt.startswith("."):
        monto_txt = "0" + monto_txt
    try:
        return round(float(monto_txt), 2)
    except:
        return 0.00


def extraer_fecha(linea):
    match = re.search(r"\b\d{2}-\d{2}\b", linea)
    return match.group(0) if match else ""


def extraer_monto(linea):
    montos = re.findall(r"(?:\d{1,3}(?:,\d{3})*|\d+)?\.\d{2}-", linea)
    if not montos:
        return 0.00
    return limpiar_monto(montos[-1])


def obtener_concepto_limpio(linea, categoria):
    linea_mayus = linea.upper()

    if categoria == "IMPUESTO ITF":
        return "IMPUESTO ITF"

    if "ENVIO.EST.CTA" in linea_mayus or "ENVIO EST CTA" in linea_mayus or "ENVÍO.EST.CTA" in linea_mayus:
        return "ENVIO.EST.CTA"

    if "TLC 0030TO MANT" in linea_mayus:
        return "TLC 0030TO MANT"

    if "MANT TD" in linea_mayus:
        return "MANT TD ADIC NEG"

    if "COM.MANTENIM" in linea_mayus or "COM MANTENIM" in linea_mayus:
        return "COM.MANTENIM"

    if "COMISION" in linea_mayus or "COMISIÓN" in linea_mayus:
        return "COMISION"

    if "MANTENIMIENTO" in linea_mayus:
        return "MANTENIMIENTO"

    return "GASTO BANCARIO"


def clasificar_linea(linea):
    linea_mayus = linea.upper()

    for patron in PATRONES_ITF:
        if patron in linea_mayus:
            return "IMPUESTO ITF"

    for patron in PATRONES_GASTOS:
        if patron in linea_mayus:
            return "GASTOS BANCARIOS"

    return None


def procesar_pdf(nombre_archivo, ruta_archivo):
    texto = extraer_texto_pdf(ruta_archivo)
    lineas = texto.splitlines()
    registros = []

    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue

        categoria = clasificar_linea(linea)

        if categoria:
            monto = extraer_monto(linea)

            if monto > 0:
                registros.append({
