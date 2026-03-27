"""
pages/topicos_diarios.py
========================
Página de descubrimiento y selección de tópicos del día.

Flujo:
  1. Carga los tópicos del día desde cache (0 tokens) o consulta Gemini si es la primera vez.
  2. Muestra dos listas: tópicos relacionados con salud/psicología y tópicos de actualidad
     abordados desde perspectiva médica/psicológica.
  3. Permite seleccionar uno o varios tópicos de la lista, o escribir un tópico libre.
  4. Al generar, crea 3 posts por cada tópico seleccionado.
"""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Tópicos del Día · Blog Content Generator",
    page_icon="📰",
    layout="wide",
)

# ── Estilos ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .topic-header  { font-size:1rem; font-weight:700; margin-bottom:0.3rem; }
    .sec-med-rel   { border-left:4px solid #0d6efd; padding-left:0.8rem; margin-bottom:1rem; }
    .sec-med-ext   { border-left:4px solid #6610f2; padding-left:0.8rem; margin-bottom:1rem; }
    .sec-psi-rel   { border-left:4px solid #198754; padding-left:0.8rem; margin-bottom:1rem; }
    .sec-psi-ext   { border-left:4px solid #fd7e14; padding-left:0.8rem; margin-bottom:1rem; }
    .badge-cached  { background:#d1e7dd; color:#0f5132; padding:2px 8px; border-radius:10px; font-size:0.78rem; }
    .badge-fresh   { background:#cfe2ff; color:#084298; padding:2px 8px; border-radius:10px; font-size:0.78rem; }
    .sug-table th  { background:#f8f9fa; font-size:0.85rem; }
    .sug-table td  { font-size:0.87rem; vertical-align:top; }
    [class*="st-key-dbl_"] { visibility:hidden!important; height:0!important; overflow:hidden!important; }
</style>
""", unsafe_allow_html=True)

# ── Modos globales ────────────────────────────────────────────────────────────
mock_mode   = os.getenv("GEMINI_MOCK_MODE", "true").lower() in ("true", "1", "yes")
simulate_wp = os.getenv("WP_MODE", "simulated").lower() != "live"

# ── Sidebar (reutiliza la lógica de tokens) ───────────────────────────────────
with st.sidebar:
    st.title("🔑 Tokens")
    try:
        from core.token_manager import TokenManager, FREE_TIER_RPD
        if "token_manager" not in st.session_state:
            st.session_state["token_manager"] = TokenManager.from_env()
        tm = st.session_state["token_manager"]
        if mock_mode:
            st.info("🟡 Modo MOCK — 0 tokens.", icon="ℹ️")
        else:
            s = tm.get_summary()
            if s["valid_keys"] == 0:
                st.error("❌ Sin API keys válidas.")
            elif not tm.any_key_available:
                st.error("🚫 Todas las claves agotadas.")
            else:
                st.success(f"✅ {s['valid_keys']}/{s['total_keys']} claves activas")
        st.caption(f"Tokens hoy: {tm.get_summary()['pool_today_tokens']:,}")
    except ImportError:
        st.warning("Instala dependencias: `pip install -r requirements.txt`")

    st.divider()
    if st.button("← Volver al inicio", use_container_width=True):
        st.switch_page("app.py")

# ── Cabecera ──────────────────────────────────────────────────────────────────
st.markdown("## 📰 Tópicos del Día")
st.markdown(
    "Descubre los temas más relevantes del momento para tu blog. "
    "La consulta a Gemini se realiza **una sola vez al día** — las visitas posteriores "
    "cargan el listado guardado sin consumir tokens."
)
st.divider()

# ── Inicializar session_state ────────────────────────────────────────────────
for _k, _v in [
    ("topics_data",       None),
    ("topics_from_cache", False),
    ("suggestions",       None),   # list[PostSuggestion]
    ("edited_titles",     {}),     # {"topico:type": "título editado"}
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Cargar tópicos ────────────────────────────────────────────────────────────
col_load, col_refresh = st.columns([3, 1])

with col_load:
    load_pressed    = st.button("🔍 Cargar tópicos del día", type="primary", use_container_width=True)
with col_refresh:
    refresh_pressed = st.button("🔄 Refrescar (nueva consulta)", use_container_width=True,
                                help="Realiza una nueva consulta a Gemini aunque ya exista cache para hoy.")

# Auto-carga si ya hay datos o si se pulsa el botón
should_load = load_pressed or refresh_pressed or (st.session_state["topics_data"] is not None)

if should_load and st.session_state["topics_data"] is None or load_pressed or refresh_pressed:
    if load_pressed or refresh_pressed or st.session_state["topics_data"] is None:
        try:
            from core.topic_discovery import get_topics, load_cached_topics
            from core.gemini_client import GeminiClient
            from core.token_manager import TokenManager

            tm_instance = st.session_state.get("token_manager")
            gemini = GeminiClient(
                token_manager=tm_instance,
                mock_mode=mock_mode,
            )

            force = refresh_pressed
            # Verificar si hay cache antes de llamar
            cached_pre = load_cached_topics()
            is_from_cache = (cached_pre is not None) and not force

            with st.spinner("Consultando Gemini para obtener los tópicos del día…" if not is_from_cache else "Cargando tópicos del cache…"):
                data = get_topics(gemini, force_refresh=force)

            st.session_state["topics_data"]       = data
            st.session_state["topics_from_cache"] = is_from_cache
            # Actualizar token manager
            st.session_state["token_manager"] = gemini.token_manager

        except Exception as exc:
            st.error(f"❌ Error al obtener tópicos: `{exc}`", icon="❌")
            st.stop()

# ── Mostrar tópicos ───────────────────────────────────────────────────────────
topics_data = st.session_state.get("topics_data")

if topics_data is None:
    st.info("Pulsa **Cargar tópicos del día** para obtener el listado de hoy.", icon="👆")
    st.stop()

# Badge de origen
from_cache = st.session_state.get("topics_from_cache", False)
fecha      = topics_data.get("fecha", "")
if from_cache:
    st.markdown(f'<span class="badge-cached">💾 Desde cache · {fecha}</span>', unsafe_allow_html=True)
else:
    st.markdown(f'<span class="badge-fresh">✨ Consultado ahora · {fecha}</span>', unsafe_allow_html=True)

st.markdown("")

med_rel    = topics_data.get("medicina_relacionados",    [])
med_norel  = topics_data.get("medicina_no_relacionados", [])
psi_rel    = topics_data.get("psicologia_relacionados",    [])
psi_norel  = topics_data.get("psicologia_no_relacionados", [])

# ── Selección de tópicos — 2 columnas × 2 secciones ──────────────────────────
st.markdown("### 📌 Selecciona los tópicos que deseas desarrollar")
st.caption(
    "Marca uno o varios. Tras seleccionarlos, Gemini sugerirá 3 títulos altamente "
    "indexables por cada tópico (1 request) antes de generar."
)

col_izq, col_der = st.columns(2, gap="large")
selected_topics: list[str] = []

with col_izq:
    st.markdown(
        '<div class="sec-med-rel"><span class="topic-header">🩺 MEDICINA · Actualidad del sector</span></div>',
        unsafe_allow_html=True,
    )
    for i, t in enumerate(med_rel):
        if st.checkbox(t, key=f"mrel_{i}"):
            selected_topics.append(t)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<div class="sec-psi-rel"><span class="topic-header">🧠 PSICOLOGÍA · Actualidad del sector</span></div>',
        unsafe_allow_html=True,
    )
    for i, t in enumerate(psi_rel):
        if st.checkbox(t, key=f"prel_{i}"):
            selected_topics.append(t)

with col_der:
    st.markdown(
        '<div class="sec-med-ext"><span class="topic-header">🌍 MEDICINA · Actualidad global reinterpretada</span></div>',
        unsafe_allow_html=True,
    )
    for i, t in enumerate(med_norel):
        if st.checkbox(t, key=f"mnorel_{i}"):
            selected_topics.append(t)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<div class="sec-psi-ext"><span class="topic-header">🌐 PSICOLOGÍA · Actualidad global reinterpretada</span></div>',
        unsafe_allow_html=True,
    )
    for i, t in enumerate(psi_norel):
        if st.checkbox(t, key=f"pnorel_{i}"):
            selected_topics.append(t)

st.divider()

# ── Tópico libre ──────────────────────────────────────────────────────────────
st.markdown("### ✏️ O escribe un tópico libre")
free_topic = st.text_input(
    label="Tópico libre",
    placeholder="Ej: el impacto del insomnio en el rendimiento académico de los adolescentes",
    label_visibility="collapsed",
    help="También se generarán 3 posts para este tópico además de los seleccionados.",
)
if free_topic.strip():
    selected_topics.append(free_topic.strip())

# ── Opciones de generación ────────────────────────────────────────────────────
st.divider()
st.markdown("### ⚙️ Opciones de generación")

col_opt1, col_opt2, col_opt3 = st.columns(3)

with col_opt1:
    reviewer = st.radio(
        "Revisado por",
        options=["— Sin revisor —", "🩺 Médico", "🧠 Psicólogo", "✍️ Editor"],
        index=2,   # psicólogo por defecto (contexto del blog)
        help="Adapta el tono y terminología del artículo.",
    )

with col_opt2:
    gemini_model = st.selectbox(
        "Modelo Gemini",
        options=["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"],
        index=0,
    )

with col_opt3:
    focus_global = st.text_area(
        "Enfoque global (opcional)",
        placeholder="Ej: orientado a padres con hijos entre 5 y 12 años",
        height=100,
        help="Se aplicará a TODOS los tópicos seleccionados.",
    )

_reviewer_map = {
    "— Sin revisor —": "",
    "🩺 Médico":        "Médico",
    "🧠 Psicólogo":     "Psicólogo",
    "✍️ Editor":        "Editor",
}
reviewer_key = _reviewer_map.get(reviewer, "")

# ── Resumen y paso de sugerencias ──────────────────────────────────────────────
st.divider()

n_topics = len(selected_topics)

if n_topics == 0:
    st.warning("Selecciona al menos un tópico o escribe uno libre para continuar.", icon="⚠️")
    st.stop()

st.info(
    f"**{n_topics} tópico(s) seleccionado(s)** — Paso 1: Gemini sugerirá 3 títulos "
    f"indexables por cada tópico (1 request), luego podrás editarlos antes de generar.",
    icon="📊",
)

# ── PASO 1: Sugerir estructura ────────────────────────────────────────────────
suggest_btn = st.button(
    "🔎 Sugerir títulos de blogs (1 request a Gemini)",
    type="primary",
    use_container_width=True,
    help="Gemini analiza cada tópico seleccionado y propone los 3 mejores títulos "
         "SEO (evergreen · listicle · how-to) en una sola consulta.",
)

if suggest_btn:
    # Limpiar sugerencias anteriores si cambiaron los tópicos
    st.session_state["suggestions"]    = None
    st.session_state["edited_titles"]  = {}
    try:
        from core.post_type_advisor import suggest_post_structure
        from core.gemini_client import GeminiClient

        tm_instance = st.session_state.get("token_manager")
        gemini = GeminiClient(token_manager=tm_instance, mock_mode=mock_mode)

        with st.spinner("Consultando Gemini para sugerir los mejores títulos…"):
            suggestions = suggest_post_structure(gemini, selected_topics)

        st.session_state["suggestions"]   = [s.to_dict() for s in suggestions]
        _et_new: dict[str, str] = {}
        for s in suggestions:
            _et_new[f"{s.topico}:opinion"]  = s.evergreen
            _et_new[f"{s.topico}:listicle"] = s.listicle
            _et_new[f"{s.topico}:howto"]    = s.howto
        st.session_state["edited_titles"] = _et_new
        st.session_state["token_manager"] = gemini.token_manager
        st.rerun()
    except Exception as exc:
        st.error(f"❌ Error al obtener sugerencias: `{exc}`")
        st.stop()

# ── PASO 2: Mostrar y editar sugerencias ──────────────────────────────────────
suggestions_raw = st.session_state.get("suggestions")
if suggestions_raw is None:
    st.stop()

st.markdown("### ✏️ Revisa y edita los títulos sugeridos")
st.caption(
    "Puedes modificar cualquier título antes de generar. "
    "Los posts se crearán con los títulos que veas aquí."
)

# Labels de los tipos
_TYPE_LABELS = {
    "opinion":  "💬 Evergreen",
    "listicle": "📋 Listicle / Top N",
    "howto":    "🛠️ How-to / Guía",
}

edited = st.session_state["edited_titles"]

for sg in suggestions_raw:
    topico = sg["topico"]
    short  = topico[:70] + ("…" if len(topico) > 70 else "")
    with st.expander(f"📂 {short}", expanded=True):
        cols = st.columns([1, 4])
        for post_type, label in _TYPE_LABELS.items():
            key_et = f"{topico}:{post_type}"
            current = edited.get(key_et, sg.get(
                {"opinion": "evergreen", "listicle": "listicle", "howto": "howto"}[post_type], ""
            ))
            with st.container():
                col_lbl, col_inp = st.columns([1, 5])
                col_lbl.markdown(f"**{label}**")
                new_val = col_inp.text_input(
                    label          = label,
                    value          = current,
                    key            = f"et_{topico}_{post_type}",
                    label_visibility="collapsed",
                )
                edited[key_et] = new_val

st.session_state["edited_titles"] = edited

# ── PASO 3: Generar ───────────────────────────────────────────────────────────
st.divider()
n_posts = n_topics * 3
generate_btn = st.button(
    f"✨ Generar {n_posts} posts ({n_topics} tópico{'s' if n_topics > 1 else ''})",
    type="primary",
    use_container_width=True,
)

if not generate_btn:
    st.stop()

# ============================================================================
# GENERACIÓN
# ============================================================================
try:
    from core.orchestrator import ContentOrchestrator
    from core.token_manager import TokenManager
except ImportError as e:
    st.error(f"Error al importar módulos: `{e}`")
    st.stop()

tm_instance = st.session_state.get("token_manager")

# Un progress_bar por tópico
overall_bar  = st.progress(0, text="Iniciando generación…")
detail_text  = st.empty()

all_results: list[dict] = []   # {"topic": str, "drafts": list[PostDraft]}
total_steps  = n_topics * 3

completed_steps = 0

for topic_idx, topic in enumerate(selected_topics):
    topic_label = topic[:60] + ("…" if len(topic) > 60 else "")
    st.markdown(f"---\n#### 🚀 Generando: _{topic_label}_")

    topic_bar = st.progress(0, text=f"Tópico {topic_idx + 1}/{n_topics}: {topic_label}")

    # Construir mapa de títulos personalizados para este tópico
    _et = st.session_state.get("edited_titles", {})
    custom_titles_for_topic = {
        "opinion":  _et.get(f"{topic}:opinion",  ""),
        "listicle": _et.get(f"{topic}:listicle", ""),
        "howto":    _et.get(f"{topic}:howto",    ""),
    }
    # Quitar entradas vacías
    custom_titles_for_topic = {k: v for k, v in custom_titles_for_topic.items() if v.strip()}

    def make_progress_cb(bar, topic_lbl, t_idx, n_t, outer_bar, outer_step_ref):
        def cb(step: int, total: int, message: str):
            bar.progress(int((step / total) * 100), text=message)
            new_global = (outer_step_ref[0] + step) / total_steps
            outer_bar.progress(
                min(1.0, new_global),
                text=f"Tópico {t_idx + 1}/{n_t}: {message}",
            )
        return cb

    completed_ref = [completed_steps]
    progress_cb = make_progress_cb(
        topic_bar, topic_label, topic_idx, n_topics, overall_bar, completed_ref
    )

    try:
        orchestrator = ContentOrchestrator.from_env(
            progress_cb   = progress_cb,
            token_manager = tm_instance,
            gemini_model  = gemini_model,
        )
        drafts = orchestrator.run(
            topic,
            mode          = "libre",
            focus         = focus_global.strip(),
            reviewer      = reviewer_key,
            custom_titles = custom_titles_for_topic or None,
        )
        # Actualizar token manager
        tm_instance = orchestrator.gemini.token_manager
        st.session_state["token_manager"] = tm_instance

        all_results.append({"topic": topic, "drafts": drafts})
        completed_steps += 3
        topic_bar.progress(100, text=f"✅ {topic_label} — completado")

    except Exception as exc:
        st.error(f"❌ Error generando tópico «{topic_label}»: `{exc}`")
        completed_steps += 3   # avanzar igual para no bloquear el progreso

overall_bar.progress(100, text="¡Generación completada!")
st.balloons()

# ── Resultados ────────────────────────────────────────────────────────────────
st.success(
    f"✅ **{len(all_results)} tópico(s) generados** · "
    f"{sum(len(r['drafts']) for r in all_results)} posts guardados.",
    icon="🎉",
)

type_icons = {
    "comparativa": "📊", "guia": "📖", "resena_seo": "🔍",
    "evergreen":   "💬", "listicle": "📋", "howto": "🛠️",
    "opinion":     "✍️",
}
type_labels = {
    "comparativa": "Comparativa",
    "guia":        "Guía",
    "resena_seo":  "Reseña SEO",
    "evergreen":   "Opinión",
    "listicle":    "Listicle",
    "howto":       "How-to",
    "opinion":     "Opinión",
}

for result in all_results:
    topic   = result["topic"]
    drafts  = result["drafts"]
    with st.expander(f"📂 {topic}", expanded=True):
        for draft in drafts:
            icon  = type_icons.get(str(draft.post_type), "📄")
            label = type_labels.get(str(draft.post_type), str(draft.post_type))
            if draft.title.startswith("[ERROR]"):
                st.error(f"❌ {label}: {draft.content[:200]}")
            else:
                col_info, col_btn = st.columns([4, 1])
                with col_info:
                    st.markdown(f"**{icon} {label}** — {draft.title}")
                    st.caption(f"🔑 `{draft.focus_keyword}` · _{draft.meta_description[:80]}…_")
                with col_btn:
                    if draft.draft_file:
                        def _go(f=draft.draft_file):
                            st.session_state["_nav_file"] = f
                            st.switch_page("pages/ver_borrador.py")
                        st.button(
                            "📝 Ver",
                            key=f"open_{draft.post_type}_{draft.wp_post_id}",
                            on_click=_go,
                            use_container_width=True,
                            type="primary",
                        )

st.divider()
if st.button("← Volver al inicio", use_container_width=True):
    st.switch_page("app.py")
