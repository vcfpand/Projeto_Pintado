import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
import requests
from io import BytesIO

st.set_page_config(page_title="Pintado Dashboard - Analítico", layout="wide")

# ======================================
# LOGIN
# ======================================

if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:

    st.title("🐟 Monitoramento - Pintado")

    try:
        senha_mestra = st.secrets["SENHA_ACESSO"]
        senha = st.text_input("Senha:", type="password")

        if st.button("Entrar"):

            if senha == senha_mestra:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta")

    except:
        st.error("Configure SENHA_ACESSO nos secrets")

    st.stop()

# ======================================
# CONFIGURAÇÕES
# ======================================

DIAS_TOTAIS = 46

RACAO_INICIAL = {
    "T00":14.74,
    "T10":12.58,
    "T20":12.90,
    "T30":13.51
}

# ======================================
# LEITURA DOS DADOS
# ======================================

@st.cache_data(ttl=300)
def load_data():

    url = st.secrets["URL_ONEDRIVE"].strip()

    resp = requests.get(url)

    if resp.status_code != 200:
        st.error("Erro ao baixar planilha")
        return None

    xls = BytesIO(resp.content)

    df_d = pd.read_excel(xls, sheet_name="Parametros_diarios")
    xls.seek(0)

    df_b = pd.read_excel(xls, sheet_name="Biometrias")

    df_d.columns = df_d.columns.str.lower().str.strip()
    df_b.columns = df_b.columns.str.lower().str.strip()

    df = pd.merge(
        df_d,
        df_b[['caixa','n_peixes_inicial','peso_medio_inicial']],
        on='caixa'
    )

    cols = ['ph','temp','od','cond','amonia','nitrito','mort','consumo','dia_exp']

    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df['caixa'] = df['caixa'].astype(str)

    return df


df = load_data()

if df is None:
    st.stop()

# ======================================
# SIDEBAR
# ======================================

st.sidebar.header("Configurações")

remover_outliers = st.sidebar.toggle(
    "Remover Outliers (Z-score)",
    False
)

peso_alvo = st.sidebar.slider(
    "Peso final esperado",
    40.0,
    150.0,
    90.0
)

trat_sel = st.sidebar.multiselect(
    "Tratamentos",
    ["T00","T10","T20","T30"],
    default=["T00","T10","T20","T30"]
)

# ======================================
# REMOÇÃO DE OUTLIERS
# ======================================

def remove_outliers(df, cols, z=2):

    df2 = df.copy()

    for c in cols:

        if df2[c].std() > 0:

            zscore = np.abs(
                (df2[c] - df2[c].mean()) / df2[c].std()
            )

            df2 = df2[(zscore < z) | df2[c].isna()]

    return df2


if remover_outliers:

    df = remove_outliers(
        df,
        ['ph','temp','od','cond','amonia','nitrito']
    )

# ======================================
# CÁLCULOS ZOOTÉCNICOS
# ======================================

peso_ini = df['peso_medio_inicial'].mean()

tce = (np.log(peso_alvo) - np.log(peso_ini)) / DIAS_TOTAIS

df['peso_est'] = df['peso_medio_inicial'] * np.exp(tce * df['dia_exp'])

df['mort_acum'] = df.groupby('caixa')['mort'].cumsum().fillna(0)

df['n_peixes_atual'] = df['n_peixes_inicial'] - df['mort_acum']

df['biomassa_est_g'] = df['peso_est'] * df['n_peixes_atual']

df['ganho_biomassa_g'] = df['biomassa_est_g'] - (
    df['peso_medio_inicial'] * df['n_peixes_inicial']
)

df['consumo_preenchido'] = df['consumo'].fillna(0)

df['consumo_acum'] = df.groupby('caixa')['consumo_preenchido'].cumsum()

df['caa_est'] = np.where(
    df['ganho_biomassa_g'] > 0,
    df['consumo_acum'] / df['ganho_biomassa_g'],
    np.nan
)

df['taxa_arracoamento'] = np.where(
    df['biomassa_est_g'] > 0,
    (df['consumo'] / df['biomassa_est_g']) * 100,
    np.nan
)

# ======================================
# PROGRESSO DO EXPERIMENTO
# ======================================

df_real = df.dropna(subset=['consumo'])

if not df_real.empty:
    dia_max = int(df_real['dia_exp'].max())
else:
    dia_max = 1

st.title("Experimento Nutricional – Pintado")

st.write(f"Dia {dia_max} de {DIAS_TOTAIS}")

st.progress(min(dia_max/DIAS_TOTAIS,1.0))

# ======================================
# FILTRO
# ======================================

dias = st.slider(
    "Intervalo de dias",
    0,
    dia_max,
    (0,dia_max)
)

df_f = df[
    (df['tratamento'].isin(trat_sel)) &
    (df['dia_exp'].between(dias[0],dias[1]))
]

# ======================================
# ALERTAS AUTOMÁTICOS
# ======================================

st.subheader("Alertas Ambientais")

media_od = df_f['od'].mean()
media_amonia = df_f['amonia'].mean()

if media_od < 4:
    st.error("OD médio abaixo do ideal (<4 mg/L)")

if media_amonia > 0.5:
    st.warning("Amônia elevada")

# ======================================
# GRÁFICOS
# ======================================

tab1,tab2,tab3 = st.tabs(
    ["Zootecnia","Água","Estatística"]
)

# ==============================
# ZOOTECNIA
# ==============================

with tab1:

    c1,c2,c3 = st.columns(3)

    fig = px.line(
        df_f,
        x="dia_exp",
        y="peso_est",
        color="tratamento",
        title="Peso Estimado"
    )

    c1.plotly_chart(fig,use_container_width=True)

    fig = px.line(
        df_f,
        x="dia_exp",
        y="caa_est",
        color="tratamento",
        title="CAA estimada"
    )

    c2.plotly_chart(fig,use_container_width=True)

    fig = px.line(
        df_f,
        x="dia_exp",
        y="biomassa_est_g",
        color="tratamento",
        title="Biomassa estimada"
    )

    c3.plotly_chart(fig,use_container_width=True)

# ==============================
# ÁGUA
# ==============================

with tab2:

    params = ['temp','od','amonia','nitrito','ph','cond']

    for p in params:

        fig = px.line(
            df_f,
            x="dia_exp",
            y=p,
            color="tratamento",
            title=p.upper()
        )

        st.plotly_chart(fig,use_container_width=True)

# ==============================
# ESTATÍSTICA
# ==============================

with tab3:

    st.subheader("Correlação Ambiental")

    cols = ['taxa_arracoamento','amonia','od','temp','ph']

    corr = df_f[cols].dropna().corr()

    fig = px.imshow(
        corr,
        text_auto=True,
        aspect="auto",
        title="Matriz de Correlação"
    )

    st.plotly_chart(fig,use_container_width=True)
