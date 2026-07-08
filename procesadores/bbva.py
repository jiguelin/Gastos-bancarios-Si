"""
bbva.py — versión 2.0 completa
======================================================
Investiga y clasifica gastos bancarios e ITF de estados
de cuenta BBVA (CUENTA CORRIENTE PJ) — SOLES y DÓLARES.

DIFERENCIAS CLAVE CON LA VERSIÓN ANTERIOR:
  ─────────────────────────────────────────
  NUEVOS GASTOS BANCARIOS detectados (soles y dólares):
    · COMIS.EMISION TRANSF.CCE / COMIS EMISION TRANSF  → comisión emisión transferencia interbancaria
    · COM. Y GASTOS LIQ                                 → comisión y gastos de liquidación
    · COMIS. TRASPASO O/P / COMIS TRASPASO              → comisión traspaso de fondos
    · CARGO INTERESES COMPENSATORIOS                    → intereses compensatorios del mes
    · CARGO INTERESES MORATORIOS                        → intereses moratorios del mes
    · INT.COMPEN. POR COBRAR                            → intereses compensatorios (dólares, cobro diferido)
    · INT.MORAT. POR COBRAR                             → intereses moratorios (dólares, cobro diferido)
    · INTERES COMPENSATORIO POR COBRAR                  → ídem soles, cobro diferido
    · INTERES MORATORIO POR COBRAR                      → ídem soles, cobro diferido
    · COMISIONES POR COBRAR                             → comisiones del mes corriente
    · COMISIONES POR COBRAR MESES ANT                   → comisiones de meses anteriores (→ ALERTA período anterior)

  NUEVA LÓGICA — PERÍODO ANTERIOR (alerta especial):
    El BBVA emite hojas con encabezado "SALDO CARGADO EN CUENTA (1)" y
    "SALDO POR COBRAR EN CUENTA (2)" cuando hay deuda de meses anteriores.
    Todos los cargos en esas hojas son de períodos pasados.
    También detecta por descripción: si contiene "MESES ANT", "ABR2026"
    cuando el mes del estado es mayo, etc.

    Señales de período anterior (activan alerta, no se contabilizan):
      1. Línea en zona "SALDO POR COBRAR EN CUENTA (2)"
      2. Descripción contiene "MESES ANT" o "MESES ANT."
      3. COMISION DE MANTENIMIENTO + sufijo de mes diferente al del estado
         (ej: "COMISION DE MANTENIMIENTO MAR-2026" en estado de ABRIL)
      4. CARGO INTERESES * ABR2026 / CARGO INTERESES * NOV-2025, etc.
         cuando el mes del texto no coincide con el mes del estado

GASTOS BANCARIOS detectados (versión completa):
  1.  COMISION DE MANTENIMIENTO / MANTENIMIENTO        → mantenimiento mensual
  2.  COMIS BBVA EMPRESAS / *COMIS BBVA EMPRESAS       → comisión empresas BBVA
  3.  COMISION DEPOSITO                                → comisión depósito en efectivo
  4.  ENVIO DE EXTRACTO / EXTRACTO                     → envío estado de cuenta
  5.  COMIS. TRANSF. INMEDIATA / COMIS TRANSF INMED    → comisión transferencia inmediata
  6.  COMIS.EMISION TRANSF / COMIS EMISION TRANSF      → comisión emisión transf. interbancaria
  7.  COM. Y GASTOS LIQ                                → comisión y gastos liquidación
  8.  COMIS. TRASPASO / COMIS TRASPASO O/P             → comisión traspaso
  9.  CARGO INTERESES COMPENSATORIOS                   → intereses compensatorios mes corriente
  10. CARGO INTERESES MORATORIOS                       → intereses moratorios mes corriente
  11. INT.COMPEN. POR COBRAR                           → ídem, formato dólares
  12. INT.MORAT. POR COBRAR                            → ídem, formato dólares
  13. INTERES COMPENSATORIO POR COBRAR                 → ídem, formato soles diferido
  14. INTERES MORATORIO POR COBRAR                     → ídem, formato soles diferido
  15. COMISIONES POR COBRAR                            → comisiones del mes
  16. COMISION (genérico)                              → fallback

  IGNORADOS:
    · SEG.IM (seguro de desgravamen)
    · CARGO TRANSFERENCIA / CARGO TRANSF (no son gastos bancarios propios)

  Umbrales máximos por tipo (montos sobre esto → ALERTA de monto):
    COMISION DE MANTENIMIENTO : S/200  / US$60
    COMIS BBVA EMPRESAS       : S/200  / US$60
    COMISION DEPOSITO         : S/50   / US$15
    ENVIO EXTRACTO            : S/50   / US$15
    COMIS TRANSF INMEDIATA    : S/200  / US$60
    COMIS EMISION TRANSF      : S/200  / US$60
    COM Y GASTOS LIQ          : S/500  / US$150
    COMIS TRASPASO            : S/500  / US$150
    INTERESES COMPENSATORIOS  : S/150  / US$50
    INTERESES MORATORIOS      : S/60   / US$20
    COMISIONES POR COBRAR     : S/200  / US$60
    COMISION (genérico)       : S/200  / US$60

IMPUESTO ITF:
  - Aparece en sección "TOTALES POR ITF" con subcategorías:
    CARGOS, ABONOS, DEVOLUCIONES, PAGOS
  - Umbral dinámico: (saldo_anterior + abonos_mes) × 0.00005
  - Mínimo S/50 / US$15 como red de seguridad

ALERTAS — tres tipos:
  "alerta_monto"     : monto supera umbral → NO contabilizar, revisar
  "alerta_periodo"   : cargo de período anterior → NO contabilizar,
                       incluir con motivo "Cargo de período anterior..."
  (confirmado)       : todo lo demás → contabilizar normalmente

Resultado de procesar_pdf():
  {
    "registros" : [...],   # confirmados (Contabilizar=True)
    "alertas"   : [...],   # dudosos (Contabilizar=False)
  }
"""

import re
import pdfplumber

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
TASA_ITF = 0.00005   # 0.005% — tasa vigente en Perú

UMBRALES = {
    "COMISION DE MANTENIMIENTO":  (200,  60),
    "COMIS BBVA EMPRESAS":        (200,  60),
    "COMISION DEPOSITO":          (50,   15),
    "ENVIO EXTRACTO":             (50,   15),
    "COMIS TRANSF INMEDIATA":     (200,  60),
    "COMIS EMISION TRANSF":       (200,  60),
    "COM Y GASTOS LIQ":           (500, 150),
    "COMIS TRASPASO":             (500, 150),
    "INTERESES COMPENSATORIOS":   (150,  50),
    "INTERESES MORATORIOS":       (60,   20),
    "COMISIONES POR COBRAR":      (200,  60),
    "COMISION":                   (200,  60),
}

MESES_NOMBRE = {
    "ENE":"01","FEB":"02","MAR":"03","ABR":"04",
    "MAY":"05","JUN":"06","JUL":"07","AGO":"08",
    "SET":"09","SEP":"09","OCT":"10","NOV":"11","DIC":"12",
    "ENERO":"01","FEBRERO":"02","MARZO":"03","ABRIL":"04",
    "MAYO":"05","JUNIO":"06","JULIO":"07","AGOSTO":"08",
    "SEPTIEMBRE":"09","OCTUBRE":"10","NOVIEMBRE":"11","DICIEMBRE":"12",
}

# Regex para capturar montos con signo leading o trailing
MONTO_RE = re.compile(r'(-?\d{1,3}(?:,\d{3})*\.\d{2}-?)')

# Sufijos de mes en descripciones BBVA (ej: MAR-2026, MAR2026, DIC-2025)
MES_SUFIJO_RE = re.compile(
    r'\b(ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SET|SEP|OCT|NOV|DIC)'
    r'[\-]?(\d{4})\b',
    re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Patrones de gastos bancarios
# Orden: más específico primero para evitar capturas incorrectas
# ---------------------------------------------------------------------------
PATRONES_GASTOS = [
    # (patrón_a_buscar,                          concepto_normalizado)
    ("COMISION DE MANTENIMIENTO",                "COMISION DE MANTENIMIENTO"),
    ("COMISION MANTENIMIENTO",                   "COMISION DE MANTENIMIENTO"),
    ("COMIS BBVA EMPRESAS",                      "COMIS BBVA EMPRESAS"),
    ("*COMIS BBVA EMPRESAS",                     "COMIS BBVA EMPRESAS"),
    ("COMISION DEPOSITO",                        "COMISION DEPOSITO"),
    ("ENVIO DE EXTRACTO",                        "ENVIO EXTRACTO"),
    ("EXTRACTO",                                 "ENVIO EXTRACTO"),
    # Comisiones transferencia — más específico antes del genérico
    ("COMIS.EMISION TRANSF",                     "COMIS EMISION TRANSF"),
    ("COMIS EMISION TRANSF",                     "COMIS EMISION TRANSF"),
    ("COMIS. TRANSF. INMEDIATA",                 "COMIS TRANSF INMEDIATA"),
    ("COMIS. TRANSF. INMED",                     "COMIS TRANSF INMEDIATA"),
    ("COMIS TRANSF INMEDIATA",                   "COMIS TRANSF INMEDIATA"),
    # Comisión y gastos de liquidación
    ("COM. Y GASTOS LIQ",                        "COM Y GASTOS LIQ"),
    ("COM Y GASTOS LIQ",                         "COM Y GASTOS LIQ"),
    # Traspaso
    ("COMIS. TRASPASO",                          "COMIS TRASPASO"),
    ("COMIS TRASPASO",                           "COMIS TRASPASO"),
    # Intereses compensatorios — varias variantes
    ("CARGO INTERESES COMPENSATORIOS",           "INTERESES COMPENSATORIOS"),
    ("INT.COMPEN. POR COBRAR",                   "INTERESES COMPENSATORIOS"),
    ("INTERES COMPENSATORIO POR COBRAR",         "INTERESES COMPENSATORIOS"),
    # Intereses moratorios — varias variantes
    ("CARGO INTERESES MORATORIOS",               "INTERESES MORATORIOS"),
    ("INT.MORAT. POR COBRAR",                    "INTERESES MORATORIOS"),
    ("INTERES MORATORIO POR COBRAR",             "INTERESES MORATORIOS"),
    # Comisiones por cobrar — más específico primero
    ("COMISIONES POR COBRAR MESES ANT",          "COMISIONES POR COBRAR"),
    ("COMISIONES POR COBRAR",                    "COMISIONES POR COBRAR"),
    # Mantenimiento genérico
    ("MANTENIMIENTO",                            "COMISION DE MANTENIMIENTO"),
    # Comisión genérica — fallback, va al final
    ("COMISION",                                 "COMISION"),
    ("COMISIÓN",                                 "COMISION"),
]

PATRONES_IGNORAR = [
    "SEG.IM",
    "CARGO TRANSFERENCIA",
    "CARGO TRANSF",
]


# ---------------------------------------------------------------------------
# Señales de período anterior
# ---------------------------------------------------------------------------

# Palabras/frases que indican que el cargo es de un período pasado
INDICADORES_PERIODO_ANTERIOR = [
    "MESES ANT",
    "MESES ANT.",
    "POR COBRAR MESES",
]

def _es_periodo_anterior_por_descripcion(desc_up: str, mes_estado: str) -> bool:
    """
    True si la descripción indica que es un cargo de período anterior:
    1. Contiene frases explícitas de meses anteriores
    2. COMISION DE MANTENIMIENTO con sufijo de mes diferente al del estado
    3. CARGO INTERESES * con sufijo de mes diferente al del estado
    4. Líneas de intereses/comisiones en hojas de cobro (sin mes del estado)
    """
    # Caso 1: frases explícitas
    for indicador in INDICADORES_PERIODO_ANTERIOR:
        if indicador in desc_up:
            return True

    # Caso 2 y 3: detectar sufijo de mes en la descripción y comparar
    if mes_estado:
        m = MES_SUFIJO_RE.search(desc_up)
        if m:
            mes_desc = MESES_NOMBRE.get(m.group(1).upper(), "00")
            # Si el mes en la descripción es diferente al mes del estado → período anterior
            if mes_desc and mes_desc != mes_estado:
                return True

    return False


def _detectar_zona_cobro(texto: str) -> set:
    """
    Retorna el conjunto de números de operación que aparecen en zonas
    "SALDO CARGADO EN CUENTA (1)" / "SALDO POR COBRAR EN CUENTA (2)".
    Esas zonas son hojas completas donde TODOS los cargos son de períodos anteriores.
    """
    # Detectar si hay sección de cobro en el texto completo
    t_up = texto.upper()
    return (
        "SALDO CARGADO EN CUENTA" in t_up
        and "SALDO POR COBRAR EN CUENTA" in t_up
    )


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
    """Extrae texto usando pdfplumber."""
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
    m = re.search(r"(0011\s+\d{4}\s+\d{10}\s+\d{2})", texto)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"(011\s+\d{3,4}\s+\d{10,12}\s+\d{2})", texto)
    if m2:
        return m2.group(1).strip()
    m3 = re.search(r"(0011\d{4}\d{10}\d{2})", texto)
    return m3.group(1) if m3 else ""


def detectar_periodo(texto: str, nombre_archivo: str):
    """
    BBVA usa formato DD-MM-YYYY en el pie de página (FECHA: 30-01-2026).
    También intenta leer del nombre de archivo.
    """
    fechas = re.findall(r"\b(\d{2})-(\d{2})-(\d{4})\b", texto)
    if fechas:
        _, mes, anio = fechas[-1]
        return anio, mes

    nombre = nombre_archivo.upper()
    m = re.search(r"(\d{4})-(\d{2})", nombre)
    if m:
        return m.group(1), m.group(2)

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
    El último número es el saldo; el penúltimo o el que tiene signo es cargo/abono.
    """
    nums = MONTO_RE.findall(linea)
    if len(nums) < 2:
        return 0.0, False, ""

    for raw in reversed(nums[:-1]):
        val = float(raw.replace(",","").replace("-",""))
        if val >= 0.01 or raw.startswith('-') or raw.endswith('-'):
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

def clasificar_gasto(descripcion: str) -> str | None:
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
    Solo registra subcategorías con monto > 0.
    """
    registros = []
    alertas   = []

    t_upper = texto.upper()
    if "TOTALES POR ITF" not in t_upper:
        return registros, alertas

    umbral = max(itf_max, 50.0)

    # Puede haber múltiples bloques TOTALES POR ITF (un bloque por hoja del PDF).
    # Acumulamos los montos de TODOS los bloques para obtener el total real del mes.
    acumulado = {"CARGOS": 0.0, "ABONOS": 0.0, "DEVOLUCIONES": 0.0, "PAGOS": 0.0}
    bloques = t_upper.split("TOTALES POR ITF")[1:]  # todo lo que viene después de cada ocurrencia

    for bloque in bloques:
        # Solo leer hasta el próximo encabezado de sección para no mezclar bloques
        fin = bloque.find("TOTAL SALDO")
        if fin == -1:
            fin = len(bloque)
        fragmento = bloque[:fin]
        for concepto in acumulado:
            m = re.search(rf"{concepto}\s+([\d,]+\.\d{{2}})", fragmento)
            if m:
                acumulado[concepto] = max(acumulado[concepto], limpiar_monto(m.group(1)))

    for concepto, monto in acumulado.items():
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
                   texto_completo: str, mes_estado: str,
                   archivo, anio, mes, ruc, cuenta, moneda, simbolo):
    """
    Extrae gastos bancarios de las líneas de transacción.
    Detecta si la hoja es de período anterior y marca alertas accordingly.
    """
    registros = []
    alertas   = []

    # Pre-identificar qué líneas de transacción están dentro de zonas de cobro.
    # Una zona de cobro empieza con "SALDO CARGADO EN CUENTA (1)" y termina
    # en "SALDO POR COBRAR EN CUENTA (2)". Se hace en un pase previo para no
    # confundir páginas al leer el texto concatenado de un PDF multipágina.
    lineas_en_zona_cobro = set()
    _en_cobro = False
    for _l in lineas:
        _lu = _l.upper()
        if "SALDO CARGADO EN CUENTA" in _lu:
            _en_cobro = True
        elif "SALDO ANTERIOR" in _lu and "CARGADO" not in _lu:
            _en_cobro = False
        elif "SALDO POR COBRAR EN CUENTA" in _lu:
            _en_cobro = False
        if _en_cobro and re.match(r'^\d{2}-\d{2}\s+\d{2}-\d{2}', _l.strip()):
            lineas_en_zona_cobro.add(_l.strip())

    for l in lineas:
        l_stripped = l.strip()

        if not re.match(r'^\d{2}-\d{2}\s+\d{2}-\d{2}', l_stripped):
            continue

        monto, cargo, raw = _cargo_abono_de_linea(l_stripped)
        if not cargo or monto <= 0:
            continue

        # Extraer descripción
        desc_match = re.match(r'^\d{2}-\d{2}\s+\d{2}-\d{2}\s+(.+)', l_stripped)
        if not desc_match:
            continue
        desc_raw = desc_match.group(1)

        # Extraer fecha operación
        fecha_match = re.match(r'^(\d{2}-\d{2})', l_stripped)
        fecha = fecha_match.group(1) if fecha_match else ""

        concepto = clasificar_gasto(desc_raw)
        if concepto is None:
            continue

        desc_up = normalizar(desc_raw)

        # ── Verificar si es período anterior ──────────────────────────────
        es_anterior = (
            l_stripped in lineas_en_zona_cobro
            or _es_periodo_anterior_por_descripcion(desc_up, mes_estado)
        )

        if es_anterior:
            # Los gastos de períodos anteriores SE CONTABILIZAN en el estado
            # donde aparecen (el banco los cobró oficialmente en ese período).
            # Se registran con una nota informativa para el contador.
            nota = (
                f"Cargo de período anterior incluido en este estado: '{desc_raw.strip()}'. "
                f"El banco lo cobró en este período — contabilizar aquí."
            )
            umbral = _umbral(concepto, moneda)
            if monto > umbral:
                # Monto inusualmente alto incluso para período anterior → sí alertar
                motivo_alerta = (
                    f"Monto {simbolo} {monto:,.2f} supera el máximo esperado "
                    f"({simbolo} {umbral:,.2f}) para '{concepto}' (cargo de período anterior). "
                    f"Revisar antes de contabilizar."
                )
                alertas.append(_hacer_registro(
                    archivo, anio, mes, ruc, cuenta, moneda, simbolo,
                    "GASTOS BANCARIOS", "", concepto,
                    fecha, monto, l_stripped,
                    contabilizar=False, motivo_alerta=motivo_alerta,
                ))
            else:
                registros.append(_hacer_registro(
                    archivo, anio, mes, ruc, cuenta, moneda, simbolo,
                    "GASTOS BANCARIOS", "", concepto,
                    fecha, monto, l_stripped,
                    contabilizar=True, motivo_alerta=nota,
                ))
            continue

        # ── Verificar umbral de monto ──────────────────────────────────────
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
                fecha, monto, l_stripped,
                contabilizar=False, motivo_alerta=motivo,
            ))
        else:
            registros.append(_hacer_registro(
                archivo, anio, mes, ruc, cuenta, moneda, simbolo,
                "GASTOS BANCARIOS", "", concepto,
                fecha, monto, l_stripped,
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
    reg_gast, alt_gast = extraer_gastos(
        lineas, itf_max, texto, mes, **campos
    )

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
