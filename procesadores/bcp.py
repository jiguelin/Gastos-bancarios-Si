"""
bcp.py — versión final completa
======================================================
Investiga y clasifica gastos bancarios e ITF de estados
de cuenta BCP (CUENTA AHORROS CLASICA / CUENTA CORRIENTE PJ).

DIFERENCIAS CLAVE CON INTERBANK Y BBVA:
  - El PDF del BCP suele ser ESCANEADO → se usa pdfplumber con
    tolerancias amplias; si falla, se cae a fitz (PyMuPDF).
  - Las fechas aparecen como "01ABR", "30ABR" (díaMES abreviado
    en español), NO en formato DD/MM ni DD-MM.
  - Los montos NO llevan signo: la columna de posición (CARGOS/DEBE
    vs ABONOS/HABER) determina si es cargo o abono. Sin acceso al
    layout real de columnas, la heurística usada es:
      · Se detecta si la línea pertenece a la zona de CARGOS mirando
        el patrón de la línea completa con dos grupos de montos.
      · Para gastos bancarios y ITF el monto es siempre CARGO.
  - El ITF aparece en líneas individuales con descripción
    "IMPUESTO ITF" y montos muy pequeños (0.05, 0.10, etc.).
  - Los gastos de mantenimiento aparecen descritos como:
      "MANT TD ADIC NEG"  → S/10 aprox  (quincena)
      "MANT. CUENTA MMMAA" → S/18 aprox (fin de mes)
      "ENVIO.ESTADO.CTA"  → S/10 aprox  (envío estado de cuenta)

GASTOS BANCARIOS detectados:
  1. MANT TD / MANT TD ADIC NEG   → mantenimiento tarjeta débito adicional
  2. MANT. CUENTA / MANT CUENTA   → mantenimiento mensual de cuenta
  3. MANTENIMIENTO                → genérico
  4. COM.MANTENIM / COM MANTENIM  → comisión mantenimiento
  5. COMISION / COMISIÓN          → comisión genérica
  6. ENVIO.ESTADO.CTA / ENVIO EST.CTA → envío estado de cuenta
  7. PORTES                       → portes

  Umbrales máximos por tipo (montos sobre esto → ALERTA):
    MANT TD ADIC NEG     : S/30   / US$10
    MANT. CUENTA         : S/50   / US$15
    MANTENIMIENTO        : S/100  / US$30
    COMISION             : S/200  / US$60
    ENVIO ESTADO CTA     : S/20   / US$6
    PORTES               : S/20   / US$6

IMPUESTO ITF:
  - Líneas con descripción exacta "IMPUESTO ITF"
  - Umbral dinámico: (saldo_anterior + abonos_mes) × 0.00005
  - Mínimo S/0.50 como red de seguridad (el BCP cobra fracciones de céntimo)
  - Monto > umbral → ALERTA

ALERTAS — dos niveles:
  "alerta"    : monto supera umbral → NO se contabiliza, usuario debe revisar
  (confirmado): todo lo demás → se contabiliza normalmente

Resultado de procesar_pdf():
  {
    "registros" : [...],   # movimientos confirmados (Contabilizar=True)
    "alertas"   : [...],   # movimientos dudosos (Contabilizar=False)
  }
"""

import re

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
TASA_ITF = 0.00005   # 0.005% — tasa vigente en Perú

UMBRALES = {
    "MANT TD ADIC NEG":   (30,   10),
    "MANT. CUENTA":       (50,   15),
    "MANTENIMIENTO":      (100,  30),
    "COMISION":           (200,  60),
    "ENVIO ESTADO CTA":   (20,    6),
    "TLC 0030TO MANT":   (200,  60),
    "PORTES":             (20,    6),
}

MESES_ABR = {
    "ENE": "01", "FEB": "02", "MAR": "03", "ABR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AGO": "08",
    "SET": "09", "SEP": "09", "OCT": "10", "NOV": "11", "DIC": "12",
}

# Regex para montos BCP — soporta AMBOS formatos:
#   Cuenta Ahorros  : 0.05   18.00   1,402.00   (sin signo, con cero inicial)
#   Cuenta Corriente: .25-   .05-    1,711.89-  (sin cero, guión al final)
#   También captura: 27,024.92  (totales)
MONTO_RE = re.compile(
    r"""
    (?:
        \d{1,3}(?:,\d{3})*\.\d{2}   # formato estándar: 1,402.00 / 18.00
        |
        \.\d{2}                          # solo centavos: .25  .05
    )
    -?                                      # guión opcional al final (cargo)
    """,
    re.VERBOSE
)

# Regex para fechas BCP — soporta DOS formatos:
#   Cuenta Ahorros   : "01ABR", "30ABR" (díaMES abreviado)
#   Cuenta Corriente : "02-05", "31-05" (DD-MM al inicio de línea)
FECHA_BCP_AHORROS_RE   = re.compile(r"\b(\d{2})(ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SET|SEP|OCT|NOV|DIC)\b")
FECHA_BCP_CORRIENTE_RE = re.compile(r"^(\d{2})-(\d{2})\b")  # anclado al inicio


# ---------------------------------------------------------------------------
# Patrones de gastos bancarios
# Orden importa: más específicos primero
# ---------------------------------------------------------------------------
PATRONES_GASTOS = [
    # (patrón_a_buscar,          concepto_normalizado)
    ("MANT TD ADIC NEG",         "MANT TD ADIC NEG"),
    ("MANT TD",                   "MANT TD ADIC NEG"),
    ("MANT. CUENTA",              "MANT. CUENTA"),
    ("MANT CUENTA",               "MANT. CUENTA"),
    ("MANTENIMIENTO PENDIENT",    "MANTENIMIENTO"),
    ("MANTENIMIENTO",             "MANTENIMIENTO"),
    ("COM.MANTENIM",              "COMISION"),
    ("COM MANTENIM",              "COMISION"),
    ("COMISIÓN",                  "COMISION"),
    ("COMISION",                  "COMISION"),
    ("ENVIO.ESTADO.CTA",          "ENVIO ESTADO CTA"),
    ("ENVIO ESTADO.CTA",          "ENVIO ESTADO CTA"),
    ("ENVIO.EST.CTA",             "ENVIO ESTADO CTA"),
    ("ENVIO EST CTA",             "ENVIO ESTADO CTA"),
    ("ENVÍO.ESTADO.CTA",          "ENVIO ESTADO CTA"),
    ("ENVIO.ESTADO. CTA",         "ENVIO ESTADO CTA"),
    ("TLC 0030TO MANT",           "TLC 0030TO MANT"),
    ("PORTES",                    "PORTES"),
]

PATRONES_ITF = ["IMPUESTO ITF"]

# Palabras clave que descartan una línea como gasto bancario aunque
# contenga alguna palabra del patrón (evitar falsos positivos)
PATRONES_IGNORAR = [
    "YAPE", "PLIN", "DEPOSITO", "DEPÓSITO", "GRIFO", "TRAN.",
    "TRANSF", "PAYJ", "PAYI", "FACEBK", "GOOGLE", "BITE",
    "ENTEO", "ENTE0", "MIBAG", "MAR PACIFIC", "IZI", "ESTACION",
    "DEP.EN", "ABON PLIN", "ABON YAPE", "YC-PAGALO", "SERGAS",
    "LIMA EXPRESA", "YQ-", "CALI0", "CALI00",
]


# ---------------------------------------------------------------------------
# Extracción de texto desde PDF escaneado
# ---------------------------------------------------------------------------

def extraer_texto_pdf(ruta_pdf: str) -> str:
    """
    Estrategia en dos pasos para PDFs BCP (frecuentemente escaneados):
      1. pdfplumber con tolerancias amplias → mejor para texto real
      2. PyMuPDF (fitz) como fallback → para PDFs imagen con OCR embebido
    """
    texto = _extraer_con_pdfplumber(ruta_pdf)
    if texto.strip():
        return texto
    return _extraer_con_fitz(ruta_pdf)


def _extraer_con_pdfplumber(ruta_pdf: str) -> str:
    try:
        from collections import defaultdict
        import pdfplumber

        texto_total = ""
        with pdfplumber.open(ruta_pdf) as pdf:
            for page in pdf.pages:
                words = page.extract_words(
                    keep_blank_chars=False,
                    use_text_flow=False,
                    x_tolerance=4,    # tolerancia más amplia para PDFs escaneados
                    y_tolerance=4,
                )
                if not words:
                    continue

                by_y = defaultdict(list)
                for w in words:
                    y = round(w["top"] / 4) * 4   # agrupar por filas de ~4pt
                    by_y[y].append(w)

                for y in sorted(by_y.keys()):
                    ws = sorted(by_y[y], key=lambda w: w["x0"])
                    line = ""
                    prev_x1 = 0
                    for w in ws:
                        gap = w["x0"] - prev_x1
                        spaces = max(1, int(gap / 5.0)) if prev_x1 > 0 else 0
                        line += " " * spaces + w["text"]
                        prev_x1 = w["x1"]
                    texto_total += line.strip() + "\n"

        return texto_total
    except Exception:
        return ""


def _extraer_con_fitz(ruta_pdf: str) -> str:
    try:
        import fitz
        texto = ""
        with fitz.open(ruta_pdf) as doc:
            for pagina in doc:
                texto += pagina.get_text("text") + "\n"
        return texto
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def limpiar_monto(valor: str) -> float:
    try:
        return round(float(str(valor).strip().replace(",", "").replace("-", "")), 2)
    except Exception:
        return 0.00


def normalizar(texto: str) -> str:
    return str(texto).upper().strip()


def corregir_ocr_bcp(linea: str) -> str:
    """
    Corrige errores frecuentes del OCR en PDFs BCP escaneados:

    1. El dígito 0 se lee como 8 en montos pequeños de ITF.
       Patrón: monto que empieza con "8." seguido de exactamente 2 dígitos
       en una línea que contiene "IMPUESTO ITF" → corregir a "0."
       Ejemplos: 8.05 → 0.05 · 8.10 → 0.10 · 8.20 → 0.20

    2. "ENVIO. ESTADO. CTA" (con espacios tras el punto) → "ENVIO.ESTADO.CTA"
       El escáner a veces separa las palabras del código de transacción.

    3. "MANT. CUENTA" puede aparecer como "MANT.CUENTA" sin espacio → normalizar.
    """
    linea_up = linea.upper()

    # Corrección 1: 0 → 8 en montos ITF pequeños
    if "IMPUESTO ITF" in linea_up:
        # Reemplaza montos del tipo "8.XX" (donde XX son 2 dígitos) por "0.XX"
        # Solo aplica cuando el monto es < 9.00 para evitar falsos positivos
        linea = re.sub(r'\b8\.(\d{2})\b', lambda m: f'0.{m.group(1)}', linea)

    # Corrección 2: espacios OCR en códigos de transacción BCP
    linea = re.sub(r'ENVIO\.\s*ESTADO\.\s*CTA', 'ENVIO.ESTADO.CTA', linea, flags=re.IGNORECASE)
    linea = re.sub(r'ENVIO\s+ESTADO\s+CTA',    'ENVIO.ESTADO.CTA', linea, flags=re.IGNORECASE)
    linea = re.sub(r'ENVIO\.\s*EST\.\s*CTA',   'ENVIO.ESTADO.CTA', linea, flags=re.IGNORECASE)

    # Corrección 3: MANT sin espacio
    linea = re.sub(r'MANT\.CUENTA', 'MANT. CUENTA', linea, flags=re.IGNORECASE)

    return linea


def extraer_primer_monto(linea: str) -> float:
    """
    Extrae el primer monto numérico válido de una línea BCP.
    Soporta:
      - Cuenta ahorros  : 0.05  18.00  1,402.00  (sin guión)
      - Cuenta corriente: .25-  .05-   1,711.89- (con guión final, sin cero)
    El guión al final indica cargo en cuenta corriente; lo ignoramos
    porque en bcp.py asumimos que IMPUESTO ITF y gastos son siempre cargos.
    """
    montos = MONTO_RE.findall(linea)
    for m in montos:
        val = limpiar_monto(m)
        if val > 0:
            return val
    return 0.00


def extraer_fecha_bcp(linea: str) -> str:
    """
    Extrae fecha en formato BCP → devuelve "DD/MM" para consistencia.
    Soporta dos formatos:
      - Cuenta ahorros   : "01ABR" → "01/04"
      - Cuenta corriente : "02-05" → "02/05"  (anclado al inicio de línea)
    """
    # Cuenta corriente: formato DD-MM al inicio de línea
    m = FECHA_BCP_CORRIENTE_RE.match(linea)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    # Cuenta ahorros: formato díaMES abreviado en cualquier parte
    m = FECHA_BCP_AHORROS_RE.search(linea)
    if m:
        dia = m.group(1)
        mes = MESES_ABR.get(m.group(2), "00")
        return f"{dia}/{mes}"
    return ""


# ---------------------------------------------------------------------------
# Detección de cabecera del estado de cuenta
# ---------------------------------------------------------------------------

def detectar_periodo(texto: str, nombre_archivo: str):
    """
    BCP usa "DEL DD/MM/AA AL DD/MM/AA" o "DEL 01/04/26 AL 30/04/26".
    También intenta leer del nombre de archivo.
    """
    # Formato en el PDF: DEL 01/04/26 AL 30/04/26
    m = re.search(r"DEL\s+\d{2}/(\d{2})/(\d{2})\s+AL", texto, re.IGNORECASE)
    if m:
        mes  = m.group(1)
        anio = "20" + m.group(2)
        return anio, mes

    # Formato nombre archivo: ABRIL, ABR, 04, 2026, etc.
    nombre = nombre_archivo.upper()
    for nombre_mes, num_mes in {
        "ENERO":"01","FEBRERO":"02","MARZO":"03","ABRIL":"04",
        "MAYO":"05","JUNIO":"06","JULIO":"07","AGOSTO":"08",
        "SETIEMBRE":"09","SEPTIEMBRE":"09","OCTUBRE":"10",
        "NOVIEMBRE":"11","DICIEMBRE":"12",
        **{v: v for v in MESES_ABR.values()},
    }.items():
        if nombre_mes in nombre:
            anio_m = re.search(r"(20\d{2})", nombre)
            anio = anio_m.group(1) if anio_m else "SIN_ANIO"
            return anio, num_mes

    m2 = re.search(r"(\d{4})[_\-]?(\d{2})", nombre)
    if m2:
        return m2.group(1), m2.group(2)

    return "SIN_ANIO", "00"


def detectar_moneda(texto: str):
    t = texto.upper()
    if "MONEDA" in t:
        if "DOLAR" in t or "DOLLAR" in t or "US$" in t:
            return "DOLARES", "US$"
        if "SOLES" in t or "S/" in t:
            return "SOLES", "S/"
    # BCP suele ser soles por defecto
    return "SOLES", "S/"


def detectar_cuenta(lineas: list) -> str:
    """Detecta el código de cuenta BCP: formato 194-XXXXXXXX-X-XX"""
    for linea in lineas:
        m = re.search(r"\d{3}-\d{8}-\d-\d{2}", linea)
        if m:
            return m.group(0)
    return ""


def detectar_cliente(lineas: list) -> str:
    """El cliente suele aparecer en las primeras líneas en mayúsculas."""
    for linea in lineas[:20]:
        linea = linea.strip()
        # Líneas en mayúsculas con más de 4 chars que no sean encabezados
        if (len(linea) > 4 and linea.isupper()
                and not any(k in linea for k in [
                    "ESTADO", "CUENTA", "PAGINA", "CÓDIGO", "CODIGO",
                    "FECHA", "MONEDA", "BCP", "SALDO", "DESCRIPCION",
                    "CARGO", "ABONO", "PROC", "VALOR", "MZ.", "LT.",
                    "URB.", "LIMA", "PUNTA",
                ])):
            return linea
    return ""


def detectar_saldo_anterior(texto: str) -> float:
    """
    BCP muestra el saldo anterior como primera línea de movimientos
    con descripción "SALDO ANTERIOR" y el monto en la columna ABONOS/HABER.
    """
    m = re.search(r"SALDO ANTERIOR\s+([\d,]+\.\d{2})", texto, re.IGNORECASE)
    if m:
        return limpiar_monto(m.group(1))
    return 0.00


def calcular_total_abonos(lineas: list) -> float:
    """
    Suma todos los montos en líneas que contienen fechas BCP
    y que probablemente son abonos (segundo monto de la línea si hay dos).
    Heurística conservadora: usa el total de movimiento de la última página.
    """
    total = 0.0
    for linea in lineas:
        linea_up = normalizar(linea)
        # La línea de TOTAL MOVIMIENTO tiene el total acumulado de abonos
        if "TOTAL MOVIMIENTO" in linea_up:
            montos = MONTO_RE.findall(linea)
            if len(montos) >= 2:
                # Primer monto = total cargos, segundo = total abonos
                try:
                    total = limpiar_monto(montos[1])
                except Exception:
                    pass
    return total


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------

def _tiene_patron_ignorar(linea_up: str) -> bool:
    return any(p in linea_up for p in PATRONES_IGNORAR)


def clasificar_linea(linea: str):
    """
    Retorna ("IMPUESTO ITF" | "GASTOS BANCARIOS" | None, concepto_detectado).
    """
    linea_up = normalizar(linea)

    # Descartar líneas que claramente no son gastos bancarios
    if _tiene_patron_ignorar(linea_up):
        return None, ""

    # ITF primero (más específico)
    for patron in PATRONES_ITF:
        if patron in linea_up:
            return "IMPUESTO ITF", "IMPUESTO ITF"

    # Gastos bancarios
    for patron, concepto in PATRONES_GASTOS:
        if patron in linea_up:
            return "GASTOS BANCARIOS", concepto

    return None, ""


def _umbral(concepto: str, moneda: str) -> float:
    idx = 0 if moneda == "SOLES" else 1
    return UMBRALES.get(concepto, (99999, 99999))[idx]


# ---------------------------------------------------------------------------
# Constructor de registro (alineado con Interbank y BBVA)
# ---------------------------------------------------------------------------

def _hacer_registro(
    archivo, anio, mes, cliente, cuenta, moneda, simbolo,
    categoria, concepto_detectado, fecha, monto, linea_original,
    contabilizar=True, motivo_alerta=""
) -> dict:
    return {
        "Archivo":            archivo,
        "Año":                anio,
        "Mes":                mes,
        "Banco":              "BCP",
        "Cliente":            cliente,
        "Cuenta":             cuenta,
        "Moneda":             moneda,
        "Símbolo":            simbolo,
        "Concepto":           categoria,
        "Tipo ITF":           "ITF" if categoria == "IMPUESTO ITF" else "",
        "Fecha":              fecha,
        "Monto":              monto,
        "Concepto detectado": concepto_detectado,
        "Línea original":     linea_original,
        "Contabilizar":       contabilizar,
        "Motivo alerta":      motivo_alerta,
    }


# ---------------------------------------------------------------------------
# Procesamiento principal
# ---------------------------------------------------------------------------

def procesar_pdf(nombre_archivo: str, ruta_archivo: str) -> dict:
    """
    Retorna:
    {
        "registros" : [...]   # movimientos confirmados (Contabilizar=True)
        "alertas"   : [...]   # movimientos dudosos (Contabilizar=False)
    }
    Ambas listas usan las mismas columnas. Las alertas incluyen "Motivo alerta".
    """
    texto = extraer_texto_pdf(ruta_archivo)
    if not texto.strip():
        return {"registros": [], "alertas": []}

    lineas          = [l.strip() for l in texto.splitlines() if l.strip()]
    moneda, simbolo = detectar_moneda(texto)
    anio, mes       = detectar_periodo(texto, nombre_archivo)
    cliente         = detectar_cliente(lineas)
    cuenta          = detectar_cuenta(lineas)
    saldo_anterior  = detectar_saldo_anterior(texto)
    total_abonos    = calcular_total_abonos(lineas)

    # Umbral ITF dinámico (mínimo S/0.50 porque el BCP cobra fracciones)
    itf_max = max((saldo_anterior + total_abonos) * TASA_ITF, 0.50)

    registros = []
    alertas   = []

    campos_base = dict(
        archivo=nombre_archivo, anio=anio, mes=mes,
        cliente=cliente, cuenta=cuenta,
        moneda=moneda, simbolo=simbolo,
    )

    for linea in lineas:
        # Corregir errores OCR del escáner BCP antes de clasificar
        linea = corregir_ocr_bcp(linea)

        categoria, concepto_detectado = clasificar_linea(linea)
        if categoria is None:
            continue

        monto = extraer_primer_monto(linea)
        if monto <= 0:
            continue

        fecha = extraer_fecha_bcp(linea)

        # ── IMPUESTO ITF ──────────────────────────────────────────────────
        if categoria == "IMPUESTO ITF":
            if monto > itf_max:
                motivo = (
                    f"ITF de {simbolo} {monto:.2f} supera el máximo teórico posible "
                    f"({simbolo} {itf_max:.2f} = (saldo anterior + abonos del mes) "
                    f"× 0.005%). Verificar en el estado de cuenta original."
                )
                alertas.append(_hacer_registro(
                    **campos_base,
                    categoria=categoria,
                    concepto_detectado="ITF SOSPECHOSO",
                    fecha=fecha, monto=monto,
                    linea_original=linea,
                    contabilizar=False,
                    motivo_alerta=motivo,
                ))
            else:
                registros.append(_hacer_registro(
                    **campos_base,
                    categoria=categoria,
                    concepto_detectado=concepto_detectado,
                    fecha=fecha, monto=monto,
                    linea_original=linea,
                    contabilizar=True,
                ))
            continue

        # ── GASTOS BANCARIOS ──────────────────────────────────────────────
        umbral = _umbral(concepto_detectado, moneda)
        if monto > umbral:
            motivo = (
                f"Monto {simbolo} {monto:,.2f} supera el máximo esperado "
                f"({simbolo} {umbral:,.2f}) para '{concepto_detectado}'. "
                f"Revisar el estado de cuenta original — si es correcto, confirmar."
            )
            alertas.append(_hacer_registro(
                **campos_base,
                categoria=categoria,
                concepto_detectado=concepto_detectado,
                fecha=fecha, monto=monto,
                linea_original=linea,
                contabilizar=False,
                motivo_alerta=motivo,
            ))
        else:
            registros.append(_hacer_registro(
                **campos_base,
                categoria=categoria,
                concepto_detectado=concepto_detectado,
                fecha=fecha, monto=monto,
                linea_original=linea,
                contabilizar=True,
            ))

    return {"registros": registros, "alertas": alertas}


# ---------------------------------------------------------------------------
# Helpers para el reporte (alineados con Interbank y BBVA)
# ---------------------------------------------------------------------------

def total_confirmado(resultado: dict, categoria: str = None) -> float:
    """Suma de registros confirmados, opcionalmente filtrado por categoría."""
    regs = resultado["registros"]
    if categoria:
        regs = [r for r in regs if r["Concepto"] == categoria]
    return round(sum(r["Monto"] for r in regs), 2)


def total_con_alertas(resultado: dict, categoria: str = None) -> float:
    """Suma confirmados + alertas (si el usuario decide confirmar los dudosos)."""
    todos = resultado["registros"] + resultado["alertas"]
    if categoria:
        todos = [r for r in todos if r["Concepto"] == categoria]
    return round(sum(r["Monto"] for r in todos), 2)


def get_columnas_ordenadas() -> list:
    return [
        "Archivo", "Año", "Mes", "Banco", "Cliente", "Cuenta",
        "Moneda", "Símbolo", "Concepto", "Tipo ITF",
        "Fecha", "Monto", "Concepto detectado", "Línea original",
        "Contabilizar", "Motivo alerta",
    ]
