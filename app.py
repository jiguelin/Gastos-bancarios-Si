import streamlit as st
import pandas as pd
import tempfile
import os
from datetime import datetime

from procesadores import bcp, interbank, bbva
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
    .metric-card.itf { border-left-color: #e53e3e; }
    .metric-card.gastos { border-left-color: #d97706; }
    .metric-card.total { border-left-color: #1f4e78; }
    .metric-label { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-bottom: 0.3rem; }
    .metric-value { font-size: 1.7rem; font-weight: 700; color: #0d1b2a; font-family: 'IBM Plex Mono', monospace; }
    .metric-sub { font-size: 0.8rem; color: #94a3b8; margin-top: 0.2rem; }
    .alert-warning { background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px; padding: 0.9rem 1.1rem; font-size: 0.87rem; color: #92400e; }
    .alert-success { background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 0.9rem 1.1rem; font-size: 0.87rem; color: #166534; }
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
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    st.markdown("---")
    banco = st.radio(
        "Selecciona el banco",
        options=["BCP", "INTERBANK", "BBVA"],
        index=0,
    )
    st.markdown("---")
    st.markdown("""
    **📋 Detecta automáticamente:**

    **BCP**
    - Impuesto ITF
    - MANT TD ADIC NEG
    - COM.MANTENIM
    - TLC 0030TO MANT
    - ENVIO.EST.CTA

    **INTERBANK**
    - Impuesto ITF
    - Mantenimiento
    - Envío estado de cuenta
    - Comisiones / EECC

    **BBVA**
    - Impuesto ITF
    - Comisión de mantenimiento
    - Envío de extracto
    - COMIS BBVA EMPRESAS
    *(SEG.IM es ignorado)*
    """)
    st.caption("v1.0 · Streamlit + PyMuPDF")

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

todos_los_registros = []
archivos_procesados = []
archivos_sin_datos = []

with st.spinner(f"Procesando {len(archivos)} archivo(s)..."):
    for archivo in archivos:
        if not archivo.name.lower().endswith(".pdf"):
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(archivo.read())
            ruta_tmp = tmp.name
        try:
            if banco == "BCP":
                registros = bcp.procesar_pdf(archivo.name, ruta_tmp)
            elif banco == "INTERBANK":
                registros = interbank.procesar_pdf(archivo.name, ruta_tmp)
            else:
                registros = bbva.procesar_pdf(archivo.name, ruta_tmp)

            if registros:
                todos_los_registros.extend(registros)
                archivos_procesados.append(f"✅ {archivo.name} → {len(registros)} registros")
            else:
                archivos_sin_datos.append(f"⚠️ {archivo.name} → sin ITF ni gastos detectados")
        except Exception as e:
            archivos_sin_datos.append(f"❌ {archivo.name} → error: {str(e)}")
        finally:
            os.unlink(ruta_tmp)

with st.expander("📋 Log de procesamiento", expanded=False):
    for msg in archivos_procesados:
        st.markdown(f"- {msg}")
    for msg in archivos_sin_datos:
        st.markdown(f"- {msg}")

if not todos_los_registros:
    st.error("No se encontraron registros de ITF ni gastos bancarios en los archivos subidos.")
    st.stop()

df = pd.DataFrame(todos_los_registros)

if banco == "INTERBANK":
    columnas = interbank.get_columnas_ordenadas()
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    df = df[columnas]
elif banco == "BBVA":
    columnas = bbva.get_columnas_ordenadas()
    for col in columnas:
        if col not in df.columns:
            df[col] = ""
    df = df[columnas]

df_itf = df[df["Concepto"] == "IMPUESTO ITF"].copy()
df_gastos = df[df["Concepto"] == "GASTOS BANCARIOS"].copy()

total_itf = round(df_itf["Monto"].sum(), 2) if not df_itf.empty else 0.00
total_gastos = round(df_gastos["Monto"].sum(), 2) if not df_gastos.empty else 0.00
total_general = round(total_itf + total_gastos, 2)

st.markdown("---")
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown(f"""
    <div class="metric-card itf">
        <div class="metric-label">🔴 Impuesto ITF</div>
        <div class="metric-value">S/ {total_itf:,.2f}</div>
        <div class="metric-sub">{len(df_itf)} operación(es)</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    st.markdown(f"""
    <div class="metric-card gastos">
        <div class="metric-label">🟡 Gastos Bancarios</div>
        <div class="metric-value">S/ {total_gastos:,.2f}</div>
        <div class="metric-sub">{len(df_gastos)} operación(es)</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    st.markdown(f"""
    <div class="metric-card total">
        <div class="metric-label">🔵 Total General</div>
        <div class="metric-value">S/ {total_general:,.2f}</div>
        <div class="metric-sub">{len(df)} registro(s) en total</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")
tab1, tab2, tab3 = st.tabs(["📊 Resumen", "🔴 Detalle ITF", "🟡 Detalle Gastos"])

with tab1:
    if banco == "BCP":
        df_res_show = (
            df.groupby("Concepto")
            .agg(**{
                "Cantidad de operaciones": ("Monto", "count"),
                "Monto Total": ("Monto", "sum")
            })
            .reset_index()
        )
        df_res_show["Monto Total"] = df_res_show["Monto Total"].round(2)
    else:
        df_res_show = (
            df.groupby(["Año", "Mes", "Moneda", "Símbolo", "Concepto"], as_index=False)
            .agg(**{
                "Cantidad de operaciones": ("Monto", "count"),
                "Monto Total": ("Monto", "sum")
            })
        )
        df_res_show["Monto Total"] = df_res_show["Monto Total"].round(2)
    st.dataframe(df_res_show, use_container_width=True, hide_index=True)

with tab2:
    if df_itf.empty:
        st.info("No se detectaron registros de Impuesto ITF.")
    else:
        cols_mostrar = ["Fecha", "Monto", "Concepto detectado", "Archivo"]
        cols_ok = [c for c in cols_mostrar if c in df_itf.columns]
        st.dataframe(df_itf[cols_ok], use_container_width=True, hide_index=True)

with tab3:
    if df_gastos.empty:
        st.info("No se detectaron gastos bancarios.")
    else:
        cols_mostrar = ["Fecha", "Monto", "Concepto detectado", "Archivo"]
        cols_ok = [c for c in cols_mostrar if c in df_gastos.columns]
        st.dataframe(df_gastos[cols_ok], use_container_width=True, hide_index=True)

st.markdown("---")
st.markdown("**📒 Asiento sugerido**")
st.markdown("""
| Concepto | Operaciones | Monto Total | Indicación |
|---|---|---|---|
| IMPUESTO ITF | {} | S/ {:.2f} | Registrar un solo asiento mensual por ITF |
| GASTOS BANCARIOS | {} | S/ {:.2f} | Registrar un solo asiento mensual por comisiones, mantenimientos y envío de estado de cuenta |
""".format(len(df_itf), total_itf, len(df_gastos), total_gastos))

st.markdown("---")
st.markdown("### 📥 Descargar reporte Excel")

try:
    if banco == "BCP":
        excel_bytes = generar_excel_bcp(df, total_itf, total_gastos, df_itf, df_gastos)
    elif banco == "INTERBANK":
        excel_bytes = generar_excel_interbank(
            df, total_itf, total_gastos, df_itf, df_gastos,
            interbank.get_columnas_ordenadas()
        )
    else:
        excel_bytes = generar_excel_bbva(
            df, df_itf, df_gastos,
            bbva.get_columnas_ordenadas()
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
