"""
interbank.py  — versión corregida
=========================================================
CAMBIOS RESPECTO A LA VERSIÓN ANTERIOR:
1. EXTRACCIÓN DE TEXTO: se reemplaza fitz.get_text("text") por pdftotext -layout
   (subprocess). El modo -layout mantiene cada transacción en UNA SOLA LÍNEA,
   evitando que las columnas se fragmenten en líneas separadas.

2. PARSER DE MOVIMIENTOS: se reemplaza reconstruir_movimientos_interbank() por
   parsear_movimientos_layout() que usa una expresión regular por línea en lugar
   de buscar pares de fechas en líneas sueltas.

3. PATRONES_GASTOS: se agrega "COMI." para capturar "COMI. PAGOS PLANILLA"
   (comisión por pago de planilla).

4. EXTRACCIÓN DE CANAL y CÓDIGO DE OPERACIÓN: ahora se hace desde los campos
   reales de la línea (no por regex sobre texto mezclado).

5. DETECCIÓN DE CLIENTE/CUENTA/MONEDA: ahora lee líneas con layout y detecta
   los campos de cabecera correctamente con su formato "ETIQUETA:   VALOR".
"""

import re
import subprocess

# ---------------------------------------------------------------------------
# Patrones de clasificación
# ---------------------------------------------------------------------------

PATRONES_GASTOS = [
    "MANTENIMIENTO",
    "MANTENIMIENTO PENDIENT",
    "ENVIO",
    "ENVÍO",
    "ESTADO DE CUENTA",
    "EECC",
    "COMISION",
    "COMISIÓN",
    "COM.",        # cubre "COM.O/C" y variantes
    "COMI.",       # ← NUEVO: cubre "COMI. PAGOS PLANILLA"
    "CARGO SERVICIO",
    "CARGO POR SERVICIO",
    "TARJETA",
    "MEMBRESIA",
    "MEMBRESÍA",
    "RENOVACION",
    "RENOVACIÓN",
]

PATRONES_IGNORAR = [
    "CARGO TRANSFERENCIA",
    "ABONO",
    "CONSUMO POS",
    "TRANSFERENCIA",
    "PAGO",
    "DEPOSITO",
    "DEPÓSITO",
]

PATRONES_REVISAR = [
    "SEGURO",
    "SEG.",
]

MESES_NOMBRE = {
    "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04",
    "MAYO": "05", "JUNIO": "06", "JULIO": "07", "AGOSTO": "08",
    "SETIEMBRE": "09", "SEPTIEMBRE": "09", "OCTUBRE": "10",
    "NOVIEMBRE": "11", "DICIEMBRE": "12",
}

# Canales conocidos en estados de cuenta Interbank
CANALES_CONOCIDOS = {"WEB", "INTERNO", "TIENDA", "SOPORTE"}

# ---------------------------------------------------------------------------
# Regex principal de transacción (modo -layout: una línea por movimiento)
# ---------------------------------------------------------------------------
#
# Formato de columnas en el PDF con layout:
#   DD/MM  DD/MM  MOVIMIENTO          DETALLE          [COD_OP]  [CANAL]  [COD_UBIC]  IMPORTE  SALDO
#
# La descripción completa (entre la segunda fecha y el importe) puede contener
# subespacios, por eso se captura todo y luego se parte en subcampos.
#
TX_RE = re.compile(
    r"^\s*(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+"   # grupo 1: fecha_op, grupo 2: fecha_proc
    r"(.+?)\s{2,}"                             # grupo 3: descripción completa (lazy)
    r"(-?\d{1,3}(?:,\d{3})*\.\d{2}|-?\d+\.\d{2})"  # grupo 4: importe
    r"\s+"
    r"(\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2})"      # grupo 5: saldo
    r"\s*$"
)

# Separador de subcampos dentro de la descripción (2 o más espacios)
PARTES_RE = re.compile(r"\s{2,}")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def limpiar_monto(valor: str) -> float:
    valor = str(valor).strip().replace(",", "").replace("-", "")
    try:
        return round(float(valor), 2)
    except Exception:
        return 0.00


def normalizar(texto: str) -> str:
    return str(texto).upper().strip()


def detectar_periodo(texto: str, nombre_archivo: str):
    match = re.search(
        r"Mes:\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+(\d{4})",
        texto,
        re.IGNORECASE,
    )
    if match:
        mes_txt = match.group(1).upper()
        anio = match.group(2)
        return anio, MESES_NOMBRE.get(mes_txt, "00")

    nombre = nombre_archivo.upper()
    m = re.search(r"(\d{2})(20\d{2})", nombre)
    if m:
        return m.group(2), m.group(1)
    m = re.search(r"(20\d{2})(\d{2})", nombre)
    if m:
        return m.group(1), m.group(2)

    return "SIN_ANIO", "00"


# ---------------------------------------------------------------------------
# Extracción de texto con pdftotext -layout
# ---------------------------------------------------------------------------

def extraer_texto_pdf(ruta_pdf: str) -> str:
    """
    Usa pdftotext -layout para preservar el alineado columnar del estado de
    cuenta Interbank. Cada transacción queda en UNA SOLA LÍNEA, lo que permite
    parsearla con una simple expresión regular.

    Versión anterior usaba fitz.get_text("text"), que fragmenta las columnas
    en líneas separadas y rompe el parser de pares de fechas.
    """
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", ruta_pdf, "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Detección de campos de cabecera
# ---------------------------------------------------------------------------

def _valor_cabecera(lineas_layout: list, etiqueta: str) -> str:
    """
    Busca en líneas con layout la etiqueta seguida de espacios y devuelve
    el valor. Ejemplo: "CLIENTE:               ALTOQUE SAC"
    """
    pat = re.compile(
        r"^\s*" + re.escape(etiqueta.upper().rstrip(":")) + r"\s*:\s*(.+)$",
        re.IGNORECASE,
    )
    for linea in lineas_layout:
        m = pat.match(linea)
        if m:
            return m.group(1).strip()
    return ""


def detectar_cliente(lineas):
    return _valor_cabecera(lineas, "CLIENTE")


def detectar_cuenta(lineas):
    return _valor_cabecera(lineas, "CUENTA")


def detectar_cci(lineas):
    return _valor_cabecera(lineas, "CCI")


def detectar_moneda(lineas):
    moneda = _valor_cabecera(lineas, "MONEDA").upper()
    if "SOLES" in moneda:
        return "SOLES", "S/"
    if "DOLARES" in moneda or "DÓLARES" in moneda:
        return "DOLARES", "US$"
    return "NO DETECTADA", ""


# ---------------------------------------------------------------------------
# Parser principal de movimientos
# ---------------------------------------------------------------------------

def _parsear_subcampos(desc_raw: str):
    """
    Divide la descripción completa (todo lo que hay entre las dos fechas y el
    importe) en: movimiento, detalle, cod_op, canal.

    Los subcampos están separados por 2+ espacios en el PDF con layout.
    """
    partes = [p.strip() for p in PARTES_RE.split(desc_raw.strip()) if p.strip()]

    movimiento = partes[0] if partes else ""
    detalle = partes[1] if len(partes) > 1 else ""
    cod_op = ""
    canal = ""

    for parte in partes[2:]:
        p_upper = parte.upper()
        if p_upper in CANALES_CONOCIDOS:
            canal = p_upper
        elif re.fullmatch(r"\d{6,10}", parte):
            cod_op = parte
        # Los códigos de ubicación numéricos cortos (ej. "523") se ignoran

    return movimiento, detalle, cod_op, canal


def parsear_movimientos_layout(texto: str) -> list:
    """
    Nuevo parser basado en pdftotext -layout.
    Cada línea que empiece con DD/MM  DD/MM es una transacción completa.
    """
    movimientos = []
    for linea in texto.splitlines():
        m = TX_RE.match(linea)
        if not m:
            continue
        fecha_op, fecha_proc, desc_raw, importe_txt, saldo_txt = m.groups()
        mov, det, cod_op, canal = _parsear_subcampos(desc_raw)

        # La descripción completa para clasificación es movimiento + detalle
        descripcion_completa = (mov + " " + det).strip()

        movimientos.append({
            "Fecha": fecha_op,
            "Fecha Proceso": fecha_proc,
            "Movimiento": mov,
            "Detalle": det,
            "Descripcion": descripcion_completa,
            "Importe Texto": importe_txt,
            "Saldo Texto": saldo_txt,
            "Código operación": cod_op,
            "Canal": canal,
            "Línea original": linea.strip(),
        })
    return movimientos


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------

def clasificar_movimiento(descripcion: str):
    desc = normalizar(descripcion)

    if "ITF" in desc:
        return "IMPUESTO ITF"

    for patron in PATRONES_REVISAR:
        if patron in desc:
            return None  # requiere revisión manual

    # IMPORTANTE: verificar PATRONES_GASTOS ANTES que PATRONES_IGNORAR
    # para que "COMI. PAGOS PLANILLA" no sea ignorado por el patrón "PAGO"
    for patron in PATRONES_GASTOS:
        if patron in desc:
            return "GASTOS BANCARIOS"

    for patron in PATRONES_IGNORAR:
        if patron in desc:
            return None

    return None


def obtener_concepto_detectado(descripcion: str, categoria: str) -> str:
    desc = normalizar(descripcion)

    if categoria == "IMPUESTO ITF":
        if "OPERACION" in desc or "OPERACIÓN" in desc:
            return "ITF OPERACION"
        return "ITF"

    if "MANTENIMIENTO" in desc:
        return "MANTENIMIENTO"
    if "ENVIO" in desc or "ENVÍO" in desc or "ESTADO DE CUENTA" in desc or "EECC" in desc:
        return "ENVIO ESTADO DE CUENTA"
    if "COMI." in desc:
        return "COMISION PLANILLA"
    if "COMISION" in desc or "COMISIÓN" in desc or "COM." in desc or "CARGO SERVICIO" in desc:
        return "COMISION"
    if "TARJETA" in desc or "MEMBRESIA" in desc or "MEMBRESÍA" in desc \
            or "RENOVACION" in desc or "RENOVACIÓN" in desc:
        return "TARJETA"

    return "GASTO BANCARIO"


# ---------------------------------------------------------------------------
# Procesamiento principal
# ---------------------------------------------------------------------------

def procesar_pdf(nombre_archivo: str, ruta_archivo: str) -> list:
    texto = extraer_texto_pdf(ruta_archivo)
    if not texto.strip():
        return []

    lineas = texto.splitlines()  # mantiene espaciado para cabecera

    cliente = detectar_cliente(lineas)
    cuenta = detectar_cuenta(lineas)
    cci = detectar_cci(lineas)
    moneda, simbolo = detectar_moneda(lineas)
    anio, mes = detectar_periodo(texto, nombre_archivo)

    movimientos = parsear_movimientos_layout(texto)
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
            "Código operación": mov["Código operación"],
            "Canal": mov["Canal"],
            "Línea original": mov["Línea original"],
            "Saldo": saldo,
        })

    return registros


def get_columnas_ordenadas() -> list:
    return [
        "Archivo", "Año", "Mes", "Banco", "Cliente", "Cuenta", "CCI",
        "Moneda", "Símbolo", "Concepto", "Tipo ITF",
        "Fecha", "Fecha Proceso", "Monto", "Concepto detectado",
        "Código operación", "Canal", "Línea original", "Saldo",
    ]
