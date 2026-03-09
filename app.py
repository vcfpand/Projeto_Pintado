import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
import requests
from io import BytesIO

st.set_page_config(page_title="Pintado Dashboard - Analítico", layout="wide")

# ==========================================
# INTEGRAÇÃO GEMINI (NOVA API: google-genai)
# ==========================================
usa_gemini = False
client = None
try:
    from google import genai
    if "GEMINI_API_KEY" in st.secrets:
        client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
        usa_gemini = True
except ImportError:
    st.sidebar.warning("⚠️ Biblioteca 'google-genai' não instalada.")
except Exception as e:
    st.sidebar.warning(f"⚠️ Erro Gemini: {e}")

# ==========================================
# 1. LOGIN SIMPLES E SEGURO
# ==========================================
if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    st.title("🐟 Monitoramento - Pintado")
    
    try:
        senha_mestra = st.secrets["SENHA_ACESSO"]
        senha_digitada = st.text_input("Digite a senha para carregar os dados:", type="password")
        
        if st.button("Entrar"):
            if senha_digitada == senha_mestra:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta.")
    except Exception:
        st.error("Configure a SENHA_ACESSO nos Secrets do Streamlit.")
    st.stop()

# ==========================================
# 2. LEITURA DE DADOS (GOOGLE DRIVE)
# ==========================================
RACAO_INICIAL = {"T00": 14.74, "T10": 12.58, "T20": 12.90, "T30": 13.51}
DIAS_TOTAIS = 46      

@st.cache_data(ttl=300)
def load_data():
    try:
        url = st.secrets["URL_ONEDRIVE"].strip()
        resp = requests.get(url)
        
        if resp.status_code != 200:
            st.error("Erro ao baixar planilha. Verifique o link.")
            return None

        xls = BytesIO(resp.content)
        df_d = pd.read_excel(xls, sheet_name="Parametros_diarios", engine='openpyxl')
        xls.seek(0)
        df_b = pd.read_excel(xls, sheet_name="Biometrias", engine='openpyxl')
        
        df_d.columns = [c.strip().lower() for c in df_d.columns]
        df_b.columns = [c.strip().lower() for c in df_b.columns]
        
        cols_num = ['ph', 'temp', 'od', 'cond', 'amonia', 'nitrito', 'mort', 'consumo', 'dia_exp']
        for col in cols_num:
            if col in df_d.columns:
                if df_d[col].dtype == object:
                    df_d[col] = df_d[col].astype(str).str.replace(',', '.')
                df_d[col] = pd.to_numeric(df_d[col], errors='coerce')

        df = pd.merge(df_d, df_b[['caixa', 'n_peixes_inicial', 'peso_medio_inicial']], on='caixa')
        df['caixa'] = df['caixa'].astype(str)
        return df
    except Exception as e:
        st.error(f"Falha de sistema: {e}")
        return None

df = load_data()

if df is not None:
    # ==========================================
    # 3. CÁLCULOS ZOOTÉCNICOS ESSENCIAIS
    # ==========================================
    st.sidebar.header("🎯 Projeção de Abate")
    peso_alvo = st.sidebar.slider("Peso Final Esperado (g)", 40.0, 150.0, 90.0)
    
    peso_ini = df['peso_medio_inicial'].mean()
    tce = (np.log(peso_alvo) - np.log(peso_ini)) / DIAS_TOTAIS
    st.sidebar.info(f"TCE Necessária: **{tce*100:.2f}% /dia**")
    st.sidebar.divider()
    
    trat_sel = st.sidebar.multiselect("Tratamentos", ["T00", "T10", "T20", "T30"], default=["T00", "T10", "T20", "T30"])

    # Matemática da Biomassa
    df['peso_est'] = df['peso_medio_inicial'] * np.exp(tce * df['dia_exp'])
    df['mort_acum'] = df.groupby('caixa')['mort'].cumsum().fillna(0)
    df['n_peixes_atual'] = df['n_peixes_inicial'] - df['mort_acum']
    df['biomassa_est_g'] = df['peso_est'] * df['n_peixes_atual']
    df['ganho_biomassa_g'] = df['biomassa_est_g'] - (df['peso_medio_inicial'] * df['n_peixes_inicial'])
    
    df['consumo_acum'] = df.groupby('caixa')['consumo'].cumsum()
    df['caa_est'] = np.where(df['ganho_biomassa_g'] > 0.01, df['consumo_acum'] / df['ganho_biomassa_g'], 0.0)
    df['taxa_arracoamento'] = (df['consumo'] / df['biomassa_est_g']) * 100

    # Lógica de Dias
    df_real = df.dropna(subset=['consumo'])
    dia_max_preenchido = int(df_real['dia_exp'].max()) if not df_real.empty else 1
    
    st.write(f"**Progresso do Ensaio:** Dia {dia_max_preenchido} de {DIAS_TOTAIS}")
    st.progress(min(dia_max_preenchido / DIAS_TOTAIS, 1.0))
    st.divider()

    dias_sel = st.sidebar.slider("Filtro de Dias", 0, dia_max_preenchido, (0, dia_max_preenchido))
    df_f = df[(df['tratamento'].isin(trat_sel)) & (df['dia_exp'].between(dias_sel[0], dias_sel[1]))]

    # ==========================================
    # CARDS DE DESEMPENHO (KPIs Completos)
    # ==========================================
    st.subheader(f"📊 Desempenho Zootécnico (Dia {dias_sel[0]} a {dias_sel[1]})")
    
    cols = st.columns(len(trat_sel)) if trat_sel else []
    dados_gemini = {} 
    
    for i, trat in enumerate(["T00", "T10", "T20", "T30"]):
        if trat in trat_sel:
            d_trat = df_f[df_f['tratamento'] == trat]
            d_ontem = d_trat[d_trat['dia_exp'] == (dias_sel[1] - 1)] if dias_sel[1] > 0 else pd.DataFrame()
            d_hoje = d_trat[d_trat['dia_exp'] == dias_sel[1]]
            
            m_ph = d_trat['ph'].mean()
            m_temp = d_trat['temp'].mean()
            m_od = d_trat['od'].mean()
            m_cond = d_trat['cond'].mean()
            m_amonia = d_trat['amonia'].mean()
            m_nitrito = d_trat['nitrito'].mean()
            
            cons_acumulado = d_trat['consumo'].sum()
            cons_hoje = d_hoje['consumo'].sum() if not d_hoje.empty else 0
            cons_ontem = d_ontem['consumo'].sum() if not d_ontem.empty else 0
            
            delta_cons = ((cons_hoje - cons_ontem) / cons_ontem * 100) if cons_ontem > 0 else 0
                
            est_restante_kg = RACAO_INICIAL[trat] - (cons_acumulado / 1000)
            mort_total = d_trat['mort'].sum()
            
            dados_gemini[trat] = {"Consumo": cons_hoje, "Var_%": delta_cons, "Mort": mort_total, "Amonia": m_amonia, "OD": m_od}
            
            with cols[i]:
                with st.container(border=True):
                    st.markdown(f"<h3 style='text-align: center; color: #4DA8DA;'>{trat}</h3>", unsafe_allow_html=True)
                    st.markdown("**Médias Ambientais:**")
                    st.write(f"🧪 pH: **{m_ph:.2f}** | 🌡️ Temp: **{m_temp:.1f}**")
                    st.write(f"🫧 OD: **{m_od:.2f}** | ⚡ Cond: **{m_cond:.1f}**")
                    st.write(f"☣️ Amônia: **{m_amonia:.3f}** | ☠️ Nitrito: **{m_nitrito:.3f}**")
                    st.divider()
                    st.metric("Consumo Total (g)", f"{cons_acumulado:.0f}")
                    st.metric("Consumo Diário", f"{cons_hoje:.0f} g", f"{delta_cons:.1f}%", delta_color="normal")
                    st.divider()
                    st.metric("Ração Disp. (kg)", f"{est_restante_kg:.2f}")
                    st.metric("Mortalidade Total", f"{int(mort_total)}")

    # ==========================================
    # ANÁLISE GERAL (GEMINI IA)
    # ==========================================
    if usa_gemini and client is not None:
        with st.container(border=True):
            st.markdown("#### 🧠 Análise Geral do Experimento (Google Gemini)")
            if st.button("Gerar Relatório Zootécnico Diário", type="primary"):
                with st.spinner("Analisando os dados coletados..."):
                    prompt = f"""Atue como um Especialista em Aquicultura. Analise os seguintes dados do último dia avaliado para os 4 tratamentos: {dados_gemini}.
                    Produza uma análise direta em 2 parágrafos:
                    1. Avalie a resposta alimentar (olhando para a variação % de consumo entre os dias). Algum tratamento reduziu o consumo abruptamente?
                    2. Avalie a sanidade e o ambiente. Há alguma correlação aparente entre as taxas de amônia/OD e a mortalidade registrada?
                    Responda de forma profissional e objetiva."""
                    
                    try:
                        resposta = client.models.generate_content(
                            model="gemini-3-flash-preview",
                            contents=prompt
                        )
                        st.info(resposta.text)
                    except Exception as err:
                        st.error(f"Erro na API: {err}")

    st.divider()

    # ==========================================
    # ABAS E GRÁFICOS
    # ==========================================
    tab1, tab2, tab3 = st.tabs(["📈 Zootecnia", "🧪 Água", "🔬 Estatística"])

    with tab1:
        st.subheader("Desempenho Biológico")
        c1, c2, c3 = st.columns(3)
        c1.plotly_chart(px.line(df_f, x="dia_exp", y="peso_est", color="tratamento", title="Peso (g)", template="plotly_dark"), use_container_width=True)
        c2.plotly_chart(px.line(df_f, x="dia_exp", y="caa_est", color="tratamento", title="CAA Estimada", template="plotly_dark"), use_container_width=True)
        c3.plotly_chart(px.line(df_f, x="dia_exp", y="biomassa_est_g", color="tratamento", title="Biomassa (g)", template="plotly_dark"), use_container_width=True)

    with tab2:
        st.subheader("Evolução dos Parâmetros Físico-Químicos")
        param_list = ['temp', 'od', 'amonia', 'nitrito', 'ph', 'cond']
        
        for i in range(0, len(param_list), 3):
            cols_agua = st.columns(3)
            for j in range(3):
                if i + j < len(param_list):
                    p = param_list[i+j]
                    cols_agua[j].plotly_chart(px.line(df_f.groupby(['dia_exp','tratamento'])[p].mean().reset_index(), x="dia_exp", y=p, color="tratamento", title=p.upper(), template="plotly_dark", markers=True), use_container_width=True)

    with tab3:
        st.subheader("Correlação Ambiental e Comportamental")
        c_est1, c_est2 = st.columns(2)
        with c_est1:
            p_corr = st.selectbox("Eixo X (Parâmetro):", ['amonia', 'od', 'temp', 'ph'])
            st.plotly_chart(px.scatter(df_f, x=p_corr, y="taxa_arracoamento", color="tratamento", trendline="ols", title=f"Impacto do {p_corr.upper()} no Apetite (%PV)", template="plotly_dark"), use_container_width=True)
        with c_est2:
            st.write("Esta análise de regressão demonstra como variações pontuais na qualidade da água afetam a voracidade (consumo em relação à biomassa) dos peixes em cada tratamento.")
