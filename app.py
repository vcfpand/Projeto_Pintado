import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np

st.set_page_config(page_title="Pintado Dashboard v13", layout="wide")

# --- CONFIGURAÇÕES DO EXPERIMENTO ---
RACAO_INICIAL = {"T00": 14.74, "T10": 12.58, "T20": 12.90, "T30": 13.51}
DIAS_TOTAIS = 46      

st.title("🐟 Sistema de Monitoramento Zootécnico - Pintado")

@st.cache_data
def load_raw_data():
    arquivo = "DadosExperimento.xlsx"
    df_diario = pd.read_excel(arquivo, sheet_name="Parametros_diarios")
    df_bio = pd.read_excel(arquivo, sheet_name="Biometrias")
    
    df_diario.columns = [c.strip().lower() for c in df_diario.columns]
    df_bio.columns = [c.strip().lower() for c in df_bio.columns]
    
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
    st.sidebar.header("⚙️ Projeção Biológica")
    peso_alvo = st.sidebar.slider("🎯 Peso Final Esperado (g)", min_value=40.0, max_value=150.0, value=90.0, step=1.0)
    
    peso_ini_global = df_raw['peso_medio_inicial'].mean()
    TCE_ESTIMADA = (np.log(peso_alvo) - np.log(peso_ini_global)) / DIAS_TOTAIS
    
    st.sidebar.info(f"📈 Para atingir **{peso_alvo}g**, o lote precisa crescer **{TCE_ESTIMADA*100:.2f}% ao dia**.")
    st.sidebar.divider()
    
    st.sidebar.header("⚙️ Filtros")
    trat_sel = st.sidebar.multiselect("Tratamentos", sorted(df_raw['tratamento'].unique()), default=df_raw['tratamento'].unique())

    # --- PROCESSAMENTO DINÂMICO ---
    df_full = df_raw.copy()
    
    df_full['peso_est'] = df_full['peso_medio_inicial'] * np.exp(TCE_ESTIMADA * df_full['dia_exp'])
    df_full['mort_acum'] = df_full.groupby('caixa')['mort'].cumsum().fillna(0)
    df_full['n_peixes_atual'] = df_full['n_peixes_inicial'] - df_full['mort_acum']
    
    df_full['biomassa_inicial_g'] = df_full['peso_medio_inicial'] * df_full['n_peixes_inicial']
    df_full['biomassa_est_g'] = df_full['peso_est'] * df_full['n_peixes_atual']
    df_full['ganho_biomassa_g'] = df_full['biomassa_est_g'] - df_full['biomassa_inicial_g']
    
    df_full['consumo_acum'] = df_full.groupby('caixa')['consumo'].cumsum()
    df_full['caa_est'] = np.where(df_full['ganho_biomassa_g'] > 0.01, df_full['consumo_acum'] / df_full['ganho_biomassa_g'], 0.0)
    df_full['caa_est'] = df_full['caa_est'].replace([np.inf, -np.inf], 0.0).fillna(0.0)
    
    df_full['taxa_arracoamento'] = (df_full['consumo'] / df_full['biomassa_est_g']) * 100

    # --- LÓGICA DE DIAS E BARRA DE PROGRESSO ---
    df_real = df_full.dropna(subset=['consumo'])
    dia_atual = int(df_real['dia_exp'].max()) if not df_real.empty else 1 
    dia_atual = max(1, dia_atual)

    progresso = min(dia_atual / DIAS_TOTAIS, 1.0)
    st.write(f"**Progresso do Experimento:** Dia {dia_atual} de {DIAS_TOTAIS} (Término em 17/04) — {progresso*100:.1f}% concluído")
    st.progress(progresso)
    st.divider()

    dias_sel = st.sidebar.slider("Intervalo de Dias", 0, dia_atual, (0, dia_atual))
    df_f = df_full[(df_full['tratamento'].isin(trat_sel)) & (df_full['dia_exp'].between(dias_sel[0], dias_sel[1]))]

    # --- DASHBOARD SUPERIOR ---
    st.subheader(f"📊 Desempenho Zootécnico (Dia {dias_sel[0]} ao {dias_sel[1]})")
    ordem_fixa = ["T00", "T10", "T20", "T30"]
    tratamentos_exibicao = [t for t in ordem_fixa if t in trat_sel]
    
    if len(tratamentos_exibicao) > 0:
        cols = st.columns(len(tratamentos_exibicao))
        for i, trat in enumerate(tratamentos_exibicao):
            d_trat = df_f[df_f['tratamento'] == trat]
            d_trat_ultimo_dia = d_trat[d_trat['dia_exp'] == dias_sel[1]]
            
            peso_ini = d_trat['peso_medio_inicial'].mean() if not d_trat.empty else 0.0
            peso_est = d_trat_ultimo_dia['peso_est'].mean() if not d_trat_ultimo_dia.empty else 0.0
            taxa_pv = d_trat['taxa_arracoamento'].mean() if not d_trat.empty else 0.0
            val_caa = d_trat_ultimo_dia['caa_est'].mean() if not d_trat_ultimo_dia.empty else 0.0
            
            with cols[i]:
                st.info(f"**{trat}**")
                st.metric("Peso Inicial", f"{peso_ini:.2f} g")
                st.metric("Peso Est.", f"{peso_est:.2f} g")
                st.metric("Taxa Arraç.", f"{taxa_pv:.2f} % PV")
                st.metric("CAA Est.", f"{val_caa:.2f}")

    st.divider()

    # --- TABS ---
    tab1, tab2, tab3, tab4 = st.tabs(["📈 Zootecnia", "🧪 Qualidade de Água", "🔬 Estatística & Correlação", "📦 Estoque (Projeção)"])

    # ==========================================
    # TAB 1: ZOOTECNIA
    # ==========================================
    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(px.line(df_f, x="dia_exp", y="peso_est", color="tratamento", title="Curva de Crescimento Projetada (g)", template="plotly_dark"), use_container_width=True)
        with c2:
            st.plotly_chart(px.line(df_f, x="dia_exp", y="caa_est", color="tratamento", title="Evolução da Conversão Alimentar (CAA)", template="plotly_dark"), use_container_width=True)
            
        # RELATÓRIO IA - ZOOTECNIA
        st.markdown("---")
        st.subheader("🤖 Relatório Analítico: Zootecnia")
        
        df_ultimo = df_f[df_f['dia_exp'] == dias_sel[1]]
        if not df_ultimo.empty:
            melhor_caa = df_ultimo.groupby('tratamento')['caa_est'].mean().idxmin()
            val_melhor_caa = df_ultimo.groupby('tratamento')['caa_est'].mean().min()
            mort_total = df_f['mort'].sum()
            
            ponto_forte = f"O tratamento **{melhor_caa}** está apresentando a maior eficiência alimentar (CAA estimada em {val_melhor_caa:.2f})."
            ponto_critico = f"Foi detectada uma mortalidade acumulada de {int(mort_total)} peixe(s) no período." if mort_total > 0 else "Nenhuma mortalidade registrada no período analisado. Excelente sanidade do lote."
            
            st.success(f"**✅ Ponto Forte:** {ponto_forte}")
            if mort_total > 0:
                st.error(f"**⚠️ Ponto Crítico:** {ponto_critico}")
            else:
                st.success(f"**✅ Ponto Crítico:** {ponto_critico}")

    # ==========================================
    # TAB 2: QUALIDADE DE ÁGUA
    # ==========================================
    with tab2:
        c_sel1, c_sel2 = st.columns([1, 2])
        param = c_sel1.selectbox("Parâmetro", ['ph', 'temp', 'od', 'amonia', 'nitrito'], key="param_agua")
        visao_agua = c_sel2.radio("Visualizar por:", ["Média do Tratamento", "Boxplot (Distribuição)", "Individual por Caixa"], horizontal=True)
        
        if visao_agua == "Média do Tratamento":
            st.plotly_chart(px.line(df_f.groupby(['dia_exp', 'tratamento'])[param].mean().reset_index(), x="dia_exp", y=param, color="tratamento", markers=True, template="plotly_dark"), use_container_width=True)
        elif visao_agua == "Boxplot (Distribuição)":
            st.plotly_chart(px.box(df_f, x="tratamento", y=param, color="tratamento", points="all", template="plotly_dark"), use_container_width=True)
        else:
            st.plotly_chart(px.line(df_f, x="dia_exp", y=param, color="caixa", facet_col="tratamento", facet_col_wrap=2, markers=True, template="plotly_dark"), use_container_width=True)
            

        # RELATÓRIO IA - ÁGUA
        st.markdown("---")
        st.subheader("🤖 Relatório Analítico: Qualidade de Água")
        
        if not df_f.empty and df_f['amonia'].notna().any():
            max_amonia = df_f['amonia'].max()
            min_od = df_f['od'].min()
            
            alertas = []
            if max_amonia > 0.5:
                alertas.append(f"Picos de **Amônia** ({max_amonia:.2f} mg/L) detectados. Risco de toxidez branquial.")
            if min_od < 4.0:
                alertas.append(f"Quedas críticas de **Oxigênio Dissolvido** ({min_od:.2f} mg/L). Verifique a aeração.")
                
            if alertas:
                for alerta in alertas:
                    st.warning(f"**⚠️ Alerta Ambiental:** {alerta}")
            else:
                st.success("**✅ Ponto Forte:** Os parâmetros de água analisados encontram-se em faixas seguras e estáveis para o Pintado.")

    # ==========================================
    # TAB 3: ESTATÍSTICA & CORRELAÇÃO
    # ==========================================
    with tab3:
        param_corr = st.selectbox("Escolha o Parâmetro vs Consumo (% PV):", ['amonia', 'od', 'temp', 'ph'], key="param_corr")
        st.plotly_chart(px.scatter(df_f, x=param_corr, y="taxa_arracoamento", color="tratamento", trendline="ols", labels={'taxa_arracoamento': 'Consumo (% PV)'}, template="plotly_dark", opacity=0.7), use_container_width=True)

        # RELATÓRIO IA - CORRELAÇÃO
        st.markdown("---")
        st.subheader("🤖 Relatório Analítico: Comportamento")
        
        if len(df_f) > 3 and df_f[param_corr].nunique() > 1:
            corr_val = df_f[param_corr].corr(df_f['taxa_arracoamento'])
            if pd.notna(corr_val):
                if corr_val < -0.4:
                    insight = f"Correlação **negativa forte ({corr_val:.2f})**. O aumento de '{param_corr.upper()}' está inibindo visivelmente o apetite dos peixes."
                    st.error(f"**⚠️ Ponto Crítico:** {insight}")
                elif corr_val > 0.4:
                    insight = f"Correlação **positiva forte ({corr_val:.2f})**. O aumento de '{param_corr.upper()}' estimulou o consumo de ração."
                    st.success(f"**💡 Insight:** {insight}")
                else:
                    st.info(f"**📊 Estabilidade:** A correlação entre '{param_corr.upper()}' e o consumo é fraca ({corr_val:.2f}). O parâmetro não afetou drasticamente a alimentação.")
            else:
                st.info("Aguardando mais variação de dados para calcular a correlação.")
        else:
            st.info("Dados insuficientes para gerar análise estatística neste intervalo.")

    # ==========================================
    # TAB 4: ESTOQUE
    # ==========================================
    with tab4:
        dias_restantes = DIAS_TOTAIS - dia_atual
        dados_est = []
        for t in ["T00", "T10", "T20", "T30"]:
            df_trat = df_full[df_full['tratamento'] == t]
            cons_t_g = df_trat['consumo'].sum() if not df_trat['consumo'].isnull().all() else 0
            est_atual_kg = RACAO_INICIAL[t] - (cons_t_g / 1000)
            
            df_hoje = df_trat[df_trat['dia_exp'] == dia_atual]
            if not df_hoje.empty and df_hoje['taxa_arracoamento'].notna().any() and df_hoje['taxa_arracoamento'].mean() > 0:
                taxa_pv_proj = df_hoje['taxa_arracoamento'].mean() / 100.0
                peso_hoje = df_hoje['peso_est'].mean()
                vivos_hoje = df_hoje['n_peixes_atual'].sum()
            else:
                taxa_pv_proj = 0.03 
                peso_hoje = df_trat['peso_medio_inicial'].mean()
                vivos_hoje = df_trat['n_peixes_inicial'].sum()
            
            necessidade_futura_g = 0
            peso_simulado = peso_hoje
            for _ in range(dias_restantes):
                peso_simulado = peso_simulado * np.exp(TCE_ESTIMADA)
                biomassa_simulada = peso_simulado * vivos_hoje      
                necessidade_futura_g += biomassa_simulada * taxa_pv_proj
                
            necessidade_kg = necessidade_futura_g / 1000
            saldo = est_atual_kg - necessidade_kg
            dados_est.append({"Tratamento": t, "Estoque (kg)": est_atual_kg, "Necessidade (kg)": necessidade_kg, "Saldo": saldo})
            
        df_p = pd.DataFrame(dados_est)
        st.plotly_chart(px.bar(df_p, x="Tratamento", y=["Estoque (kg)", "Necessidade (kg)"], barmode="group", text_auto='.2f', template="plotly_dark", color_discrete_sequence=["#1f77b4", "#ff7f0e"]), use_container_width=True)

        # RELATÓRIO IA - LOGÍSTICA
        st.markdown("---")
        st.subheader("🤖 Relatório Analítico: Logística de Insumos")
        
        df_deficit = df_p[df_p['Saldo'] < 0]
        if not df_deficit.empty:
            trats = ", ".join(df_deficit['Tratamento'].tolist())
            total_falta = abs(df_deficit['Saldo'].sum())
            st.error(f"**🚨 Ponto Crítico de Compras:** Faltará ração para os tratamentos **{trats}**. Volume total de recompra estimado: **{total_falta:.2f} kg**.")
        else:
            sobra_total = df_p['Saldo'].sum()
            st.success(f"**✅ Ponto Forte:** Planejamento de insumos perfeito. Não haverá falta de ração e estima-se uma sobra global de **{sobra_total:.2f} kg** ao final do ciclo.")

except Exception as e:
    st.error(f"Erro detectado: {e}")