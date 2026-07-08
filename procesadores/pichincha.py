"""
pichincha.py — versión 1.0 completa
======================================================
Investiga y clasifica gastos bancarios e ITF de estados
de cuenta Banco Pichincha (CUENTA DE AHORRO PJ).

SOPORTA DOS FORMATOS DE DOCUMENTO:
  1. PDF FÍSICO (Estado de Cuenta de Ahorro impreso)
     Columnas: DIA | N°DOC. | OPERACION | DESCRIPCION | ABONO | CARGO | SALDO
     - Montos cargo con guión al final: "1.65-"  "46.86-"
     - Saldo con sufijo CR: "30,810.39CR"
     - Fecha: "DD/MM" (solo día y mes)
     - Header: "ESTADO DE CUENTA DE AHORRO"
     - Identificación moneda: "DOLARES" o "SOLES" en el encabezado

  2. CORTE DEL SISTEMA (exportación web Cash Management)
     Columnas: FECHA | HORA | DESCRIPCIÓN DE TRANSACCIÓN | TIPO | AGENCIA | MONTO | SALDO
     - Montos negativos con guión al inicio: "- 1.65"  "- 46.86"
     - Tipo DÉBITO = cargo, CRÉDITO = abono
     - Fecha: "DD/MM/YYYY" completa
     - Header: "Solicitud de Corte de Estado de Cuenta"
     - Identificación moneda: "USD" o "PEN" en el campo Moneda

GASTOS BANCARIOS detectados:
  1. CARGO POR PORTES / CARGO POR PORTES       → portes mensuales
  2. CARGOS POR MANTENIMIENTO                  → mantenimiento mensual
  3. COMI FLAT XXXXXX-XXXXXXX                  → Comisión Cash Financiero mensual
  4. COMISION (genérico)                        → fallback

  Umbrales máximos por tipo (montos sobre esto → ALERTA):
    PORTES                : S/20   / US$5
    MANTENIMIENTO         : S/50   / US$10
    COMI FLAT             : S/300  / US$100
    COMISION              : S/100  / US$30

IMPUESTO ITF:
  - Aparece inline como operación con descripción que empieza con "ITF"
  - Patrones identificados:
      · "ITF 6011CR TRF/EXT"        → ITF transferencia exterior/interbancaria
      · "ITF 6915TRANSF.CTA"        → ITF transferencia entre cuentas
      · "ITF 5915TRANS.INTE"        → ITF transferencia interna
  - Umbral dinámico: (saldo_anterior + abonos_mes) × 0.00005
  - Mínimo US$0.50 / S/0.50

ALERTAS:
  - Monto supera umbral → no contabilizar, revisar

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

UMBRALES = {
    "PORTES":        (20,    5),
    "MANTENIMIENTO": (50,   10),
    "COMI FLAT":     (300, 100),
    "COMISION":      (100,  30),
}

MESES_NOMBRE = {
    "01": "01", "02": "02", "03": "03", "04": "04",
    "05": "05", "06": "06", "07": "07", "08": "08",
    "09": "09", "10": "10", "11": "11", "12": "12",
}

# Patrones ITF (descripción empieza con ITF)
ITF_RE = re.compile(r'^ITF\s+', re.IGNORECASE)

# Patrones de gastos bancarios (orden: más específico primero)
PATRONES_GASTOS = [
    ("CARGOS POR MANTENIMIENTO",  "MANTENIMIENTO"),
    ("CARGO POR MANTENIMIENTO",   "MANTENIMIENTO"),
    ("MANTENIMIENTO",             "MANTENIMIENTO"),
    ("CARGO POR PORTES",          "PORTES"),
    ("CARGOS POR PORTES",         "PORTES"),
    ("PORTES",                    "PORTES"),
    ("COMI FLAT",                 "COMI FLAT"),
    ("COMISION",                  "COMISION"),
    ("COMISIÓN",                  "COMISION"),
]

PATRONES_IGNORAR = [
    "ORD ", "I/T-", "I/T ", "INTERESES", "RETIRO",
    "CAPITALIZACION", "TRANSF CCE", "CRÉDITO",
]

# Regex para montos Pichincha
# PDF físico: "1.65-"  "46.86-"  "30,810.39CR"
# Sistema:    "- 1.65"  "27,910.07"
MONTO_CARGO_FISICO_RE = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})-')
MONTO_SISTEMA_RE      = re.compile(r'-\s*(\d{1,3}(?:,\d{3})*\.\d{2})')
MONTO_GENERICO_RE     = re.compile(r'(\d{1,3}(?:,\d{3})*\.\d{2})')


# ---------------------------------------------------------------------------
# Extracción de texto
# ---------------------------------------------------------------------------

def extraer_texto_pdf(ruta_pdf: str) -> str:
    """
    Extrae texto del PDF usando la mejor estrategia según el tipo:
    - Si pdfplumber da líneas largas (> 60 chars promedio) → es formato sistema
      o físico con tabla, usar pdfplumber.
    - Si pdfplumber falla o da líneas cortas (campos separados) → usar fitz.
    """
    t_plumber = _extraer_pdfplumber(ruta_pdf)

    if t_plumber.strip():
        # Calcular longitud promedio de líneas para detectar formato
        lineas = [l for l in t_plumber.splitlines() if len(l.strip()) > 10]
        avg_len = sum(len(l) for l in lineas) / max(len(lineas), 1)
        # Si las líneas son largas → pdfplumber tiene el layout correcto
        if avg_len > 50:
            return t_plumber

    # Fitz como fallback (físico con campos separados o pdfplumber falló)
    t_fitz = _extraer_fitz(ruta_pdf)
    return t_fitz if t_fitz.strip() else t_plumber


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


# ---------------------------------------------------------------------------
# Detección de formato y cabecera
# ---------------------------------------------------------------------------

def detectar_formato(texto: str) -> str:
    """Retorna 'fisico' o 'sistema'."""
    t = texto.upper()
    if "SOLICITUD DE CORTE" in t or "DESCRIPCIÓN DE TRANSACCIÓN" in t:
        return "sistema"
    if "ESTADO DE CUENTA DE AHORRO" in t or "DIA" in t and "N°DOC" in t:
        return "fisico"
    return "fisico"  # default


def detectar_moneda(texto: str):
    t = texto.upper()
    if "DOLARES" in t or "USD" in t or "AHO - USD" in t:
        return "DOLARES", "US$"
    return "SOLES", "S/"


def detectar_periodo(texto: str, nombre_archivo: str):
    # PDF físico: "FECHA: 31/01/26"
    m = re.search(r'FECHA:\s*\d{2}/(\d{2})/(\d{2})', texto, re.IGNORECASE)
    if m:
        mes  = m.group(1)
        anio = "20" + m.group(2)
        return anio, mes

    # Sistema: "Desde: DD/MM/YYYY  Hasta: DD/MM/YYYY"
    m2 = re.search(r'Hasta[:\s]+\d{2}/(\d{2})/(\d{4})', texto, re.IGNORECASE)
    if m2:
        return m2.group(2), m2.group(1)

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
    # PDF físico: "CTA.: 000-001722675152"
    m = re.search(r'CTA\.[:\s]+(\d{3}-\d{12,15})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    # Sistema: "001722675152 - BANCO PICHINCHA"
    m2 = re.search(r'(\d{12,15})\s*-\s*BANCO PICHINCHA', texto, re.IGNORECASE)
    if m2:
        return m2.group(1)
    return ""


def detectar_ruc(texto: str) -> str:
    m = re.search(r'R\.U\.C\.?\s*[:\s]+(\d{11})', texto, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r'Ruc[:\s]+(\d{11})', texto, re.IGNORECASE)
    return m2.group(1) if m2 else ""


def detectar_cliente(texto: str) -> str:
    m = re.search(r'(?:TITULAR|Nombre)[:\s]+([A-Z][A-Z\s\.]+S\.?A\.?C?\.?)', texto)
    if m:
        return m.group(1).strip()
    return ""


def detectar_saldo_anterior(texto: str) -> float:
    # PDF físico: "SALDO AL 31/12/25   .63CR"  o  "SALDO AL 31/01/26   31,870.33CR"
    m = re.search(r'SALDO AL\s+\d{2}/\d{2}/?\d{0,4}\s+([\d,]+\.\d{2})CR', texto, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ""))
    # Sistema: "Saldo Disponible: 31,870.33"
    m2 = re.search(r'Saldo\s+(?:Disponible|Contable)[:\s]+([\d,]+\.\d{2})', texto, re.IGNORECASE)
    if m2:
        return float(m2.group(1).replace(",", ""))
    return 0.0


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def limpiar_monto(valor: str) -> float:
    try:
        return round(float(str(valor).strip().replace(",", "").replace("-", "").replace("CR", "")), 2)
    except Exception:
        return 0.00


def normalizar(texto: str) -> str:
    return str(texto).upper().strip()


def _umbral(concepto: str, moneda: str) -> float:
    idx = 0 if moneda == "SOLES" else 1
    return UMBRALES.get(concepto, (99999, 99999))[idx]


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------

def es_itf(descripcion: str) -> bool:
    return bool(ITF_RE.match(descripcion.strip()))


def clasificar_gasto(descripcion: str) -> str | None:
    desc = normalizar(descripcion)
    for patron in PATRONES_IGNORAR:
        if patron in desc:
            return None
    for patron, concepto in PATRONES_GASTOS:
        if patron in desc:
            return concepto
    return None


# ---------------------------------------------------------------------------
# Constructor de registro
# ---------------------------------------------------------------------------

def _hacer_registro(
    archivo, anio, mes, cliente, ruc, cuenta, moneda, simbolo,
    categoria, concepto_detectado, fecha, monto, linea_original,
    contabilizar=True, motivo_alerta=""
) -> dict:
    return {
        "Archivo":            archivo,
        "Año":                anio,
        "Mes":                mes,
        "Banco":              "PICHINCHA",
        "Cliente":            cliente,
        "RUC":                ruc,
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
# Parser formato FÍSICO
# ---------------------------------------------------------------------------

def _parsear_fisico(lineas: list, mes_estado: str):
    """
    Parsea líneas del formato PDF físico Pichincha.
    Retorna lista de dict con: fecha, descripcion, monto_cargo, linea
    """
    movimientos = []
    # Líneas de transacción empiezan con DD/MM (día/mes)
    TX_RE = re.compile(r'^(\d{2}/\d{2})\s+\d+\s+(.+)')

    for linea in lineas:
        linea = linea.strip()
        m = TX_RE.match(linea)
        if not m:
            continue

        fecha_raw = m.group(1)
        resto     = m.group(2)

        # Extraer monto cargo: número seguido de guión al final
        # Buscar todos los números con guión
        cargos = MONTO_CARGO_FISICO_RE.findall(linea)
        if not cargos:
            continue

        # El primer monto con guión que no sea el saldo (los saldos van al final con CR)
        monto = limpiar_monto(cargos[0])
        if monto <= 0:
            continue

        # Descripción: todo antes del primer número
        desc_match = re.match(r'^\d{2}/\d{2}\s+\d+\s+(.+?)(?:\s+[\d,]+\.\d{2})', linea)
        descripcion = desc_match.group(1).strip() if desc_match else resto.strip()

        # Agregar mes/año del estado para completar fecha
        dia_mes = fecha_raw  # "DD/MM"

        movimientos.append({
            "fecha":       dia_mes,
            "descripcion": descripcion,
            "monto":       monto,
            "linea":       linea,
        })

    return movimientos



# ---------------------------------------------------------------------------
# Parser formato FÍSICO con campos separados (fitz)
# ---------------------------------------------------------------------------

def _parsear_fisico_campos(lineas: list):
    """
    Parser para PDFs físicos Pichincha donde fitz extrae cada campo
    en una línea separada:
      "DD/MM"  → "NDOC"  → "OPERACION"  → ["DESCRIPCION"]  → montos
    """
    movimientos = []
    FECHA_RE = re.compile(r'^\d{2}/\d{2}$')
    NDOC_RE  = re.compile(r'^\d{5,7}$')
    MONTO_RE = re.compile(r'^\d{1,3}(?:,\d{3})*\.\d{2}-?$')

    i = 0
    while i < len(lineas):
        l = lineas[i].strip()

        if not FECHA_RE.match(l):
            i += 1
            continue

        fecha = l
        i += 1

        # N°DOC (puede ser 0000000 o número real)
        if i < len(lineas) and NDOC_RE.match(lineas[i].strip()):
            i += 1

        # OPERACION (descripción principal)
        if i >= len(lineas):
            break
        operacion = lineas[i].strip()
        i += 1

        # DESCRIPCION opcional (texto no numérico)
        descripcion_extra = ""
        if i < len(lineas) and not MONTO_RE.match(lineas[i].strip()):
            candidato = lineas[i].strip()
            # Si no es otra fecha ni ndoc → es descripción extra
            if not FECHA_RE.match(candidato) and not NDOC_RE.match(candidato):
                descripcion_extra = candidato
                i += 1

        # Recolectar montos hasta la próxima fecha o fin
        montos_cargo = []
        while i < len(lineas):
            l_num = lineas[i].strip()
            if FECHA_RE.match(l_num) or (len(l_num) < 5 and not MONTO_RE.match(l_num)):
                break
            m = MONTO_RE.match(l_num)
            if m:
                # Monto con guión = cargo; sin guión = abono o saldo
                if l_num.endswith('-'):
                    montos_cargo.append(limpiar_monto(l_num))
                i += 1
            else:
                break

        if not montos_cargo:
            continue

        # Usar el primer monto cargo
        monto = montos_cargo[0]
        if monto <= 0:
            continue

        movimientos.append({
            "fecha":       fecha,
            "descripcion": operacion,
            "monto":       monto,
            "linea":       f"{fecha} {operacion} {monto:.2f}-",
        })

    return movimientos


# ---------------------------------------------------------------------------
# Parser formato SISTEMA (corte web)
# ---------------------------------------------------------------------------

def _parsear_sistema(lineas: list):
    """
    Parsea líneas del formato corte del sistema Pichincha.
    pdfplumber extrae el texto en una línea completa por transacción:
      "DD/MM/YYYY HH:MM:SS DESCRIPCION DÉBITO OF. PRINCIPAL - 1.35 SALDO"
    """
    movimientos = []
    # Regex para línea de transacción completa (formato pdfplumber, una línea por fila)
    # "DD/MM/YYYY HH:MM:SS DESCRIPCION TIPO AGENCIA - MONTO SALDO"
    # Usamos búsqueda de substrings para tipo en lugar de regex con tildes
    FECHA_TX = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2})\s+(.+)$')
    MONTO_CARGO = re.compile(r'\s-\s([\d,]+\.\d{2})\s[\d,]+\.\d{2}\s*$')

    for linea in lineas:
        l = linea.strip()
        # Normalizar caracteres especiales para comparación
        l_norm = l.replace('É', 'E').replace('é', 'e').replace('Ó', 'O')

        m = FECHA_TX.match(l_norm)
        if not m:
            continue

        fecha_full = m.group(1)
        resto      = m.group(3)

        # Detectar tipo (DEBITO/CREDITO) en la línea
        if 'DEBITO' in l_norm.upper() or 'DÉBITO' in l.upper():
            tipo = 'DEBITO'
        elif 'CREDITO' in l_norm.upper() or 'CRÉDITO' in l.upper():
            tipo = 'CREDITO'
        else:
            continue

        # Solo DÉBITO
        if tipo != 'DEBITO':
            continue

        # Extraer descripción: todo antes de DEBITO/CREDITO
        for marcador in ['DÉBITO', 'DEBITO', 'CRÉDITO', 'CREDITO']:
            idx = l.upper().find(marcador)
            if idx != -1:
                # descripción está después de HORA y antes del tipo
                partes_header = l[:idx].split(None, 2)  # fecha hora descripcion
                descripcion = partes_header[2].strip() if len(partes_header) > 2 else ""
                break
        else:
            continue

        # Extraer monto cargo: " - 1.35 SALDO" al final
        m2 = MONTO_CARGO.search(l)
        if not m2:
            # Alternativa: buscar "- MONTO" al final
            m3 = re.search(r'\s-\s?([\d,]+\.\d{2})\s', l)
            if not m3:
                continue
            monto = limpiar_monto(m3.group(1))
        else:
            monto = limpiar_monto(m2.group(1))

        if monto <= 0:
            continue

        partes = fecha_full.split("/")
        dia_mes = f"{partes[0]}/{partes[1]}"

        movimientos.append({
            "fecha":       dia_mes,
            "descripcion": descripcion,
            "monto":       monto,
            "linea":       l,
        })

    return movimientos


# ---------------------------------------------------------------------------
# Cálculo de abonos para umbral ITF
# ---------------------------------------------------------------------------

def _calcular_abonos(texto: str, formato: str) -> float:
    """Suma de abonos para calcular umbral ITF dinámico."""
    total = 0.0
    if formato == "fisico":
        # En el formato físico buscar TOTAL seguido de monto abono
        m = re.search(r'TOTAL\s+(?:S/|USD\.?|US\$)?\s*([\d,]+\.\d{2})', texto, re.IGNORECASE)
        if m:
            total = float(m.group(1).replace(",", ""))
    else:
        # Sistema: sumar todos los CRÉDITO
        for linea in texto.splitlines():
            if "CRÉDITO" in linea.upper() or "CREDITO" in linea.upper():
                nums = MONTO_GENERICO_RE.findall(linea)
                if nums:
                    try:
                        total += float(nums[0].replace(",", ""))
                    except Exception:
                        pass
    return total


# ---------------------------------------------------------------------------
# Procesamiento principal
# ---------------------------------------------------------------------------

def _leer_totales_header(texto: str, nombre_archivo: str, ruta_archivo: str) -> dict | None:
    """
    Fallback para PDFs imagen de Pichincha sistema donde solo se puede leer
    el header con totales: "ITF:4.55"  "COMISION:S/51.86"
    Retorna un dict con registros sintéticos o None si no hay datos.
    """
    # Intentar leer totales del header
    itf_m   = re.search(r'ITF[:\s]*\$?S?/?\s*([\d.]+)', texto, re.IGNORECASE)
    comis_m = re.search(r'COMISION[:\s]*\$?S?/?\s*([\d.]+)', texto, re.IGNORECASE)

    if not itf_m and not comis_m:
        return None

    moneda, simbolo = detectar_moneda(texto)
    anio, mes       = detectar_periodo(texto, nombre_archivo)
    cliente         = detectar_cliente(texto)
    ruc             = detectar_ruc(texto)
    cuenta          = detectar_cuenta(texto)

    # También intentar con fitz para más datos del header
    t_fitz = _extraer_fitz(ruta_archivo)
    if not itf_m:
        itf_m = re.search(r'ITF[:\s]*\$?S?/?\s*([\d.]+)', t_fitz, re.IGNORECASE)
    if not comis_m:
        comis_m = re.search(r'COMISION[:\s]*\$?S?/?\s*([\d.]+)', t_fitz, re.IGNORECASE)
    if not moneda or moneda == "NO DETECTADA":
        moneda, simbolo = detectar_moneda(t_fitz)
    if anio == "SIN_ANIO":
        anio, mes = detectar_periodo(t_fitz, nombre_archivo)

    registros = []
    campos = dict(
        archivo=nombre_archivo, anio=anio, mes=mes,
        cliente=cliente, ruc=ruc, cuenta=cuenta,
        moneda=moneda, simbolo=simbolo,
    )

    nota = "Extraído del total del header (PDF imagen — movimientos no legibles individualmente)"

    if itf_m:
        monto_itf = float(itf_m.group(1))
        if monto_itf > 0:
            registros.append(_hacer_registro(
                **campos, categoria="IMPUESTO ITF",
                concepto_detectado="ITF (total header)",
                fecha="", monto=monto_itf,
                linea_original=f"ITF:{simbolo}{monto_itf}",
                contabilizar=True, motivo_alerta=nota,
            ))

    if comis_m:
        monto_comis = float(comis_m.group(1))
        if monto_comis > 0:
            registros.append(_hacer_registro(
                **campos, categoria="GASTOS BANCARIOS",
                concepto_detectado="COMISION (total header)",
                fecha="", monto=monto_comis,
                linea_original=f"COMISION:{simbolo}{monto_comis}",
                contabilizar=True, motivo_alerta=nota,
            ))

    return {"registros": registros, "alertas": []} if registros else None


def procesar_pdf(nombre_archivo: str, ruta_archivo: str) -> dict:
    texto = extraer_texto_pdf(ruta_archivo)
    if not texto.strip():
        return {"registros": [], "alertas": []}

    t_upper = texto.upper()
    if not any(k in t_upper for k in ["BANCO PICHINCHA", "PICHINCHA", "ESTADO DE CUENTA DE AHORRO",
                                        "SOLICITUD DE CORTE", "CTA.: 000-001"]):
        return {"registros": [], "alertas": []}

    formato         = detectar_formato(texto)
    moneda, simbolo = detectar_moneda(texto)
    anio, mes       = detectar_periodo(texto, nombre_archivo)
    cliente         = detectar_cliente(texto)
    ruc             = detectar_ruc(texto)
    cuenta          = detectar_cuenta(texto)
    saldo_anterior  = detectar_saldo_anterior(texto)
    total_abonos    = _calcular_abonos(texto, formato)
    itf_max         = max((saldo_anterior + total_abonos) * TASA_ITF, 0.50)

    lineas = [l.strip() for l in texto.splitlines() if l.strip()]

    if formato == "fisico":
        linea_completa = any(
            re.match(r'^\d{2}/\d{2}\s+\d{7}\s+\S', l) for l in lineas
        )
        if linea_completa:
            movimientos = _parsear_fisico(lineas, mes)
        else:
            movimientos = _parsear_fisico_campos(lineas)
    else:
        movimientos = _parsear_sistema(lineas)

    # Si no hay movimientos y es formato sistema → puede ser PDF imagen
    # Intentar leer totales del header como fallback
    if not movimientos and formato == "sistema":
        fallback = _leer_totales_header(texto, nombre_archivo, ruta_archivo)
        if fallback:
            return fallback

    campos_base = dict(
        archivo=nombre_archivo, anio=anio, mes=mes,
        cliente=cliente, ruc=ruc, cuenta=cuenta,
        moneda=moneda, simbolo=simbolo,
    )

    registros = []
    alertas   = []

    for mov in movimientos:
        desc  = mov["descripcion"]
        monto = mov["monto"]
        fecha = mov["fecha"]
        linea = mov["linea"]

        # ── ITF ──────────────────────────────────────────────────────────
        if es_itf(desc):
            if monto > itf_max:
                motivo = (
                    f"ITF de {simbolo} {monto:.2f} supera el máximo teórico posible "
                    f"({simbolo} {itf_max:.2f}). Verificar en el estado de cuenta original."
                )
                alertas.append(_hacer_registro(
                    **campos_base, categoria="IMPUESTO ITF",
                    concepto_detectado="ITF SOSPECHOSO",
                    fecha=fecha, monto=monto, linea_original=linea,
                    contabilizar=False, motivo_alerta=motivo,
                ))
            else:
                alertas_itf = desc.upper()
                concepto_itf = "ITF"
                registros.append(_hacer_registro(
                    **campos_base, categoria="IMPUESTO ITF",
                    concepto_detectado=concepto_itf,
                    fecha=fecha, monto=monto, linea_original=linea,
                    contabilizar=True,
                ))
            continue

        # ── GASTOS BANCARIOS ──────────────────────────────────────────────
        concepto = clasificar_gasto(desc)
        if concepto is None:
            continue

        umbral = _umbral(concepto, moneda)
        if monto > umbral:
            motivo = (
                f"Monto {simbolo} {monto:,.2f} supera el máximo esperado "
                f"({simbolo} {umbral:,.2f}) para '{concepto}'. "
                f"Revisar el estado de cuenta original."
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
