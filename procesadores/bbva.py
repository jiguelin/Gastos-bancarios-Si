import fitz
import re

PATRONES_GASTOS = [
    "COMISION DE MANTENIMIENTO",
    "ENVIO DE EXTRACTO DE MOVIMIENTO",
    "*COMIS BBVA EMPRESAS",
    "COMIS BBVA EMPRESAS",
    "COMISION",
    "COMISIÓN",
    "MANTENIMIENTO",
    "EXTRACTO"
]

PATRONES_IGNORAR = [
    "SEG.IM"
]


def limpiar_monto(valor):
    valor = str(valor).strip().replace(",", "").replace("-", "")
    try:
        return round(float(valor), 2)
    except:
        return 0.00


def extraer_texto_pdf(ruta_pdf):
    texto = ""
    try:
        with fitz.open(ruta_pdf) as doc:
            for pagina in doc:
                texto += pagina.get_text("text") + "\n"
        return texto
    except Exception as e:
        return ""


def detectar_moneda(texto):
    texto_mayus = texto.upper()
    if "MONEDA: DOLARES" in texto_mayus or "MONEDA: DÓLARES" in texto_mayus:
        return "DOLARES", "US$"
    if "MONEDA: SOLES" in texto_mayus:
        return "SOLES", "S/"
    return "NO DETECTADA", ""


def detectar_ruc(texto):
    match = re.search(r"RUC\s+(\d{11})", texto.upper())
    return match.group(1) if match else ""


def detectar_cuenta(texto):
    match = re.search(r"0011\s+\d{4}\s+\d{10}\s+\d{2}", texto)
    return match.group(0).strip() if match else ""


def detectar_periodo(texto, nombre_archivo):
    fechas = re.findall(r"\b(\d{2})-(\d{2})-(\d{4})\b", texto)
    if fechas:
        dia, mes, anio = fechas[-1]
        return anio, mes

    meses = {
        "ENE": "01", "FEB": "02", "MAR": "03", "ABR": "04",
        "MAY": "05", "JUN": "06", "JUL": "07", "AGO": "08",
        "SET": "09", "SEP": "09", "OCT": "10", "NOV": "11", "DIC": "12"
    }

    nombre = nombre_archivo.upper()
    mes_detectado = "00"
    for clave, valor in meses.items():
        if clave in nombre:
            mes_detectado = valor
            break

    anio_match = re.search(r"\b(20\d{2}|\d{2})\b", nombre)
    if anio_match:
        anio = anio_match.group(1)
        if len(anio) == 2:
            anio = "20" + anio
    else:
        anio = "SIN_ANIO"

    return anio, mes_detectado


def extraer_itf(texto, archivo, anio, mes, moneda, simbolo, ruc, cuenta):
    registros = []
    texto_mayus = texto.upper()

    if "TOTALES POR ITF" not in texto_mayus:
        return registros

    bloque = texto_mayus.split("TOTALES POR ITF")[-1]

    for concepto in ["CARGOS", "ABONOS", "DEVOLUCIONES", "PAGOS"]:
        patron = rf"{concepto}\s+([\d,]+\.\d{{2}})"
        match = re.search(patron, bloque)

        if match:
            monto = limpiar_monto(match.group(1))
            if monto > 0:
                registros.append({
                    "Archivo": archivo,
                    "Año": anio,
                    "Mes": mes,
                    "Banco": "BBVA",
                    "RUC": ruc,
                    "Cuenta": cuenta,
                    "Moneda": moneda,
                    "Símbolo": simbolo,
                    "Concepto": "IMPUESTO ITF",
                    "Tipo ITF": concepto,
                    "Fecha": "",
                    "Monto": monto,
                    "Concepto detectado": f"ITF - {concepto}",
                    "Línea original": f"{concepto} {monto:.2f}"
                })

    return registros


def agrupar_palabras_por_fila(words, tolerancia_y=2.5):
    filas = []
    words_ordenadas = sorted(words, key=lambda w: (w[1], w[0]))

    for word in words_ordenadas:
        x0, y0, x1, y1, texto, *_ = word
        agregado = False

        for fila in filas:
            if abs(fila["y"] - y0) <= tolerancia_y:
                fila["words"].append(word)
                agregado = True
                break

        if not agregado:
            filas.append({"y": y0, "words": [word]})

    return filas


def obtener_texto_por_rango(fila, x_min, x_max):
    palabras = []
    for w in fila["words"]:
        x0, y0, x1, y1, texto, *_ = w
        if x_min <= x0 <= x_max:
            palabras.append((x0, texto))
    palabras = sorted(palabras, key=lambda x: x[0])
    return " ".join([p[1] for p in palabras]).strip()


def obtener_monto_cargo_abono(fila):
    posibles = []
    for w in fila["words"]:
        x0, y0, x1, y1, texto, *_ = w
        if 370 <= x0 <= 470:
            if re.match(r"^-?[\d,]+\.\d{2}$", texto.strip()):
                posibles.append(texto.strip())
    if posibles:
        return limpiar_monto(posibles[0])
    return 0.00


def debe_ignorarse(descripcion):
    desc = descripcion.upper()
    return any(patron in desc for patron in PATRONES_IGNORAR)


def es_gasto_bancario(descripcion):
    desc = descripcion.upper()
    if debe_ignorarse(desc):
        return False
    return any(patron in desc for patron in PATRONES_GASTOS)


def concepto_limpio(descripcion):
    desc = descripcion.upper()
    if "COMIS BBVA EMPRESAS" in desc:
        return "COMIS BBVA EMPRESAS"
    if "COMISION DE MANTENIMIENTO" in desc:
        return "COMISION DE MANTENIMIENTO"
    if "ENVIO DE EXTRACTO DE MOVIMIENTO" in desc:
        return "ENVIO DE EXTRACTO DE MOVIMIENTO"
    if "COMISION" in desc or "COMISIÓN" in desc:
        return "COMISION"
    if "MANTENIMIENTO" in desc:
        return "MANTENIMIENTO"
    if "EXTRACTO" in desc:
        return "EXTRACTO"
    return "GASTO BANCARIO"


def extraer_gastos_pdf_por_coordenadas(ruta_pdf, archivo, anio, mes, moneda, simbolo, ruc, cuenta):
    registros = []

    try:
        doc = fitz.open(ruta_pdf)
    except Exception:
        return registros

    for num_pagina, pagina in enumerate(doc, start=1):
        words = pagina.get_text("words")
        filas = agrupar_palabras_por_fila(words)

        for fila in filas:
            fecha_oper = obtener_texto_por_rango(fila, 35, 75)
            fecha_valor = obtener_texto_por_rango(fila, 75, 110)
            descripcion = obtener_texto_por_rango(fila, 110, 255)
            monto = obtener_monto_cargo_abono(fila)

            if not descripcion:
                continue

            if es_gasto_bancario(descripcion) and monto > 0:
                registros.append({
                    "Archivo": archivo,
                    "Año": anio,
                    "Mes": mes,
                    "Banco": "BBVA",
                    "RUC": ruc,
                    "Cuenta": cuenta,
                    "Moneda": moneda,
                    "Símbolo": simbolo,
                    "Concepto": "GASTOS BANCARIOS",
                    "Tipo ITF": "",
                    "Fecha": fecha_oper,
                    "Monto": monto,
                    "Concepto detectado": concepto_limpio(descripcion),
                    "Línea original": f"{fecha_oper} {fecha_valor} {descripcion} {monto:.2f}",
                    "Página": num_pagina
                })

    return registros


def procesar_pdf(nombre_archivo, ruta_archivo):
    texto = extraer_texto_pdf(ruta_archivo)

    if not texto.strip():
        return []

    if "BBVA" not in texto.upper() and "BBVABANCOCONTINENTAL" not in texto.upper():
        return []

    moneda, simbolo = detectar_moneda(texto)
    anio, mes = detectar_periodo(texto, nombre_archivo)
    ruc = detectar_ruc(texto)
    cuenta = detectar_cuenta(texto)

    registros = []
    registros += extraer_itf(texto, nombre_archivo, anio, mes, moneda, simbolo, ruc, cuenta)
    registros += extraer_gastos_pdf_por_coordenadas(
        ruta_archivo, nombre_archivo, anio, mes, moneda, simbolo, ruc, cuenta
    )

    return registros


def get_columnas_ordenadas():
    return [
        "Archivo", "Año", "Mes", "Banco", "RUC", "Cuenta",
        "Moneda", "Símbolo", "Concepto", "Tipo ITF",
        "Fecha", "Monto", "Concepto detectado", "Línea original"
    ]
