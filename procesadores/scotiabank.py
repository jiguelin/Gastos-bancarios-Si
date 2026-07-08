"""
scotiabank.py — versión 1.0
======================================================
Investiga y clasifica gastos bancarios e ITF de estados
de cuenta Scotiabank (CUENTA DE AHORROS M.E. DOLARES AMERICANOS).

FORMATO DEL ESTADO DE CUENTA:
  Columnas: FECHA OPER | FECHA VALOR | ORIG | CONCEPTO | REFERENCIA | CARGO | ABONO | SALDO
  - Una línea completa por transacción (pdfplumber lo extrae bien)
  - Fechas: "DD/MM" (sin año — el año se toma del encabezado)
  - Montos sin signo — la columna CARGO o ABONO determina el tipo
  - Formato de línea:
      "DD/MM DD/MM ORIG CONCEPTO REFERENCIA CARGO_o_ABONO SALDO"
  - Ejemplo cargo:
      "17/03 17/03 001 IMPUESTO A LOS DEBITOS 0120260317 0.20 33,023.58"
  - Ejemplo abono:
      "31/03 31/03 001 INTERESES ACREEDORES 0999030323 1.87 4.18"
  - Para distinguir cargo vs abono: la posición del monto en la línea
    se compara con la tabla de totales al pie: "Saldo Final al DD de MES del YYYY"
    donde aparecen cargo total y abono total en posiciones fijas.
    Alternativa más simple: por concepto — ITF y gastos son siempre cargos,
    INTERESES ACREEDORES siempre es abono.

IMPUESTO ITF (dos subtipos):
  - "IMPUESTO A LOS DEBITOS"  → ITF sobre débitos de la cuenta
  - "IMPUESTO A LOS CREDITOS" → ITF sobre créditos de la cuenta
  Ambos son cargos al cliente. El resumen al pie del estado tiene la tabla:
    "Impuesto a los Débitos  | monto_usd | monto_soles"
    "Impuesto a los Créditos | monto_usd | monto_soles"
  Se usa ese resumen como validación cruzada.

GASTOS BANCARIOS:
  1. "Mant Tarjet Empres Certificada" / "MANT TARJET"  → mantenimiento tarjeta empresarial
  2. "COMISION TARJETA JOY NEGOCIOS" / "COMISION TARJETA" → comisión tarjeta Joy Negocios
  3. "COMISION" (genérico)                              → fallback

  IGNORADOS (no son gastos bancarios propios):
    TRANSF.CTAS PROPIAS/CCE, TBK-TRANSFERENC.INMEDIATA CCE,
    TRANSFERENCIA VIA CCE, TRANSFERENCIA ENTRE CUENTAS,
    TRANSFERENCIA INMED. CCE LINEA, ORD. PAGO EMITIDA,
    ORD. PAGO RECIBIDA, DEPOSITO CHEQUE O.B., TRF/CCE DEVOLUCIONES

  Umbrales (montos sobre esto → ALERTA):
    MANT TARJET      : US$150
    COMISION TARJETA : US$50
    COMISION         : US$100

INTERESES A FAVOR:
  - "INTERESES ACREEDORES" → siempre en columna ABONO
  - Se registra como categoría "INTERESES A FAVOR"

Solo dólares por ahora (no hay ejemplos de soles disponibles).

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
MONEDA   = "DOLARES"
SIMBOLO  = "US$"

UMBRALES = {
    "MANT TARJET":       150,
    "COMISION TARJETA":   50,
    "COMISION":          100,
}

# Regex para montos en líneas Scotiabank
# Montos: "0.20"  "53.00"  "1,234.56"  "38,135.61"
MONTO_RE = re.compile(r'\b(\d{1,3}(?:,\d{3})*\.\d{2})\b')

# Regex para línea de transacción Scotiabank:
# "DD/MM DD/MM ORIG CONCEPTO REFERENCIA MONTO1 [MONTO2]"
TX_RE = re.compile(
    r'^(\d{2}/\d{2})\s+\d{2}/\d{2}\s+\d+\s+(.+?)\s+(\S+)\s+'
    r'([\d,]+\.\d{2})(?:\s+([\d,]+\.\d{2}))?$'
)

# ---------------------------------------------------------------------------
# Patrones de clasificación
# ---------------------------------------------------------------------------

PATRONES_ITF = [
    ("IMPUESTO A LOS DEBITOS",   "ITF DEBITOS"),
    ("IMPUESTO A LOS CREDITOS",  "ITF CREDITOS"),
]

PATRONES_GASTOS = [
    ("MANT TARJET",              "MANT TARJET"),
    ("MANT. TARJET",             "MANT TARJET"),
    ("MANTENIMIENTO TARJETA",    "MANT TARJET"),
    ("COMISION TARJETA",         "COMISION TARJETA"),
    ("COMISION",                 "COMISION"),
    ("COMISIÓN",                 "COMISION"),
]

PATRONES_INTERESES = [
    "INTERESES ACREEDORES",
]

PATRONES_IGNORAR = [
    "TRANSF.CTAS PROPIAS",
    "TRANSF CTAS PROPIAS",
    "TBK-TRANSFERENC",
    "TRANSFERENCIA VIA CCE",
    "TRANSFERENCIA ENTRE CUENTAS",
    "TRANSFERENCIA INMED",
    "ORD. PAGO EMITIDA",
    "ORD. PAGO RECIBIDA",
    "ORD PAGO EMITIDA",
    "ORD PAGO RECIBIDA",
    "DEPOSITO CHEQUE",
    "TRF/CCE DEVOLUCIONES",
    "TRANSF - GESTION",
]


# ---------------------------------------------------------------------------
# Extracción de texto
# ---------------------------------------------------------------------------

def extraer_texto_pdf(ruta_pdf: str) -> str:
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
    """
    Scotiabank: "Desde 01-MAR- 2026 Al 31-MAR-2026"
    """
    MESES = {
        "ENE":"01","FEB":"02","MAR":"03","ABR":"04","MAY":"05","JUN":"06",
        "JUL":"07","AGO":"08","SET":"09","SEP":"09","OCT":"10","NOV":"11","DIC":"12",
    }
    # Formato "Al DD-MMM- YYYY" o "Al DD-MMM-YYYY"
    m = re.search(r'Al\s+\d{1,2}[-\s]([A-Z]{3})[-\s]+(\d{4})', texto, re.IGNORECASE)
    if m:
        mes  = MESES.get(m.group(1).upper(), "00")
        anio = m.group(2)
        return anio, mes

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
    m = re.search(r'No\.\s*(\d{3}-\d{7})', texto, re.IGNORECASE)
    return m.group(1) if m else ""


def detectar_cliente(texto: str) -> str:
    # Aparece en la segunda línea grande del estado
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    for i, l in enumerate(lineas[:15]):
        if re.match(r'^[A-ZÁÉÍÓÚ][A-ZÁÉÍÓÚÑ\s\.]+(?:S\.?A\.?C?\.?|E\.?I\.?R\.?L\.?|S\.?A\.?)\s*$', l):
            return l
    return ""


def detectar_ruc(texto: str) -> str:
    m = re.search(r'RUC[\.:\s]+(\d{11})', texto, re.IGNORECASE)
    return m.group(1) if m else ""


def detectar_saldo_anterior(texto: str) -> float:
    # "Saldo Final al DD de MES del YYYY   38,135.61"
    m = re.search(r'Saldo Final al .+?\s+([\d,]+\.\d{2})\s*$', texto, re.MULTILINE)
    if m:
        return float(m.group(1).replace(",", ""))
    return 0.0


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def limpiar_monto(valor: str) -> float:
    try:
        return round(float(str(valor).strip().replace(",", "")), 2)
    except Exception:
        return 0.00


def normalizar(texto: str) -> str:
    return str(texto).upper().strip()


def _umbral(concepto: str) -> float:
    return UMBRALES.get(concepto, 99999)


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------

def _ignorar(desc_up: str) -> bool:
    return any(p in desc_up for p in PATRONES_IGNORAR)


def clasificar_concepto(descripcion: str):
    """
    Retorna (categoria, concepto_detectado, es_abono) o None.
    categoria: "IMPUESTO ITF" | "GASTOS BANCARIOS" | "INTERESES A FAVOR"
    """
    desc_up = normalizar(descripcion)

    if _ignorar(desc_up):
        return None

    # ITF primero
    for patron, concepto in PATRONES_ITF:
        if patron in desc_up:
            return ("IMPUESTO ITF", concepto, False)

    # Intereses a favor
    for patron in PATRONES_INTERESES:
        if patron in desc_up:
            return ("INTERESES A FAVOR", "INTERESES ACREEDORES", True)

    # Gastos bancarios
    for patron, concepto in PATRONES_GASTOS:
        if patron in desc_up:
            return ("GASTOS BANCARIOS", concepto, False)

    return None


# ---------------------------------------------------------------------------
# Parser de líneas de transacción
# ---------------------------------------------------------------------------

def _parsear_lineas(lineas: list):
    """
    Parsea líneas del formato Scotiabank:
      "DD/MM DD/MM ORIG CONCEPTO REFERENCIA MONTO [SALDO]"

    La posición de los montos:
      - Si hay 2 montos: el primero es CARGO o ABONO, el segundo es SALDO
      - Si hay 1 monto: es el SALDO (línea sin movimiento, ignorar)

    Para determinar si el único monto de movimiento es cargo o abono,
    se usa la lógica de clasificación: ITF y gastos → cargo; intereses → abono.
    """
    movimientos = []

    for linea in lineas:
        linea = linea.strip()

        # Debe empezar con DD/MM DD/MM ORIG
        m = re.match(r'^(\d{2}/\d{2})\s+\d{2}/\d{2}\s+(\d+)\s+(.+)', linea)
        if not m:
            continue

        fecha_raw = m.group(1)
        resto     = m.group(3)

        # Extraer todos los montos de la línea
        montos = MONTO_RE.findall(linea)
        if len(montos) < 2:
            continue  # necesita al menos monto de movimiento + saldo

        # El saldo es el último monto (o el último con sufijo CR no aplica aquí)
        # El monto de movimiento es el penúltimo
        monto_movimiento = limpiar_monto(montos[-2])
        if monto_movimiento <= 0:
            continue

        # Extraer descripción: todo entre ORIG y la referencia
        # La referencia es el token antes del primer monto numérico significativo
        # Heurística: descripción es todo antes del último grupo alfanumérico
        # antes de los montos
        desc_match = re.match(
            r'^\d{2}/\d{2}\s+\d{2}/\d{2}\s+\d+\s+(.+?)\s+\S+\s+[\d,]+\.\d{2}',
            linea
        )
        descripcion = desc_match.group(1).strip() if desc_match else resto.strip()

        # Limpiar montos de la descripción si quedaron pegados
        descripcion = re.sub(r'\s+[\d,]+\.\d{2}.*$', '', descripcion).strip()

        movimientos.append({
            "fecha":       fecha_raw,
            "descripcion": descripcion,
            "monto":       monto_movimiento,
            "linea":       linea,
        })

    return movimientos


# ---------------------------------------------------------------------------
# Validación cruzada con tabla de ITF al pie
# ---------------------------------------------------------------------------

def _leer_resumen_itf(texto: str) -> dict:
    """
    Scotiabank incluye tabla de resumen ITF al final:
      "Impuesto a los Débitos  | 0.30 | 1.02"
      "Impuesto a los Créditos | 4.10 | 13.93"
      "Total General           | 8.05 | 27.30"
    Lee estos valores como validación.
    """
    resultado = {}
    m_deb = re.search(
        r'Impuesto a los D[eé]bitos\s+([\d,]+\.\d{2})',
        texto, re.IGNORECASE
    )
    m_cred = re.search(
        r'Impuesto a los Cr[eé]ditos\s+([\d,]+\.\d{2})',
        texto, re.IGNORECASE
    )
    if m_deb:
        resultado["debitos"] = limpiar_monto(m_deb.group(1))
    if m_cred:
        resultado["creditos"] = limpiar_monto(m_cred.group(1))
    return resultado


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
        "Banco":              "SCOTIABANK",
        "Cliente":            cliente,
        "RUC":                ruc,
        "Cuenta":             cuenta,
        "Moneda":             MONEDA,
        "Símbolo":            SIMBOLO,
        "Concepto":           categoria,
        "Tipo ITF":           concepto_detectado if categoria == "IMPUESTO ITF" else "",
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
        "registros": [...],   # confirmados (Contabilizar=True)
        "alertas":   [...],   # dudosos (Contabilizar=False)
    }
    """
    texto = extraer_texto_pdf(ruta_archivo)
    if not texto.strip():
        return {"registros": [], "alertas": []}

    t_upper = texto.upper()
    if not any(k in t_upper for k in ["SCOTIABANK", "DOLARES AMERICANOS", "IMPUESTO A LOS DEBITOS",
                                       "INTERESES ACREEDORES"]):
        return {"registros": [], "alertas": []}

    anio, mes      = detectar_periodo(texto, nombre_archivo)
    cliente        = detectar_cliente(texto)
    ruc            = detectar_ruc(texto)
    cuenta         = detectar_cuenta(texto)
    saldo_anterior = detectar_saldo_anterior(texto)
    resumen_itf    = _leer_resumen_itf(texto)

    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    movimientos = _parsear_lineas(lineas)

    # Calcular total abonos para umbral ITF dinámico
    # Usar la suma de abonos del total al pie si está disponible
    total_abonos_linea = re.search(
        r'Saldo Final al .+?\s+[\d,]+\.\d{2}\s+([\d,]+\.\d{2})\s+[\d,]+\.\d{2}',
        texto, re.MULTILINE
    )
    total_abonos = float(total_abonos_linea.group(1).replace(",", "")) if total_abonos_linea else 0.0
    itf_max = max((saldo_anterior + total_abonos) * TASA_ITF, 0.50)

    campos_base = dict(
        archivo=nombre_archivo, anio=anio, mes=mes,
        cliente=cliente, ruc=ruc, cuenta=cuenta,
    )

    registros = []
    alertas   = []

    for mov in movimientos:
        desc  = mov["descripcion"]
        monto = mov["monto"]
        fecha = mov["fecha"]
        linea = mov["linea"]

        resultado = clasificar_concepto(desc)
        if resultado is None:
            continue

        categoria, concepto_detectado, es_abono = resultado

        # ── INTERESES A FAVOR ─────────────────────────────────────────────
        if categoria == "INTERESES A FAVOR":
            registros.append(_hacer_registro(
                **campos_base, categoria=categoria,
                concepto_detectado=concepto_detectado,
                fecha=fecha, monto=monto, linea_original=linea,
                contabilizar=True,
            ))
            continue

        # ── IMPUESTO ITF ──────────────────────────────────────────────────
        if categoria == "IMPUESTO ITF":
            if monto > itf_max:
                motivo = (
                    f"ITF de {SIMBOLO} {monto:.2f} supera el máximo teórico posible "
                    f"({SIMBOLO} {itf_max:.2f}). Verificar en el estado de cuenta original."
                )
                alertas.append(_hacer_registro(
                    **campos_base, categoria=categoria,
                    concepto_detectado="ITF SOSPECHOSO",
                    fecha=fecha, monto=monto, linea_original=linea,
                    contabilizar=False, motivo_alerta=motivo,
                ))
            else:
                registros.append(_hacer_registro(
                    **campos_base, categoria=categoria,
                    concepto_detectado=concepto_detectado,
                    fecha=fecha, monto=monto, linea_original=linea,
                    contabilizar=True,
                ))
            continue

        # ── GASTOS BANCARIOS ──────────────────────────────────────────────
        umbral = _umbral(concepto_detectado)
        if monto > umbral:
            motivo = (
                f"Monto {SIMBOLO} {monto:,.2f} supera el máximo esperado "
                f"({SIMBOLO} {umbral:,.2f}) para '{concepto_detectado}'. "
                f"Revisar el estado de cuenta original."
            )
            alertas.append(_hacer_registro(
                **campos_base, categoria=categoria,
                concepto_detectado=concepto_detectado,
                fecha=fecha, monto=monto, linea_original=linea,
                contabilizar=False, motivo_alerta=motivo,
            ))
        else:
            registros.append(_hacer_registro(
                **campos_base, categoria=categoria,
                concepto_detectado=concepto_detectado,
                fecha=fecha, monto=monto, linea_original=linea,
                contabilizar=True,
            ))

    # ── Validación cruzada con resumen ITF del pie ─────────────────────────
    # Si el resumen dice más ITF del que detectamos línea a línea,
    # agregamos un registro sintético con la diferencia como nota
    if resumen_itf:
        itf_detectado_deb  = sum(
            r["Monto"] for r in registros
            if r["Concepto"] == "IMPUESTO ITF" and "DEBIT" in r["Tipo ITF"]
        )
        itf_detectado_cred = sum(
            r["Monto"] for r in registros
            if r["Concepto"] == "IMPUESTO ITF" and "CREDIT" in r["Tipo ITF"]
        )

        dif_deb  = round(resumen_itf.get("debitos", 0) - itf_detectado_deb, 2)
        dif_cred = round(resumen_itf.get("creditos", 0) - itf_detectado_cred, 2)

        for dif, tipo_itf, label in [
            (dif_deb,  "ITF DEBITOS",  "Impuesto a los Débitos"),
            (dif_cred, "ITF CREDITOS", "Impuesto a los Créditos"),
        ]:
            if dif > 0.01:
                nota = (
                    f"Diferencia detectada: el resumen del estado de cuenta indica "
                    f"{SIMBOLO} {resumen_itf.get('debitos' if 'DEB' in tipo_itf else 'creditos', 0):.2f} "
                    f"de {label}, pero solo se leyeron {SIMBOLO} "
                    f"{itf_detectado_deb if 'DEB' in tipo_itf else itf_detectado_cred:.2f} "
                    f"en las líneas individuales. Se agrega la diferencia."
                )
                registros.append(_hacer_registro(
                    **campos_base, categoria="IMPUESTO ITF",
                    concepto_detectado=f"{tipo_itf} (complemento resumen)",
                    fecha="", monto=dif, linea_original=f"Resumen: {label} {dif:.2f}",
                    contabilizar=True, motivo_alerta=nota,
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
