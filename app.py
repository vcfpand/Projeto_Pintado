import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import requests
from io import BytesIO
import logging
from datetime import datetime

# ==========================================
# CONFIGURAÇÃO DE LOGGING
# ==========================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="Pintado Dashboard - Analítico",
    layout="wide",
    page_icon="🐟",
    initial_sidebar_state="expanded",
)

# Tema de cores por tratamento (consistente em todos os gráficos)
COR_TRATAMENTO = {"T00": "#4DA8DA", "T10": "#2ECC71", "T20": "#F39C12", "T30": "#E74C3C"}

# ==========================================
# VALIDAÇÃO DE SECRETS OBRIGATÓRIOS
# ==========================================
REQUIRED_SECRETS = ["SENHA_ACESSO", "URL_ONEDRIVE"]
for secret in REQUIRED_SECRETS:
    if secret not in st.secrets:
        st.error(f"❌ Secret ausente: `{secret}`. Configure em Secrets do Streamlit.")
        st.stop()
logger.info("✅ Secrets obrigatórios validados")

# ==========================================
# INTEGRAÇÃO GEMINI
# ==========================================
usa_gemini = False
client = None

GEMINI_MODEL = "gemini-3-flash-preview"

def call_gemini_api(model, prompt):
    raise RuntimeError("Gemini não disponível")

try:
    from google import genai
    from tenacity import retry, stop_after_attempt, wait_exponential

    if "GEMINI_API_KEY" in st.secrets:
        client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
        usa_gemini = True
        logger.info("✅ Gemini inicializado com sucesso")

        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            reraise=True,  # propaga o erro real em vez de embrulhá-lo em RetryError
        )
        def call_gemini_api(model, prompt):
            """Chama API Gemini com retry e backoff exponencial."""
            return client.models.generate_content(model=model, contents=prompt)

except ImportError:
    st.sidebar.warning("⚠️ Biblioteca 'google-genai' ou 'tenacity' não instalada.")
    logger.warning("Gemini não disponível - biblioteca não instalada")

except Exception as e:
    st.sidebar.warning(f"⚠️ Erro ao inicializar Gemini: {e}")
    logger.error(f"Erro ao inicializar Gemini: {e}")

# ==========================================
# 1. LOGIN
# ==========================================
if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    col_login, _ = st.columns([1, 2])
    with col_login:
        st.title("🐟 Monitoramento - Pintado")
        with st.container(border=True):
            st.markdown("**Acesso Restrito**")
            senha_digitada = st.text_input("Senha:", type="password", placeholder="Digite sua senha")
            if st.button("Entrar", type="primary", use_container_width=True):
                if senha_digitada == st.secrets.get("SENHA_ACESSO", ""):
                    st.session_state["autenticado"] = True
                    st.rerun()
                else:
                    st.error("❌ Senha incorreta.")
    st.stop()

# ==========================================
# 2. CONSTANTES E LEITURA DE DADOS
# ==========================================
st.title("🐟 Pintado — Substituição da farinha de peixe por farinha de larvas da mosca-soldado-negro (*Hermetia illucens*) na alimentação de juvenis de pintado (*Pseudoplatystoma corruscans*)")
st.caption("Monitoramento analítico de juvenis de *Pseudoplatystoma corruscans*")
st.divider()

RACAO_INICIAL = {"T00": 14.74, "T10": 12.58, "T20": 12.90, "T30": 13.51}
DIAS_TOTAIS = 53
CACHE_TTL = 120  # segundos

# Limites de alerta para qualidade da água
ALERTAS_AGUA = {
    "nitrito": {"max": 0.1,  "label": "Nitrito (mg/L)"},
    "od":      {"min": 5.0,  "label": "OD (mg/L)"},
    "ph":      {"min": 6.5,  "max": 8.5, "label": "pH"},
    "temp":    {"min": 24.0, "max": 30.0, "label": "Temperatura (°C)"},
}

# Faixas de NH₃ tóxica (não ionizada) para Pintado — baseado na tabela de Emerson et al. (1975)
# Cores extraídas da tabela de referência (verde → amarelo → laranja → vermelho)
# < 0.02  → 🟢 Seguro    (zona verde da tabela)
# 0.02–0.05 → 🟡 Atenção  (zona amarela)
# 0.05–0.10 → 🟠 Crítico  (zona laranja/rosa)
# ≥ 0.10  → 🔴 Perigoso  (zona vermelha)
NH3_SEGURO   = 0.02   # mg/L
NH3_ATENCAO  = 0.05   # mg/L
NH3_CRITICO  = 0.10   # mg/L
# Aliases mantidos por compatibilidade com card KPI
NH3_LIMITE_ALERTA  = NH3_SEGURO
NH3_LIMITE_CRITICO = NH3_CRITICO

# Faixas de nitrito (NO₂⁻) para Pintado
# 0.00       → Ideal   (verde)
# 0.01–0.24  → Aceitável (amarelo)
# 0.25–0.99  → Crítico  (laranja)
# ≥ 1.00     → Perigoso (vermelho)
NITRITO_IDEAL    = 0.0
NITRITO_ACEIT    = 0.25
NITRITO_CRITICO  = 0.50
NITRITO_PERIGOSO = 1.00

@st.cache_data(ttl=CACHE_TTL, show_spinner="Carregando dados do OneDrive...")
def load_data() -> pd.DataFrame | None:
    """Carrega dados do OneDrive com validação"""
    try:
        url = st.secrets["URL_ONEDRIVE"].strip()
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        xls = BytesIO(resp.content)
        df_d = pd.read_excel(xls, sheet_name="Parametros_diarios", engine="openpyxl")
        xls.seek(0)
        df_b = pd.read_excel(xls, sheet_name="Biometrias", engine="openpyxl")

        df_d.columns = [c.strip().lower() for c in df_d.columns]
        df_b.columns = [c.strip().lower() for c in df_b.columns]

        cols_num = ["ph", "temp", "od", "cond", "amonia", "nitrito", "mort", "consumo", "dia_exp"]
        for col in cols_num:
            if col in df_d.columns:
                if df_d[col].dtype == object:
                    df_d[col] = df_d[col].astype(str).str.replace(",", ".")
                df_d[col] = pd.to_numeric(df_d[col], errors="coerce")

        df_b_resumo = df_b[["caixa", "n_peixes_inicial", "peso_medio_inicial"]].drop_duplicates(subset=["caixa"])
        df = pd.merge(df_d, df_b_resumo, on="caixa", how="left")
        df["caixa"] = df["caixa"].astype(str)

        logger.info(f"✅ Dados carregados: {len(df)} registros")
        return df

    except requests.exceptions.Timeout:
        st.error("❌ Timeout ao baixar planilha. Verifique sua conexão.")
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"❌ Erro HTTP: {e}")
        return None
    except Exception as e:
        st.error(f"❌ Falha de sistema: {e}")
        logger.error(f"Erro ao carregar dados: {e}", exc_info=True)
        return None


def validate_data(df_in: pd.DataFrame) -> pd.DataFrame:
    required_cols = ["caixa", "tratamento", "dia_exp", "consumo", "ph", "temp", "od", "mort"]
    missing = [c for c in required_cols if c not in df_in.columns]
    if missing:
        raise ValueError(f"❌ Colunas obrigatórias ausentes: {missing}")
    if df_in.empty:
        raise ValueError("❌ DataFrame vazio após carregamento")
    return df_in


def remove_outliers_zscore(df_in: pd.DataFrame, colunas_alvo: list, limite_z: float = 3) -> pd.DataFrame:
    df_limpo = df_in.copy()
    for col in colunas_alvo:
        if col in df_limpo.columns and df_limpo[col].notna().any() and df_limpo[col].std() > 0:
            z = np.abs((df_limpo[col] - df_limpo[col].mean()) / df_limpo[col].std())
            df_limpo = df_limpo[(z < limite_z) | (df_limpo[col].isna())]
    return df_limpo


def calcular_nh3_toxica(amonia_total: float, ph: float, temp_c: float) -> float:
    """
    Calcula a concentração de amônia não ionizada (NH₃ tóxica) a partir da amônia total,
    pH e temperatura. Fórmula de Emerson et al. (1975), padrão em aquicultura.

    pKa = 0.09018 + 2729.92 / (T + 273.15)
    f   = 1 / (10^(pKa - pH) + 1)
    NH₃ = amônia_total × f
    """
    if any(pd.isna(v) for v in [amonia_total, ph, temp_c]):
        return float("nan")
    pka = 0.09018 + (2729.92 / (temp_c + 273.15))
    f   = 1.0 / (10 ** (pka - ph) + 1.0)
    return amonia_total * f


def calcular_alertas(df_trat: pd.DataFrame) -> list[dict]:
    """Retorna lista de alertas críticos de qualidade da água.
    - Nitrito, amônia total e NH₃ tóxica: último valor registrado.
    - OD, pH, temperatura, condutividade: média do intervalo selecionado.
    """
    alertas = []
    df_sorted = df_trat.sort_values("dia_exp")

    # ── Parâmetros avaliados pela média (OD, pH, temp, cond) ─────────
    PARAMS_MEDIA = {k: v for k, v in ALERTAS_AGUA.items() if k != "nitrito"}
    for param, limites in PARAMS_MEDIA.items():
        if param not in df_trat.columns:
            continue
        valor = df_trat[param].mean()
        if pd.isna(valor):
            continue
        if "max" in limites and valor > limites["max"]:
            alertas.append({"param": limites["label"], "valor": valor, "tipo": "⚠️ ALTO",  "limite": limites["max"], "nh3": False, "rotulo": "média"})
        if "min" in limites and valor < limites["min"]:
            alertas.append({"param": limites["label"], "valor": valor, "tipo": "⚠️ BAIXO", "limite": limites["min"], "nh3": False, "rotulo": "média"})

    # ── Nitrito — último valor registrado com escala de severidade ──
    ult_nitrito = df_sorted.dropna(subset=["nitrito"])
    if not ult_nitrito.empty:
        nitrito_val = ult_nitrito["nitrito"].iloc[-1]
        dia_nit     = int(ult_nitrito["dia_exp"].iloc[-1])

        if nitrito_val >= NITRITO_PERIGOSO:
            tipo_nit  = "🔴 PERIGOSO"
            faixa_nit = f"≥ {NITRITO_PERIGOSO} mg/L"
            nivel_nit = "perigoso"
        elif nitrito_val >= NITRITO_CRITICO:
            tipo_nit  = "🟠 CRÍTICO"
            faixa_nit = f"{NITRITO_CRITICO}–{NITRITO_PERIGOSO} mg/L"
            nivel_nit = "critico"
        elif nitrito_val >= NITRITO_ACEIT:
            tipo_nit  = "🟡 ACEITÁVEL"
            faixa_nit = f"{NITRITO_ACEIT}–{NITRITO_CRITICO} mg/L"
            nivel_nit = "aceitavel"
        else:
            tipo_nit  = "🟢 IDEAL"
            faixa_nit = f"< {NITRITO_ACEIT} mg/L"
            nivel_nit = "ideal"

        alertas.append({
            "param":    f"Nitrito NO₂⁻ (Dia {dia_nit})",
            "valor":    nitrito_val,
            "tipo":     tipo_nit,
            "faixa":    faixa_nit,
            "nivel":    nivel_nit,
            "nh3":      False,
            "nitrito":  True,
            "rotulo":   "último registro",
        })

    # ── Amônia Total e NH₃ tóxica — último valor registrado ─────────
    ult_amonia = df_sorted.dropna(subset=["amonia"])
    ult_ph     = df_sorted.dropna(subset=["ph"])
    ult_temp   = df_sorted.dropna(subset=["temp"])

    if not ult_amonia.empty:
        amonia_val = ult_amonia["amonia"].iloc[-1]
        dia_ref    = int(ult_amonia["dia_exp"].iloc[-1])
        ph_val     = ult_ph["ph"].iloc[-1]    if not ult_ph.empty    else float("nan")
        temp_val   = ult_temp["temp"].iloc[-1] if not ult_temp.empty  else float("nan")

        nh3 = calcular_nh3_toxica(amonia_val, ph_val, temp_val)

        if pd.notna(nh3):
            if nh3 >= NH3_CRITICO:
                tipo_nh3  = "🔴 PERIGOSO"
                nivel_nh3 = "perigoso"
                faixa_nh3 = f"≥ {NH3_CRITICO} mg/L"
            elif nh3 >= NH3_ATENCAO:
                tipo_nh3  = "🟠 CRÍTICO"
                nivel_nh3 = "critico"
                faixa_nh3 = f"{NH3_ATENCAO}–{NH3_CRITICO} mg/L"
            elif nh3 >= NH3_SEGURO:
                tipo_nh3  = "🟡 ATENÇÃO"
                nivel_nh3 = "atencao"
                faixa_nh3 = f"{NH3_SEGURO}–{NH3_ATENCAO} mg/L"
            else:
                tipo_nh3  = "🟢 SEGURO"
                nivel_nh3 = "seguro"
                faixa_nh3 = f"< {NH3_SEGURO} mg/L"

            alertas.append({
                "param":        f"NH₃ Tóxica (Dia {dia_ref})",
                "valor":        nh3,
                "tipo":         tipo_nh3,
                "nivel":        nivel_nh3,
                "faixa":        faixa_nh3,
                "nh3":          True,
                "amonia_total": amonia_val,
                "ph_ref":       ph_val,
                "temp_ref":     temp_val,
            })

    return alertas


# ==========================================
# CARREGAMENTO
# ==========================================
df = load_data()
if df is None:
    st.error("❌ Falha ao carregar dados. Verifique `URL_ONEDRIVE` nos Secrets.")
    st.stop()

try:
    df = validate_data(df)
except ValueError as e:
    st.error(str(e))
    st.stop()

# ==========================================
# 3. SIDEBAR
# ==========================================
st.sidebar.header("⚙️ Configurações Globais")

col_sb1, col_sb2 = st.sidebar.columns(2)
if col_sb1.button("🔄 Recarregar", use_container_width=True, help="Força recarga do OneDrive"):
    st.cache_data.clear()
    st.rerun()
if col_sb2.button("🚪 Sair", use_container_width=True):
    st.session_state["autenticado"] = False
    st.rerun()

remover_outliers = st.sidebar.toggle("Limpar Outliers (Z-Score=3)", value=False)
st.sidebar.divider()

st.sidebar.header("🎯 Projeção de Abate")
peso_alvo = st.sidebar.slider("Peso Final Esperado (g)", 40.0, 150.0, 90.0)
peso_ini = df["peso_medio_inicial"].mean()
tce = (np.log(peso_alvo) - np.log(peso_ini)) / DIAS_TOTAIS
st.sidebar.info(f"TCE Necessária: **{tce * 100:.2f}% /dia**")
st.sidebar.divider()

trat_sel = st.sidebar.multiselect(
    "Tratamentos", ["T00", "T10", "T20", "T30"], default=["T00", "T10", "T20", "T30"]
)

if remover_outliers:
    df = remove_outliers_zscore(df, ["ph", "temp", "od", "cond", "amonia", "nitrito", "consumo"])

# ==========================================
# 4. PRÉ-PROCESSAMENTO
# ==========================================
df_unico_dia = df.drop_duplicates(subset=["caixa", "dia_exp"]).copy()
df_unico_dia["consumo_preenchido"] = df_unico_dia["consumo"].fillna(0)
df_unico_dia["consumo_acum"] = df_unico_dia.groupby("caixa")["consumo_preenchido"].cumsum()
df_unico_dia["mort_preenchida"] = df_unico_dia["mort"].fillna(0)
df_unico_dia["mort_acum"] = df_unico_dia.groupby("caixa")["mort_preenchida"].cumsum()

df = pd.merge(
    df, df_unico_dia[["caixa", "dia_exp", "consumo_acum", "mort_acum"]],
    on=["caixa", "dia_exp"], how="left"
)

df["peso_est"] = df["peso_medio_inicial"] * np.exp(tce * df["dia_exp"])
df["n_peixes_atual"] = df["n_peixes_inicial"] - df["mort_acum"]
df["biomassa_est_g"] = df["peso_est"] * df["n_peixes_atual"]
df["ganho_biomassa_g"] = df["biomassa_est_g"] - (df["peso_medio_inicial"] * df["n_peixes_inicial"])
df["gpd"] = df["peso_est"].diff().clip(lower=0)  # Ganho de Peso Diário estimado

df["caa_est"] = np.where(
    (df["ganho_biomassa_g"] > 0.01) & (df["ganho_biomassa_g"].notna()),
    df["consumo_acum"] / df["ganho_biomassa_g"],
    np.nan,
)
df["taxa_arracoamento"] = np.where(
    (df["biomassa_est_g"] > 0) & (df["biomassa_est_g"].notna()),
    (df["consumo"] / df["biomassa_est_g"]) * 100,
    np.nan,
)
df["sobrevivencia_pct"] = np.where(
    df["n_peixes_inicial"] > 0,
    (df["n_peixes_atual"] / df["n_peixes_inicial"]) * 100,
    np.nan,
)

df_real = df.dropna(subset=["consumo"])
dia_max_preenchido = int(df_real["dia_exp"].max()) if not df_real.empty else 1

# Filtro de dias
dias_sel = st.sidebar.slider("Filtro de Dias", 0, dia_max_preenchido, (0, dia_max_preenchido))

df_f = df[
    (df["tratamento"].isin(trat_sel)) & (df["dia_exp"].between(dias_sel[0], dias_sel[1]))
].dropna(subset=["dia_exp", "tratamento"])

# ==========================================
# 5. BARRA DE PROGRESSO
# ==========================================
prog_col1, prog_col2 = st.columns([3, 1])
with prog_col1:
    st.write(f"**Progresso do Ensaio:** Dia {dia_max_preenchido} de {DIAS_TOTAIS} ({dia_max_preenchido/DIAS_TOTAIS*100:.0f}%)")
    st.progress(min(dia_max_preenchido / DIAS_TOTAIS, 1.0))
with prog_col2:
    dias_restantes = DIAS_TOTAIS - dia_max_preenchido
    st.metric("Dias Restantes", f"{dias_restantes}")
st.divider()

# ==========================================
# 6. ALERTAS AUTOMÁTICOS DE QUALIDADE DA ÁGUA
# ==========================================
if trat_sel:
    todos_alertas = {}
    for trat in trat_sel:
        d_trat = df_f[df_f["tratamento"] == trat]
        alertas = calcular_alertas(d_trat)
        if alertas:
            todos_alertas[trat] = alertas

    if todos_alertas:
        with st.expander("⚠️ Alertas de Qualidade da Água — Clique para expandir", expanded=True):
            for trat, alertas in todos_alertas.items():
                st.markdown(f"**{trat}**")
                for a in alertas:
                    if a.get("nh3"):
                        linha1 = f"{a['tipo']} — **{a['param']}**: `{a['valor']:.4f} mg/L` — Faixa: {a['faixa']}"
                        linha2 = f"Calculada com: NH₄⁺ Total = `{a['amonia_total']:.3f} mg/L` | pH = `{a['ph_ref']:.2f}` | Temp = `{a['temp_ref']:.1f} °C`"
                        linha3 = "Ref: Emerson et al. (1975) | Escala: 🟢 <0.02 Seguro | 🟡 0.02–0.05 Atenção | 🟠 0.05–0.10 Crítico | 🔴 ≥0.10 Perigoso"
                        msg_nh3 = linha1 + "  \n" + linha2 + "  \n" + linha3
                        nivel_nh3 = a.get("nivel", "seguro")
                        if nivel_nh3 == "perigoso":
                            st.error(msg_nh3)
                        elif nivel_nh3 == "critico":
                            st.warning(msg_nh3)
                        elif nivel_nh3 == "atencao":
                            st.info(msg_nh3)
                        else:
                            st.success(msg_nh3)
                    elif a.get("nitrito"):
                        msg_nit = (
                            f"{a['tipo']} — **{a['param']}**: `{a['valor']:.3f} mg/L`  \n"
                            f"Faixa: {a['faixa']}  \n"
                            f"Referência: 0.00 🟢 Ideal | 0.25 🟡 Aceitável | 0.50 🟠 Crítico | ≥1.00 🔴 Perigoso"
                        )
                        nivel = a.get("nivel", "ideal")
                        if nivel == "perigoso":
                            st.error(msg_nit)
                        elif nivel == "critico":
                            st.warning(msg_nit)
                        elif nivel == "aceitavel":
                            st.info(msg_nit)
                        else:
                            st.success(msg_nit)
                    else:
                        rotulo = a.get("rotulo", "média")
                        st.warning(f"{a['tipo']} — {a['param']}: **{a['valor']:.3f}** ({rotulo}, limite: {a['limite']})")

# ==========================================
# 7. CARDS KPI
# ==========================================
st.subheader(f"📊 Desempenho Zootécnico — Dia {dias_sel[0]} a {dias_sel[1]}")

cols = st.columns(len(trat_sel)) if trat_sel else []
dados_gemini = {}

for i, trat in enumerate(trat_sel):
    d_trat = df_f[df_f["tratamento"] == trat]
    d_trat_unico = d_trat.drop_duplicates(subset=["caixa", "dia_exp"])
    df_trat_consumo = d_trat_unico.dropna(subset=["consumo"])

    if not df_trat_consumo.empty:
        ultimo_dia = df_trat_consumo["dia_exp"].max()
        dia_anterior = df_trat_consumo[df_trat_consumo["dia_exp"] < ultimo_dia]["dia_exp"].max()
        d_hoje = df_trat_consumo[df_trat_consumo["dia_exp"] == ultimo_dia]
        d_ontem = df_trat_consumo[df_trat_consumo["dia_exp"] == dia_anterior] if pd.notna(dia_anterior) else pd.DataFrame()
        dia_ref = f"Dia {int(ultimo_dia)}"
    else:
        d_hoje = d_ontem = pd.DataFrame()
        dia_ref = "Sem Dados"

    m_ph = d_trat["ph"].mean()
    m_temp = d_trat["temp"].mean()
    m_od = d_trat["od"].mean()
    m_cond = d_trat["cond"].mean()
    m_sobrev = d_trat["sobrevivencia_pct"].mean()

    # Amônia e nitrito: último valor registrado (não média — valores pontuais de análise)
    _ult_amonia = d_trat_unico.dropna(subset=["amonia"]).sort_values("dia_exp")
    _ult_nitrito = d_trat_unico.dropna(subset=["nitrito"]).sort_values("dia_exp")
    m_amonia = _ult_amonia["amonia"].iloc[-1] if not _ult_amonia.empty else float("nan")
    m_nitrito = _ult_nitrito["nitrito"].iloc[-1] if not _ult_nitrito.empty else float("nan")
    dia_amonia = int(_ult_amonia["dia_exp"].iloc[-1]) if not _ult_amonia.empty else None
    dia_nitrito = int(_ult_nitrito["dia_exp"].iloc[-1]) if not _ult_nitrito.empty else None

    # NH₃ tóxica — calculada aqui para uso tanto nos dados_gemini quanto no card
    _nh3_card = calcular_nh3_toxica(m_amonia, m_ph, m_temp)

    cons_acumulado = d_trat_unico.groupby("caixa")["consumo_acum"].max().sum() if not d_trat_unico.empty else 0
    cons_hoje = d_hoje["consumo"].sum() if not d_hoje.empty else 0
    cons_ontem = d_ontem["consumo"].sum() if not d_ontem.empty else 0
    delta_cons = ((cons_hoje - cons_ontem) / cons_ontem * 100) if cons_ontem > 0 else 0
    est_restante_kg = RACAO_INICIAL.get(trat, 0) - (cons_acumulado / 1000)
    mort_total = d_trat_unico.groupby("caixa")["mort_acum"].max().sum() if not d_trat_unico.empty else 0

    dados_gemini[trat] = {
        "Consumo_Ultimo_Dia": cons_hoje,
        "Consumo_Dia_Anterior": cons_ontem,
        "Var_%": round(delta_cons, 2),
        "Mort": int(mort_total),
        "Amonia_total_ultimo": round(m_amonia, 3) if pd.notna(m_amonia) else "N/A",
        "NH3_toxica_calculada": round(_nh3_card, 4) if pd.notna(_nh3_card) else "N/A",
        "OD": round(m_od, 2) if pd.notna(m_od) else "N/A",
        "Sobrevivencia_%": round(m_sobrev, 1) if pd.notna(m_sobrev) else "N/A",
    }

    cor = COR_TRATAMENTO.get(trat, "#888888")
    with cols[i]:
        with st.container(border=True):
            st.markdown(f"<h3 style='text-align:center;color:{cor};'>{trat}</h3>", unsafe_allow_html=True)

            st.markdown("**🌊 Parâmetros Ambientais**")
            c_a, c_b = st.columns(2)
            c_a.metric("pH", f"{m_ph:.2f}" if pd.notna(m_ph) else "—")
            c_b.metric("Temp (°C)", f"{m_temp:.1f}" if pd.notna(m_temp) else "—")
            c_a.metric("OD (mg/L)", f"{m_od:.2f}" if pd.notna(m_od) else "—")
            c_b.metric("Cond (µS)", f"{m_cond:.1f}" if pd.notna(m_cond) else "—")
            _nh3_label = "🔴 NH₃" if (pd.notna(_nh3_card) and _nh3_card >= NH3_LIMITE_CRITICO) else                          "⚠️ NH₃" if (pd.notna(_nh3_card) and _nh3_card >= NH3_LIMITE_ALERTA) else "✅ NH₃"

            c_a.metric(
                f"NH₄⁺ Total (D{dia_amonia})" if dia_amonia else "NH₄⁺ Total",
                f"{m_amonia:.3f}" if pd.notna(m_amonia) else "—",
                help="Amônia total (NH₄⁺ + NH₃) — último valor registrado"
            )
            c_b.metric(
                f"Nitrito (D{dia_nitrito})" if dia_nitrito else "Nitrito",
                f"{m_nitrito:.3f}" if pd.notna(m_nitrito) else "—",
                help="Último valor registrado (não média)"
            )
            c_a.metric(
                _nh3_label + " Tóxica",
                f"{_nh3_card:.4f} mg/L" if pd.notna(_nh3_card) else "—",
                help=f"NH₃ não ionizada (Emerson 1975). Limite alerta: {NH3_LIMITE_ALERTA} | crítico: {NH3_LIMITE_CRITICO} mg/L"
            )

            st.divider()
            st.markdown(f"**🍽️ Arraçoamento ({dia_ref})**")
            st.metric("Acumulado", f"{cons_acumulado:.0f} g")
            ca, cb = st.columns(2)
            ca.metric("Ant.", f"{cons_ontem:.0f} g")
            cb.metric("Último", f"{cons_hoje:.0f} g", f"{delta_cons:+.1f}%")

            st.divider()
            st.markdown("**📋 Gestão**")
            c1, c2 = st.columns(2)
            c1.metric("Ração Disp.", f"{est_restante_kg:.2f} kg")
            c2.metric("Mortalidade", f"{int(mort_total)}")
            if pd.notna(m_sobrev):
                sobrev_color = "normal" if m_sobrev >= 95 else "inverse"
                st.metric("Sobrevivência", f"{m_sobrev:.1f}%", delta_color=sobrev_color)

# ==========================================
# 8. ANÁLISE GEMINI — RELATÓRIO DIÁRIO
# ==========================================
if usa_gemini and client is not None:
    with st.container(border=True):
        st.markdown("#### 🧠 Análise Geral do Experimento (Google Gemini)")
        if st.button("Gerar Relatório Zootécnico Diário", type="primary"):
            with st.spinner("Analisando..."):
                prompt = f"""Atue como um Especialista em Aquicultura. Analise os dados abaixo de juvenis de Pintado
                com substituição de farinha de peixe por farinha de mosca-soldado-negro: {dados_gemini}.
                Produza uma análise em 2 parágrafos:
                1. Avalie a resposta alimentar: algum tratamento reduziu consumo abruptamente?
                2. Avalie sanidade e ambiente: há correlação entre amônia/OD e mortalidade?
                Responda de forma profissional e objetiva."""
                try:
                    resposta = call_gemini_api(model=GEMINI_MODEL, prompt=prompt)
                    st.info(resposta.text)
                except Exception as err:
                    causa = getattr(err, "message", None) or str(err)
                    st.error(f"❌ Erro na API Gemini: {causa}")
                    st.caption("💡 Verifique se a `GEMINI_API_KEY` está correta em Settings → Secrets.")
                    logger.error(f"Erro Gemini: {err}", exc_info=True)

st.divider()

# ==========================================
# 9. ABAS PRINCIPAIS
# ==========================================
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📈 Zootecnia", "🧪 Água", "📉 Mortalidade", "🔬 Estatística", "📥 Dados"]
)

# ------- TAB 1: ZOOTECNIA -------
with tab1:
    st.subheader("Desempenho Biológico")

    c1, c2, c3 = st.columns(3)
    try:
        fig_peso = px.line(df_f, x="dia_exp", y="peso_est", color="tratamento",
                           color_discrete_map=COR_TRATAMENTO,
                           title="Peso Projetado (g)", template="plotly_dark", markers=True)
        c1.plotly_chart(fig_peso, use_container_width=True)

        fig_caa = px.line(df_f, x="dia_exp", y="caa_est", color="tratamento",
                          color_discrete_map=COR_TRATAMENTO,
                          title="CAA Estimada", template="plotly_dark", markers=True)
        c2.plotly_chart(fig_caa, use_container_width=True)

        fig_bio = px.line(df_f, x="dia_exp", y="biomassa_est_g", color="tratamento",
                          color_discrete_map=COR_TRATAMENTO,
                          title="Biomassa Estimada (g)", template="plotly_dark", markers=True)
        c3.plotly_chart(fig_bio, use_container_width=True)
    except Exception as e:
        st.error(f"❌ Erro nos gráficos de zootecnia: {e}")

    st.subheader("Consumo e Taxa de Arraçoamento")
    c4, c5 = st.columns(2)
    try:
        # Consumo diário por tratamento (média)
        df_cons_agg = df_f.dropna(subset=["consumo"]).groupby(["dia_exp", "tratamento"])["consumo"].mean().reset_index()
        fig_cons = px.bar(df_cons_agg, x="dia_exp", y="consumo", color="tratamento",
                          color_discrete_map=COR_TRATAMENTO, barmode="group",
                          title="Consumo Diário Médio por Tratamento (g)", template="plotly_dark")
        c4.plotly_chart(fig_cons, use_container_width=True)

        fig_ta = px.line(df_f, x="dia_exp", y="taxa_arracoamento", color="tratamento",
                         color_discrete_map=COR_TRATAMENTO,
                         title="Taxa de Arraçoamento (% Biomassa/dia)", template="plotly_dark", markers=True)
        c5.plotly_chart(fig_ta, use_container_width=True)
    except Exception as e:
        st.error(f"❌ Erro nos gráficos de consumo: {e}")

    # TCE calculada por tratamento (resumo final)
    st.subheader("📊 Resumo Comparativo de Desempenho")
    resumo_rows = []
    for trat in trat_sel:
        d = df_f[df_f["tratamento"] == trat].dropna(subset=["peso_est"])
        if d.empty:
            continue
        peso_ini_trat = d["peso_medio_inicial"].mean()
        peso_final_trat = d[d["dia_exp"] == d["dia_exp"].max()]["peso_est"].mean()
        dias = d["dia_exp"].max() - d["dia_exp"].min()
        tce_trat = (np.log(peso_final_trat) - np.log(peso_ini_trat)) / dias * 100 if dias > 0 and peso_ini_trat > 0 else np.nan
        gp = peso_final_trat - peso_ini_trat
        d_unico = d.drop_duplicates(subset=["caixa", "dia_exp"])
        mort = d_unico.groupby("caixa")["mort_acum"].max().sum()
        resumo_rows.append({
            "Tratamento": trat,
            "Peso Inicial (g)": round(peso_ini_trat, 2),
            "Peso Final Est. (g)": round(peso_final_trat, 2),
            "GP (g)": round(gp, 2),
            "TCE (%/dia)": round(tce_trat, 3) if pd.notna(tce_trat) else "—",
            "Mortalidade Total": int(mort),
        })
    if resumo_rows:
        st.dataframe(pd.DataFrame(resumo_rows).set_index("Tratamento"), use_container_width=True)

# ------- TAB 2: ÁGUA -------
with tab2:
    st.subheader("Evolução dos Parâmetros Físico-Químicos")
    tipo_grafico = st.radio(
        "Visualização:",
        ["Linha (Média Tratamento)", "Linha (Por Caixa)", "Boxplot (Distribuição)"],
        horizontal=True,
    )
    param_list = ["temp", "od", "amonia", "nitrito", "ph", "cond"]

    try:
        for i in range(0, len(param_list), 3):
            cols_agua = st.columns(3)
            for j in range(3):
                if i + j >= len(param_list):
                    break
                p = param_list[i + j]
                if tipo_grafico == "Linha (Média Tratamento)":
                    df_agg = df_f.groupby(["dia_exp", "tratamento"])[p].mean().reset_index()
                    fig = px.line(df_agg, x="dia_exp", y=p, color="tratamento",
                                  color_discrete_map=COR_TRATAMENTO,
                                  title=p.upper(), template="plotly_dark", markers=True)
                elif tipo_grafico == "Linha (Por Caixa)":
                    fig = px.line(df_f, x="dia_exp", y=p, color="caixa",
                                  facet_col="tratamento", facet_col_wrap=2,
                                  title=p.upper(), template="plotly_dark")
                else:
                    fig = px.box(df_f, x="tratamento", y=p, color="tratamento",
                                 color_discrete_map=COR_TRATAMENTO,
                                 title=p.upper(), template="plotly_dark", points="all")
                cols_agua[j].plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"❌ Erro nos gráficos de água: {e}")

    # Heatmap dos parâmetros médios por tratamento
    st.subheader("🌡️ Heatmap — Médias por Tratamento")
    try:
        df_heat = df_f.groupby("tratamento")[param_list].mean().round(3)
        fig_heat = px.imshow(
            df_heat.T, text_auto=True, color_continuous_scale="RdYlGn_r",
            title="Médias dos Parâmetros por Tratamento", template="plotly_dark",
            labels={"x": "Tratamento", "y": "Parâmetro", "color": "Valor"},
        )
        st.plotly_chart(fig_heat, use_container_width=True)
    except Exception as e:
        st.error(f"❌ Erro no heatmap: {e}")

# ------- TAB 3: MORTALIDADE -------
with tab3:
    st.subheader("📉 Análise de Mortalidade e Sobrevivência")
    c_m1, c_m2 = st.columns(2)

    try:
        # Mortalidade acumulada
        df_mort_agg = (
            df_f.drop_duplicates(subset=["caixa", "dia_exp"])
            .groupby(["dia_exp", "tratamento"])["mort_acum"]
            .mean()
            .reset_index()
        )
        fig_mort = px.line(df_mort_agg, x="dia_exp", y="mort_acum", color="tratamento",
                           color_discrete_map=COR_TRATAMENTO,
                           title="Mortalidade Acumulada Média por Tratamento",
                           template="plotly_dark", markers=True)
        c_m1.plotly_chart(fig_mort, use_container_width=True)

        # Sobrevivência %
        df_sobrev = (
            df_f.drop_duplicates(subset=["caixa", "dia_exp"])
            .groupby(["dia_exp", "tratamento"])["sobrevivencia_pct"]
            .mean()
            .reset_index()
        )
        fig_sobrev = px.line(df_sobrev, x="dia_exp", y="sobrevivencia_pct", color="tratamento",
                             color_discrete_map=COR_TRATAMENTO,
                             title="Sobrevivência (%) por Tratamento",
                             template="plotly_dark", markers=True,
                             range_y=[80, 101])
        fig_sobrev.add_hline(y=95, line_dash="dash", line_color="yellow",
                             annotation_text="Alerta 95%")
        c_m2.plotly_chart(fig_sobrev, use_container_width=True)
    except Exception as e:
        st.error(f"❌ Erro nos gráficos de mortalidade: {e}")

    # Mortalidade diária (eventos)
    try:
        st.subheader("Eventos de Mortalidade Diária")
        df_mort_dia = (
            df_f.dropna(subset=["mort"])
            .groupby(["dia_exp", "tratamento"])["mort"]
            .sum()
            .reset_index()
        )
        fig_mort_dia = px.bar(df_mort_dia, x="dia_exp", y="mort", color="tratamento",
                              color_discrete_map=COR_TRATAMENTO, barmode="group",
                              title="Mortalidade Diária por Tratamento",
                              template="plotly_dark")
        st.plotly_chart(fig_mort_dia, use_container_width=True)
    except Exception as e:
        st.error(f"❌ Erro no gráfico de mortalidade diária: {e}")

# ------- TAB 4: ESTATÍSTICA -------
with tab4:
    st.subheader("🔬 Correlação Ambiental e Comportamental")
    c_e1, c_e2 = st.columns(2)

    with c_e1:
        p_corr = st.selectbox("Eixo X:", ["amonia", "od", "temp", "ph", "nitrito", "cond"])
        try:
            fig_sc = px.scatter(
                df_f, x=p_corr, y="taxa_arracoamento", color="tratamento",
                color_discrete_map=COR_TRATAMENTO,
                trendline="ols", title=f"Impacto de {p_corr.upper()} no Apetite",
                template="plotly_dark",
            )
            st.plotly_chart(fig_sc, use_container_width=True)
        except Exception as e:
            st.error(f"❌ Erro no scatter: {e}")

    with c_e2:
        # Matriz de correlação completa
        st.markdown("**Matriz de Correlação de Pearson**")
        try:
            cols_corr = ["taxa_arracoamento", "amonia", "od", "temp", "ph", "nitrito", "cond", "biomassa_est_g"]
            df_corr = df_f[[c for c in cols_corr if c in df_f.columns]].dropna()
            if len(df_corr) >= 3:
                matriz = df_corr.corr().round(2)
                fig_corr = px.imshow(
                    matriz, text_auto=True, color_continuous_scale="RdBu_r",
                    zmin=-1, zmax=1, template="plotly_dark",
                    title="Correlações (Pearson)",
                )
                st.plotly_chart(fig_corr, use_container_width=True)
            else:
                st.warning("⚠️ Dados insuficientes para calcular correlações.")
        except Exception as e:
            st.error(f"❌ Erro na matriz de correlação: {e}")

    # Relatório IA
    if usa_gemini and client is not None:
        if st.button("🧠 Gerar Relatório Estatístico (IA)", key="btn_estat_ai"):
            with st.spinner("Processando..."):
                try:
                    cols_calc = ["taxa_arracoamento", "amonia", "od", "temp", "ph"]
                    df_limpo_corr = df_f[[c for c in cols_calc if c in df_f.columns]].dropna()
                    if len(df_limpo_corr) < 3:
                        st.warning("⚠️ Dados insuficientes.")
                    else:
                        matriz_corr = df_limpo_corr.corr().round(3).to_dict()
                        prompt_estat = f"""Atue como Investigador Biostatístico em ensaio de substituição de farinha de peixe
                        por Hermetia illucens para Pintado. Matriz de correlação de Pearson: {matriz_corr}.
                        Relate em 3 tópicos:
                        1. Interpretação da correlação entre {p_corr.upper()} e taxa de arraçoamento.
                        2. Impacto multivariado: OD e Amônia interagem com o consumo?
                        3. Conclusão para manejo de estufa.
                        Use linguagem científica formal."""
                        resposta_estat = call_gemini_api(model=GEMINI_MODEL, prompt=prompt_estat)
                        st.success(resposta_estat.text)
                except Exception as e:
                    causa = getattr(e, "message", None) or str(e)
                    st.error(f"❌ Erro na análise estatística: {causa}")
                    st.caption("💡 Verifique se a `GEMINI_API_KEY` está correta em Settings → Secrets.")
                    logger.error(f"Erro Gemini estatística: {e}", exc_info=True)

# ------- TAB 5: DADOS E EXPORTAÇÃO -------
with tab5:
    st.subheader("📥 Dados Filtrados e Exportação")

    col_exp1, col_exp2 = st.columns([2, 1])

    with col_exp1:
        st.markdown("**Tabela de Dados Brutos** (Tratamentos e período selecionados)")
        colunas_exibir = ["tratamento", "caixa", "dia_exp", "consumo", "consumo_acum",
                          "ph", "temp", "od", "amonia", "nitrito", "mort", "mort_acum",
                          "peso_est", "biomassa_est_g", "taxa_arracoamento", "sobrevivencia_pct"]
        colunas_disponiveis = [c for c in colunas_exibir if c in df_f.columns]
        df_exibir = df_f[colunas_disponiveis].sort_values(["tratamento", "caixa", "dia_exp"])

        busca = st.text_input("🔍 Filtrar por caixa ou tratamento:", "")
        if busca:
            mask = df_exibir.apply(lambda row: row.astype(str).str.contains(busca, case=False).any(), axis=1)
            df_exibir = df_exibir[mask]

        st.dataframe(df_exibir.reset_index(drop=True), use_container_width=True, height=350)
        st.caption(f"{len(df_exibir)} registros exibidos")

    with col_exp2:
        st.markdown("**Exportar Dados**")

        # CSV
        csv_data = df_exibir.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇️ Baixar CSV",
            data=csv_data,
            file_name=f"pintado_dados_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

        # Excel
        try:
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                df_exibir.to_excel(writer, sheet_name="Dados_Filtrados", index=False)
                # Resumo por tratamento
                resumo = df_exibir.groupby("tratamento")[
                    [c for c in ["consumo", "ph", "temp", "od", "amonia", "taxa_arracoamento"] if c in df_exibir.columns]
                ].describe().round(3)
                resumo.to_excel(writer, sheet_name="Resumo_Estatistico")
            buffer.seek(0)
            st.download_button(
                label="⬇️ Baixar Excel",
                data=buffer,
                file_name=f"pintado_dados_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        except Exception as e:
            st.warning(f"⚠️ Excel indisponível: {e}")

        st.divider()
        st.markdown("**Estatísticas Rápidas**")
        if not df_exibir.empty:
            for col_stat in ["consumo", "ph", "temp", "od"]:
                if col_stat in df_exibir.columns:
                    val = df_exibir[col_stat].mean()
                    if pd.notna(val):
                        st.metric(col_stat.upper(), f"{val:.2f}")

# ==========================================
# RODAPÉ
# ==========================================
st.divider()
st.caption(
    f"🐟 Pintado Dashboard v2.0 · Última atualização dos dados: Dia {dia_max_preenchido}/{DIAS_TOTAIS} "
    f"· Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
)
