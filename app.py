"""
app.py  —  Interfaz Streamlit del Generador de Contenido para WordPress
============================================================================
Cómo ejecutar:
    streamlit run app.py

Modos de operación:
    · SIMULADO (por defecto): no requiere WordPress ni API Key de Gemini.
                              Los borradores se guardan en drafts_output/ como JSON.
    · REAL:                   configura .env con GEMINI_API_KEY_1, _2... y
                              cambia GEMINI_MOCK_MODE=false y WP_MODE=live
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Helper: localizar el archivo JSON de un borrador ────────────────────────
def _find_draft_file(wp_post_id, post_type: str) -> str | None:
    """
    Busca en drafts_output/ el archivo JSON que corresponde a un borrador
    recién generado, por su wp_post_id y post_type.
    Devuelve solo el nombre de archivo (sin ruta) o None si no existe.
    """
    drafts_dir = Path("drafts_output")
    if not drafts_dir.exists():
        return None
    # Patrón: draft_<id>_<post_type>.json
    pattern = f"draft_{wp_post_id}_{post_type}.json"
    target  = drafts_dir / pattern
    if target.exists():
        return pattern
    # Fallback: buscar cualquier archivo que contenga el id
    for f in drafts_dir.glob(f"draft_{wp_post_id}_*.json"):
        return f.name
    return None


st.set_page_config(
    page_title = "Blog Content Generator",
    page_icon  = "✍️",
    layout     = "wide",
)

# ── Estilos personalizados ───────────────────────────────────────────────────
st.markdown("""
<style>
    .main-title   { font-size: 2.2rem; font-weight: 700; margin-bottom: 0; }
    .subtitle     { color: #888; margin-top: 0; margin-bottom: 1.5rem; }
    .mode-badge   { padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: 600; }
    .mode-sim     { background: #fff3cd; color: #856404; }
    .mode-real    { background: #d1e7dd; color: #0f5132; }
    .key-active   { background: #d1e7dd; border-radius: 6px; padding: 4px 8px; }
    .key-warn     { background: #fff3cd; border-radius: 6px; padding: 4px 8px; }
    .key-dead     { background: #f8d7da; border-radius: 6px; padding: 4px 8px; }
    .key-inactive { background: #e9ecef; border-radius: 6px; padding: 4px 8px; color:#888; }
</style>
""", unsafe_allow_html=True)

# ── Modos globales ────────────────────────────────────────────────────────────
mock_mode     = os.getenv("GEMINI_MOCK_MODE", "true").lower() in ("true", "1", "yes")
wp_mode_env   = os.getenv("WP_MODE", "simulated").lower()
simulate_wp   = wp_mode_env != "live"

# ============================================================================
# SIDEBAR — Panel de gestión de tokens y API Keys
# ============================================================================
with st.sidebar:
    st.title("🔑 Gestión de Tokens")

    # ── Importar TokenManager (puede fallar antes de instalar deps) ──────────
    try:
        from core.token_manager import TokenManager, FREE_TIER_RPD, TOKENS_PER_BLOG_EST

        if "token_manager" not in st.session_state:
            st.session_state["token_manager"] = TokenManager.from_env()

        tm = st.session_state["token_manager"]

        # ── Estado del modo ──────────────────────────────────────────────────
        if mock_mode:
            st.info("🟡 **Modo MOCK activo**\n\n0 tokens gastados.", icon="ℹ️")
        else:
            summary = tm.get_summary()
            valid   = summary["valid_keys"]
            total   = summary["total_keys"]

            if valid == 0:
                st.error("❌ No hay API keys válidas configuradas.", icon="❌")
            elif not tm.any_key_available:
                st.error("🚫 Todas las claves agotadas por hoy.", icon="⛔")
            else:
                st.success(
                    f"✅ {valid}/{total} claves activas",
                    icon="✅",
                )

        st.divider()

        # ── Resumen del pool (siempre visible) ───────────────────────────────
        st.subheader("📊 Uso Total del Pool")
        summary = tm.get_summary()

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Tokens hoy", f"{summary['pool_today_tokens']:,}")
            st.metric("Tokens total", f"{summary['pool_total_tokens']:,}")
        with col2:
            st.metric("Requests hoy", f"{summary['pool_today_requests']:,}")
            blogs_left = summary["pool_blogs_remaining"]
            st.metric(
                "Blogs restantes hoy",
                f"{blogs_left}",
                help=f"Estimado: ~{TOKENS_PER_BLOG_EST:,} tokens por blog (3 posts)"
            )

        # Barra de capacidad total del pool
        if not mock_mode:
            total_req_pool  = FREE_TIER_RPD * summary["valid_keys"]
            used_req_pool   = summary["pool_today_requests"]
            pct_pool        = min(1.0, used_req_pool / max(total_req_pool, 1))
            bar_color       = "normal" if pct_pool < 0.8 else "inverse"
            st.progress(pct_pool, text=f"Pool: {used_req_pool}/{total_req_pool} req usadas hoy")

        st.divider()

        # ── Estado de cada clave ─────────────────────────────────────────────
        st.subheader("🗝️ Estado por Clave")

        active_alias = summary["active_alias"]

        for key_data in summary["keys"]:
            alias       = key_data["alias"]
            preview     = key_data["key_preview"]
            pct         = min(1.0, key_data["today_requests"] / FREE_TIER_RPD)
            is_active   = (alias == active_alias)
            is_invalid  = preview == "(no configurada)"
            is_exhausted = key_data["today_requests"] >= FREE_TIER_RPD

            # Encabezado de la clave
            label_parts = []
            if is_active and not mock_mode:
                label_parts.append("▶")
            label_parts.append(alias)
            if is_invalid:
                label_parts.append("❌")
            elif is_exhausted:
                label_parts.append("🔴 agotada")
            elif pct >= 0.8:
                label_parts.append("🟡 casi llena")
            elif is_active:
                label_parts.append("🟢 activa")
            else:
                label_parts.append("⚪")

            with st.expander(" ".join(label_parts), expanded=is_active and not mock_mode):
                st.caption(f"🔐 `{preview}`")

                if not is_invalid and not mock_mode:
                    # Barra de uso diario
                    st.progress(pct, text=f"{key_data['today_requests']}/{FREE_TIER_RPD} req hoy")

                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.metric("Tokens hoy", f"{key_data['today_tokens']:,}")
                        st.metric("Total histórico", f"{key_data['total_tokens']:,}")
                    with col_b:
                        remaining = max(0, FREE_TIER_RPD - key_data["today_requests"])
                        blogs_key = remaining // 3
                        st.metric("Req restantes", remaining)
                        st.metric("Blogs restantes", blogs_key)

                    if key_data.get("last_used"):
                        st.caption(f"Último uso: {key_data['last_used'][:16].replace('T', ' ')}")

                    if key_data.get("errors_today", 0) > 0:
                        st.warning(f"⚠️ {key_data['errors_today']} errores hoy", icon="⚠️")

                    # Botón para cambio manual de clave activa
                    if not is_active and not is_exhausted and not is_invalid:
                        if st.button(f"Activar {alias}", key=f"activate_{alias}"):
                            tm.set_active_key(alias)
                            st.session_state["token_manager"] = tm
                            st.rerun()
                    elif is_active:
                        st.caption("← Clave actualmente en uso")

                elif is_invalid:
                    st.caption("Configura esta clave en `.env`")

        st.divider()

        # ── Controles manuales ───────────────────────────────────────────────
        st.subheader("⚙️ Controles")

        if not mock_mode:
            col_rot, col_ref = st.columns(2)
            with col_rot:
                if st.button("🔄 Rotar clave", use_container_width=True,
                             help="Pasa a la siguiente clave disponible"):
                    rotated = tm.rotate(reason="manual-gui")
                    if rotated:
                        st.success(f"Activa: {tm.active_key.alias}")
                    else:
                        st.error("No hay más claves disponibles")
                    st.rerun()
            with col_ref:
                if st.button("🔃 Refrescar", use_container_width=True):
                    st.rerun()

        # ── Estimador de capacidad ───────────────────────────────────────────
        st.divider()
        st.subheader("🧮 Estimador")

        valid_keys = summary["valid_keys"] if not mock_mode else 0
        blogs_day  = (FREE_TIER_RPD * valid_keys) // 3  if valid_keys else 0
        blogs_week = blogs_day * 7
        blogs_month = blogs_day * 30

        st.markdown(f"""
| Período | Blogs posibles |
|---------|---------------|
| Por día | **{blogs_day:,}** |
| Por semana | **{blogs_week:,}** |
| Por mes | **{blogs_month:,}** |
""")
        st.caption(
            f"Basado en {valid_keys} clave(s) × {FREE_TIER_RPD} req/día "
            f"÷ 3 req/blog · Tier gratuito gemini-2.5-flash"
        )

        if mock_mode:
            st.caption("_(Activa GEMINI_MOCK_MODE=false para ver datos reales)_")

    except ImportError:
        st.warning(
            "Instala las dependencias:\n```\npip install -r requirements.txt\n```",
            icon="⚠️",
        )

# ============================================================================
# CABECERA PRINCIPAL
# ============================================================================
col_title, col_mode = st.columns([4, 1])
with col_title:
    st.markdown('<p class="main-title">✍️ Blog Content Generator</p>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle">Genera 3 borradores SEO con un solo input · Human-in-the-Loop</p>', unsafe_allow_html=True)

with col_mode:
    st.markdown("<br>", unsafe_allow_html=True)
    if mock_mode:
        st.markdown('<span class="mode-badge mode-sim">🟡 MOCK activo</span>', unsafe_allow_html=True)
    elif simulate_wp:
        st.markdown('<span class="mode-badge mode-sim">⚠️ WP Simulado</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="mode-badge mode-real">✅ WP Real</span>', unsafe_allow_html=True)

st.divider()

# ── Navegación al editor — los callbacks solo escriben session_state ─────────
if "_nav_file" in st.session_state:
    st.switch_page("pages/ver_borrador.py")

# ── Layout principal ──────────────────────────────────────────────────────────
col_form, col_history = st.columns([3, 2], gap="large")

# ============================================================================
# COLUMNA IZQUIERDA — Formulario de generación
# ============================================================================
with col_form:
    st.subheader("🚀 Nueva Generación")

    with st.form("generation_form", clear_on_submit=False):
        user_input = st.text_input(
            label       = "Tópico o URL de Amazon",
            placeholder = "Ej: medicina moderna  ·  auriculares Sony  ·  https://amazon.es/dp/XXXX",
            help        = "Escribe cualquier tópico (medicina moderna, inteligencia artificial…) "
                          "o pega una URL de Amazon para modo afiliado.",
        )

        focus_input = st.text_area(
            label       = "Enfoque del artículo (opcional)",
            placeholder = "Ej: el impacto de las pantallas azules en niños menores de 12 años "
                          "y su relación con los trastornos del sueño.",
            height      = 90,
            help        = "Define el ángulo o perspectiva específica que debe tener el artículo. "
                          "Si lo dejas vacío, el modelo elegirá el enfoque más relevante para el tópico.",
        )

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            gen_mode = st.radio(
                "Modo de generación",
                options  = ["🔍 Auto-detectar", "🛒 Amazon / Afiliado", "📝 Tópico libre"],
                index    = 0,
                help     = "**Auto**: detecta si es URL de Amazon o tópico libre.\n"
                           "**Amazon**: comparativa + guía + reseña con CTAs.\n"
                           "**Tópico libre**: opinión + listicle + how-to, sin afiliado.",
            )
        with col_b:
            reviewer = st.radio(
                "Revisado por",
                options  = ["— Sin revisor —", "🩺 Médico", "🧠 Psicólogo", "✍️ Editor"],
                index    = 0,
                help     = "Adapta el tono, rigor y terminología del contenido según el profesional "
                           "que lo revisará antes de publicar.\n\n"
                           "**Médico**: terminología clínica, fuentes EBM, nota de advertencia médica.\n"
                           "**Psicólogo**: lenguaje de salud mental, anti-estigma, recursos de apoyo.\n"
                           "**Editor**: fluidez narrativa, transiciones, coherencia estructural.",
            )
        with col_c:
            gemini_model = st.selectbox(
                "Modelo Gemini",
                options = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"],
                index   = 0,
                help    = "2.5-flash es el más reciente y compatible con el tier gratuito.",
            )

        submitted = st.form_submit_button("✨ Generar 3 Borradores", use_container_width=True, type="primary")

    # ── Avisos de estado ─────────────────────────────────────────────────────
    if mock_mode:
        st.info(
            "**Modo MOCK activo** — 0 tokens gastados.\n\n"
            "Para usar Gemini real: cambia `GEMINI_MOCK_MODE=false` en `.env` "
            "y añade tus claves `GEMINI_API_KEY_1`, `_2`...",
            icon="🟡",
        )
    elif simulate_wp:
        st.info(
            "**WordPress en modo simulado.** Los borradores se guardan en `drafts_output/`.",
            icon="ℹ️",
        )
    else:
        st.success(f"WordPress real: `{os.getenv('WP_BASE_URL', '')}`", icon="✅")

    # ── Procesamiento ─────────────────────────────────────────────────────────
    if submitted:
        if not user_input.strip():
            st.warning("Por favor escribe un tópico o URL antes de generar.", icon="⚠️")
            st.stop()

        try:
            from core.orchestrator import ContentOrchestrator
        except ImportError as e:
            st.error(
                f"Error al importar módulos: `{e}`\n\n"
                "Ejecuta: `pip install -r requirements.txt`",
                icon="❌",
            )
            st.stop()

        # Barra de progreso
        progress_bar = st.progress(0, text="Iniciando…")
        status_text  = st.empty()

        def update_progress(step: int, total: int, message: str):
            pct = int((step / total) * 100)
            progress_bar.progress(pct, text=message)
            status_text.caption(f"Paso {step}/{total}: {message}")

        # Pasar el token_manager existente al orquestador
        tm_instance = st.session_state.get("token_manager")

        try:
            orchestrator = ContentOrchestrator.from_env(
                progress_cb   = update_progress,
                token_manager = tm_instance,
                gemini_model  = gemini_model,
            )
        except KeyError as e:
            st.error(f"Variable de entorno faltante: `{e}`. Revisa tu `.env`.", icon="❌")
            st.stop()

        # Traducir el radio al parámetro mode del orquestador
        _mode_map = {
            "🔍 Auto-detectar":    "auto",
            "🛒 Amazon / Afiliado": "amazon",
            "📝 Tópico libre":     "libre",
        }
        gen_mode_key = _mode_map.get(gen_mode, "auto")

        # Traducir el radio de revisor al string limpio
        _reviewer_map = {
            "— Sin revisor —": "",
            "🩺 Médico":        "Médico",
            "🧠 Psicólogo":     "Psicólogo",
            "✍️ Editor":        "Editor",
        }
        reviewer_key = _reviewer_map.get(reviewer, "")

        with st.spinner("Procesando…"):
            try:
                drafts = orchestrator.run(
                    user_input.strip(),
                    mode     = gen_mode_key,
                    focus    = focus_input.strip(),
                    reviewer = reviewer_key,
                )
                st.session_state["last_drafts"]   = drafts
                st.session_state["last_topic"]    = user_input.strip()
                st.session_state["last_focus"]    = focus_input.strip()
                st.session_state["last_reviewer"] = reviewer_key
                # Actualizar token_manager con el estado más reciente
                st.session_state["token_manager"] = orchestrator.gemini.token_manager
            except Exception as exc:
                st.error(f"Error durante la generación: `{exc}`", icon="❌")
                st.stop()

        progress_bar.progress(100, text="¡Completado!")
        status_text.empty()
        st.balloons()
        st.success(f"✅ **3 borradores generados** para: _{user_input.strip()}_")
        st.rerun()  # refrescar sidebar con nuevos datos de tokens

    # ── Resultados ────────────────────────────────────────────────────────────
    if "last_drafts" in st.session_state:
        st.subheader("📋 Borradores Generados")

        # Badge de enfoque y revisor usados
        last_focus    = st.session_state.get("last_focus", "")
        last_reviewer = st.session_state.get("last_reviewer", "")
        if last_focus:
            st.caption(f"📌 **Enfoque:** _{last_focus}_")
        if last_reviewer:
            st.caption(f"👤 **Revisado por:** {last_reviewer}")

        type_icons = {
            "comparativa": "📊", "guia": "📖", "resena_seo": "🔍",
            "evergreen":   "💬", "listicle": "📋", "howto": "🛠️",
        }
        type_labels = {
            "comparativa": "Post A — Comparativa",
            "guia":        "Post B — Guía de Beneficios",
            "resena_seo":  "Post C — Reseña SEO",
            "evergreen":   "Post A — Artículo de Opinión",
            "listicle":    "Post B — Listicle / Top N",
            "howto":       "Post C — Guía Paso a Paso",
        }

        for draft in st.session_state["last_drafts"]:
            icon     = type_icons.get(draft.post_type, "📄")
            label    = type_labels.get(draft.post_type, draft.post_type)
            is_error = draft.title.startswith("[ERROR]")

            if is_error:
                with st.expander(f"❌ {label} — Error", expanded=True):
                    st.error(draft.content, icon="❌")
            else:
                col_info, col_btn = st.columns([3, 1])
                with col_info:
                    st.markdown(f"**{icon} {label}**")
                    st.caption(
                        f"🔑 `{draft.focus_keyword}`  ·  "
                        f"_{draft.meta_description[:80]}…_"
                    )
                with col_btn:
                    draft_file = draft.draft_file or _find_draft_file(draft.wp_post_id, draft.post_type)
                    if draft_file:
                        def _go_to_draft(f=draft_file):
                            st.session_state["_nav_file"] = f
                        st.button(
                            "📝 Ver / Editar",
                            key      = f"open_{draft.post_type}",
                            help     = f"Abrir {draft_file} en el editor",
                            on_click = _go_to_draft,
                            use_container_width=True,
                            type="primary",
                        )
                    else:
                        st.caption("_(archivo no encontrado)_")

                    if not simulate_wp and draft.wp_post_id:
                        wp_edit = (
                            f"{os.getenv('WP_BASE_URL')}"
                            f"/wp-admin/post.php?post={draft.wp_post_id}&action=edit"
                        )
                        st.link_button("🌐 WP", wp_edit, use_container_width=True)
                st.divider()

# ============================================================================
# COLUMNA DERECHA — Acceso rápido al historial y borradores
# ============================================================================
with col_history:
    st.subheader("📜 Historial & Borradores")

    # ── Botón historial ──────────────────────────────────────────────────────
    st.markdown(
        "Consulta el historial completo de todas las generaciones "
        "realizadas, con filtros por tipo y modo."
    )
    if st.button(
        "📜 Ver historial completo →",
        use_container_width=True,
        type="primary",
    ):
        st.switch_page("pages/historial.py")

    st.divider()

    # ── Acceso a borradores guardados ────────────────────────────────────────
    st.markdown(
        "Abre el editor para revisar, modificar o borrar "
        "cualquier borrador guardado localmente."
    )
    if st.button(
        "📝 Ver / Editar borradores →",
        use_container_width=True,
    ):
        st.switch_page("pages/ver_borrador.py")

    st.divider()

    # ── Resumen rápido ───────────────────────────────────────────────────────
    drafts_dir  = Path("drafts_output")
    draft_files = list(drafts_dir.glob("draft_*.json")) if drafts_dir.exists() else []
    log_path    = Path("logs/generation_log.jsonl")
    log_lines   = 0
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as _f:
            log_lines = sum(1 for ln in _f if ln.strip())

    col_a, col_b = st.columns(2)
    col_a.metric("� Borradores guardados", len(draft_files))
    col_b.metric("📋 Posts en historial",   log_lines)

# ============================================================================
# PIE DE PÁGINA
# ============================================================================
st.divider()
st.caption(
    "Blog Content Generator · Human-in-the-Loop · "
    "Gemini API + WordPress REST API · v1.1"
)

