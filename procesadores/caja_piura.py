"""
caja_piura.py — versión 1.0
======================================================
Investiga y clasifica ITF, gastos bancarios e intereses de los
estados de cuenta de CAJA PIURA (CMAC-PIURA S.A.C.) — CUENTA AHORRO,
tanto en SOLES ("MONEDA NACIONAL") como en DÓLARES ("MONEDA EXTRANJERA").

FORMATO DEL ESTADO DE CUENTA:
  "ESTADO DE CUENTA MENSUAL - CUENTA AHORRO"
  Columnas: Día | Concepto | Depósitos | ITF | Retiros | ITF | Orden | Saldo

  · Hay DOS columnas de ITF: una a la derecha de Depósitos (ITF cobrado
    sobre un abono) y otra a la derecha de Retiros (ITF cobrado sobre un
    cargo). Ambas son ITF a cargo del cliente.
  · Los montos menores a 1 vienen sin cero inicial: ".10", ".05", ".07".
  · Fechas "DD/MM" — el año sale del encabezado "Del 01 al 31 de mayo de 2026".
  · Como las columnas solo se distinguen por su posición, el procesador
    lee las coordenadas de cada palabra con pdfplumber y asigna cada
    número a la columna cuyo encabezado está más cerca (alineado a la
    derecha).
  · Cada fila se valida contra el saldo:
        saldo_anterior + depósito − ITF_dep − retiro − ITF_ret = saldo
    Si no cuadra, el ITF/gasto de esa fila pasa a ALERTA.
  · La fila "Totales :" se usa como validación cruzada del ITF del mes.

IMPUESTO ITF:
  - Cualquier fila con ITF > 0 en cualquiera de las dos columnas.

GASTOS BANCARIOS (filas de Retiros cuyo concepto contiene):
  COMISION / COMIS / MANTENIMIENTO / PORTES / ENVIO / GASTO / CARGO POR

POSIBLE COMISIÓN CCE (ALERTA, no se suma salvo que se marque):
  - Caja Piura no muestra la comisión de las transferencias interbancarias
    en una línea aparte. En las "TRANSF. EN LÍNEA VÍA CCE" aparece un
    importe del tipo 8,002.30 / 602.30 / 20,002.30, es decir, un monto
    redondo + S/ 2.30. Se reporta esos 2.30 como alerta para revisión.

INTERESES A FAVOR:
  - "PAGO DE INTERESES" (columna Depósitos) → categoría "INTERESES A FAVOR"

Resultado de procesar_pdf():
  {
    "registros" : [...],   # confirmados (Contabilizar=True)
    "alertas"   : [...],   # dudosos (Contabilizar=False)
  }
"""

import re
import unicodedata
import pdfplumber

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
BANCO = "CAJA PIURA"

MONTO_RE = re.compile(r'^(?:\d{1,3}(?:,\d{3})*)?\.\d{2}$')
FECHA_RE = re.compile(r'^\d{2}/\d{2}$')

# Columnas en el orden del encabezado
COLUMNAS = ["deposito", "itf_dep", "retiro", "itf_ret", "orden", "saldo"]

# Posición (borde derecho, en puntos) de cada encabezado en los PDF de
# Caja Piura — solo se usa si no se encuentra el encabezado en la página.
POSICIONES_DEFECTO = {
    "deposito": 312.0, "itf_dep": 336.0, "retiro": 390.0,
    "itf_ret": 416.0, "orden": 464.0, "saldo": 520.0,
}

PATRONES_GASTOS = [
    ("MANTENIMIENTO", "MANTENIMIENTO DE CUENTA"),
    ("MANT.",         "MANTENIMIENTO DE CUENTA"),
    ("PORTES",        "PORTES"),
    ("ENVIO",         "ENVÍO DE ESTADO DE CUENTA"),
    ("COMISION",      "COMISIÓN"),
    ("COMIS",         "COMISIÓN"),
    ("GASTO",         "GASTO BANCARIO"),
    ("CARGO POR",     "CARGO BANCARIO"),
]

PATRONES_INTERESES = ["PAGO DE INTERESES", "INTERESES"]

UMBRAL_GASTO = {"SOLES": 50.0, "DOLARES": 20.0}
COMISION_CCE = 2.30

MESES = {
    "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04",
    "MAYO": "05", "JUNIO": "06", "JULIO": "07", "AGOSTO": "08",
    "SETIEMBRE": "09", "SEPTIEMBRE": "09", "OCTUBRE": "10",
    "NOVIEMBRE": "11", "DICIEMBRE": "12",
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _sin_tildes(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c))


def normalizar(texto: str) -> str:
    # El PDF trae "L?NEA V?A" en lugar de "LÍNEA VÍA"
    return _sin_tildes(texto).upper().replace("?", "I").strip()


def limpiar_monto(valor: str) -> float:
    try:
        return round(float(str(valor).strip().replace(",", "")), 2)
    except Exception:
        return 0.00


# ---------------------------------------------------------------------------
# Cabecera
# ---------------------------------------------------------------------------

def detectar_moneda(texto: str):
    t = normalizar(texto)
    if "MONEDA EXTRANJERA" in t:
        return "DOLARES", "US$"
    return "SOLES", "S/"


def detectar_periodo(texto: str, nombre_archivo: str):
    t = normalizar(texto)
    m = re.search(r'DEL\s+\d{1,2}\s+AL\s+\d{1,2}\s+DE\s+([A-Z]+)\s+DE\s+(\d{4})', t)
    if m and m.group(1) in MESES:
        return m.group(2), MESES[m.group(1)]
    # Respaldo: "Fecha: 01/06/2026" es el día siguiente al cierre
    m = re.search(r'FECHA:\s*\d{2}/(\d{2})/(\d{4})', t)
    if m:
        mes, anio = int(m.group(1)) - 1, int(m.group(2))
        if mes == 0:
            mes, anio = 12, anio - 1
        return str(anio), f"{mes:02d}"
    nombre = normalizar(nombre_archivo)
    for mes_nombre, mes_num in MESES.items():
        if mes_nombre in nombre:
            anio_m = re.search(r"(20\d{2})", nombre)
            return (anio_m.group(1) if anio_m else "SIN_ANIO"), mes_num
    return "SIN_ANIO", "00"


def detectar_cuenta(texto: str) -> str:
    m = re.search(r'(\d{3}-\d{2}-\d{5,})', texto)
    return m.group(1) if m else ""


def detectar_cliente(texto: str) -> str:
    m = re.search(r'Cliente\s*:\s*(.+)', texto)
    return re.split(r'\s{2,}', m.group(1).strip())[0] if m else ""


def detectar_ruc(texto: str) -> str:
    m = re.search(r'D\.O\.I\s*:\s*(\d{8,11})', texto)
    return m.group(1) if m else ""


def detectar_saldo_anterior(texto: str) -> float:
    m = re.search(r'Saldo al\s+[\d/]+\s*:\s*([\d,]*\.\d{2})', texto)
    return limpiar_monto(m.group(1)) if m else 0.0


# ---------------------------------------------------------------------------
# Lectura de filas por coordenadas
# ---------------------------------------------------------------------------

def _agrupar_lineas(words, tolerancia=3):
    lineas = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lineas and abs(lineas[-1][0]["top"] - w["top"]) <= tolerancia:
            lineas[-1].append(w)
        else:
            lineas.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in lineas]


def _posiciones_encabezado(lineas):
    """Busca la línea 'Día Concepto Depósitos ITF Retiros ITF Orden Saldo'."""
    for linea in lineas:
        textos = [normalizar(w["text"]) for w in linea]
        if "DEPOSITOS" in textos and "RETIROS" in textos and "ORDEN" in textos:
            pos = {}
            itfs = [w for w in linea if normalizar(w["text"]) == "ITF"]
            for w in linea:
                t = normalizar(w["text"])
                if t == "DEPOSITOS":
                    pos["deposito"] = w["x1"]
                elif t == "RETIROS":
                    pos["retiro"] = w["x1"]
                elif t == "ORDEN":
                    pos["orden"] = w["x1"]
                elif t == "SALDO":
                    pos["saldo"] = w["x1"]
            if len(itfs) >= 2:
                pos["itf_dep"] = itfs[0]["x1"]
                pos["itf_ret"] = itfs[1]["x1"]
            if len(pos) == 6:
                return pos
    return dict(POSICIONES_DEFECTO)


def _columna_de(x1: float, posiciones: dict) -> str:
    return min(posiciones, key=lambda c: abs(posiciones[c] - x1))


def _leer_filas(ruta_pdf: str):
    """Devuelve (texto_completo, filas, totales_itf o None)."""
    texto = ""
    filas = []
    totales_itf = None

    with pdfplumber.open(ruta_pdf) as pdf:
        for page in pdf.pages:
            texto += (page.extract_text() or "") + "\n"
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            lineas = _agrupar_lineas(words)
            posiciones = _posiciones_encabezado(lineas)
            limite_concepto = posiciones["deposito"] - 60

            for linea in lineas:
                primero = linea[0]["text"]
                es_total = normalizar(primero).startswith("TOTALES")
                if not (FECHA_RE.match(primero) or es_total):
                    continue

                concepto = []
                valores = {c: 0.0 for c in COLUMNAS}
                for w in linea[1:] if not es_total else linea:
                    t = w["text"]
                    if MONTO_RE.match(t) and w["x0"] > limite_concepto:
                        col = _columna_de(w["x1"], posiciones)
                        valores[col] = limpiar_monto(t)
                    elif w["x1"] < limite_concepto:
                        concepto.append(t)

                if es_total:
                    totales_itf = round(valores["itf_dep"] + valores["itf_ret"], 2)
                    continue

                desc = " ".join(concepto)
                filas.append({
                    "fecha": primero,
                    "descripcion": desc,
                    **valores,
                    "linea": " ".join(w["text"] for w in linea),
                })
    return texto, filas, totales_itf


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------

def _es_intereses(desc: str) -> bool:
    d = normalizar(desc)
    return any(p in d for p in PATRONES_INTERESES)


def _clasificar_gasto(desc: str):
    d = normalizar(desc)
    if "TRANSF" in d or "ABONO" in d or "DEPOSITO" in d or "RETIRO" in d:
        return None
    for patron, nombre in PATRONES_GASTOS:
        if patron in d:
            return nombre
    return None


def _posible_comision_cce(desc: str, retiro: float) -> bool:
    d = normalizar(desc)
    if "CCE" not in d or retiro <= COMISION_CCE:
        return False
    base = round(retiro - COMISION_CCE, 2)
    return base > 0 and abs(base - round(base / 100) * 100) < 0.001


# ---------------------------------------------------------------------------
# Constructor de registro
# ---------------------------------------------------------------------------

def _hacer_registro(
    archivo, anio, mes, cliente, ruc, cuenta, moneda, simbolo,
    categoria, concepto_detectado, fecha, monto, linea_original,
    contabilizar=True, motivo_alerta="", tipo_itf=""
) -> dict:
    return {
        "Archivo":            archivo,
        "Año":                anio,
        "Mes":                mes,
        "Banco":              BANCO,
        "Cliente":            cliente,
        "RUC":                ruc,
        "Cuenta":             cuenta,
        "Moneda":             moneda,
        "Símbolo":            simbolo,
        "Concepto":           categoria,
        "Tipo ITF":           tipo_itf,
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
    try:
        texto, filas, totales_itf = _leer_filas(ruta_archivo)
    except Exception:
        return {"registros": [], "alertas": [], "_sin_texto": True}

    if not texto.strip():
        return {"registros": [], "alertas": [], "_sin_texto": True}

    t_upper = normalizar(texto)
    if not any(k in t_upper for k in ["CMAC-PIURA", "CAJAPIURA", "CAJA PIURA"]):
        return {"registros": [], "alertas": []}

    anio, mes        = detectar_periodo(texto, nombre_archivo)
    moneda, simbolo  = detectar_moneda(texto)
    base = dict(
        archivo=nombre_archivo, anio=anio, mes=mes,
        cliente=detectar_cliente(texto), ruc=detectar_ruc(texto),
        cuenta=detectar_cuenta(texto), moneda=moneda, simbolo=simbolo,
    )
    saldo = detectar_saldo_anterior(texto)

    registros, alertas = [], []

    for f in filas:
        fecha, desc, linea = f["fecha"], f["descripcion"], f["linea"]

        # Validar la fila contra el saldo
        esperado = round(saldo + f["deposito"] - f["itf_dep"] - f["retiro"] - f["itf_ret"], 2)
        cuadra = f["saldo"] == 0 or abs(esperado - f["saldo"]) < 0.015
        motivo_cuadre = (
            f"La fila no cuadra con el saldo (esperado {simbolo} {esperado:,.2f}, "
            f"estado de cuenta {simbolo} {f['saldo']:,.2f}). Revisar las columnas en el PDF."
        )
        if f["saldo"]:
            saldo = f["saldo"]

        # ── ITF (dos columnas) ───────────────────────────────────────────
        for col, tipo in (("itf_dep", "ITF sobre depósito"), ("itf_ret", "ITF sobre retiro")):
            monto = f[col]
            if monto <= 0:
                continue
            operacion = f["deposito"] if col == "itf_dep" else f["retiro"]
            # ITF = 0.005% truncado a múltiplos de 0.05 → tolerancia amplia
            itf_max = max(operacion * 0.00005 + 0.05, 0.05)
            if not cuadra:
                motivo = motivo_cuadre
            elif monto > itf_max:
                motivo = (f"ITF de {simbolo} {monto:.2f} es mayor al esperado para una "
                          f"operación de {simbolo} {operacion:,.2f}. Verificar en el PDF.")
            else:
                motivo = ""
            reg = _hacer_registro(
                **base, categoria="IMPUESTO ITF", concepto_detectado=f"ITF — {desc}",
                fecha=fecha, monto=monto, linea_original=linea,
                contabilizar=not motivo, motivo_alerta=motivo, tipo_itf=tipo,
            )
            (alertas if motivo else registros).append(reg)

        # ── INTERESES A FAVOR ────────────────────────────────────────────
        if f["deposito"] > 0 and _es_intereses(desc):
            registros.append(_hacer_registro(
                **base, categoria="INTERESES A FAVOR", concepto_detectado=desc,
                fecha=fecha, monto=f["deposito"], linea_original=linea,
            ))
            continue

        # ── GASTOS BANCARIOS ─────────────────────────────────────────────
        if f["retiro"] > 0:
            gasto = _clasificar_gasto(desc)
            if gasto:
                umbral = UMBRAL_GASTO[moneda]
                if not cuadra:
                    motivo = motivo_cuadre
                elif f["retiro"] > umbral:
                    motivo = (f"{gasto} de {simbolo} {f['retiro']:,.2f} supera el máximo "
                              f"esperado ({simbolo} {umbral:,.2f}). Revisar el PDF.")
                else:
                    motivo = ""
                reg = _hacer_registro(
                    **base, categoria="GASTOS BANCARIOS", concepto_detectado=gasto,
                    fecha=fecha, monto=f["retiro"], linea_original=linea,
                    contabilizar=not motivo, motivo_alerta=motivo,
                )
                (alertas if motivo else registros).append(reg)

            elif moneda == "SOLES" and _posible_comision_cce(desc, f["retiro"]):
                alertas.append(_hacer_registro(
                    **base, categoria="GASTOS BANCARIOS",
                    concepto_detectado="POSIBLE COMISIÓN TRANSF. CCE",
                    fecha=fecha, monto=COMISION_CCE, linea_original=linea,
                    contabilizar=False,
                    motivo_alerta=(
                        f"La transferencia CCE de S/ {f['retiro']:,.2f} parece ser un monto "
                        f"redondo (S/ {f['retiro'] - COMISION_CCE:,.2f}) + S/ 2.30. "
                        f"Caja Piura no muestra la comisión aparte: confirmar con el "
                        f"voucher antes de incluirla."
                    ),
                ))

    # ── Validación cruzada con la fila "Totales" ─────────────────────────
    itf_detectado = round(sum(r["Monto"] for r in registros + alertas
                              if r["Concepto"] == "IMPUESTO ITF"), 2)
    if totales_itf is not None and abs(itf_detectado - totales_itf) > 0.009:
        alertas.append(_hacer_registro(
            **base, categoria="IMPUESTO ITF", concepto_detectado="DIFERENCIA ITF vs TOTALES",
            fecha="", monto=round(totales_itf - itf_detectado, 2),
            linea_original=f"Totales ITF del PDF: {totales_itf:.2f} · detectado: {itf_detectado:.2f}",
            contabilizar=False,
            motivo_alerta="El ITF detectado fila por fila no coincide con la fila Totales del PDF.",
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


def get_columnas_ordenadas() -> list:
    return [
        "Archivo", "Año", "Mes", "Banco", "Cliente", "RUC", "Cuenta",
        "Moneda", "Símbolo", "Concepto", "Tipo ITF",
        "Fecha", "Monto", "Concepto detectado", "Línea original",
        "Contabilizar", "Motivo alerta",
    ]
