"""
pages/2_Historial.py  —  Historial completo de generaciones
============================================================
Muestra todas las entradas del log JSONL con búsqueda y filtros.
Accesible desde el menú lateral o desde el botón en la pantalla principal.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title = "Historial de Generaciones",
    page_icon  = "📜",
    layout     = "wide",
)

TYPE_ICONS  = {
    "comparativa": "📊", "guia": "📖", "resena_seo": "🔍",
    "opinion": "💬", "listicle": "📋", "howto": "🛠️",
}
TYPE_LABELS = {
    "comparativa": "Comparativa",
    "guia":        "Guía de Beneficios",
    "resena_seo":  "Reseña SEO",
    "opinion":     "Artículo de Opinión",
    "listicle":    "Listicle / Top N",
    "howto":       "Guía Paso a Paso",
}
DRAFTS_DIR = Path("drafts_output")

st.markdown("## 📜 Historial de Generaciones")
st.markdown("Todas las sesiones de generación registradas localmente.")
st.divider()

LOG_PATH = Path("logs/generation_log.jsonl")

# ── Navegación al editor — callbacks solo escriben session_state ─────────────
if "_nav_file" in st.session_state:
    st.switch_page("pages/ver_borrador.py")

if not LOG_PATH.exists():
    st.info(
        "Aún no se ha generado ningún contenido. "
        "Ve a la pantalla principal y genera tu primer blog. ✍️",
        icon="📂",
    )
    if st.button("← Volver a la pantalla principal"):
        st.switch_page("app.py")
    st.stop()

# ── Cargar entradas ───────────────────────────────────────────────────────────
entries: list[dict] = []
with open(LOG_PATH, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

if not entries:
    st.info("El archivo de log está vacío.", icon="📂")
    st.stop()

# ── Filtros ───────────────────────────────────────────────────────────────────
col_search, col_type, col_mode = st.columns([3, 2, 2])

with col_search:
    search = st.text_input("🔍 Buscar por título o keyword", placeholder="ej: Sony WH-1000XM5")

with col_type:
    type_filter = st.selectbox(
        "Tipo de post",
        options = ["Todos", "Comparativa", "Guía", "Reseña SEO"],
        index   = 0,
    )

with col_mode:
    mode_filter = st.selectbox(
        "Modo WP",
        options = ["Todos", "Simulado", "Real"],
        index   = 0,
    )

# ── Mapeo de filtros ──────────────────────────────────────────────────────────
TYPE_MAP = {
    "Comparativa": "comparativa",
    "Guía":        "guia",
    "Reseña SEO":  "resena_seo",
}
MODE_MAP = {
    "Simulado": "simulado",
    "Real":     "real",
}

def _matches(entry: dict) -> bool:
    if search:
        haystack = (entry.get("title", "") + " " + entry.get("focus_keyword", "")).lower()
        if search.lower() not in haystack:
            return False
    if type_filter != "Todos":
        if entry.get("post_type") != TYPE_MAP.get(type_filter):
            return False
    if mode_filter != "Todos":
        if entry.get("wp_mode") != MODE_MAP.get(mode_filter):
            return False
    return True

filtered = [e for e in entries if _matches(e)]

st.caption(f"Mostrando **{len(filtered)}** entrada(s) de {len(entries)} totales.")
st.divider()

if not filtered:
    st.warning("No hay entradas que coincidan con los filtros.", icon="🔍")
    st.stop()

# ── Agrupar por sesión ────────────────────────────────────────────────────────
sessions: dict[str, list[dict]] = {}
for e in reversed(filtered):
    sid = e.get("session_id", "?")
    sessions.setdefault(sid, []).append(e)

# ── Mostrar sesiones ──────────────────────────────────────────────────────────
def _open_draft(fname: str) -> None:
    """Callback: solo escribe session_state. Switch ocurre en ver_borrador.py."""
    st.session_state["_nav_file"] = fname

for sid, session_entries in sessions.items():
    ts         = session_entries[0].get("timestamp", "")[:16].replace("T", " ")
    wp_mode    = session_entries[0].get("wp_mode", "?")
    mode_icon  = "🟡" if wp_mode == "simulado" else "✅"
    count      = len(session_entries)

    with st.expander(
        f"{mode_icon} `{ts}` — {count} post(s)  ·  sesión `{sid[-8:]}`",
        expanded=False,
    ):
        for entry in session_entries:
            pt      = entry.get("post_type", "")
            icon    = TYPE_ICONS.get(pt, "📄")
            t_label = TYPE_LABELS.get(pt, pt)
            title   = entry.get("title", "(sin título)")
            keyword = entry.get("focus_keyword", "")
            wp_id   = entry.get("wp_post_id", "?")
            tokens  = entry.get("tokens_used", 0)
            draft_f = entry.get("draft_file", "")

            col_txt, col_btn = st.columns([4, 1])
            with col_txt:
                st.markdown(
                    f"{icon} **{t_label}** — {title}  \n"
                    f"🔑 `{keyword}` · ID: `{wp_id}` · Tokens: `{tokens}`"
                )
            with col_btn:
                if draft_f:
                    fname = Path(draft_f).name
                    full_path = DRAFTS_DIR / fname
                    if full_path.exists():
                        st.button(
                            "📝 Ver / Editar",
                            key      = f"edit_{wp_id}_{pt}",
                            help     = f"Abrir {fname} en el editor",
                            on_click = _open_draft,
                            args     = (fname,),
                            use_container_width=True,
                        )
                    else:
                        st.caption("_(archivo borrado)_")
            st.markdown("---")

# ── Estadísticas resumen ──────────────────────────────────────────────────────
st.divider()
st.subheader("📊 Estadísticas")

total_tokens = sum(e.get("tokens_used", 0) for e in entries)
total_posts  = len(entries)
total_sess   = len(sessions)

col_a, col_b, col_c = st.columns(3)
col_a.metric("Total de posts generados", total_posts)
col_b.metric("Sesiones",                 total_sess)
col_c.metric("Tokens consumidos",        f"{total_tokens:,}")

# ── Volver ────────────────────────────────────────────────────────────────────
st.divider()
if st.button("← Volver a la pantalla principal"):
    st.switch_page("app.py")
