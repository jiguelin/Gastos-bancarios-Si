import fitz
import re

PATRONES_GASTOS = [
    "MANTENIMIENTO",
    "MANTENIMIENTO PENDIENT",
    "ENVIO",
    "ENVÍO",
    "ESTADO DE CUENTA",
    "EECC",
    "COMISION",
    "COMISIÓN",
    "COM.",
    "CARGO SERVICIO",
    "CARGO POR SERVICIO",
    "TARJETA",
    "MEMBRESIA",
    "MEMBRESÍA",
    "RENOVACION",
    "RENOVACIÓN"
]

PATRONES_IGNORAR = [
    "CARGO TRANSFERENCIA",
    "ABONO",
    "CONSUMO POS",
    "TRANSFERENCIA",
    "PAGO",
    "DEPOSITO",
    "DEPÓSITO"
]

PATRONES_REVISAR = [
    "SEGURO",
    "SEG."
]

MESES_NOMBRE = {
    "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04",
    "MAYO": "05", "JUNIO": "06", "JULIO": "07", "AGOSTO": "08",
    "SETIEMBRE": "09", "SEPTIEMBRE": "09", "OCTUBRE": "10",
    "NOVIEMBRE": "11", "DICIEMBRE": "12"
}


def extraer_texto_pdf(ruta_pdf):
    texto = ""
    try:
        with fitz.open(ruta_pdf) as doc:
            for pagina in doc:
                texto += pagina.get_text("text") + "\n"
        return texto
    except Exception as e:
        return ""


def limpiar_monto(valor):
    valor = str(valor).strip().replace(",", "").replace("-", "")
    try:
        return round(float(valor), 2)
    except:
        return 0.00


def es_fecha_corta(valor):
    return bool(re.fullmatch(r"\d{2}/\d{2}", str(valor).strip()))


def es_monto(valor):
    return bool(re.fullmatch(r"-?\d{1,3}(?:,\d{3})*\.\d{2}|-?\d+\.\d{2}", str(valor).strip()))


def normalizar(texto):
    return str(texto).upper().strip()


def valor_despues_de_etiqueta(lineas, etiqueta):
    etiqueta = etiqueta.upper().replace(":", "")
    for i, linea in enumerate(lineas):
        linea_limpia = linea.strip()
        if linea_limpia.upper().replace(":", "") == etiqueta:
            if i + 1 < len(lineas):
                return lineas[i + 1].strip()
        if linea_limpia.upper().startswith(etiqueta + ":"):
            valor = linea_limpia.split(":", 1)[1].strip()
            if valor:
                return valor
            if i + 1 < len(lineas):
                return lineas[i + 1].strip()
    return ""


def detectar_cliente(lineas):
    return valor_despues_de_etiqueta(lineas, "CLIENTE")


def detectar_cuenta(lineas):
    return valor_despues_de_etiqueta(lineas, "CUENTA")


def detectar_cci(lineas):
    return valor_despues_de_etiqueta(lineas, "CCI")


def detectar_moneda(lineas):
    moneda = valor_despues_de_etiqueta(lineas, "MONEDA").upper()
    if "SOLES" in moneda:
        return "SOLES", "S/"
    if "DOLARES" in moneda or "DÓLARES" in moneda:
        return "DOLARES", "US$"
    return "NO DETECTADA", ""


def detectar_periodo(texto, nombre_archivo):
    match = re.search(
        r"Mes:\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+(\d{4})",
        texto,
        re.IGNORECASE
    )
    if match:
        mes_texto = match.group(1).upper()
        anio = match.group(2)
        mes = MESES_NOMBRE.get(mes_texto, "00")
        return anio, mes

    nombre = nombre_archivo.upper()
    match_archivo = re.search(r"(\d{2})(20\d{2})", nombre)
    if match_archivo:
        return match_archivo.group(2), match_archivo.group(1)

    match_archivo_2 = re.search(r"(20\d{2})(\d{2})", nombre)
    if match_archivo_2:
        return match_archivo_2.group(1), match_archivo_2.group(2)

    return "SIN_ANIO", "00"


def reconstruir_movimientos_interbank(texto):
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    movimientos = []
    i = 0

    while i < len(lineas) - 2:
        if es_fecha_corta(lineas[i]) and es_fecha_corta(lineas[i + 1]):
            fecha_op = lineas[i]
            fecha_proc = lineas[i + 1]
            bloque = []
            j = i + 2

            while j < len(lineas):
                if j + 1 < len(lineas) and es_fecha_corta(lineas[j]) and es_fecha_corta(lineas[j + 1]):
                    break
                bloque.append(lineas[j])
                montos_en_bloque = [x for x in bloque if es_monto(x)]
                if len(montos_en_bloque) >= 2:
                    break
                j += 1

            montos = [x for x in bloque if es_monto(x)]

            if len(montos) >= 2:
                importe_txt = montos[-2]
                saldo_txt = montos[-1]
                partes_texto = [x for x in bloque if not es_monto(x)]
                descripcion = " ".join(partes_texto).strip()

                movimientos.append({
                    "Fecha": fecha_op,
                    "Fecha Proceso": fecha_proc,
                    "Descripcion": descripcion,
                    "Importe Texto": importe_txt,
                    "Saldo Texto": saldo_txt,
                    "Linea Original": f"{fecha_op} {fecha_proc} {descripcion} {importe_txt} {saldo_txt}"
                })
                i = j + 1
            else:
                i += 1
        else:
            i += 1

    return movimientos


def clasificar_movimiento(descripcion):
    desc = normalizar(descripcion)

    if "ITF" in desc:
        return "IMPUESTO ITF"

    for patron in PATRONES_REVISAR:
        if patron in desc:
            return None

    for patron in PATRONES_IGNORAR:
        if patron in desc:
            return None

    for patron in PATRONES_GASTOS:
        if patron in desc:
            return "GASTOS BANCARIOS"

    return None


def obtener_concepto_detectado(descripcion, categoria):
    desc = normalizar(descripcion)

    if categoria == "IMPUESTO ITF":
        if "OPERACION" in desc or "OPERACIÓN" in desc:
            return "ITF OPERACION"
        return "ITF"

    if "MANTENIMIENTO" in desc:
        return "MANTENIMIENTO"

    if "ENVIO" in desc or "ENVÍO" in desc or "ESTADO DE CUENTA" in desc or "EECC" in desc:
        return "ENVIO ESTADO DE CUENTA"

    if "COMISION" in desc or "COMISIÓN" in desc or "COM." in desc or "CARGO SERVICIO" in desc:
        return "COMISION"

    if "TARJETA" in desc or "MEMBRESIA" in desc or "MEMBRESÍA" in desc or "RENOVACION" in desc or "RENOVACIÓN" in desc:
        return "TARJETA"

    return "GASTO BANCARIO"


def extraer_codigo_operacion(descripcion):
    codigos = re.findall(r"\b\d{6,10}\b", descripcion)
    return codigos[-1] if codigos else ""


def extraer_canal(descripcion):
    desc = normalizar(descripcion)
    if "WEB" in desc:
        return "WEB"
    if "INTERNO" in desc:
        return "INTERNO"
    return ""


def procesar_pdf(nombre_archivo, ruta_archivo):
    texto = extraer_texto_pdf(ruta_archivo)

    if not texto.strip():
        return []

    lineas = [l.strip() for l in texto.splitlines() if l.strip()]

    cliente = detectar_cliente(lineas)
    cuenta = detectar_cuenta(lineas)
    cci = detectar_cci(lineas)
    moneda, simbolo = detectar_moneda(lineas)
    anio, mes = detectar_periodo(texto, nombre_archivo)

    movimientos = reconstruir_movimientos_interbank(texto)
    registros = []

    for mov in movimientos:
        descripcion = mov["Descripcion"]
        categoria = clasificar_movimiento(descripcion)

        if categoria is None:
            continue

        monto = limpiar_monto(mov["Importe Texto"])
        saldo = limpiar_monto(mov["Saldo Texto"])

        if monto <= 0:
            continue

        registros.append({
            "Archivo": nombre_archivo,
            "Año": anio,
            "Mes": mes,
            "Banco": "INTERBANK",
            "Cliente": cliente,
            "Cuenta": cuenta,
            "CCI": cci,
            "Moneda": moneda,
            "Símbolo": simbolo,
            "Concepto": categoria,
            "Tipo ITF": "ITF" if categoria == "IMPUESTO ITF" else "",
            "Fecha": mov["Fecha"],
            "Fecha Proceso": mov["Fecha Proceso"],
            "Monto": monto,
            "Concepto detectado": obtener_concepto_detectado(descripcion, categoria),
            "Código operación": extraer_codigo_operacion(descripcion),
            "Canal": extraer_canal(descripcion),
            "Línea original": mov["Linea Original"],
            "Saldo": saldo
        })

    return registros


def get_columnas_ordenadas():
    return [
        "Archivo", "Año", "Mes", "Banco", "Cliente", "Cuenta", "CCI",
        "Moneda", "Símbolo", "Concepto", "Tipo ITF",
        "Fecha", "Fecha Proceso", "Monto", "Concepto detectado",
        "Código operación", "Canal", "Línea original", "Saldo"
    ]
