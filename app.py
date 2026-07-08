import streamlit as st
import pandas as pd
import tempfile
import os
from datetime import datetime

from procesadores import bcp, interbank, bbva, pichincha, caja_huancayo
from utils.excel_formatter import (
    generar_excel_bcp,
    generar_excel_interbank,
    generar_excel_bbva,
    nombre_archivo_reporte,
)

st.set_page_config(
    page_title="Detector ITF & Gastos Bancarios",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@300;400;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap');
    html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
    .main-header {
        background: linear-gradient(135deg, #0d1b2a 0%, #1f4e78 60%, #2e86c1 100%);
        padding: 2.5rem 2rem 2rem 2rem;
        border-radius: 12px;
        margin-bottom: 2rem;
        color: white;
    }
    .main-header h1 { font-size: 2rem; font-weight: 700; margin: 0; color: white; }
    .main-header p { font-size: 0.95rem; color: #a8d1f0; margin: 0.4rem 0 0 0; font-weight: 300; }
    .banco-badge {
        display: inline-block;
        background: rgba(255,255,255,0.15);
        color: white;
        border: 1px solid rgba(255,255,255,0.3);
        border-radius: 20px;
        padding: 0.25rem 0.85rem;
        font-size: 0.78rem;
        font-weight: 600;
        margin-top: 1rem;
        font-family: 'IBM Plex Mono', monospace;
    }
    .metric-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-left: 4px solid #1f4e78;
        border-radius: 8px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1rem;
    }
    .metric-card.itf     { border-left-color: #e53e3e; }
    .metric-card.gastos  { border-left-color: #d97706; }
    .metric-card.total   { border-left-color: #1f4e78; }
    .metric-card.alertas { border-left-color: #f59e0b; background: #fffbeb; }
    .metric-label { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 0.3rem; }
    .metric-value { font-size: 1.7rem; font-weight: 700; color: #0d1b2a; font-family: 'IBM Plex Mono', monospace; }
    .metric-sub   { font-size: 0.8rem; color: #94a3b8; margin-top: 0.2rem; }
    .metric-sub.warn { color: #92400e; }
    .alert-warning { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px; padding: 0.9rem 1.1rem; font-size: 0.87rem; color: #92400e; margin-bottom: 0.8rem; }
    .alert-success { background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 0.9rem 1.1rem; font-size: 0.87rem; color: #166534; }
    .alerta-row { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px; padding: 0.9rem 1.1rem; margin-bottom: 0.6rem; font-size: 0.87rem; }
    .alerta-row strong { color: #92400e; }
    .alerta-motivo { color: #78350f; margin-top: 0.3rem; font-size: 0.82rem; }
    .stDownloadButton > button {
        background: #1f4e78 !important;
        color: white !important;
        font-weight: 600 !important;
        border: none !important;
        border-radius: 8px !important;
        padding: 0.6rem 1.5rem !important;
        font-size: 0.9rem !important;
        width: 100% !important;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
    <h1>🏦 Detector de ITF y Gastos Bancarios</h1>
    <p>Procesamiento automático de estados de cuenta en PDF → Excel con formato contable</p>
    <span class="banco-badge">BCP</span>
    <span class="banco-badge">INTERBANK</span>
    <span class="banco-badge">BBVA</span>
    <span class="banco-badge">PICHINCHA</span>
    <span class="banco-badge">CAJA HUANCAYO</span>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    st.markdown("---")
    banco = st.radio(
        "Selecciona el banco",
        options=["BCP", "INTERBANK", "BBVA", "PICHINCHA", "CAJA HUANCAYO"],
        index=0,
    )
    st.markdown("---")
    st.markdown("""
    **📋 Detecta automáticamente:**

    **BCP**
    - Impuesto ITF
    - MANT TD ADIC NEG
    - MANT. CUENTA
    - COM.MANTENIM
    - ENVIO ESTADO CTA

    **INTERBANK**
    - Impuesto ITF
    - Mantenimiento
    - Comisión lote I-BANC (S/5.30)
    - Comisión exceso tienda
    - Comisión depósito tienda
    - Comisión pago planilla

    **BBVA**
    - Impuesto ITF
    - Comisión de mantenimiento
    - Envío de extracto
    - COMIS BBVA EMPRESAS
    *(SEG.IM es ignorado)*

    **PICHINCHA** *(Soles y Dólares)*
    - Impuesto ITF (inline)
    - Cargo por Portes
    - Cargos por Mantenimiento
    - COMI FLAT (Cash Financiero)

    **CAJA HUANCAYO** *(Soles)*
    - ITF (columna propia)
    - Comisión x Transferencia
    *(PDFs imagen → sin texto extraíble)*
    """)
    st.caption("v1.3 · Streamlit + pdfplumber + fitz")

st.markdown(f"#### 📂 Sube uno o varios estados de cuenta **{banco}** en PDF")

archivos = st.file_uploader(
    label="Arrastra aquí o haz clic para seleccionar",
    type=["pdf"],
    accept_multiple_files=True,
)

if not archivos:
    st.markdown("""
    <div class="alert-warning">
        ⚠️ Aún no has subido ningún archivo PDF. Selecciona el banco en el panel izquierdo y sube tus estados de cuenta.
    </div>
    """, unsafe_allow_html=True)
    st.stop()

# ---------------------------------------------------------------------------
# Procesamiento — los tres bancos ahora retornan {"registros": [], "alertas": []}
# ---------------------------------------------------------------------------

todos_los_registros = []
todas_las_alertas   = []
archivos_procesados = []
archivos_sin_datos  = []

with st.spinner(f"Procesando {len(archivos)} archivo(s)..."):
    for archivo in archivos:
        if not archivo.name.lower().endswith(".pdf"):
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(archivo.read())
            ruta_tmp = tmp.name
        try:
            if banco == "BCP":
                resultado = bcp.procesar_pdf(archivo.name, ruta_tmp)
            elif banco == "INTERBANK":
                resultado = interbank.procesar_pdf(archivo.name, ruta_tmp)
            elif banco == "BBVA":
                resultado = bbva.procesar_pdf(archivo.name, ruta_tmp)
            elif banco == "PICHINCHA":
                resultado = pichincha.procesar_pdf(archivo.name, ruta_tmp)
            else:  # CAJA HUANCAYO
                resultado = caja_huancayo.procesar_pdf(archivo.name, ruta_tmp)

            registros = resultado["registros"]
            alertas   = resultado["alertas"]

            if registros or alertas:
                todos_los_registros.extend(registros)
                todas_las_alertas.extend(alertas)
                msg = f"✅ {archivo.name} → {len(registros)} confirmados"
                if alertas:
                    msg += f" · {len(alertas)} alerta(s)"
                archivos_procesados.append(msg)
            else:
                if resultado.get("_sin_texto"):
                    archivos_sin_datos.append(
                        f"⚠️ {archivo.name} → PDF imagen sin texto extraíble. "
                        f"Ingresar datos manualmente o usar OCR."
                    )
                else:
                    archivos_sin_datos.append(
                        f"⚠️ {archivo.name} → sin ITF ni gastos detectados"
                    )

        except Exception as e:
            archivos_sin_datos.append(f"❌ {archivo.name} → error: {str(e)}")
        finally:
            os.unlink(ruta_tmp)

with st.expander("📋 Log de procesamiento", expanded=False):
    for msg in archivos_procesados:
        st.markdown(f"- {msg}")
    for msg in archivos_sin_datos:
        st.markdown(f"- {msg}")

if not todos_los_registros and not todas_las_alertas:
    st.error("No se encontraron registros de ITF ni gastos bancarios en los archivos subidos.")
    st.stop()

# ---------------------------------------------------------------------------
# DataFrame principal — solo registros confirmados
# ---------------------------------------------------------------------------

df = pd.DataFrame(todos_los_registros) if todos_los_registros else pd.DataFrame()

if banco == "BCP" and not df.empty:
    columnas = bcp.get_columnas_ordenadas()
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    df = df[columnas]
elif banco == "INTERBANK" and not df.empty:
    columnas = interbank.get_columnas_ordenadas()
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    df = df[columnas]
elif banco == "BBVA" and not df.empty:
    columnas = bbva.get_columnas_ordenadas()
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    df = df[columnas]
elif banco == "PICHINCHA" and not df.empty:
    columnas = pichincha.get_columnas_ordenadas()
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    df = df[columnas]
elif banco == "CAJA HUANCAYO" and not df.empty:
    columnas = caja_huancayo.get_columnas_ordenadas()
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    df = df[columnas]

df_itf    = df[df["Concepto"] == "IMPUESTO ITF"].copy()     if not df.empty else pd.DataFrame()
df_gastos = df[df["Concepto"] == "GASTOS BANCARIOS"].copy() if not df.empty else pd.DataFrame()

total_itf     = round(df_itf["Monto"].sum(), 2)    if not df_itf.empty    else 0.00
total_gastos  = round(df_gastos["Monto"].sum(), 2) if not df_gastos.empty else 0.00
total_general = round(total_itf + total_gastos, 2)

# Detectar símbolo de moneda del DataFrame (S/ o US$)
simbolo_display = "S/"
if not df.empty and "Símbolo" in df.columns:
    simbolos = df["Símbolo"].dropna().unique()
    if len(simbolos) == 1:
        simbolo_display = simbolos[0]
    elif len(simbolos) > 1:
        simbolo_display = "(*)"  # mezcla de monedas — raro pero posible
elif todas_las_alertas and "Símbolo" in todas_las_alertas[0]:
    simbolo_display = todas_las_alertas[0]["Símbolo"]

# Totales incluyendo alertas (si el usuario confirma todo)
if todas_las_alertas:
    df_alertas = pd.DataFrame(todas_las_alertas)
    total_itf_con_alertas    = round(
        total_itf + df_alertas[df_alertas["Concepto"] == "IMPUESTO ITF"]["Monto"].sum(), 2
    )
    total_gastos_con_alertas = round(
        total_gastos + df_alertas[df_alertas["Concepto"] == "GASTOS BANCARIOS"]["Monto"].sum(), 2
    )
else:
    df_alertas = pd.DataFrame()
    total_itf_con_alertas    = total_itf
    total_gastos_con_alertas = total_gastos

# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

st.markdown("---")

if todas_las_alertas:
    col1, col2, col3, col4 = st.columns(4)
    with col4:
        st.markdown(f"""
        <div class="metric-card alertas">
            <div class="metric-label">⚠️ Para revisar</div>
            <div class="metric-value" style="font-size:1.3rem; color:#92400e;">{len(todas_las_alertas)}</div>
            <div class="metric-sub warn">No incluidos en el total</div>
        </div>
        """, unsafe_allow_html=True)
else:
    col1, col2, col3 = st.columns(3)

with col1:
    sub_itf = f"{len(df_itf)} operación(es)"
    if total_itf_con_alertas != total_itf:
        sub_itf += f" · con dudosos: {simbolo_display} {total_itf_con_alertas:,.2f}"
    st.markdown(f"""
    <div class="metric-card itf">
        <div class="metric-label">🔴 Impuesto ITF</div>
        <div class="metric-value">{simbolo_display} {total_itf:,.2f}</div>
        <div class="metric-sub">{sub_itf}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    sub_gst = f"{len(df_gastos)} operación(es)"
    if total_gastos_con_alertas != total_gastos:
        sub_gst += f" · con dudosos: {simbolo_display} {total_gastos_con_alertas:,.2f}"
    st.markdown(f"""
    <div class="metric-card gastos">
        <div class="metric-label">🟡 Gastos Bancarios</div>
        <div class="metric-value">{simbolo_display} {total_gastos:,.2f}</div>
        <div class="metric-sub">{sub_gst}</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    sub_tot = f"{len(df)} registro(s) en total"
    if (total_itf_con_alertas + total_gastos_con_alertas) != total_general:
        sub_tot += f" · con dudosos: {simbolo_display} {total_itf_con_alertas + total_gastos_con_alertas:,.2f}"
    st.markdown(f"""
    <div class="metric-card total">
        <div class="metric-label">🔵 Total General</div>
        <div class="metric-value">{simbolo_display} {total_general:,.2f}</div>
        <div class="metric-sub">{sub_tot}</div>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Alertas — sección visible para todos los bancos cuando hay dudosos
# ---------------------------------------------------------------------------

if todas_las_alertas:
    st.markdown("---")
    st.markdown(f"### ⚠️ {len(todas_las_alertas)} operación(es) para revisar")
    st.markdown(
        "Estas operaciones **no están incluidas** en los totales de arriba. "
        "Revisá el estado de cuenta original y decide si corresponde contabilizarlas."
    )
    for a in todas_las_alertas:
        simbolo = a.get("Símbolo", "S/")
        st.markdown(f"""
        <div class="alerta-row">
            <strong>{a.get('Fecha','')} · {a.get('Concepto detectado','')} · {simbolo} {a.get('Monto', 0):,.2f}</strong><br>
            <span class="alerta-motivo">💡 {a.get('Motivo alerta','')}</span><br>
            <code style="font-size:0.78rem; color:#78350f;">{a.get('Línea original','')}</code>
        </div>
        """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Tabs de detalle
# ---------------------------------------------------------------------------

st.markdown("---")

if todas_las_alertas:
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Resumen", "🔴 Detalle ITF", "🟡 Detalle Gastos", "⚠️ Alertas"])
else:
    tab1, tab2, tab3 = st.tabs(["📊 Resumen", "🔴 Detalle ITF", "🟡 Detalle Gastos"])
    tab4 = None

with tab1:
    if banco == "BCP":
        df_res_show = (
            df.groupby("Concepto")
            .agg(**{
                "Cantidad de operaciones": ("Monto", "count"),
                "Monto Total": ("Monto", "sum"),
            })
            .reset_index()
        )
        df_res_show["Monto Total"] = df_res_show["Monto Total"].round(2)
    elif banco in ("PICHINCHA", "CAJA HUANCAYO") and not df.empty:
        cols_group = [c for c in ["Año", "Mes", "Moneda", "Símbolo", "Concepto"] if c in df.columns]
        df_res_show = (
            df.groupby(cols_group, as_index=False)
            .agg(**{
                "Cantidad de operaciones": ("Monto", "count"),
                "Monto Total": ("Monto", "sum"),
            })
        )
        df_res_show["Monto Total"] = df_res_show["Monto Total"].round(2)
    else:
        if df.empty:
            df_res_show = pd.DataFrame(columns=["Año", "Mes", "Moneda", "Símbolo", "Concepto", "Cantidad de operaciones", "Monto Total"])
        else:
            cols_group = [c for c in ["Año", "Mes", "Moneda", "Símbolo", "Concepto"] if c in df.columns]
            df_res_show = (
                df.groupby(cols_group, as_index=False)
                .agg(**{
                    "Cantidad de operaciones": ("Monto", "count"),
                    "Monto Total": ("Monto", "sum"),
                })
            )
            df_res_show["Monto Total"] = df_res_show["Monto Total"].round(2)
    st.dataframe(df_res_show, use_container_width=True, hide_index=True)

with tab2:
    if df_itf.empty:
        st.info("No se detectaron registros de Impuesto ITF.")
    else:
        cols_itf = ["Fecha", "Monto", "Concepto detectado", "Archivo"]
        cols_ok  = [c for c in cols_itf if c in df_itf.columns]
        st.dataframe(df_itf[cols_ok], use_container_width=True, hide_index=True)

with tab3:
    if df_gastos.empty:
        st.info("No se detectaron gastos bancarios.")
    else:
        cols_gst = ["Fecha", "Monto", "Concepto detectado", "Archivo"]
        cols_ok  = [c for c in cols_gst if c in df_gastos.columns]
        st.dataframe(df_gastos[cols_ok], use_container_width=True, hide_index=True)

if tab4 is not None:
    with tab4:
        if df_alertas.empty:
            st.info("Sin alertas.")
        else:
            cols_alt = ["Fecha", "Monto", "Concepto detectado", "Motivo alerta", "Archivo"]
            cols_ok  = [c for c in cols_alt if c in df_alertas.columns]
            st.dataframe(df_alertas[cols_ok], use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Asiento sugerido
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown("**📒 Asiento sugerido**")
st.markdown("""
| Concepto | Operaciones | Monto Total | Indicación |
|---|---|---|---|
| IMPUESTO ITF | {} | {} {:.2f} | Registrar un solo asiento mensual por ITF |
| GASTOS BANCARIOS | {} | {} {:.2f} | Registrar un solo asiento mensual por comisiones y mantenimientos |
""".format(len(df_itf), simbolo_display, total_itf, len(df_gastos), simbolo_display, total_gastos))

# ---------------------------------------------------------------------------
# Descarga Excel
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown("### 📥 Descargar reporte Excel")

try:
    if banco == "BCP":
        excel_bytes = generar_excel_bcp(df, total_itf, total_gastos, df_itf, df_gastos)
    elif banco == "INTERBANK":
        excel_bytes = generar_excel_interbank(
            df, total_itf, total_gastos, df_itf, df_gastos,
            interbank.get_columnas_ordenadas(),
        )
    elif banco == "BBVA":
        excel_bytes = generar_excel_bbva(
            df, df_itf, df_gastos,
            bbva.get_columnas_ordenadas(),
        )
    else:
        # Pichincha y Caja Huancayo usan el mismo formato que BBVA
        modulo = pichincha if banco == "PICHINCHA" else caja_huancayo
        excel_bytes = generar_excel_bbva(
            df, df_itf, df_gastos,
            modulo.get_columnas_ordenadas(),
        )

    nombre = nombre_archivo_reporte(banco)

    st.download_button(
        label=f"⬇️ Descargar {nombre}",
        data=excel_bytes,
        file_name=nombre,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.markdown(f"""
    <div class="alert-success">
        ✅ Reporte listo: <strong>{nombre}</strong><br>
        Hojas incluidas: <em>Resumen · Detalle ITF · Detalle Gastos · Asiento sugerido{'  · Base completa' if banco != 'BCP' else ''}</em>
    </div>
    """, unsafe_allow_html=True)

except Exception as e:
    st.error(f"Error generando el Excel: {str(e)}")
