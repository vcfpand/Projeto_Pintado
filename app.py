import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
import requests
from io import BytesIO

st.set_page_config(page_title="Pintado Dashboard - Analítico", layout="wide")

# ==========================================
# INTEGRAÇÃO GEMINI (IA) - TENTATIVA DE IMPORTAÇÃO SEGURA
# ==========================================
usa_gemini = False
try:
    import google.generativeai as genai
    if "GEMINI_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        model = genai.GenerativeModel('gemini-1.5-flash')
        usa_gemini = True
except ImportError:
    st.sidebar.warning("⚠️ Biblioteca 'google-generativeai' não instalada. O relatório de IA está desativado.")
except Exception as e:
    st.sidebar.warning(f"⚠️ Erro ao configurar Gemini: {e}")

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
            st.error("Erro ao baixar planilha do Google Drive. Verifique o link.")
            return None

        xls = BytesIO(resp.content)
        df_d = pd.read_excel(xls, sheet_name="Parametros_diarios", engine='openpyxl')
        xls.seek(0)
        df_b = pd.read_excel(xls, sheet_name="Biometrias", engine='openpyxl')
        
        # Limpeza Básica
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

    # Filtro de Dias para os painéis
    dias_sel = st.sidebar.slider("Filtro de Dias (Painéis)", 0, dia_max_preenchido, (0, dia_max_preenchido))
    df_f = df[(df['tratamento'].isin(trat_sel)) & (df['dia_exp'].between(dias_sel[0], dias_sel[1]))]

    # ==========================================
    # CARDS DE DESEMPENHO DIÁRIO
    # ==========================================
    st.subheader(f"📊 Relatório de Operação (Dia {dias_sel[0]} a {dias_sel[1]})")
    
    cols = st.columns(len(trat_sel)) if trat_sel else []
    
    dados_gemini = {} # Dicionário para alimentar a IA
    
    for i, trat in enumerate(["T00", "T10", "T20", "T30"]):
        if trat in trat_sel:
            d_trat = df_f[df_f['tratamento'] == trat]
            d_ontem = d_trat[d_trat['dia_exp'] == (dias_sel[1] - 1)] if dias_sel[1] > 0 else pd.DataFrame()
            d_hoje = d_trat[d_trat['dia_exp'] == dias_sel[1]]
            
            # Médias de Água
            m_ph = d_trat['ph'].mean()
            m_temp = d_trat['temp'].mean()
            m_od = d_trat['od'].mean()
            m_cond = d_trat['cond'].mean()
            m_amonia = d_trat['amonia'].mean()
            m_nitrito = d_trat['nitrito'].mean()
            
            # Consumo
            cons_acumulado = d_trat['consumo'].sum()
            cons_hoje = d_hoje['consumo'].sum() if not d_hoje.empty else 0
            cons_ontem = d_ontem['consumo'].sum() if not d_ontem.empty else 0
            
            delta_cons = 0
            if cons_ontem > 0:
                delta_cons = ((cons_hoje - cons_ontem) / cons_ontem) * 100
                
            # Estoque e Mort.
            est_restante_kg = RACAO_INICIAL[trat] - (cons_acumulado / 1000)
            mort_total = d_trat['mort'].sum()
            
            # Guarda info pro Gemini
            dados_gemini[trat] = {"Consumo_g": cons_hoje, "Var_Consumo_%": delta_cons, "Mort": mort_total, "Amonia": m_amonia}
            
            with cols[i]:
                st.markdown(f"### **{trat}**")
                with st.container(border=True):
                    st.markdown("**💧 Qualidade de Água (Médias):**")
                    st.write(f"pH: {m_ph:.2f} | Temp: {m_temp:.1f}°C")
                    st.write(f"OD: {m_od:.2f} | Cond: {m_cond:.1f}")
                    st.write(f"Amonia: {m_amonia:.3f} | Nitrito: {m_nitrito:.3f}")
                    st.divider()
                    st.metric("Consumo Acumulado (g)", f"{cons_acumulado:.0f}")
                    st.metric("Consumo Diário", f"{cons_hoje:.0f} g", f"{delta_cons:.1f}% vs ontem", delta_color="normal")
                    st.divider()
                    st.metric("Ração Restante (kg)", f"{est_restante_kg:.2f}")
                    st.metric("Mortalidade (Período)", f"{int(mort_total)}")

    # ==========================================
    # ANÁLISE GERAL (GEMINI) - AGORA LOGO ABAIXO DOS CARDS
    # ==========================================
    if usa_gemini:
        with st.container(border=True):
            st.markdown("#### 🤖 Análise Zootécnica Geral (IA)")
            if st.button("Gerar Relatório de Desempenho", type="primary"):
                with st.spinner("Analisando as médias do período..."):
                    prompt = f"""Atue como um Zootecnista responsável por um ensaio de nutrição com juvenis de Pintado. 
                    Analise os dados sumarizados do último dia selecionado: {dados_gemini}.
                    Escreva 2 parágrafos. O primeiro sobre a aceitação da dieta (avaliando as variações percentuais de consumo de ontem para hoje). O segundo sobre riscos ambientais, correlacionando mortalidade com possíveis níveis de amônia apresentados. Seja técnico e objetivo."""
                    
                    try:
                        resposta = model.generate_content(prompt)
                        st.info(resposta.text)
                    except Exception as err:
                        st.error(f"Erro ao contatar API do Gemini: {err}")
    else:
        st.info("💡 A Inteligência Artificial está desativada. Para ativar, certifique-se de que a biblioteca 'google-generativeai' está instalada e a chave configurada nos Secrets.")

    st.divider()

    # ==========================================
    # 4. GRÁFICOS DIRETOS AO PONTO
    # ==========================================
    tab1, tab2, tab3 = st.tabs(["📈 Zootecnia", "🧪 Água", "🔬 Estatística & Estoque"])

    with tab1:
        c1, c2 = st.columns(2)
        c1.plotly_chart(px.line(df_f, x="dia_exp", y="peso_est", color="tratamento", title="Curva de Crescimento (g)", template="plotly_dark"), use_container_width=True)
        c2.plotly_chart(px.line(df_f, x="dia_exp", y="caa_est", color="tratamento", title="Conversão Alimentar (CAA)", template="plotly_dark"), use_container_width=True)

    with tab2:
        st.subheader("Parâmetros Fisico-Químicos")
        param_agua_list = ['temp', 'od', 'amonia', 'nitrito', 'ph', 'cond']
        
        # 2 gráficos por linha
        for i in range(0, len(param_agua_list), 2):
            colA, colB = st.columns(2)
            p1 = param_agua_list[i]
            colA.plotly_chart(px.line(df_f.groupby(['dia_exp','tratamento'])[p1].mean().reset_index(), x="dia_exp", y=p1, color="tratamento", title=f"Evolução: {p1.upper()}", template="plotly_dark", markers=True), use_container_width=True)
            
            if i + 1 < len(param_agua_list):
                p2 = param_agua_list[i+1]
                colB.plotly_chart(px.line(df_f.groupby(['dia_exp','tratamento'])[p2].mean().reset_index(), x="dia_exp", y=p2, color="tratamento", title=f"Evolução: {p2.upper()}", template="plotly_dark", markers=True), use_container_width=True)

    with tab3:
        col_est1, col_est2 = st.columns(2)
        with col_est1:
            st.subheader("Simulador de Demanda")
            dias_rest = DIAS_TOTAIS - dia_max_preenchido
            res_estoque = []
            for t in ["T00", "T10", "T20", "T30"]:
                df_t = df[df['tratamento'] == t]
                cons_real = df_t['consumo'].sum()
                est_kg = RACAO_INICIAL[t] - (cons_real / 1000)
                
                df_hoje = df_t[df_t['dia_exp'] == dia_max_preenchido]
                if not df_hoje.empty and df_hoje['taxa_arracoamento'].mean() > 0:
                    t_sim, p_sim = df_hoje['taxa_arracoamento'].mean() / 100, df_hoje['peso_est'].mean()
                    vivos = df_hoje['n_peixes_atual'].sum()
                else:
                    t_sim, p_sim, vivos = 0.03, df_t['peso_medio_inicial'].mean(), df_t['n_peixes_inicial'].sum()
                
                demanda_f = 0
                for _ in range(dias_rest):
                    p_sim *= np.exp(tce)
                    demanda_f += (p_sim * vivos * t_sim)
                
                res_estoque.append({"Tratamento": t, "Estoque (kg)": est_kg, "Falta (kg)": demanda_f/1000})
            
            df_p = pd.DataFrame(res_estoque)
            st.plotly_chart(px.bar(df_p, x="Tratamento", y=["Estoque (kg)", "Falta (kg)"], barmode="group", template="plotly_dark"), use_container_width=True)

        with col_est2:
            st.subheader("Dispersão: Ambiente vs Consumo")
            p_corr = st.selectbox("Eixo X:", ['amonia', 'od', 'temp', 'ph'])
            st.plotly_chart(px.scatter(df_f, x=p_corr, y="taxa_arracoamento", color="tratamento", trendline="ols", template="plotly_dark"), use_container_width=True)
