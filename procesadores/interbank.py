"""
interbank.py — versión final completa
======================================================
Investiga y clasifica gastos bancarios e ITF de estados
de cuenta Interbank (CTA. NEGOCIOS PJ).

GASTOS BANCARIOS detectados:
  1. MANTENIMIENTO / MANTENIMIENTO PENDIENT    → S/50 fijo mensual
  2. COMISION EXCESO TIENDA / COMISION TIE     → cobro por exceder ops en tienda
  3. N/D AUTORIZADA + COMISION OP              → comisión por depósito en tienda
  4. COMI. PAGOS PLANILLA                      → S/1-6 por trabajador pagado
  5. N/D I-BANC + COM.O/C  (SOLES)             → registra SOLO S/5.30 de comisión
  6. N/D I-BANC + COM.O/C  (DÓLARES)           → registra SOLO US$1.16 de comisión

  Umbrales máximos por tipo (montos sobre esto → ALERTA):
    MANTENIMIENTO            : S/200  / US$50
    COMISION EXCESO TIENDA   : S/200  / US$100
    COMISION OP (tienda)     : S/500  / US$150
    COMI. PAGOS PLANILLA     : S/100  / US$30
    (I-BANC usa comisión fija, no necesita umbral)

IMPUESTO ITF:
  - Solo líneas que EMPIEZAN con "ITF" y tienen importe negativo
  - Umbral dinámico: (saldo_previo + abonos_mes) × 0.00005 con mínimo S/50
  - Patrón "N/A VARIOS" + ITF → siempre sospechoso (es abono, no impuesto)

ALERTAS — tres niveles:
  "alerta"    : monto supera umbral → NO se contabiliza, usuario debe revisar
  "sospechoso": patrón N/A VARIOS ITF → NO se contabiliza, mensaje explicativo
  (confirmado): todo lo demás → se contabiliza normalmente

Resultado de procesar_pdf():
  {
    "registros" : [...],   # movimientos confirmados
    "alertas"   : [...],   # para mostrar al usuario
  }
  Cada registro y alerta comparte las mismas columnas.
  Las alertas tienen además "motivo_alerta" y "contabilizar" = False.
"""

import re
import subprocess

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
TASA_ITF = 0.00005          # 0.005% — tasa vigente desde 2011, confirmada 2026
COMISION_IBANC_SOLES   = 5.30
COMISION_IBANC_DOLARES = 1.16

# Umbral máximo por tipo de gasto (soles / dólares)
# Cualquier monto sobre esto genera alerta en lugar de registrarse
UMBRALES = {
    "MANTENIMIENTO":          (200,  50),
    "COMISION EXCESO TIENDA": (200, 100),
    "COMISION OP":            (500, 150),
    "COMISION PLANILLA":      (100,  30),
}

MESES_NOMBRE = {
    "ENERO":"01","FEBRERO":"02","MARZO":"03","ABRIL":"04",
    "MAYO":"05","JUNIO":"06","JULIO":"07","AGOSTO":"08",
    "SETIEMBRE":"09","SEPTIEMBRE":"09","OCTUBRE":"10",
    "NOVIEMBRE":"11","DICIEMBRE":"12",
}

CANALES_CONOCIDOS = {"WEB","INTERNO","TIENDA","SOPORTE","ATM"}

# ---------------------------------------------------------------------------
# Regex de transacción  (pdftotext -layout → una línea por movimiento)
# ---------------------------------------------------------------------------
TX_RE = re.compile(
    r"^\s*(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+"
    r"(.+?)\s{2,}"
    r"(-?\d{1,3}(?:,\d{3})*\.\d{2}|-?\d+\.\d{2})"
    r"\s+"
    r"(\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2})"
    r"\s*$"
)
PARTES_RE = re.compile(r"\s{2,}")


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


def extraer_texto_pdf(ruta_pdf: str) -> str:
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", ruta_pdf, "-"],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout
    except Exception:
        return ""


def detectar_periodo(texto: str, nombre_archivo: str):
    m = re.search(r"Mes:\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+(\d{4})", texto, re.IGNORECASE)
    if m:
        return m.group(2), MESES_NOMBRE.get(m.group(1).upper(), "00")
    nombre = nombre_archivo.upper()
    m = re.search(r"(\d{2})(20\d{2})", nombre)
    if m:
        return m.group(2), m.group(1)
    m = re.search(r"(20\d{2})(\d{2})", nombre)
    if m:
        return m.group(1), m.group(2)
    return "SIN_ANIO", "00"


def _valor_cabecera(lineas: list, etiqueta: str) -> str:
    pat = re.compile(
        r"^\s*" + re.escape(etiqueta.upper().rstrip(":")) + r"\s*:\s*(.+)$",
        re.IGNORECASE,
    )
    for linea in lineas:
        m = pat.match(linea)
        if m:
            return m.group(1).strip()
    return ""


def detectar_cliente(lineas): return _valor_cabecera(lineas, "CLIENTE")
def detectar_cuenta(lineas):  return _valor_cabecera(lineas, "CUENTA")
def detectar_cci(lineas):     return _valor_cabecera(lineas, "CCI")

def detectar_moneda(lineas):
    v = _valor_cabecera(lineas, "MONEDA").upper()
    if "SOLES" in v:       return "SOLES",   "S/"
    if "DOLARES" in v or "DÓLARES" in v: return "DOLARES", "US$"
    # Formato "Consulta de Movimientos": "Cuenta: Corriente Soles 200-..."
    for linea in lineas:
        l = linea.upper()
        if "CORRIENTE SOLES" in l or "AHORRO SOLES" in l or "NEGOCIOS SOLES" in l:
            return "SOLES", "S/"
        if "CORRIENTE DOLAR" in l or "AHORRO DOLAR" in l or "NEGOCIOS DOLAR" in l:
            return "DOLARES", "US$"
        # Fallback: detect S/ symbol in amounts column header
        if "S/ " in linea and "IMPORTE" in l:
            return "SOLES", "S/"
    return "NO DETECTADA", "S/"   # default soles for Peruvian banks


def detectar_saldo_previo(texto: str) -> float:
    """
    El saldo previo es el primer número de la fila resumen del mes,
    que contiene al menos 8 montos seguidos (saldo_previo, depositos,
    cheques, otros, retiros, cheques_cargo, otros_cargo, acreedor,
    deudor, saldo_final).
    """
    for linea in texto.splitlines():
        nums = re.findall(r"\d{1,3}(?:,\d{3})*\.\d{2}", linea)
        if len(nums) >= 8:
            try:
                return float(nums[0].replace(",", ""))
            except Exception:
                pass
    return 0.0


# ---------------------------------------------------------------------------
# Parser de movimientos
# ---------------------------------------------------------------------------

def _parsear_subcampos(desc_raw: str):
    partes = [p.strip() for p in PARTES_RE.split(desc_raw.strip()) if p.strip()]
    movimiento = partes[0] if partes else ""
    detalle    = partes[1] if len(partes) > 1 else ""
    cod_op = ""
    canal  = ""
    for parte in partes[2:]:
        if parte.upper() in CANALES_CONOCIDOS:
            canal = parte.upper()
        elif re.fullmatch(r"\d{6,10}", parte):
            cod_op = parte
    return movimiento, detalle, cod_op, canal


def parsear_movimientos_layout(texto: str) -> list:
    movimientos = []
    for linea in texto.splitlines():
        m = TX_RE.match(linea)
        if not m:
            continue
        fo, fp, desc_raw, imp_txt, sal_txt = m.groups()
        mov, det, cod_op, canal = _parsear_subcampos(desc_raw)
        movimientos.append({
            "Fecha":          fo,
            "Fecha Proceso":  fp,
            "Movimiento":     mov,
            "Detalle":        det,
            "Descripcion":    (mov + " " + det).strip(),
            "Importe Texto":  imp_txt,
            "Saldo Texto":    sal_txt,
            "Código operación": cod_op,
            "Canal":          canal,
            "Línea original": linea.strip(),
        })
    return movimientos


def calcular_total_abonos(movimientos: list) -> float:
    """
    Suma todos los abonos reales del mes (importes positivos),
    excluyendo líneas N/A VARIOS que son abonos con referencia ITF
    (ej: préstamos disfrazados).
    """
    total = 0.0
    for mov in movimientos:
        imp = mov["Importe Texto"].strip()
        if imp.startswith("-"):
            continue
        desc_up = normalizar(mov["Descripcion"])
        if "N/A VARIOS" in desc_up:
            continue
        total += limpiar_monto(imp)
    return total


# ---------------------------------------------------------------------------
# Clasificación de gastos bancarios
# ---------------------------------------------------------------------------

def _es_ibanc_comc(desc: str) -> bool:
    return "N/D I-BANC + COM.O/C" in desc


def _concepto_gasto(desc: str, es_ibanc: bool) -> str:
    if es_ibanc:                                    return "COMISION I-BANC"
    if "MANTENIMIENTO" in desc:                     return "MANTENIMIENTO"
    if "COMISION EXCESO TIENDA" in desc:            return "COMISION EXCESO TIENDA"
    if "COMISION TIE" in desc:                      return "COMISION EXCESO TIENDA"
    if "N/D AUTORIZADA" in desc:                    return "COMISION OP"
    if "COMI." in desc and "PLANILLA" in desc:      return "COMISION PLANILLA"
    if "ENVIO" in desc or "ENVÍO" in desc:          return "ENVIO ESTADO DE CUENTA"
    if "ESTADO DE CUENTA" in desc or "EECC" in desc: return "ENVIO ESTADO DE CUENTA"
    if "MEMBRESIA" in desc or "MEMBRESÍA" in desc:  return "MEMBRESIA"
    if "RENOVACION" in desc or "RENOVACIÓN" in desc: return "RENOVACION"
    if "CARGO SERVICIO" in desc:                    return "CARGO SERVICIO"
    return "GASTO BANCARIO"


def clasificar_gasto(descripcion: str, importe_raw: str, moneda: str):
    """
    Retorna (concepto_detectado, monto_a_registrar) o (None, 0) si ignorar.
    Para N/D I-BANC + COM.O/C devuelve solo la comisión fija (5.30 / 1.16).
    """
    if not importe_raw.startswith("-"):
        return None, 0

    monto = limpiar_monto(importe_raw)
    if monto <= 0:
        return None, 0

    desc = normalizar(descripcion)

    # ── N/D I-BANC + COM.O/C ─────────────────────────────────────────────
    if _es_ibanc_comc(desc):
        fee = COMISION_IBANC_SOLES if moneda == "SOLES" else COMISION_IBANC_DOLARES
        return "COMISION I-BANC", fee if monto >= fee else 0

    # ── GASTOS DIRECTOS ───────────────────────────────────────────────────
    # Verificar GASTOS antes que IGNORAR: "COMI. PAGOS PLANILLA" contiene
    # "PAGO" que está en IGNORAR → se cortocircuitaría sin esta prioridad.

    PATRONES_GASTOS = [
        "MANTENIMIENTO",
        "COMISION EXCESO TIENDA",
        "COMISION TIE",
        "COMI.",               # COMI. PAGOS PLANILLA — NO debe coincidir con N/D I-BANC
        "ENVIO", "ENVÍO",
        "ESTADO DE CUENTA", "EECC",
        "CARGO SERVICIO", "CARGO POR SERVICIO",
        "MEMBRESIA", "MEMBRESÍA",
        "RENOVACION", "RENOVACIÓN",
    ]

    # N/D AUTORIZADA solo si también dice COMISION en la descripción completa
    if "N/D AUTORIZADA" in desc and "COMISION" in desc:
        return "COMISION OP", monto

    for patron in PATRONES_GASTOS:
        if patron in desc:
            return _concepto_gasto(desc, False), monto

    # ── IGNORAR ───────────────────────────────────────────────────────────
    PATRONES_IGNORAR = [
        "CARGO TRANSFERENCIA", "CARGO TRANSF",
        "ABONO", "CONSUMO POS", "FAST FUNDS",
        "TRANSFERENCIA", "PAGO",
        "DEPOSITO", "DEPÓSITO", "DEP.EFECTIVO", "DEP.CHQ",
        "RETIRO", "TRAN TIL",
        "N/D VARIOS", "N/A VARIOS",
        "CARGO PAGO PLANILLAS",
        "PAG SNT",
    ]

    for patron in PATRONES_IGNORAR:
        if patron in desc:
            return None, 0

    return None, 0


def _umbral_gasto(concepto: str, moneda: str) -> float:
    """Devuelve el monto máximo esperado para ese tipo de comisión."""
    idx = 0 if moneda == "SOLES" else 1
    return UMBRALES.get(concepto, (99999, 99999))[idx]


# ---------------------------------------------------------------------------
# Clasificación ITF
# ---------------------------------------------------------------------------

def clasificar_itf(descripcion: str, importe_raw: str, itf_max: float):
    """
    Retorna (es_itf, sospechoso, motivo_alerta).
    - es_itf        : True si la línea es un cargo ITF real
    - sospechoso    : True si el monto supera el umbral dinámico
    - motivo_alerta : string explicativo (vacío si todo OK)
    """
    desc = normalizar(descripcion)

    # Patrón N/A VARIOS: abono con referencia ITF, no es impuesto
    if "N/A VARIOS" in desc and "ITF" in desc:
        return False, True, (
            "Línea 'N/A VARIOS' con referencia ITF — es un abono o ingreso "
            "(ej: préstamo, devolución), NO un impuesto ITF. "
            "Verificar en el estado de cuenta original."
        )

    # Solo procesar líneas que EMPIECEN con ITF y sean cargo negativo
    if not re.match(r"^ITF\b", desc):
        return False, False, ""
    if not importe_raw.startswith("-"):
        return False, True, (
            "Línea ITF con importe positivo (abono) — no es un impuesto. "
            "Verificar en el estado de cuenta original."
        )

    monto = limpiar_monto(importe_raw)
    if monto <= 0:
        return False, False, ""

    # Verificar umbral dinámico
    umbral = max(itf_max, 50.0)   # mínimo S/50 como red de seguridad
    if monto > umbral:
        return True, True, (
            f"ITF de {monto:.2f} supera el máximo teórico posible para este estado "
            f"de cuenta ({umbral:.2f} = (saldo previo + abonos del mes) × 0.005%). "
            f"Verificar en el estado de cuenta original."
        )

    return True, False, ""


# ---------------------------------------------------------------------------
# Constructor de registro
# ---------------------------------------------------------------------------

def _hacer_registro(
    nombre_archivo, anio, mes, cliente, cuenta, cci, moneda, simbolo,
    mov, categoria, concepto_detectado, monto, contabilizar,
    motivo_alerta=""
) -> dict:
    return {
        "Archivo":            nombre_archivo,
        "Año":                anio,
        "Mes":                mes,
        "Banco":              "INTERBANK",
        "Cliente":            cliente,
        "Cuenta":             cuenta,
        "CCI":                cci,
        "Moneda":             moneda,
        "Símbolo":            simbolo,
        "Concepto":           categoria,
        "Tipo ITF":           "ITF" if categoria == "IMPUESTO ITF" else "",
        "Fecha":              mov["Fecha"],
        "Fecha Proceso":      mov["Fecha Proceso"],
        "Monto":              monto,
        "Concepto detectado": concepto_detectado,
        "Código operación":   mov["Código operación"],
        "Canal":              mov["Canal"],
        "Línea original":     mov["Línea original"],
        "Saldo":              limpiar_monto(mov["Saldo Texto"]),
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

    lineas = texto.splitlines()
    cliente        = detectar_cliente(lineas)
    cuenta         = detectar_cuenta(lineas)
    cci            = detectar_cci(lineas)
    moneda, simbolo = detectar_moneda(lineas)
    anio, mes      = detectar_periodo(texto, nombre_archivo)
    saldo_previo   = detectar_saldo_previo(texto)

    movimientos    = parsear_movimientos_layout(texto)
    total_abonos   = calcular_total_abonos(movimientos)
    itf_max        = (saldo_previo + total_abonos) * TASA_ITF

    registros = []
    alertas   = []

    campos_base = dict(
        nombre_archivo=nombre_archivo, anio=anio, mes=mes,
        cliente=cliente, cuenta=cuenta, cci=cci,
        moneda=moneda, simbolo=simbolo,
    )

    for mov in movimientos:
        descripcion = mov["Descripcion"]
        importe_raw = mov["Importe Texto"].strip()
        desc_up     = normalizar(descripcion)

        # ── ITF ──────────────────────────────────────────────────────────
        es_itf, sospechoso_itf, motivo_itf = clasificar_itf(
            descripcion, importe_raw, itf_max
        )

        if es_itf or sospechoso_itf:
            if sospechoso_itf:
                # No contabilizar — va a alertas
                alertas.append(_hacer_registro(
                    **campos_base, mov=mov,
                    categoria="IMPUESTO ITF",
                    concepto_detectado="ITF SOSPECHOSO",
                    monto=limpiar_monto(importe_raw),
                    contabilizar=False,
                    motivo_alerta=motivo_itf,
                ))
            else:
                monto = limpiar_monto(importe_raw)
                concepto = ("ITF OPERACION"
                            if "OPERACION" in desc_up or "OPERACIÓN" in desc_up
                            else "ITF")
                registros.append(_hacer_registro(
                    **campos_base, mov=mov,
                    categoria="IMPUESTO ITF",
                    concepto_detectado=concepto,
                    monto=monto,
                    contabilizar=True,
                ))
            continue

        # ── GASTOS BANCARIOS ──────────────────────────────────────────────
        concepto_gasto, monto_gasto = clasificar_gasto(
            descripcion, importe_raw, moneda
        )

        if concepto_gasto is None or monto_gasto <= 0:
            continue

        # Verificar umbral máximo (no aplica a COMISION I-BANC, ya tiene fee fijo)
        if concepto_gasto != "COMISION I-BANC":
            umbral = _umbral_gasto(concepto_gasto, moneda)
            if monto_gasto > umbral:
                motivo = (
                    f"Monto {simbolo} {monto_gasto:,.2f} supera el máximo esperado "
                    f"({simbolo} {umbral:,.2f}) para '{concepto_gasto}'. "
                    f"Revisar el estado de cuenta original — si es correcto, confirmar."
                )
                alertas.append(_hacer_registro(
                    **campos_base, mov=mov,
                    categoria="GASTOS BANCARIOS",
                    concepto_detectado=concepto_gasto,
                    monto=monto_gasto,
                    contabilizar=False,
                    motivo_alerta=motivo,
                ))
                continue

        registros.append(_hacer_registro(
            **campos_base, mov=mov,
            categoria="GASTOS BANCARIOS",
            concepto_detectado=concepto_gasto,
            monto=monto_gasto,
            contabilizar=True,
        ))

    return {"registros": registros, "alertas": alertas}


# ---------------------------------------------------------------------------
# Helpers para el reporte
# ---------------------------------------------------------------------------

def total_confirmado(resultado: dict, categoria: str = None) -> float:
    """Suma de registros confirmados, opcionalmente filtrado por categoría."""
    regs = resultado["registros"]
    if categoria:
        regs = [r for r in regs if r["Concepto"] == categoria]
    return round(sum(r["Monto"] for r in regs), 2)


def total_con_alertas(resultado: dict, categoria: str = None) -> float:
    """
    Suma confirmados + alertas de umbral (si el usuario decide confirmar los dudosos).
    Excluye alertas por patrón N/A VARIOS o importe positivo porque esas NO son
    cargas reales — son abonos o ingresos que nunca deben sumarse a gastos.
    """
    regs = resultado["registros"]
    # Solo alertas que son cargos reales con monto dudoso (exceso de umbral)
    # No las que son abonos/ingresos detectados como sospechosos
    alts = [a for a in resultado["alertas"]
            if a.get("Concepto detectado") != "ITF SOSPECHOSO"
            or "supera el máximo" in a.get("Motivo alerta", "")]
    todos = regs + alts
    if categoria:
        todos = [r for r in todos if r["Concepto"] == categoria]
    return round(sum(r["Monto"] for r in todos), 2)


def get_columnas_ordenadas() -> list:
    return [
        "Archivo", "Año", "Mes", "Banco", "Cliente", "Cuenta", "CCI",
        "Moneda", "Símbolo", "Concepto", "Tipo ITF",
        "Fecha", "Fecha Proceso", "Monto", "Concepto detectado",
        "Código operación", "Canal", "Línea original", "Saldo",
        "Contabilizar", "Motivo alerta",
    ]
