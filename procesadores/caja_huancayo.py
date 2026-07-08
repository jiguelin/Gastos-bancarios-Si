"""
caja_huancayo.py — versión 1.0 completa
======================================================
Investiga y clasifica gastos bancarios e ITF de estados
de cuenta Caja Huancayo (CUENTA DE AHORROS — SOLES).

FORMATO DEL EXTRACTO:
  "EXTRACTO DE CUENTA AHORROS"
  Columnas: FECHA | HORA | DESCRIPCIÓN | AGENCIA | DEPOSITO | RETIROS | I.T.F. | COM. | SALDO DISPONIBLE

  DIFERENCIA CLAVE CON OTROS BANCOS:
    · ITF y COM tienen COLUMNAS PROPIAS separadas — no están mezclados
      con la descripción de la operación.
    · La columna I.T.F. contiene el monto del impuesto directamente.
    · La columna COM. contiene la comisión directamente.
    · Si I.T.F. > 0 en una fila → registrar como ITF.
    · Si COM. > 0 en una fila → registrar como gasto bancario.
    · El ITF y la comisión pueden estar en la MISMA fila que la operación.

  Descripción de comisiones detectadas (columna DESCRIPCIÓN):
    · "COMI x TRF.INTBCO x TRANSF."  → comisión transferencia interbancaria
    · "COMI x TRF.CLI x TRANSF."     → comisión transferencia a cliente
    · "COMI x..."                      → comisión genérica
    · Cualquier fila con COM. > 0      → gasto bancario independientemente

  IMPORTANTE — PDFs escaneados:
    Febrero, Marzo y algunos otros meses pueden ser PDFs IMAGEN sin texto
    extraíble. En ese caso el procesador retorna vacío con mensaje en el log.
    Se sugiere usar OCR o ingresar esos períodos manualmente.

GASTOS BANCARIOS detectados (columna COM.):
  1. COMI x TRF.INTBCO x TRANSF.  → comisión transf. interbancaria
  2. COMI x TRF.CLI x TRANSF.     → comisión transf. cliente
  3. COMI (genérico)               → cualquier otro COMI con COM. > 0

  Umbrales máximos (montos sobre esto → ALERTA):
    COMI INTBCO : S/20
    COMI CLI    : S/50
    COMI        : S/50

IMPUESTO ITF (columna I.T.F.):
  - Cualquier fila con I.T.F. > 0
  - Umbral dinámico: (saldo_anterior + abonos_mes) × 0.00005
  - Mínimo S/0.50

Solo soles — Caja Huancayo es una caja municipal peruana.

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
TASA_ITF = 0.00005
MONEDA   = "SOLES"
SIMBOLO  = "S/"

UMBRALES = {
    "COMI INTBCO":  20,
    "COMI CLI":     50,
    "COMI":         50,
}

# ---------------------------------------------------------------------------
# Extracción de texto
# ---------------------------------------------------------------------------

def extraer_texto_pdf(ruta_pdf: str) -> str:
    """
    Extrae texto con fitz (PyMuPDF) primero, luego pdfplumber como fallback.
    Caja Huancayo genera PDFs donde fitz extrae mejor el layout tabular.
    Si el PDF es imagen escaneada, ambos retornan vacío.
    """
    texto = _extraer_fitz(ruta_pdf)
    if len(texto.strip()) > 50:
        return texto
    return _extraer_pdfplumber(ruta_pdf)


def _extraer_fitz(ruta_pdf: str) -> str:
    try:
        import fitz
        resultado = ""
        with fitz.open(ruta_pdf) as doc:
            for page in doc:
                resultado += page.get_text("text") + "\n"
        return resultado
    except Exception:
        return ""


def _extraer_pdfplumber(ruta_pdf: str) -> str:
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

def detectar_periodo(texto: str, nombre_archivo: str):
    # "PERIODO: 01/01/2026 AL 31/01/2026" → tomar fecha final
    m = re.search(r'PERIODO[:\s]+\d{2}/\d{2}/\d{4}\s+AL\s+\d{2}/(\d{2})/(\d{4})', texto, re.IGNORECASE)
    if m:
        return m.group(2), m.group(1)

    # Desde nombre de archivo
    nombre = nombre_archivo.upper()
    for mes_nombre, mes_num in {
        "ENERO":"01","FEBRERO":"02","MARZO":"03","ABRIL":"04",
        "MAYO":"05","JUNIO":"06","JULIO":"07","AGOSTO":"08",
        "SETIEMBRE":"09","SEPTIEMBRE":"09","OCTUBRE":"10",
        "NOVIEMBRE":"11","DICIEMBRE":"12",
    }.items():
        if mes_nombre in nombre:
            anio_m = re.search(r"(20\d{2})", nombre)
            anio = anio_m.group(1) if anio_m else "SIN_ANIO"
            return anio, mes_num

    return "SIN_ANIO", "00"


def detectar_cuenta(texto: str) -> str:
    m = re.search(r'NUMERO DE CUENTA[:\s]+([\d]+)', texto, re.IGNORECASE)
    return m.group(1) if m else ""


def detectar_cliente(texto: str) -> str:
    m = re.search(r'TITULAR\s+(.+?)(?:\s{2,}|CALLE|R\.U\.C)', texto, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def detectar_ruc(texto: str) -> str:
    m = re.search(r'R\.U\.C\.?\s*[:\s]+(\d{11})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r'RUC[:\s]+(\d{11})', texto, re.IGNORECASE)
    return m2.group(1) if m2 else ""


def detectar_saldo_anterior(texto: str) -> float:
    # "SALDO CONT. AL 12/02/2026 : 308.65"
    m = re.search(r'SALDO\s+CONT\.?\s+AL\s+[\d/]+\s*:\s*([\d,]+\.\d{2})', texto, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ""))
    # "SALDO DISPONIBLE AL 31/01/2026 : 308.65"  (saldo final del período anterior)
    return 0.0


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


def _umbral(concepto: str) -> float:
    return UMBRALES.get(concepto, 50)


def clasificar_comision(descripcion: str) -> str:
    """Clasifica el tipo de comisión por la descripción."""
    desc = normalizar(descripcion)
    if "INTBCO" in desc:
        return "COMI INTBCO"
    if "TRF.CLI" in desc or "TRF CLI" in desc:
        return "COMI CLI"
    if "COMI" in desc:
        return "COMI"
    return "COMI"  # default si COM. > 0


# ---------------------------------------------------------------------------
# Constructor de registro
# ---------------------------------------------------------------------------

def _hacer_registro(
    archivo, anio, mes, cliente, ruc, cuenta,
    categoria, concepto_detectado, fecha, monto, linea_original,
    contabilizar=True, motivo_alerta=""
) -> dict:
    return {
        "Archivo":            archivo,
        "Año":                anio,
        "Mes":                mes,
        "Banco":              "CAJA HUANCAYO",
        "Cliente":            cliente,
        "RUC":                ruc,
        "Cuenta":             cuenta,
        "Moneda":             MONEDA,
        "Símbolo":            SIMBOLO,
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
# Parser de filas
# ---------------------------------------------------------------------------

def _parsear_filas(lineas: list):
    """
    El texto extraído de Caja Huancayo viene campo por campo en líneas
    separadas (igual que el corte del sistema de Pichincha).
    Estructura esperada por fila:
      FECHA (DD/MM/YYYY) → HORA (HH:MM:SS) → DESCRIPCION → AGENCIA →
      DEPOSITO (número) → RETIROS (número) → I.T.F. (número) → COM. (número) → SALDO (número)
    """
    filas = []
    FECHA_RE   = re.compile(r'^\d{2}/\d{2}/\d{4}$')
    HORA_RE    = re.compile(r'^\d{2}:\d{2}:\d{2}$')
    NUMERO_RE  = re.compile(r'^\d{1,3}(?:,\d{3})*\.\d{2}$')

    i = 0
    while i < len(lineas):
        l = lineas[i].strip()

        if not FECHA_RE.match(l):
            i += 1
            continue

        fecha = l
        i += 1

        # hora
        hora = ""
        if i < len(lineas) and HORA_RE.match(lineas[i].strip()):
            hora = lineas[i].strip()
            i += 1

        # descripcion + agencia (hasta encontrar el primer número)
        # La agencia es texto (ej: "AG. SAN ISIDRO") — se incluye en desc_parts
        # y luego se ignora para la clasificación
        desc_parts = []
        while i < len(lineas) and not NUMERO_RE.match(lineas[i].strip()):
            parte = lineas[i].strip()
            if parte and not re.match(r'^\.+$', parte):  # ignorar líneas de solo puntos
                desc_parts.append(parte)
            i += 1
            if len(desc_parts) > 5:
                break
        # La descripción es el primer elemento; la agencia es el último texto antes de números
        descripcion = desc_parts[0] if desc_parts else ""

        # Leer hasta 5 números: DEPOSITO, RETIROS, ITF, COM, SALDO
        numeros = []
        while i < len(lineas) and len(numeros) < 5:
            if NUMERO_RE.match(lineas[i].strip()):
                numeros.append(limpiar_monto(lineas[i].strip()))
                i += 1
            else:
                break

        if len(numeros) < 4:
            continue

        # Mapear: deposito, retiros, itf, com, saldo
        deposito = numeros[0] if len(numeros) > 0 else 0.0
        retiros  = numeros[1] if len(numeros) > 1 else 0.0
        itf      = numeros[2] if len(numeros) > 2 else 0.0
        com      = numeros[3] if len(numeros) > 3 else 0.0

        # Fecha abreviada DD/MM
        partes = fecha.split("/")
        dia_mes = f"{partes[0]}/{partes[1]}" if len(partes) >= 2 else fecha

        filas.append({
            "fecha":       dia_mes,
            "descripcion": descripcion,
            "deposito":    deposito,
            "retiros":     retiros,
            "itf":         itf,
            "com":         com,
            "linea":       f"{fecha} {hora} {descripcion} dep={deposito} ret={retiros} itf={itf} com={com}",
        })

    return filas


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
        # PDF imagen sin texto extraíble — reportar en log pero no crashear
        return {"registros": [], "alertas": [], "_sin_texto": True}

    t_upper = texto.upper()
    if not any(k in t_upper for k in ["CAJA HUANCAYO", "EXTRACTO DE CUENTA AHORROS", "CAJAHUANCAYO"]):
        return {"registros": [], "alertas": []}

    anio, mes      = detectar_periodo(texto, nombre_archivo)
    cliente        = detectar_cliente(texto)
    ruc            = detectar_ruc(texto)
    cuenta         = detectar_cuenta(texto)
    saldo_anterior = detectar_saldo_anterior(texto)

    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    filas  = _parsear_filas(lineas)

    # Calcular total abonos para umbral ITF
    total_abonos = sum(f["deposito"] for f in filas)
    itf_max      = max((saldo_anterior + total_abonos) * TASA_ITF, 0.50)

    campos_base = dict(
        archivo=nombre_archivo, anio=anio, mes=mes,
        cliente=cliente, ruc=ruc, cuenta=cuenta,
    )

    registros = []
    alertas   = []

    for fila in filas:
        fecha = fila["fecha"]
        linea = fila["linea"]
        desc  = fila["descripcion"]

        # ── ITF ──────────────────────────────────────────────────────────
        if fila["itf"] > 0:
            monto = fila["itf"]
            if monto > itf_max:
                motivo = (
                    f"ITF de S/ {monto:.2f} supera el máximo teórico posible "
                    f"(S/ {itf_max:.2f}). Verificar en el extracto original."
                )
                alertas.append(_hacer_registro(
                    **campos_base, categoria="IMPUESTO ITF",
                    concepto_detectado="ITF SOSPECHOSO",
                    fecha=fecha, monto=monto, linea_original=linea,
                    contabilizar=False, motivo_alerta=motivo,
                ))
            else:
                registros.append(_hacer_registro(
                    **campos_base, categoria="IMPUESTO ITF",
                    concepto_detectado="ITF",
                    fecha=fecha, monto=monto, linea_original=linea,
                    contabilizar=True,
                ))

        # ── COMISIÓN ──────────────────────────────────────────────────────
        if fila["com"] > 0:
            monto   = fila["com"]
            concepto = clasificar_comision(desc)
            umbral   = _umbral(concepto)

            if monto > umbral:
                motivo = (
                    f"Comisión S/ {monto:,.2f} supera el máximo esperado "
                    f"(S/ {umbral:,.2f}) para '{concepto}'. "
                    f"Revisar el extracto original."
                )
                alertas.append(_hacer_registro(
                    **campos_base, categoria="GASTOS BANCARIOS",
                    concepto_detectado=concepto,
                    fecha=fecha, monto=monto, linea_original=linea,
                    contabilizar=False, motivo_alerta=motivo,
                ))
            else:
                registros.append(_hacer_registro(
                    **campos_base, categoria="GASTOS BANCARIOS",
                    concepto_detectado=concepto,
                    fecha=fecha, monto=monto, linea_original=linea,
                    contabilizar=True,
                ))

    return {"registros": registros, "alertas": alertas}


# ---------------------------------------------------------------------------
# Helpers
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
        "Archivo", "Año", "Mes", "Banco", "Cliente", "RUC", "Cuenta",
        "Moneda", "Símbolo", "Concepto", "Tipo ITF",
        "Fecha", "Monto", "Concepto detectado", "Línea original",
        "Contabilizar", "Motivo alerta",
    ]
