"""
bbva.py — versión final completa
======================================================
Investiga y clasifica gastos bancarios e ITF de estados
de cuenta BBVA (CUENTA CORRIENTE PJ).

DIFERENCIAS CLAVE CON INTERBANK:
  - El ITF en BBVA NO aparece línea por línea, sino en una sección
    "TOTALES POR ITF" al final con subcategorías: CARGOS, ABONOS,
    DEVOLUCIONES, PAGOS. Se registra una fila por subcategoría con monto > 0.

  - Los montos negativos (cargos) pueden aparecer en DOS formatos:
      · Signo al inicio:  -6.50   (formato estándar)
      · Signo al final:    6.50-  (formato antiguo BBVA)
    Ambos se detectan correctamente.

  - El ITF se valida con umbral dinámico:
      ITF_max = (saldo_anterior + abonos_mes) × 0.00005
    Si el ITF reportado supera ese umbral → ALERTA.

GASTOS BANCARIOS detectados:
  1. COMISION DEPOSITO      → cobro por depósito en efectivo en ventanilla
  2. COMISION DE MANTENIMIENTO / MANTENIMIENTO
  3. COMIS BBVA EMPRESAS / *COMIS BBVA EMPRESAS
  4. ENVIO DE EXTRACTO DE MOVIMIENTO / EXTRACTO
  5. COMISION (genérico)

  Umbrales máximos por tipo (montos sobre esto → ALERTA):
    COMISION DEPOSITO        : S/50   / US$15
    COMISION DE MANTENIMIENTO: S/200  / US$60
    COMIS BBVA EMPRESAS      : S/200  / US$60
    ENVIO EXTRACTO           : S/50   / US$15
    COMISION (genérico)      : S/200  / US$60

IGNORADOS:
  - SEG.IM (seguro de desgravamen — no es gasto bancario propiamente)

ALERTAS:
  - ITF superior al umbral dinámico
  - Gasto superior al umbral del tipo
  Ambos van a "alertas", no a "registros".

Resultado de procesar_pdf():
  {
    "registros": [...],   # confirmados, listos para contabilizar
    "alertas":   [...],   # dudosos, el usuario debe revisar
  }
"""

import re
import pdfplumber

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
TASA_ITF = 0.00005   # 0.005% — tasa vigente en Perú

UMBRALES = {
    "COMISION DEPOSITO":          (50,   15),
    "COMISION DE MANTENIMIENTO":  (200,  60),
    "COMIS BBVA EMPRESAS":        (200,  60),
    "ENVIO EXTRACTO":             (50,   15),
    "COMISION":                   (200,  60),
}

MESES_NOMBRE = {
    "ENE":"01","FEB":"02","MAR":"03","ABR":"04",
    "MAY":"05","JUN":"06","JUL":"07","AGO":"08",
    "SET":"09","SEP":"09","OCT":"10","NOV":"11","DIC":"12",
}

# Regex para capturar montos con signo leading o trailing
MONTO_RE = re.compile(r'(-?\d{1,3}(?:,\d{3})*\.\d{2}-?)')


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def limpiar_monto(valor: str) -> float:
    try:
        return round(float(str(valor).strip().replace(",","").replace("-","")), 2)
    except Exception:
        return 0.00


def normalizar(texto: str) -> str:
    return str(texto).upper().strip()


def es_cargo(raw: str) -> bool:
    """True si el monto es un cargo (negativo). Soporta -6.50 y 6.50-"""
    raw = raw.strip()
    return raw.startswith('-') or raw.endswith('-')


def extraer_texto_pdf(ruta_pdf: str) -> str:
    """Extrae texto usando pdfplumber (compatible con Streamlit Cloud)."""
    try:
        resultado = ""
        with pdfplumber.open(ruta_pdf) as pdf:
            for page in pdf.pages:
                texto = page.extract_text()
                if texto:
                    resultado += texto + "\n"
        return resultado
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Detección de cabecera
# ---------------------------------------------------------------------------

def detectar_moneda(texto: str):
    t = texto.upper()
    # Formatos conocidos:
    # "MONEDA: DOLARES"  → ESSENTTA corriente
    # "MONEDA: DÓLARES"  → variante con tilde
    # "MONEDA: DOLARES US" → PANO INVERSIONES ahorros (texto sin espacios del PDF)
    # "DOLARESUS"        → versión concatenada sin espacios
    if ("MONEDA: DOLARES" in t or "MONEDA: DÓLARES" in t or
            "DOLARESUS" in t or "DOLARES US" in t):
        return "DOLARES", "US$"
    if "MONEDA: SOLES" in t or "MONEDA:SOLES" in t:
        return "SOLES", "S/"
    return "NO DETECTADA", "S/"


def detectar_ruc(texto: str) -> str:
    m = re.search(r"RUC\s+(\d{11})", texto.upper())
    return m.group(1) if m else ""


def detectar_cuenta(texto: str) -> str:
    # Formato ESSENTTA:  "0011 0616 0100017511 05"  (entidad 0011)
    m = re.search(r"(0011\s+\d{4}\s+\d{10}\s+\d{2})", texto)
    if m:
        return m.group(1).strip()
    # Formato PANO:      "011 179 000200588546 98"  (entidad 011)
    m2 = re.search(r"(011\s+\d{3,4}\s+\d{10,12}\s+\d{2})", texto)
    if m2:
        return m2.group(1).strip()
    # formato sin espacios
    m3 = re.search(r"(0011\d{4}\d{10}\d{2})", texto)
    return m3.group(1) if m3 else ""


def detectar_periodo(texto: str, nombre_archivo: str):
    # BBVA usa formato DD-MM-YYYY en el estado
    fechas = re.findall(r"\b(\d{2})-(\d{2})-(\d{4})\b", texto)
    if fechas:
        _, mes, anio = fechas[-1]
        return anio, mes

    nombre = nombre_archivo.upper()
    # Intentar del nombre de archivo: 02__BBVA_PEN_..._2026-02
    m = re.search(r"(\d{4})-(\d{2})", nombre)
    if m:
        return m.group(1), m.group(2)

    # Buscar mes abreviado en nombre
    for clave, valor in MESES_NOMBRE.items():
        if clave in nombre:
            anio_m = re.search(r"(20\d{2})", nombre)
            anio = anio_m.group(1) if anio_m else "SIN_ANIO"
            return anio, valor

    return "SIN_ANIO", "00"


def detectar_saldo_anterior(texto: str) -> float:
    m = re.search(r"SALDO ANTERIOR\s+([\d,]+\.\d{2})", texto, re.IGNORECASE)
    return float(m.group(1).replace(",","")) if m else 0.0


# ---------------------------------------------------------------------------
# Extracción de transacciones
# ---------------------------------------------------------------------------

def _cargo_abono_de_linea(linea: str):
    """
    Extrae (monto_float, es_cargo_bool, raw_str) de una línea de transacción.
    Maneja ambos formatos de signo: -6.50 y 6.50-
    La estructura es: ... N_OPER  CARGO/ABONO  [ITF]  SALDO
    El último número es el saldo; antes hay opcionalmente el ITF (< 2.00 sin signo)
    y antes el cargo/abono.
    """
    nums = MONTO_RE.findall(linea)
    if len(nums) < 2:
        return 0.0, False, ""

    # Último número = saldo contable
    # Buscar hacia atrás el cargo/abono (primer número >= 1.00 o con signo)
    for raw in reversed(nums[:-1]):
        val = float(raw.replace(",","").replace("-",""))
        if val >= 1.0 or raw.startswith('-') or raw.endswith('-'):
            return limpiar_monto(raw), es_cargo(raw), raw

    return 0.0, False, ""


def calcular_total_abonos(lineas: list) -> float:
    total = 0.0
    for l in lineas:
        if not re.match(r'^\d{2}-\d{2}\s+\d{2}-\d{2}', l):
            continue
        monto, cargo, _ = _cargo_abono_de_linea(l)
        if not cargo and monto > 0:
            total += monto
    return total


# ---------------------------------------------------------------------------
# Clasificación de gastos
# ---------------------------------------------------------------------------

PATRONES_GASTOS = [
    ("COMISION DEPOSITO",          "COMISION DEPOSITO"),
    ("COMISION DE MANTENIMIENTO",  "COMISION DE MANTENIMIENTO"),
    ("COMIS BBVA EMPRESAS",        "COMIS BBVA EMPRESAS"),
    ("*COMIS BBVA EMPRESAS",       "COMIS BBVA EMPRESAS"),
    ("ENVIO DE EXTRACTO",          "ENVIO EXTRACTO"),
    ("EXTRACTO",                   "ENVIO EXTRACTO"),
    ("MANTENIMIENTO",              "COMISION DE MANTENIMIENTO"),
    ("COMISION",                   "COMISION"),
    ("COMISIÓN",                   "COMISION"),
]

PATRONES_IGNORAR = ["SEG.IM"]


def clasificar_gasto(descripcion: str):
    """Retorna concepto_detectado o None si se ignora."""
    desc = normalizar(descripcion)
    for patron in PATRONES_IGNORAR:
        if patron in desc:
            return None
    for patron, concepto in PATRONES_GASTOS:
        if patron in desc:
            return concepto
    return None


def _umbral(concepto: str, moneda: str) -> float:
    idx = 0 if moneda == "SOLES" else 1
    return UMBRALES.get(concepto, (99999, 99999))[idx]


# ---------------------------------------------------------------------------
# Constructor de registro
# ---------------------------------------------------------------------------

def _hacer_registro(
    archivo, anio, mes, ruc, cuenta, moneda, simbolo,
    concepto_categoria, tipo_itf, concepto_detectado,
    fecha, monto, linea_original,
    contabilizar=True, motivo_alerta=""
) -> dict:
    return {
        "Archivo":            archivo,
        "Año":                anio,
        "Mes":                mes,
        "Banco":              "BBVA",
        "RUC":                ruc,
        "Cuenta":             cuenta,
        "Moneda":             moneda,
        "Símbolo":            simbolo,
        "Concepto":           concepto_categoria,
        "Tipo ITF":           tipo_itf,
        "Fecha":              fecha,
        "Monto":              monto,
        "Concepto detectado": concepto_detectado,
        "Línea original":     linea_original,
        "Contabilizar":       contabilizar,
        "Motivo alerta":      motivo_alerta,
    }


# ---------------------------------------------------------------------------
# Extracción ITF
# ---------------------------------------------------------------------------

def extraer_itf(texto: str, itf_max: float,
                archivo, anio, mes, ruc, cuenta, moneda, simbolo):
    """
    ITF en BBVA aparece en sección TOTALES POR ITF con subcategorías.
    Valida cada subcategoría contra el umbral dinámico.
    """
    registros = []
    alertas   = []

    t_upper = texto.upper()
    if "TOTALES POR ITF" not in t_upper:
        return registros, alertas

    bloque = t_upper.split("TOTALES POR ITF")[-1]
    umbral = max(itf_max, 50.0)

    for concepto in ["CARGOS", "ABONOS", "DEVOLUCIONES", "PAGOS"]:
        m = re.search(rf"{concepto}\s+([\d,]+\.\d{{2}})", bloque)
        if not m:
            continue
        monto = limpiar_monto(m.group(1))
        if monto <= 0:
            continue

        concepto_det = f"ITF - {concepto}"
        linea_orig   = f"TOTALES POR ITF · {concepto} {monto:.2f}"

        if monto > umbral:
            motivo = (
                f"ITF {concepto} de {simbolo} {monto:.2f} supera el máximo teórico "
                f"({simbolo} {umbral:.2f} = (saldo anterior + abonos del mes) × 0.005%). "
                f"Verificar en el estado de cuenta original."
            )
            alertas.append(_hacer_registro(
                archivo, anio, mes, ruc, cuenta, moneda, simbolo,
                "IMPUESTO ITF", concepto, concepto_det,
                "", monto, linea_orig,
                contabilizar=False, motivo_alerta=motivo,
            ))
        else:
            registros.append(_hacer_registro(
                archivo, anio, mes, ruc, cuenta, moneda, simbolo,
                "IMPUESTO ITF", concepto, concepto_det,
                "", monto, linea_orig,
                contabilizar=True,
            ))

    return registros, alertas


# ---------------------------------------------------------------------------
# Extracción de gastos bancarios
# ---------------------------------------------------------------------------

def extraer_gastos(lineas: list, itf_max: float,
                   archivo, anio, mes, ruc, cuenta, moneda, simbolo):
    registros = []
    alertas   = []

    for l in lineas:
        l = l.strip()
        if not re.match(r'^\d{2}-\d{2}\s+\d{2}-\d{2}', l):
            continue

        monto, cargo, raw = _cargo_abono_de_linea(l)
        if not cargo or monto <= 0:
            continue  # solo cargos

        # Extraer descripción (entre las dos fechas y los números)
        desc_match = re.match(r'^\d{2}-\d{2}\s+\d{2}-\d{2}\s+(.+)', l)
        if not desc_match:
            continue
        desc_raw = desc_match.group(1)

        # Extraer fecha operación
        fecha_match = re.match(r'^(\d{2}-\d{2})', l)
        fecha = fecha_match.group(1) if fecha_match else ""

        concepto = clasificar_gasto(desc_raw)
        if concepto is None:
            continue

        umbral = _umbral(concepto, moneda)
        if monto > umbral:
            motivo = (
                f"Monto {simbolo} {monto:,.2f} supera el máximo esperado "
                f"({simbolo} {umbral:,.2f}) para '{concepto}'. "
                f"Revisar el estado de cuenta original — si es correcto, confirmar."
            )
            alertas.append(_hacer_registro(
                archivo, anio, mes, ruc, cuenta, moneda, simbolo,
                "GASTOS BANCARIOS", "", concepto,
                fecha, monto, l,
                contabilizar=False, motivo_alerta=motivo,
            ))
        else:
            registros.append(_hacer_registro(
                archivo, anio, mes, ruc, cuenta, moneda, simbolo,
                "GASTOS BANCARIOS", "", concepto,
                fecha, monto, l,
                contabilizar=True,
            ))

    return registros, alertas


# ---------------------------------------------------------------------------
# Procesamiento principal
# ---------------------------------------------------------------------------

def procesar_pdf(nombre_archivo: str, ruta_archivo: str) -> dict:
    """
    Retorna:
    {
        "registros": [...],   # confirmados (Contabilizar=True)
        "alertas":   [...],   # dudosos (Contabilizar=False)
    }
    """
    texto = extraer_texto_pdf(ruta_archivo)
    if not texto.strip():
        return {"registros": [], "alertas": []}

    t_upper = texto.upper()
    # Detectar BBVA en cualquier formato:
    # normal: "BBVA", "BBVA PERÚ", "BBVA BANCO CONTINENTAL"
    # concatenado (PDFs escaneados/rotados): "BANCABBVAPERU", "BBVABANCOCONTINENTAL"
    if not any(k in t_upper for k in ["BBVA", "BANCABBVA", "BBVABANCOCONTINENTAL"]):
        return {"registros": [], "alertas": []}

    moneda, simbolo  = detectar_moneda(texto)
    anio, mes        = detectar_periodo(texto, nombre_archivo)
    ruc              = detectar_ruc(texto)
    cuenta           = detectar_cuenta(texto)
    saldo_anterior   = detectar_saldo_anterior(texto)

    lineas           = [l.strip() for l in texto.splitlines() if l.strip()]
    total_abonos     = calcular_total_abonos(lineas)
    itf_max          = (saldo_anterior + total_abonos) * TASA_ITF

    campos = dict(
        archivo=nombre_archivo, anio=anio, mes=mes,
        ruc=ruc, cuenta=cuenta, moneda=moneda, simbolo=simbolo,
    )

    reg_itf,  alt_itf  = extraer_itf(texto, itf_max, **campos)
    reg_gast, alt_gast = extraer_gastos(lineas, itf_max, **campos)

    return {
        "registros": reg_itf  + reg_gast,
        "alertas":   alt_itf  + alt_gast,
    }


# ---------------------------------------------------------------------------
# Helpers para el reporte
# ---------------------------------------------------------------------------

def total_confirmado(resultado: dict, categoria: str = None) -> float:
    regs = resultado["registros"]
    if categoria:
        regs = [r for r in regs if r["Concepto"] == categoria]
    return round(sum(r["Monto"] for r in regs), 2)


def total_con_alertas(resultado: dict, categoria: str = None) -> float:
    todos = resultado["registros"] + resultado["alertas"]
    if categoria:
        todos = [r for r in todos if r["Concepto"] == categoria]
    return round(sum(r["Monto"] for r in todos), 2)


def get_columnas_ordenadas() -> list:
    return [
        "Archivo", "Año", "Mes", "Banco", "RUC", "Cuenta",
        "Moneda", "Símbolo", "Concepto", "Tipo ITF",
        "Fecha", "Monto", "Concepto detectado", "Línea original",
        "Contabilizar", "Motivo alerta",
    ]
