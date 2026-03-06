import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np

# 1. CONFIGURAÇÃO DA PÁGINA (Sempre o primeiro comando)
st.set_page_config(page_title="Pintado Dashboard v14", layout="wide")

# ==========================================
# 2. SISTEMA DE LOGIN (A "FECHADURA")
# ==========================================
# O sistema busca a senha no cofre (Secrets) do Streamlit Cloud
if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    st.title("🐟 Sistema de Monitoramento - Área Restrita")
    st.warning("🔒 Acesso restrito a pesquisadores autorizados.")
    
    # Busca a senha configurada no painel de Secrets do Streamlit
    try:
        senha_mestra = st.secrets["SENHA_ACESSO"]
        senha_digitada = st.text_input("Digite a senha de acesso:", type="password")
        
        if st.button("Entrar"):
            if senha_digitada == senha_mestra:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta. Acesso negado.")
    except KeyError:
        st.error("Erro técnico: 'SENHA_ACESSO' não configurada no cofre de segredos.")
    
    # Interrompe a execução aqui se não estiver autenticado
    st.stop()

# ==========================================
# 3. CONTINUAÇÃO DO DASHBOARD (Pós-Login)
# ==========================================

# --- CONFIGURAÇÕES DO EXPERIMENTO ---
RACAO_INICIAL = {"T00": 14.74, "T10": 12.58, "T20": 12.90, "T30": 13.51}
DIAS_TOTAIS = 46      

@st.cache_data(ttl=600) # Atualiza automaticamente a cada 10 minutos
def load_raw_data():
    # Busca a URL do OneDrive no cofre (Secrets)
    url_planilha = st.secrets["URL_ONEDRIVE"]
    
    # Carregamento via link direto
    df_diario = pd.read_excel(url_planilha, sheet_name="Parametros_diarios")
    df_bio = pd.read_excel(url_planilha, sheet_name="Biometrias")
    
    df_diario.columns = [c.strip().lower() for c in df_diario.columns]
    df_bio.columns = [c.strip().lower() for c in df_bio.columns]
    
    # Tratamento de vírgulas e "NA"s
    cols_num = ['ph', 'temp', 'od', 'cond', 'amonia', 'nitrito', 'mort', 'consumo', 'dia_exp']
    for col in cols_num:
        if col in df_diario.columns:
            if df_diario[col].dtype == object:
                df_diario[col] = df_diario[col].str.replace(',', '.')
            df_diario[col] = pd.to_numeric(df_diario[col], errors='coerce')

    df = pd.merge(df_diario, df_bio[['caixa', 'n_peixes_inicial', 'peso_medio_inicial']], on='caixa')
    df['data'] = pd.to_datetime(df['data'])
    df = df.sort_values(by=['dia_exp', 'caixa'])
    df['caixa'] = df['caixa'].astype(str)
    return df

try:
    df_raw = load_raw_data()

    # --- SIDEBAR: SIMULADOR POR PESO FINAL ---
    st.sidebar.header("🎯 Projeção Biológica")
    peso_alvo = st.sidebar.slider("Peso Final Esperado (g)", min_value=40.0, max_value=150.0, value=90.0, step=1.0)
    
    peso_ini_global = df_raw['peso_medio_inicial'].mean()
    # Calcula a TCE necessária para atingir o peso alvo no tempo total
    TCE_ESTIMADA = (np.log(peso_alvo) - np.log(peso_ini_global)) / DIAS_TOTAIS
    
    st.sidebar.info(f"📈 Taxa de crescimento necessária: **{TCE_ESTIMADA*100:.2f}% ao dia**.")
    st.sidebar.divider()
    
    st.sidebar.header("⚙️ Filtros")
    trat_sel = st.sidebar.multiselect("Tratamentos", sorted(df_raw['tratamento'].unique()), default=df_raw['tratamento'].unique())

    # --- PROCESSAMENTO DINÂMICO ---
    df_full = df_raw.copy()
    df_full['peso_est'] = df_full['peso_medio_inicial'] * np.exp(TCE_ESTIMADA * df_full['dia_exp'])
    df_full['mort_acum'] = df_full.groupby('caixa')['mort'].cumsum().fillna(0)
    df_full['n_peixes_atual'] = df_full['n_peixes_inicial'] - df_full['mort_acum']
    df_full['biomassa_est_g'] = df_full['peso_est'] * df_full['n_peixes_atual']
    df_full['ganho_biomassa_g'] = df_full['biomassa_est_g'] - (df_full['peso_medio_inicial'] * df_full['n_peixes_inicial'])
    
    df_full['consumo_acum'] = df_full.groupby('caixa')['consumo'].cumsum()
    df_full['caa_est'] = np.where(df_full['ganho_biomassa_g'] > 0.01, df_full['consumo_acum'] / df_full['ganho_biomassa_g'], 0.0)
    df_full['caa_est'] = df_full['caa_est'].replace([np.inf, -np.inf], 0.0).fillna(0.0)
    df_full['taxa_arracoamento'] = (df_full['consumo'] / df_full['biomassa_est_g']) * 100

    # --- BARRA DE PROGRESSO ---
    df_real = df_full.dropna(subset=['consumo'])
    dia_atual = int(df_real['dia_exp'].max()) if not df_real.empty else 1 
    dia_atual = max(1, dia_atual)

    progresso = min(dia_atual / DIAS_TOTAIS, 1.0)
    st.write(f"**Progresso:** Dia {dia_atual} de {DIAS_TOTAIS} — {progresso*100:.1f}% concluído")
    st.progress(progresso)
    st.divider()

    dias_sel = st.sidebar.slider("Intervalo de Dias", 0, dia_atual, (0, dia_atual))
    df_f = df_full[(df_full['tratamento'].isin(trat_sel)) & (df_full['dia_exp'].between(dias_sel[0], dias_sel[1]))]

    # --- DASHBOARD SUPERIOR ---
    st.subheader(f"📊 Desempenho Zootécnico (Dia {dias_sel[0]} ao {dias_sel[1]})")
    ordem_fixa = ["T00", "T10", "T20", "T30"]
    trat_exibir = [t for t in ordem_fixa if t in trat_sel]
    
    if trat_exibir:
        cols = st.columns(len(trat_exibir))
        for i, trat in enumerate(trat_exibir):
            d_trat = df_f[df_f['tratamento'] == trat]
            d_ultimo = d_trat[d_trat['dia_exp'] == dias_sel[1]]
            p_ini = d_trat['peso_medio_inicial'].mean() if not d_trat.empty else 0.0
            p_est = d_ultimo['peso_est'].mean() if not d_ultimo.empty else 0.0
            t_pv = d_trat['taxa_arracoamento'].mean() if not d_trat.empty else 0.0
            v_caa = d_ultimo['caa_est'].mean() if not d_ultimo.empty else 0.0
            
            with cols[i]:
                st.info(f"**{trat}**")
                st.metric("Peso Médio Est.", f"{p_est:.2f} g")
                st.metric("Taxa Arraç.", f"{t_pv:.2f} % PV")
                st.metric("CAA Est.", f"{v_caa:.2f}")

    st.divider()

    # --- TABS ---
    tab1, tab2, tab3, tab4 = st.tabs(["📈 Zootecnia", "🧪 Água", "🔬 Estatística", "📦 Estoque"])

    # --- TAB 1: ZOOTECNIA ---
    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(px.line(df_f, x="dia_exp", y="peso_est", color="tratamento", title="Crescimento Projetado (g)", template="plotly_dark"), use_container_width=True)
        with c2:
            st.plotly_chart(px.line(df_f, x="dia_exp", y="caa_est", color="tratamento", title="Conversão Alimentar (CAA)", template="plotly_dark"), use_container_width=True)
        
        st.subheader("🤖 Relatório Analítico: Zootecnia")
        df_ultimo = df_f[df_f['dia_exp'] == dias_sel[1]]
        if not df_ultimo.empty:
            melhor_caa = df_ultimo.groupby('tratamento')['caa_est'].mean().idxmin()
            val_melhor_caa = df_ultimo.groupby('tratamento')['caa_est'].mean().min()
            mort_total = df_f['mort'].sum()
            st.success(f"**✅ Ponto Forte:** O tratamento **{melhor_caa}** apresenta a maior eficiência biológica (CAA: {val_melhor_caa:.2f}).")
            critico = f"Mortalidade acumulada de {int(mort_total)} peixe(s)." if mort_total > 0 else "Sanidade perfeita (mortalidade zero)."
            st.info(f"**💡 Insight Sanidade:** {critico}")

    # --- TAB 2: ÁGUA ---
    with tab2:
        col_p1, col_p2 = st.columns([1, 2])
        param = col_p1.selectbox("Parâmetro", ['ph', 'temp', 'od', 'amonia', 'nitrito'], key="param_agua")
        visao = col_p2.radio("Visão:", ["Média", "Boxplot", "Caixas"], horizontal=True)
        
        if visao == "Média":
            fig = px.line(df_f.groupby(['dia_exp','tratamento'])[param].mean().reset_index(), x="dia_exp", y=param, color="tratamento", markers=True, template="plotly_dark")
        elif visao == "Boxplot":
            fig = px.box(df_f, x="tratamento", y=param, color="tratamento", points="all", template="plotly_dark")
        else:
            fig = px.line(df_f, x="dia_exp", y=param, color="caixa", facet_col="tratamento", facet_col_wrap=2, markers=True, template="plotly_dark")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("🤖 Relatório Analítico: Qualidade de Água")
        if not df_f.empty and df_f['amonia'].notna().any():
            max_am = df_f['amonia'].max()
            if max_am > 0.5: st.warning(f"⚠️ Alerta: Picos de Amônia detectados ({max_am:.2f} mg/L).")
            else: st.success("✅ Estabilidade: Amônia sob controle em todos os tratamentos.")

    # --- TAB 3: ESTATÍSTICA ---
    with tab3:
        p_corr = st.selectbox("Parâmetro vs Consumo (% PV):", ['amonia', 'od', 'temp', 'ph'], key="p_corr")
        st.plotly_chart(px.scatter(df_f, x=p_corr, y="taxa_arracoamento", color="tratamento", trendline="ols", template="plotly_dark", opacity=0.7), use_container_width=True)
        
        st.subheader("🤖 Relatório Analítico: Comportamento")
        if len(df_f) > 3:
            corr_val = df_f[p_corr].corr(df_f['taxa_arracoamento'])
            if abs(corr_val) > 0.4:
                impacto = "negativo" if corr_val < 0 else "positivo"
                st.error(f"⚠️ Correlação detectada: {p_corr.upper()} está exercendo impacto {impacto} no apetite.")
            else:
                st.info("📊 Estabilidade: Nenhuma correlação crítica detectada entre o ambiente e o consumo.")

    # --- TAB 4: ESTOQUE ---
    with tab4:
        st.subheader("Simulador de Demanda Exponencial")
        dias_restantes = DIAS_TOTAIS - dia_atual
        prospec = []
        for t in ["T00", "T10", "T20", "T30"]:
            df_t = df_full[df_full['tratamento'] == t]
            cons_hist = df_t['consumo'].sum()
            est_atual = RACAO_INICIAL[t] - (cons_hist / 1000)
            
            df_hoje = df_t[df_t['dia_exp'] == dia_atual]
            if not df_hoje.empty and df_hoje['taxa_arracoamento'].mean() > 0:
                t_sim = df_hoje['taxa_arracoamento'].mean() / 100.0
                p_sim = df_hoje['peso_est'].mean()
                v_sim = df_hoje['n_peixes_atual'].sum()
            else:
                t_sim, p_sim, v_sim = 0.03, df_t['peso_medio_inicial'].mean(), df_t['n_peixes_inicial'].sum()
            
            dem_f = 0
            for _ in range(dias_restantes):
                p_sim *= np.exp(TCE_ESTIMADA)
                dem_f += (p_sim * v_sim * t_sim)
            
            necessidade = dem_f / 1000
            prospec.append({"Tratamento": t, "Estoque (kg)": est_atual, "Falta (kg)": necessidade, "Saldo": est_atual - necessidade})
            
        df_p = pd.DataFrame(prospec)
        st.plotly_chart(px.bar(df_p, x="Tratamento", y=["Estoque (kg)", "Falta (kg)"], barmode="group", text_auto='.2f', template="plotly_dark", color_discrete_sequence=["#1f77b4", "#ff7f0e"]), use_container_width=True)
        
        st.subheader("🤖 Relatório Analítico: Logística")
        df_falta = df_p[df_p['Saldo'] < 0]
        if not df_falta.empty:
            total_falta = abs(df_falta['Saldo'].sum())
            st.error(f"🚨 Crítico: Faltarão {total_falta:.2f} kg de ração para encerrar o ciclo com peso alvo de {peso_alvo}g.")
        else:
            st.success("✅ Estoque seguro: O volume atual é suficiente para atingir o peso alvo.")

except Exception as e:
    st.error(f"Aguardando conexão com OneDrive... ({e})")
