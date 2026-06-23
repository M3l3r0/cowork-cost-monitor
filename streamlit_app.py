"""
Snowflake CoWork Cost Monitor v2
================================
Dashboard completo para monitorizar los costes de Snowflake CoWork (Snowflake Intelligence),
incluyendo tanto los creditos de tokens (AI) como los creditos de warehouse (compute)
atribuibles a cada usuario.

Vistas utilizadas:
- SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY (token credits)
- SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY (warehouse credits por query)
- SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY (para correlacionar queries de CoWork via query_tag)

Las queries de CoWork llevan un QUERY_TAG con el patron 'snowflake-intelligence-XXXXX'.
"""

import streamlit as st
import pandas as pd
import altair as alt
from datetime import date, timedelta

st.set_page_config(
    page_title="CoWork Cost Monitor",
    page_icon="💰",
    layout="wide",
)

# Patron del query_tag que usa CoWork para sus queries SQL
COWORK_TAG_PATTERN = "snowflake-intelligence-"

# --- Estilos Snowflake ---
st.markdown("""
<style>
    .main > div { padding-top: 1rem; }
    .stApp { background-color: #FFFFFF; }
    .kpi-card {
        background: linear-gradient(135deg, #F0F8FF 0%, #FFFFFF 100%);
        border-left: 5px solid #29B5E8;
        border-radius: 10px;
        padding: 18px 20px;
        box-shadow: 0 2px 8px rgba(41,181,232,0.12);
        margin-bottom: 8px;
    }
    .kpi-label {
        font-size: 0.8rem;
        color: #5B7A8C;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .kpi-value {
        font-size: 1.9rem;
        color: #11567F;
        font-weight: 700;
        line-height: 1.2;
    }
    .kpi-delta-up { color: #D64550; font-size: 0.85rem; font-weight: 600; }
    .kpi-delta-down { color: #1A9850; font-size: 0.85rem; font-weight: 600; }
    .kpi-delta-flat { color: #888; font-size: 0.85rem; font-weight: 600; }
    h1, h2, h3 { color: #11567F; }
    .app-header {
        background: linear-gradient(90deg, #11567F 0%, #29B5E8 100%);
        padding: 20px 28px;
        border-radius: 12px;
        margin-bottom: 20px;
    }
    .app-header h1 { color: #FFFFFF !important; margin: 0; font-size: 1.8rem; }
    .app-header p { color: #E0F4FF; margin: 4px 0 0 0; font-size: 0.95rem; }
    .alert-badge {
        background-color: #FDE8EA; color: #D64550; padding: 4px 10px;
        border-radius: 6px; font-weight: 600; font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="app-header">
    <h1>Snowflake CoWork — Monitor de Costes</h1>
    <p>Coste completo por usuario (Tokens AI + Warehouse Compute) · Snowflake Intelligence</p>
</div>
""", unsafe_allow_html=True)

conn = st.connection("snowflake")


def kpi_card(label, value, delta_html=""):
    """Render an HTML KPI card."""
    return f"""
    <div class="kpi-card">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
        {delta_html}
    </div>
    """


def delta_html(current, previous, suffix="vs anterior", invert=False):
    """Build a delta indicator. invert=False: up=bad (red, cost increase)."""
    if previous is None or previous == 0:
        return f'<div class="kpi-delta-flat">— {suffix}</div>'
    pct = (current - previous) / previous * 100
    if abs(pct) < 0.05:
        return f'<div class="kpi-delta-flat">0% {suffix}</div>'
    up = pct > 0
    cls = "kpi-delta-up" if (up != invert) else "kpi-delta-down"
    arrow = "▲" if up else "▼"
    return f'<div class="{cls}">{arrow} {pct:+.1f}% {suffix}</div>'


def to_float_cols(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(float)
    return df


def csv_download(df, filename, label="Descargar CSV"):
    st.download_button(
        label, df.to_csv(index=False).encode("utf-8"),
        filename, "text/csv", key=f"dl_{filename}"
    )


# --- Sidebar: filtros globales ---
st.sidebar.image(
    "https://www.snowflake.com/wp-content/themes/snowflake/assets/img/logo-blue.svg",
    width=160,
)
st.sidebar.title("Filtros")

max_end = date.today()
default_start = max_end - timedelta(days=30)

date_range = st.sidebar.date_input(
    "Rango de fechas (max 30 dias)",
    value=(default_start, max_end),
    max_value=max_end,
)

if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = default_start, max_end

start_str = start_date.strftime("%Y-%m-%d")
end_str = end_date.strftime("%Y-%m-%d")
period_days = max((end_date - start_date).days, 1)

# Periodo anterior de igual duracion (para comparativas)
prev_end = start_date
prev_start = start_date - timedelta(days=period_days)
prev_start_str = prev_start.strftime("%Y-%m-%d")
prev_end_str = prev_end.strftime("%Y-%m-%d")

st.sidebar.divider()
st.sidebar.subheader("Alertas / Umbrales")
threshold_user = st.sidebar.number_input(
    "Umbral por usuario (credits)", min_value=0.0, value=10.0, step=1.0,
    help="Marca en rojo a los usuarios cuyo coste total supere este valor.",
)
threshold_day = st.sidebar.number_input(
    "Umbral diario (credits)", min_value=0.0, value=5.0, step=1.0,
    help="Marca los dias cuyo consumo de token credits supere este valor.",
)

# --- Tabs principales ---
(tab_overview, tab_user_cost, tab_model, tab_trend,
 tab_compare, tab_sim, tab_chargeback, tab_budget, tab_explain) = st.tabs([
    "Resumen",
    "Coste Completo por Usuario",
    "Desglose Modelo/Servicio",
    "Tendencia Diaria",
    "Comparativa Periodos",
    "Simulador de Costes",
    "Chargeback por Equipo",
    "Budgets y Alertas",
    "Como Monitorizar Costes",
])

# =============================================================================
# TAB 1: RESUMEN
# =============================================================================
with tab_overview:
    st.subheader(f"Resumen · {start_str} → {end_str}")

    df_overview = conn.query(f"""
        SELECT
            COUNT(DISTINCT USER_NAME) AS usuarios_unicos,
            COUNT(DISTINCT REQUEST_ID) AS total_requests,
            COUNT(DISTINCT SNOWFLAKE_INTELLIGENCE_NAME) AS instancias_cowork,
            ROUND(SUM(TOKEN_CREDITS), 4) AS total_token_credits,
            SUM(TOKENS) AS total_tokens
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= '{start_str}'
          AND START_TIME < '{end_str}'
    """, ttl=600)

    # Periodo anterior para deltas
    df_prev = conn.query(f"""
        SELECT
            ROUND(SUM(TOKEN_CREDITS), 4) AS total_token_credits,
            COUNT(DISTINCT REQUEST_ID) AS total_requests
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= '{prev_start_str}'
          AND START_TIME < '{prev_end_str}'
    """, ttl=600)

    if df_overview.empty or df_overview.iloc[0]["TOTAL_TOKEN_CREDITS"] is None:
        st.warning("No hay datos de CoWork en el periodo seleccionado.")
    else:
        row = df_overview.iloc[0]
        prev_credits = float(df_prev.iloc[0]["TOTAL_TOKEN_CREDITS"]) if df_prev.iloc[0]["TOTAL_TOKEN_CREDITS"] else 0
        prev_reqs = float(df_prev.iloc[0]["TOTAL_REQUESTS"]) if df_prev.iloc[0]["TOTAL_REQUESTS"] else 0
        cur_credits = float(row["TOTAL_TOKEN_CREDITS"])
        cur_reqs = float(row["TOTAL_REQUESTS"])

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.markdown(kpi_card("Usuarios", int(row["USUARIOS_UNICOS"])), unsafe_allow_html=True)
        c2.markdown(kpi_card("Requests", f"{int(cur_reqs):,}",
                    delta_html(cur_reqs, prev_reqs)), unsafe_allow_html=True)
        c3.markdown(kpi_card("Instancias", int(row["INSTANCIAS_COWORK"])), unsafe_allow_html=True)
        c4.markdown(kpi_card("AI Credits", f"{cur_credits:.4f}",
                    delta_html(cur_credits, prev_credits)), unsafe_allow_html=True)
        c5.markdown(kpi_card("Total Tokens", f"{int(row['TOTAL_TOKENS']):,}"), unsafe_allow_html=True)

    st.divider()
    st.subheader("Top Usuarios por Token Credits")

    df_top_users = conn.query(f"""
        SELECT
            USER_NAME,
            ROUND(SUM(TOKEN_CREDITS), 4) AS total_credits,
            SUM(TOKENS) AS total_tokens,
            COUNT(DISTINCT REQUEST_ID) AS request_count
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= '{start_str}'
          AND START_TIME < '{end_str}'
        GROUP BY USER_NAME
        ORDER BY total_credits DESC
        LIMIT 20
    """, ttl=600)

    if not df_top_users.empty:
        df_top_users = to_float_cols(df_top_users, ["TOTAL_CREDITS", "TOTAL_TOKENS", "REQUEST_COUNT"])
        chart = alt.Chart(df_top_users).mark_bar(
            color="#29B5E8", cornerRadiusEnd=4
        ).encode(
            x=alt.X("TOTAL_CREDITS:Q", title="Token Credits (AI)"),
            y=alt.Y("USER_NAME:N", sort="-x", title="Usuario"),
            tooltip=["USER_NAME", "TOTAL_CREDITS", "TOTAL_TOKENS", "REQUEST_COUNT"],
        ).properties(height=400)
        st.altair_chart(chart, use_container_width=True)
        csv_download(df_top_users, "top_usuarios.csv")
    else:
        st.info("Sin datos de usuarios.")


# =============================================================================
# TAB 2: COSTE COMPLETO POR USUARIO (TOKEN + WAREHOUSE)
# =============================================================================
with tab_user_cost:
    st.subheader(f"Coste Completo por Usuario · {start_str} → {end_str}")

    st.info("""
    **Snowflake CoWork ejecuta queries SQL en el warehouse del usuario** que hace la pregunta
    (no tiene WH dedicado). Esas queries llevan un `query_tag` con el patron
    `snowflake-intelligence-XXXXX`. Usamos `QUERY_ATTRIBUTION_HISTORY` para obtener los creditos
    de compute atribuidos y los correlacionamos con el usuario.

    **Coste Total = Token Credits (AI) + Warehouse Credits (Compute)**
    """)

    df_token = conn.query(f"""
        SELECT
            USER_NAME,
            ROUND(SUM(TOKEN_CREDITS), 6) AS token_credits,
            SUM(TOKENS) AS total_tokens,
            COUNT(DISTINCT REQUEST_ID) AS request_count
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= '{start_str}'
          AND START_TIME < '{end_str}'
        GROUP BY USER_NAME
    """, ttl=600)

    df_wh = conn.query(f"""
        SELECT
            qah.USER_NAME,
            ROUND(SUM(qah.CREDITS_ATTRIBUTED_COMPUTE), 6) AS warehouse_credits,
            COUNT(DISTINCT qah.QUERY_ID) AS wh_query_count
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY qah
        WHERE qah.START_TIME >= '{start_str}'
          AND qah.START_TIME < '{end_str}'
          AND qah.QUERY_TAG LIKE '%{COWORK_TAG_PATTERN}%'
        GROUP BY qah.USER_NAME
    """, ttl=600)

    if df_token.empty:
        st.warning("No hay datos de token credits en este periodo.")
    else:
        df_token = to_float_cols(df_token, ["TOKEN_CREDITS", "TOTAL_TOKENS", "REQUEST_COUNT"])
        df_wh = to_float_cols(df_wh, ["WAREHOUSE_CREDITS", "WH_QUERY_COUNT"])

        df_merged = pd.merge(
            df_token, df_wh, on="USER_NAME", how="left"
        ).fillna({"WAREHOUSE_CREDITS": 0, "WH_QUERY_COUNT": 0})
        df_merged["TOTAL_COST_CREDITS"] = df_merged["TOKEN_CREDITS"] + df_merged["WAREHOUSE_CREDITS"]
        df_merged = df_merged.sort_values("TOTAL_COST_CREDITS", ascending=False)

        c1, c2, c3 = st.columns(3)
        c1.markdown(kpi_card("Total AI Credits", f"{df_merged['TOKEN_CREDITS'].sum():.4f}"), unsafe_allow_html=True)
        c2.markdown(kpi_card("Total WH Credits", f"{df_merged['WAREHOUSE_CREDITS'].sum():.6f}"), unsafe_allow_html=True)
        c3.markdown(kpi_card("Coste Total", f"{df_merged['TOTAL_COST_CREDITS'].sum():.4f}"), unsafe_allow_html=True)

        # Alertas de usuarios por encima del umbral
        over = df_merged[df_merged["TOTAL_COST_CREDITS"] > threshold_user]
        if not over.empty:
            st.markdown(
                f'<span class="alert-badge">⚠ {len(over)} usuario(s) superan el umbral '
                f'de {threshold_user} credits</span>', unsafe_allow_html=True)

        st.divider()

        display = df_merged[[
            "USER_NAME", "TOKEN_CREDITS", "WAREHOUSE_CREDITS",
            "TOTAL_COST_CREDITS", "TOTAL_TOKENS", "REQUEST_COUNT", "WH_QUERY_COUNT"
        ]].rename(columns={
            "USER_NAME": "Usuario",
            "TOKEN_CREDITS": "AI Credits (Tokens)",
            "WAREHOUSE_CREDITS": "WH Credits (Compute)",
            "TOTAL_COST_CREDITS": "Coste Total (Credits)",
            "TOTAL_TOKENS": "Tokens Consumidos",
            "REQUEST_COUNT": "Requests CoWork",
            "WH_QUERY_COUNT": "Queries en WH",
        })

        def highlight_over(row):
            color = "background-color: #FDE8EA" if row["Coste Total (Credits)"] > threshold_user else ""
            return [color] * len(row)

        st.dataframe(
            display.style.apply(highlight_over, axis=1).format({
                "AI Credits (Tokens)": "{:.6f}",
                "WH Credits (Compute)": "{:.6f}",
                "Coste Total (Credits)": "{:.6f}",
                "Tokens Consumidos": "{:,.0f}",
            }),
            use_container_width=True, hide_index=True,
        )
        csv_download(display, "coste_por_usuario.csv")

        st.subheader("AI vs Warehouse Credits por Usuario")
        df_chart = df_merged.head(15).melt(
            id_vars=["USER_NAME"],
            value_vars=["TOKEN_CREDITS", "WAREHOUSE_CREDITS"],
            var_name="Tipo", value_name="Credits",
        )
        df_chart["Tipo"] = df_chart["Tipo"].replace({
            "TOKEN_CREDITS": "AI (Tokens)", "WAREHOUSE_CREDITS": "Warehouse (Compute)",
        })
        chart = alt.Chart(df_chart).mark_bar().encode(
            x=alt.X("USER_NAME:N", sort="-y", title="Usuario"),
            y=alt.Y("Credits:Q", title="Credits"),
            color=alt.Color("Tipo:N", scale=alt.Scale(
                domain=["AI (Tokens)", "Warehouse (Compute)"],
                range=["#29B5E8", "#11567F"])),
            tooltip=["USER_NAME", "Tipo", "Credits"],
        ).properties(height=400)
        st.altair_chart(chart, use_container_width=True)

    st.divider()
    st.subheader("Requests mas caros")

    df_requests = conn.query(f"""
        SELECT
            REQUEST_ID, START_TIME, USER_NAME,
            SNOWFLAKE_INTELLIGENCE_NAME, AGENT_NAME,
            ROUND(TOKEN_CREDITS, 6) AS token_credits,
            TOKENS AS total_tokens,
            COALESCE(METADATA:ai_functions_credits::FLOAT, 0) AS ai_functions_credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= '{start_str}'
          AND START_TIME < '{end_str}'
        ORDER BY token_credits DESC
        LIMIT 25
    """, ttl=600)

    if not df_requests.empty:
        st.dataframe(df_requests, use_container_width=True, hide_index=True)
        csv_download(df_requests, "requests_caros.csv")


# =============================================================================
# TAB 3: DESGLOSE POR MODELO / SERVICIO
# =============================================================================
with tab_model:
    st.subheader(f"Desglose por Modelo y Servicio · {start_str} → {end_str}")

    st.info("""
    Desglose de creditos por modelo LLM y servicio subyacente (cortex_agents, cortex_analyst)
    usando las columnas granulares `CREDITS_GRANULAR` y `TOKENS_GRANULAR`.
    """)

    df_model = conn.query(f"""
        WITH flattened AS (
            SELECT
                COALESCE(NULLIF(cf4.key, ''), 'unknown') AS model_name,
                cf3.key AS service_type,
                COALESCE(cf4.value:input::FLOAT, 0) +
                COALESCE(cf4.value:output::FLOAT, 0) +
                COALESCE(cf4.value:cache_read_input::FLOAT, 0) +
                COALESCE(cf4.value:cache_write_input::FLOAT, 0) AS total_credits,
                COALESCE(tf4.value:input::FLOAT, 0) +
                COALESCE(tf4.value:output::FLOAT, 0) +
                COALESCE(tf4.value:cache_read_input::FLOAT, 0) +
                COALESCE(tf4.value:cache_write_input::FLOAT, 0) AS total_tokens,
                h.REQUEST_ID
            FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY h,
                 LATERAL FLATTEN(input => h.CREDITS_GRANULAR) cf1,
                 LATERAL FLATTEN(input => cf1.value) cf2,
                 LATERAL FLATTEN(input => cf2.value) cf3,
                 LATERAL FLATTEN(input => cf3.value) cf4,
                 LATERAL FLATTEN(input => h.TOKENS_GRANULAR) tf1,
                 LATERAL FLATTEN(input => tf1.value) tf2,
                 LATERAL FLATTEN(input => tf2.value) tf3,
                 LATERAL FLATTEN(input => tf3.value) tf4
            WHERE cf2.key != 'start_time'
              AND tf2.key != 'start_time'
              AND cf1.index = tf1.index
              AND cf2.key = tf2.key
              AND cf3.key = tf3.key
              AND cf4.key = tf4.key
              AND h.START_TIME >= '{start_str}'
              AND h.START_TIME < '{end_str}'
        )
        SELECT
            model_name AS MODEL_NAME,
            service_type AS SERVICE_TYPE,
            ROUND(SUM(total_credits), 4) AS TOTAL_CREDITS,
            SUM(total_tokens) AS TOTAL_TOKENS,
            COUNT(DISTINCT REQUEST_ID) AS REQUEST_COUNT
        FROM flattened
        GROUP BY model_name, service_type
        ORDER BY TOTAL_CREDITS DESC, model_name, service_type
    """, ttl=600)

    if not df_model.empty:
        df_model = to_float_cols(df_model, ["TOTAL_CREDITS", "TOTAL_TOKENS", "REQUEST_COUNT"])
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Credits por Servicio**")
            df_svc = df_model.groupby("SERVICE_TYPE", as_index=False)["TOTAL_CREDITS"].sum()
            chart_svc = alt.Chart(df_svc).mark_arc(innerRadius=55).encode(
                theta="TOTAL_CREDITS:Q",
                color=alt.Color("SERVICE_TYPE:N", scale=alt.Scale(scheme="blues")),
                tooltip=["SERVICE_TYPE", "TOTAL_CREDITS"],
            ).properties(height=300)
            st.altair_chart(chart_svc, use_container_width=True)
        with c2:
            st.markdown("**Credits por Modelo**")
            df_mdl = df_model.groupby("MODEL_NAME", as_index=False)["TOTAL_CREDITS"].sum()
            chart_mdl = alt.Chart(df_mdl).mark_arc(innerRadius=55).encode(
                theta="TOTAL_CREDITS:Q",
                color=alt.Color("MODEL_NAME:N", scale=alt.Scale(scheme="tealblues")),
                tooltip=["MODEL_NAME", "TOTAL_CREDITS"],
            ).properties(height=300)
            st.altair_chart(chart_mdl, use_container_width=True)

        st.markdown("**Tabla Detallada**")
        st.dataframe(df_model, use_container_width=True, hide_index=True)
        csv_download(df_model, "desglose_modelo.csv")
    else:
        st.info("Sin datos granulares de modelo/servicio en este periodo.")


# =============================================================================
# TAB 4: TENDENCIA DIARIA
# =============================================================================
with tab_trend:
    st.subheader(f"Tendencia Diaria · {start_str} → {end_str}")

    df_daily = conn.query(f"""
        SELECT
            DATE(START_TIME) AS usage_date,
            ROUND(SUM(TOKEN_CREDITS), 4) AS daily_credits,
            SUM(TOKENS) AS daily_tokens,
            COUNT(DISTINCT REQUEST_ID) AS daily_requests,
            COUNT(DISTINCT USER_NAME) AS daily_users
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= '{start_str}'
          AND START_TIME < '{end_str}'
        GROUP BY DATE(START_TIME)
        ORDER BY usage_date ASC
    """, ttl=600)

    if not df_daily.empty:
        df_daily = to_float_cols(df_daily, ["DAILY_CREDITS", "DAILY_TOKENS", "DAILY_REQUESTS", "DAILY_USERS"])
        df_daily["OVER"] = df_daily["DAILY_CREDITS"] > threshold_day

        base = alt.Chart(df_daily).encode(x=alt.X("USAGE_DATE:T", title="Fecha"))
        area = base.mark_area(opacity=0.5, line={"color": "#29B5E8"}, color="#BFE9F7").encode(
            y=alt.Y("DAILY_CREDITS:Q", title="Token Credits"),
            tooltip=["USAGE_DATE:T", "DAILY_CREDITS", "DAILY_TOKENS", "DAILY_REQUESTS"],
        )
        # Puntos rojos para dias por encima del umbral
        points = base.mark_point(size=80, filled=True).encode(
            y="DAILY_CREDITS:Q",
            color=alt.condition(
                alt.datum.OVER, alt.value("#D64550"), alt.value("#29B5E8")),
            tooltip=["USAGE_DATE:T", "DAILY_CREDITS"],
        )
        threshold_rule = alt.Chart(pd.DataFrame({"y": [threshold_day]})).mark_rule(
            color="#D64550", strokeDash=[6, 4]).encode(y="y:Q")
        st.altair_chart((area + points + threshold_rule).properties(
            height=320, title="Token Credits por Dia (linea roja = umbral)"),
            use_container_width=True)

        over_days = df_daily[df_daily["OVER"]]
        if not over_days.empty:
            st.markdown(
                f'<span class="alert-badge">⚠ {len(over_days)} dia(s) superan el umbral '
                f'de {threshold_day} credits</span>', unsafe_allow_html=True)

        chart_reqs = alt.Chart(df_daily).mark_bar(color="#11567F", opacity=0.8).encode(
            x=alt.X("USAGE_DATE:T", title="Fecha"),
            y=alt.Y("DAILY_REQUESTS:Q", title="Requests"),
            tooltip=["USAGE_DATE:T", "DAILY_REQUESTS", "DAILY_USERS"],
        ).properties(height=250, title="Requests por Dia")
        st.altair_chart(chart_reqs, use_container_width=True)

        st.dataframe(df_daily.drop(columns=["OVER"]), use_container_width=True, hide_index=True)
        csv_download(df_daily.drop(columns=["OVER"]), "tendencia_diaria.csv")
    else:
        st.info("Sin datos diarios en este periodo.")


# =============================================================================
# TAB 5: COMPARATIVA DE PERIODOS
# =============================================================================
with tab_compare:
    st.subheader("Comparativa de Periodos")
    st.caption(
        f"Actual: {start_str} → {end_str}  ·  Anterior: {prev_start_str} → {prev_end_str} "
        f"({period_days} dias cada uno)")

    def period_user_credits(s, e):
        return conn.query(f"""
            SELECT USER_NAME,
                   ROUND(SUM(TOKEN_CREDITS), 4) AS credits
            FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
            WHERE START_TIME >= '{s}' AND START_TIME < '{e}'
            GROUP BY USER_NAME
        """, ttl=600)

    df_cur = period_user_credits(start_str, end_str)
    df_pre = period_user_credits(prev_start_str, prev_end_str)

    if df_cur.empty and df_pre.empty:
        st.info("Sin datos para comparar.")
    else:
        df_cur = to_float_cols(df_cur, ["CREDITS"]).rename(columns={"CREDITS": "ACTUAL"})
        df_pre = to_float_cols(df_pre, ["CREDITS"]).rename(columns={"CREDITS": "ANTERIOR"})
        df_cmp = pd.merge(df_cur, df_pre, on="USER_NAME", how="outer").fillna(0)
        df_cmp["DELTA"] = df_cmp["ACTUAL"] - df_cmp["ANTERIOR"]
        df_cmp["PCT"] = df_cmp.apply(
            lambda r: (r["DELTA"] / r["ANTERIOR"] * 100) if r["ANTERIOR"] else None, axis=1)
        df_cmp = df_cmp.sort_values("ACTUAL", ascending=False)

        tot_cur, tot_pre = df_cmp["ACTUAL"].sum(), df_cmp["ANTERIOR"].sum()
        c1, c2, c3 = st.columns(3)
        c1.markdown(kpi_card("Periodo Actual", f"{tot_cur:.4f}"), unsafe_allow_html=True)
        c2.markdown(kpi_card("Periodo Anterior", f"{tot_pre:.4f}"), unsafe_allow_html=True)
        c3.markdown(kpi_card("Cambio", f"{tot_cur - tot_pre:+.4f}",
                    delta_html(tot_cur, tot_pre)), unsafe_allow_html=True)

        st.divider()
        df_bar = df_cmp.head(15).melt(
            id_vars=["USER_NAME"], value_vars=["ANTERIOR", "ACTUAL"],
            var_name="Periodo", value_name="Credits")
        chart = alt.Chart(df_bar).mark_bar().encode(
            x=alt.X("USER_NAME:N", sort="-y", title="Usuario"),
            y=alt.Y("Credits:Q"),
            color=alt.Color("Periodo:N", scale=alt.Scale(
                domain=["ANTERIOR", "ACTUAL"], range=["#B0BEC5", "#29B5E8"])),
            xOffset="Periodo:N",
            tooltip=["USER_NAME", "Periodo", "Credits"],
        ).properties(height=380)
        st.altair_chart(chart, use_container_width=True)

        disp = df_cmp.rename(columns={
            "USER_NAME": "Usuario", "ACTUAL": "Actual", "ANTERIOR": "Anterior",
            "DELTA": "Delta", "PCT": "% Cambio"})
        st.dataframe(
            disp.style.format({
                "Actual": "{:.4f}", "Anterior": "{:.4f}",
                "Delta": "{:+.4f}", "% Cambio": "{:+.1f}%"}, na_rep="—"),
            use_container_width=True, hide_index=True)
        csv_download(disp, "comparativa_periodos.csv")


# =============================================================================
# TAB 6: SIMULADOR DE COSTES
# =============================================================================
with tab_sim:
    st.subheader("Simulador de Costes")
    st.caption("Proyecta el coste futuro de CoWork basandose en el patron de consumo actual.")

    # Calcular medias del periodo actual
    df_avg = conn.query(f"""
        SELECT
            COUNT(DISTINCT REQUEST_ID) AS requests,
            SUM(TOKEN_CREDITS) AS token_credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= '{start_str}'
          AND START_TIME < '{end_str}'
    """, ttl=600)

    df_wh_avg = conn.query(f"""
        SELECT
            COUNT(DISTINCT QUERY_ID) AS wh_queries,
            SUM(CREDITS_ATTRIBUTED_COMPUTE) AS wh_credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY
        WHERE START_TIME >= '{start_str}'
          AND START_TIME < '{end_str}'
          AND QUERY_TAG LIKE '%{COWORK_TAG_PATTERN}%'
    """, ttl=600)

    req = float(df_avg.iloc[0]["REQUESTS"] or 0)
    tok_cr = float(df_avg.iloc[0]["TOKEN_CREDITS"] or 0)
    wh_cr = float(df_wh_avg.iloc[0]["WH_CREDITS"] or 0)

    ai_per_req = (tok_cr / req) if req else 0.0
    wh_per_req = (wh_cr / req) if req else 0.0
    total_per_req = ai_per_req + wh_per_req

    st.markdown("**Coste medio observado por request (pregunta) en el periodo actual:**")
    c1, c2, c3 = st.columns(3)
    c1.markdown(kpi_card("AI / request", f"{ai_per_req:.6f}"), unsafe_allow_html=True)
    c2.markdown(kpi_card("WH / request", f"{wh_per_req:.6f}"), unsafe_allow_html=True)
    c3.markdown(kpi_card("Total / request", f"{total_per_req:.6f}"), unsafe_allow_html=True)

    st.divider()
    st.markdown("**Configura tu escenario:**")
    s1, s2, s3 = st.columns(3)
    n_users = s1.number_input("Usuarios", min_value=1, value=10, step=1)
    q_per_user = s2.number_input("Preguntas por usuario / mes", min_value=1, value=100, step=10)
    credit_price = s3.number_input("Precio por credito ($)", min_value=0.0, value=3.0, step=0.5,
                                   help="Precio aproximado por credito Snowflake en tu contrato.")

    total_questions = n_users * q_per_user
    est_ai = total_questions * ai_per_req
    est_wh = total_questions * wh_per_req
    est_total = est_ai + est_wh

    st.divider()
    st.markdown(f"**Proyeccion mensual para {total_questions:,} preguntas:**")
    r1, r2, r3, r4 = st.columns(4)
    r1.markdown(kpi_card("AI Credits", f"{est_ai:.2f}"), unsafe_allow_html=True)
    r2.markdown(kpi_card("WH Credits", f"{est_wh:.2f}"), unsafe_allow_html=True)
    r3.markdown(kpi_card("Total Credits", f"{est_total:.2f}"), unsafe_allow_html=True)
    r4.markdown(kpi_card("Coste estimado", f"${est_total * credit_price:,.2f}"), unsafe_allow_html=True)

    if total_per_req == 0:
        st.warning("No hay datos de consumo en el periodo actual para calcular medias. "
                   "Selecciona un rango con actividad de CoWork.")

    # Curva de escalado
    st.divider()
    st.markdown("**Como escala el coste con el numero de preguntas:**")
    scale = pd.DataFrame({"preguntas": [total_questions * f for f in [0.25, 0.5, 1, 1.5, 2, 3]]})
    scale["credits"] = scale["preguntas"] * total_per_req
    scale["coste_usd"] = scale["credits"] * credit_price
    line = alt.Chart(scale).mark_line(point=True, color="#29B5E8").encode(
        x=alt.X("preguntas:Q", title="Preguntas / mes"),
        y=alt.Y("coste_usd:Q", title="Coste estimado ($)"),
        tooltip=["preguntas", "credits", "coste_usd"],
    ).properties(height=300)
    st.altair_chart(line, use_container_width=True)


# =============================================================================
# TAB 7: CHARGEBACK POR EQUIPO (TAGS)
# =============================================================================
with tab_chargeback:
    st.subheader(f"Chargeback por Equipo · {start_str} → {end_str}")
    st.info("""
    Atribuye el gasto de CoWork a equipos / centros de coste usando **tags de usuario**.
    Requiere que los usuarios tengan asignado un tag (ej. `COST_CENTER`, `TEAM`, `DEPARTMENT`)
    visible en `SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES`.
    """)

    # Descubrir tags disponibles sobre el dominio USER
    df_tags = conn.query("""
        SELECT DISTINCT TAG_NAME
        FROM SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES
        WHERE DOMAIN = 'USER'
        ORDER BY TAG_NAME
    """, ttl=3600)

    if df_tags.empty:
        st.warning(
            "No se encontraron tags aplicados a usuarios en `TAG_REFERENCES`.\n\n"
            "Para habilitar el chargeback, crea y asigna un tag a tus usuarios, por ejemplo:")
        st.code("""-- Crear el tag (una vez)
CREATE TAG IF NOT EXISTS GOVERNANCE.TAGS.COST_CENTER;

-- Asignar a usuarios
ALTER USER JKOWAL SET TAG GOVERNANCE.TAGS.COST_CENTER = 'FINANCE';
ALTER USER ASMITH SET TAG GOVERNANCE.TAGS.COST_CENTER = 'ENGINEERING';""", language="sql")
    else:
        tag_options = df_tags["TAG_NAME"].tolist()
        sel_tag = st.selectbox("Tag de usuario para agrupar", tag_options)

        df_cb = conn.query(f"""
            WITH user_tags AS (
                SELECT OBJECT_NAME AS user_name, TAG_VALUE
                FROM SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES
                WHERE DOMAIN = 'USER'
                  AND TAG_NAME = '{sel_tag}'
            ),
            si_usage AS (
                SELECT USER_NAME, REQUEST_ID, TOKEN_CREDITS, TOKENS
                FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
                WHERE START_TIME >= '{start_str}'
                  AND START_TIME < '{end_str}'
            )
            SELECT
                COALESCE(ut.TAG_VALUE, 'SIN_TAG') AS cost_center,
                ROUND(SUM(si.TOKEN_CREDITS), 4) AS total_credits,
                SUM(si.TOKENS) AS total_tokens,
                COUNT(DISTINCT si.REQUEST_ID) AS request_count,
                COUNT(DISTINCT si.USER_NAME) AS unique_users
            FROM si_usage si
            LEFT JOIN user_tags ut ON si.USER_NAME = ut.user_name
            GROUP BY cost_center
            ORDER BY total_credits DESC
        """, ttl=600)

        if df_cb.empty or df_cb["TOTAL_CREDITS"].isnull().all():
            st.info("Sin datos de CoWork en este periodo para atribuir.")
        else:
            df_cb = to_float_cols(df_cb, ["TOTAL_CREDITS", "TOTAL_TOKENS", "REQUEST_COUNT", "UNIQUE_USERS"])
            total_cb = df_cb["TOTAL_CREDITS"].sum()
            untagged = df_cb[df_cb["COST_CENTER"] == "SIN_TAG"]["TOTAL_CREDITS"].sum()

            c1, c2, c3 = st.columns(3)
            c1.markdown(kpi_card("Centros de coste", df_cb[df_cb["COST_CENTER"] != "SIN_TAG"].shape[0]),
                        unsafe_allow_html=True)
            c2.markdown(kpi_card("Total Credits", f"{total_cb:.4f}"), unsafe_allow_html=True)
            pct_untag = (untagged / total_cb * 100) if total_cb else 0
            c3.markdown(kpi_card("Sin tag", f"{pct_untag:.1f}%"), unsafe_allow_html=True)

            if untagged > 0:
                st.markdown(
                    f'<span class="alert-badge">⚠ {pct_untag:.1f}% del gasto pertenece a usuarios '
                    f'sin el tag "{sel_tag}" — revisa la cobertura de etiquetado</span>',
                    unsafe_allow_html=True)

            st.divider()
            cc1, cc2 = st.columns([1, 1])
            with cc1:
                chart_cb = alt.Chart(df_cb).mark_arc(innerRadius=55).encode(
                    theta="TOTAL_CREDITS:Q",
                    color=alt.Color("COST_CENTER:N", scale=alt.Scale(scheme="blues"),
                                    title="Centro de coste"),
                    tooltip=["COST_CENTER", "TOTAL_CREDITS", "UNIQUE_USERS"],
                ).properties(height=320, title="Distribucion por centro de coste")
                st.altair_chart(chart_cb, use_container_width=True)
            with cc2:
                bar_cb = alt.Chart(df_cb).mark_bar(color="#29B5E8", cornerRadiusEnd=4).encode(
                    x=alt.X("TOTAL_CREDITS:Q", title="Token Credits"),
                    y=alt.Y("COST_CENTER:N", sort="-x", title="Centro de coste"),
                    tooltip=["COST_CENTER", "TOTAL_CREDITS", "REQUEST_COUNT", "UNIQUE_USERS"],
                ).properties(height=320, title="Ranking de gasto")
                st.altair_chart(bar_cb, use_container_width=True)

            disp_cb = df_cb.rename(columns={
                "COST_CENTER": "Centro de coste",
                "TOTAL_CREDITS": "Token Credits",
                "TOTAL_TOKENS": "Tokens",
                "REQUEST_COUNT": "Requests",
                "UNIQUE_USERS": "Usuarios",
            })
            st.dataframe(disp_cb, use_container_width=True, hide_index=True)
            csv_download(disp_cb, "chargeback_equipo.csv")


# =============================================================================
# TAB 8: BUDGETS Y ALERTAS (GENERADOR DE SQL)
# =============================================================================
with tab_budget:
    st.subheader("Budgets y Alertas Nativas")
    st.info("""
    Las alertas de la barra lateral son **visuales** (solo se ven al abrir la app). Para recibir
    avisos automaticos, crea un **Budget** o una **Alert** nativa de Snowflake. Esta pestana
    genera el SQL listo para copiar y ejecutar en un worksheet con un rol con privilegios
    (`ACCOUNTADMIN` o un rol con `CREATE ALERT` / `EXECUTE MANAGED ALERT` y acceso a budgets).
    """)

    st.markdown("### 1. Alerta diaria sobre gasto de CoWork")
    st.caption("Notifica cuando el gasto de token credits de un dia supera un umbral.")

    a1, a2, a3 = st.columns(3)
    alert_db = a1.text_input("Database.Schema destino", value="STREAMLIT_COWORK.APP", key="alert_db")
    alert_wh = a2.text_input("Warehouse para la alerta", value="APP_WH", key="alert_wh")
    alert_threshold = a3.number_input("Umbral diario (credits)", min_value=0.0,
                                      value=float(threshold_day), step=1.0, key="alert_thr")
    notif_int = st.text_input(
        "Notification integration (email/Slack)", value="MY_EMAIL_NOTIFICATION_INT",
        help="Debe existir previamente. Ver mas abajo como crear una de email.")
    alert_email = st.text_input("Email(s) destino (separados por coma)",
                                value="finops@empresa.com")

    alert_sql = f"""-- Alerta: gasto diario de CoWork por encima de {alert_threshold} credits
CREATE OR REPLACE ALERT {alert_db}.COWORK_DAILY_COST_ALERT
  WAREHOUSE = {alert_wh}
  SCHEDULE = 'USING CRON 0 8 * * * UTC'   -- cada dia a las 08:00 UTC
  IF (EXISTS (
        SELECT 1
        FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
        WHERE START_TIME >= DATEADD('day', -1, CURRENT_DATE())
          AND START_TIME <  CURRENT_DATE()
        GROUP BY DATE(START_TIME)
        HAVING SUM(TOKEN_CREDITS) > {alert_threshold}
      ))
  THEN CALL SYSTEM$SEND_EMAIL(
        '{notif_int}',
        '{alert_email}',
        'Alerta de coste CoWork',
        'El gasto de CoWork ayer supero {alert_threshold} credits. Revisa el dashboard.'
  );

-- Activar la alerta (las alertas se crean suspendidas)
ALTER ALERT {alert_db}.COWORK_DAILY_COST_ALERT RESUME;"""

    st.code(alert_sql, language="sql")

    with st.expander("¿No tienes una notification integration de email? Crea una asi"):
        st.code("""CREATE OR REPLACE NOTIFICATION INTEGRATION MY_EMAIL_NOTIFICATION_INT
  TYPE = EMAIL
  ENABLED = TRUE
  ALLOWED_RECIPIENTS = ('finops@empresa.com');

-- El email destino debe ser de un usuario verificado en la cuenta.""", language="sql")

    st.divider()
    st.markdown("### 2. Budget mensual para CoWork")
    st.caption("Un budget monitoriza el gasto y notifica al superar el limite definido.")

    b1, b2, b3 = st.columns(3)
    budget_db = b1.text_input("Database.Schema del budget", value="STREAMLIT_COWORK.APP", key="bud_db")
    budget_limit = b2.number_input("Limite mensual (credits)", min_value=0.0, value=500.0,
                                   step=50.0, key="bud_limit")
    budget_email = b3.text_input("Email de notificacion", value="finops@empresa.com", key="bud_email")

    budget_sql = f"""-- Budget personalizado para monitorizar el gasto de CoWork
-- Requiere un rol con SNOWFLAKE.BUDGET_CREATOR y CREATE SNOWFLAKE.CORE.BUDGET en el schema.
-- CoWork esta soportado por budgets como servicio AI_SERVICES.

-- 1. La notification integration debe poder ser usada por la app SNOWFLAKE:
GRANT USAGE ON INTEGRATION {notif_int} TO APPLICATION snowflake;

-- 2. Crear el budget
CREATE OR REPLACE SNOWFLAKE.CORE.BUDGET {budget_db}.COWORK_BUDGET();

-- 3. Limite mensual (en credits)
CALL {budget_db}.COWORK_BUDGET!SET_SPENDING_LIMIT({budget_limit});

-- 4. Notificaciones por email (integration + email verificado)
CALL {budget_db}.COWORK_BUDGET!SET_EMAIL_NOTIFICATIONS(
  '{notif_int}',
  '{budget_email}'
);

-- Nota: el budget de cuenta (snowflake.local.account_root_budget) ya monitoriza TODO
-- el gasto, incluido CoWork (AI_SERVICES). Para acotar un custom budget al compute de
-- CoWork, asigna un WAREHOUSE dedicado a las instancias y agregalo al budget:
--   GRANT APPLYBUDGET ON WAREHOUSE COWORK_WH TO ROLE <budget_owner>;
--   CALL {budget_db}.COWORK_BUDGET!ADD_RESOURCE(
--     SYSTEM$REFERENCE('warehouse', 'COWORK_WH', 'SESSION', 'applybudget'));"""

    st.code(budget_sql, language="sql")

    st.divider()
    st.markdown("""
    **Como desplegarlo:**
    1. Copia el SQL de arriba.
    2. Abre un worksheet en Snowsight con un rol con privilegios (`ACCOUNTADMIN` o equivalente).
    3. Ajusta los nombres si hace falta y ejecuta.
    4. Verifica la alerta con `SHOW ALERTS IN SCHEMA {db}.{schema};` y el budget con
       `SHOW SNOWFLAKE.CORE.BUDGET INSTANCES IN ACCOUNT;`
    """)


# =============================================================================
# TAB 9: EXPLICACION - COMO MONITORIZAR COSTES
# =============================================================================
with tab_explain:
    st.subheader("Como Monitorizar los Costes de Snowflake CoWork")

    st.markdown("""
    ## Componentes del Coste de CoWork

    Cuando un usuario hace una pregunta en Snowflake CoWork, se generan **dos tipos de costes**:

    ### 1. Token Credits (Costes de AI/LLM)

    Cada interaccion consume tokens de los modelos LLM. Se registran en:

    ```
    SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
    ```

    **Columnas clave:**
    - `TOKEN_CREDITS`: Creditos totales por request
    - `TOKENS`: Total de tokens consumidos
    - `CREDITS_GRANULAR` / `TOKENS_GRANULAR`: Desglose por request/servicio/modelo
    - `METADATA:ai_functions_credits`: Creditos de AI Functions invocadas

    **Servicios subyacentes:** `cortex_agents`, `cortex_analyst`

    ---

    ### 2. Warehouse Credits (Costes de Compute)

    **Punto critico**: CoWork ejecuta las queries SQL en el **warehouse del usuario**.
    No tiene WH dedicado, asi que el compute se **mezcla** con el uso normal del usuario.

    **Como identificar estas queries** — llevan un `QUERY_TAG` con el patron
    `snowflake-intelligence-XXXXX`:

    ```sql
    SELECT *
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
    WHERE QUERY_TAG LIKE '%snowflake-intelligence-%'
      AND START_TIME >= DATEADD('day', -30, CURRENT_DATE());
    ```

    **Coste de compute atribuido por query:**

    ```sql
    SELECT
        USER_NAME,
        SUM(CREDITS_ATTRIBUTED_COMPUTE) AS warehouse_credits,
        COUNT(*) AS query_count
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY
    WHERE QUERY_TAG LIKE '%snowflake-intelligence-%'
      AND START_TIME >= DATEADD('day', -30, CURRENT_DATE())
    GROUP BY USER_NAME
    ORDER BY warehouse_credits DESC;
    ```

    ---

    ## Formula del Coste Total por Usuario

    ```
    Coste Total = Token Credits (AI) + Warehouse Credits (Compute)
    ```

    | Componente | Vista | Columna |
    |---|---|---|
    | Token Credits | `SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY` | `TOKEN_CREDITS` |
    | Warehouse Credits | `QUERY_ATTRIBUTION_HISTORY` | `CREDITS_ATTRIBUTED_COMPUTE` |

    ---

    ## Consideraciones Importantes

    ### Latencia de los Datos
    - `SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY`: hasta 24h
    - `QUERY_ATTRIBUTION_HISTORY`: hasta 8h
    - `QUERY_HISTORY`: hasta 45 min

    ### Limitaciones
    - `QUERY_ATTRIBUTION_HISTORY` no incluye queries en Adaptive Warehouses
    - Queries muy cortas (<=100ms) no aparecen
    - El formato del `QUERY_TAG` es `snowflake-intelligence-` + ID aleatorio;
      siempre filtrar con `LIKE '%snowflake-intelligence-%'`

    ### Warehouse Compartido: Implicaciones
    - El compute se atribuye al **usuario** que ejecuto la query (no al WH)
    - `QUERY_ATTRIBUTION_HISTORY` da el coste exacto por query, sin idle time
    - Para aislar costes de CoWork, considera asignar un **WH dedicado** a las instancias

    ---

    ## Queries Utiles

    ### Coste por Instancia de CoWork
    ```sql
    SELECT SNOWFLAKE_INTELLIGENCE_NAME,
           SUM(TOKEN_CREDITS) AS total_credits,
           COUNT(DISTINCT USER_NAME) AS unique_users,
           COUNT(DISTINCT REQUEST_ID) AS request_count
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY
    WHERE START_TIME >= DATEADD('day', -30, CURRENT_DATE())
    GROUP BY SNOWFLAKE_INTELLIGENCE_NAME
    ORDER BY total_credits DESC;
    ```

    ### Chargeback por Tag de Usuario
    ```sql
    WITH user_tags AS (
        SELECT OBJECT_NAME AS user_name, TAG_VALUE
        FROM SNOWFLAKE.ACCOUNT_USAGE.TAG_REFERENCES
        WHERE DOMAIN = 'USER' AND TAG_NAME = 'COST_CENTER'
    )
    SELECT COALESCE(ut.TAG_VALUE, 'SIN_TAG') AS cost_center,
           ROUND(SUM(si.TOKEN_CREDITS), 4) AS total_credits
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY si
    LEFT JOIN user_tags ut ON si.USER_NAME = ut.user_name
    WHERE si.START_TIME >= DATEADD('day', -30, CURRENT_DATE())
    GROUP BY cost_center
    ORDER BY total_credits DESC;
    ```

    ---

    ## Resumen de Vistas

    | Vista | Que contiene |
    |---|---|
    | `SNOWFLAKE_INTELLIGENCE_USAGE_HISTORY` | Token credits, tokens, desglose granular |
    | `QUERY_ATTRIBUTION_HISTORY` | Creditos de WH atribuidos por query (sin idle) |
    | `QUERY_HISTORY` | Metadata de queries (query_tag para filtrar CoWork) |
    | `WAREHOUSE_METERING_HISTORY` | Consumo total del warehouse (incluye idle) |
    | `TAG_REFERENCES` | Tags de usuarios para chargeback |

    ---

    ## Recomendaciones

    1. **Presupuesto simple**: usa solo `TOKEN_CREDITS`
    2. **Coste real completo**: Token Credits + Warehouse Credits (esta app lo hace)
    3. **Alertas**: crea un ALERT diario sobre un umbral de credits
    4. **Chargeback por equipo**: usa tags en usuarios + `TAG_REFERENCES`
    5. **WH dedicado a CoWork**: aisla los costes de compute de forma limpia
    """)
