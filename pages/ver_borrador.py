"""
ver_borrador.py - Editor WYSIWYG de borradores
Permite:
  - Visualizar el draft como HTML renderizado
  - Editar inline cada bloque (H1, H2, H3, p, li, ul, ol, blockquote)
    haciendo clic en el icono lápiz al lado de cada elemento
  - Agregar imágenes (por URL o subiendo un archivo)
  - Guardar cambios en el archivo JSON local
  - Enviar a WordPress como borrador (botón habilitado solo si hay 1+ imagen)
"""
from __future__ import annotations

import json
import os
import uuid
from html.parser import HTMLParser
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

# ── Page config — debe ser la primera llamada Streamlit del script ───────────
st.set_page_config(
    page_title = "Editor de Borrador",
    page_icon  = "✏️",
    layout     = "wide",
)

# ── Navegación entrante — debe estar antes de cualquier render ───────────────
# El callback on_click escribe session_state["_nav_file"].
# Aquí lo consumimos: seteamos query_params y hacemos rerun
# (NO switch_page a sí mismo — eso borra los query_params).
if "_nav_file" in st.session_state:
    _nav_target = st.session_state.pop("_nav_file")
    st.query_params["file"] = _nav_target
    st.rerun()

# ── Constantes ──────────────────────────────────────────────────────────────
DRAFTS_DIR = Path("drafts_output")
IMAGES_DIR = DRAFTS_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

WP_MODE = os.getenv("WP_MODE", os.getenv("WP_SIMULATE", "simulated")).lower()
IS_LIVE  = WP_MODE == "live"

TYPE_ICONS: dict[str, str] = {
    "comparativa": "📊",
    "guia":        "📖",
    "resena_seo":  "🔍",
    "opinion":     "💬",
    "listicle":    "📋",
    "howto":       "🛠️",
}

BLOCK_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre"}


# ─────────────────────────────────────────────────────────────────────────────
# HTML → lista de bloques
# ─────────────────────────────────────────────────────────────────────────────

class _BlockParser(HTMLParser):
    """Convierte HTML a lista de dicts {tag, text, raw, is_list}."""

    def __init__(self):
        super().__init__()
        self.blocks: list[dict] = []
        self._current_tag: str = ""
        self._current_raw: str = ""
        self._depth: int = 0
        self._in_block: bool = False
        self._list_stack: list[str] = []
        self._list_html: str = ""
        self._in_list: bool = False

    def handle_starttag(self, tag: str, attrs):
        if tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self._in_list = True
            attrs_str = "".join(f' {k}="{v}"' for k, v in attrs)
            self._list_html += f"<{tag}{attrs_str}>"
            return
        if self._in_list:
            attrs_str = "".join(f' {k}="{v}"' for k, v in attrs)
            self._list_html += f"<{tag}{attrs_str}>"
            return
        if tag in BLOCK_TAGS and not self._in_block:
            self._in_block = True
            self._current_tag = tag
            self._current_raw = ""
            self._depth = 1
        elif self._in_block:
            attrs_str = "".join(f' {k}="{v}"' for k, v in attrs)
            self._current_raw += f"<{tag}{attrs_str}>"
            self._depth += 1

    def handle_endtag(self, tag: str):
        if tag in ("ul", "ol") and self._list_stack and self._list_stack[-1] == tag:
            self._list_html += f"</{tag}>"
            self._list_stack.pop()
            if not self._list_stack:
                self.blocks.append({
                    "tag":     tag,
                    "text":    self._list_html,
                    "raw":     self._list_html,
                    "is_list": True,
                })
                self._list_html = ""
                self._in_list   = False
            return
        if self._in_list:
            self._list_html += f"</{tag}>"
            return
        if self._in_block:
            self._depth -= 1
            if self._depth == 0 and tag == self._current_tag:
                self.blocks.append({
                    "tag":     self._current_tag,
                    "text":    self._current_raw,
                    "raw":     self._current_raw,
                    "is_list": False,
                })
                self._in_block    = False
                self._current_tag = ""
                self._current_raw = ""
            else:
                self._current_raw += f"</{tag}>"

    def handle_data(self, data: str):
        if self._in_list:
            self._list_html += data
            return
        if self._in_block:
            self._current_raw += data

    def handle_entityref(self, name: str):
        ref = f"&{name};"
        if self._in_list:
            self._list_html += ref
        elif self._in_block:
            self._current_raw += ref

    def handle_charref(self, name: str):
        ref = f"&#{name};"
        if self._in_list:
            self._list_html += ref
        elif self._in_block:
            self._current_raw += ref


def parse_blocks(html: str) -> list[dict]:
    parser = _BlockParser()
    parser.feed(html)
    return [{**b, "idx": i, "key": f"block_{i}"} for i, b in enumerate(parser.blocks)]


def blocks_to_html(blocks: list[dict]) -> str:
    parts = []
    for b in blocks:
        tag = b["tag"]
        if b.get("is_list"):
            parts.append(b["text"])
        else:
            parts.append(f"<{tag}>{b['text']}</{tag}>")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de estado
# ─────────────────────────────────────────────────────────────────────────────

def _load_draft(filepath: Path) -> dict:
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def _save_draft(filepath: Path, data: dict):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    st.toast("Borrador guardado", icon="💾")


def _nice_label(fname: str, directory: Path) -> str:
    fp = directory / fname
    if not fp.exists():
        return fname
    try:
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        ptype = d.get("post_type", "?")
        icon  = TYPE_ICONS.get(ptype, "📄")
        title = d.get("title", fname)[:55]
        ts    = d.get("created_at", "")[:10]
        return f"{icon} [{ts}] {title}"
    except Exception:
        return fname


def _init_state(draft_data: dict, fname: str):
    if st.session_state.get("_loaded_file") != fname:
        st.session_state["_loaded_file"] = fname
        st.session_state["draft_data"]   = dict(draft_data)
        st.session_state["blocks"]       = parse_blocks(draft_data.get("content", ""))
        st.session_state["editing_idx"]  = None
        st.session_state["images"]       = list(draft_data.get("images", []))


def _rebuild_content():
    blocks = st.session_state["blocks"]
    st.session_state["draft_data"]["content"] = blocks_to_html(blocks)


# ─────────────────────────────────────────────────────────────────────────────
# Render WYSIWYG
# ─────────────────────────────────────────────────────────────────────────────

def _render_blocks():
    blocks  = st.session_state["blocks"]
    editing = st.session_state["editing_idx"]

    st.markdown("""
    <style>
    .wy-h1 { font-size:2rem;   font-weight:700; margin:.4rem 0; }
    .wy-h2 { font-size:1.5rem; font-weight:600; margin:.3rem 0; color:#1a5276; }
    .wy-h3 { font-size:1.2rem; font-weight:600; margin:.3rem 0; color:#1f618d; }
    .wy-h4 { font-size:1rem;   font-weight:600; margin:.3rem 0; }
    .wy-p  { font-size:1rem;   line-height:1.75; margin:.25rem 0; }
    .wy-bq { border-left:4px solid #aaa; padding:.3rem 1rem;
             color:#555; font-style:italic; margin:.4rem 0; }
    /* Bloque editable: resalta al pasar el ratón */
    .wy-block {
        border-radius: 4px;
        padding: 2px 6px;
        transition: background 0.15s;
        cursor: text;
    }
    .wy-block:hover {
        background: rgba(99,179,237,0.10);
        outline: 1.5px dashed #90cdf4;
    }
    .wy-hint {
        font-size:0.68rem; color:#aaa; margin-left:6px;
        display:none; vertical-align:middle;
    }
    .wy-block:hover .wy-hint { display:inline; }
    /* Botón oculto que sirve de receptor del doble clic JS */
    .wy-hidden-btn {
        visibility: hidden;
        height: 0 !important;
        overflow: hidden;
        margin: 0 !important;
        padding: 0 !important;
        line-height: 0;
    }
    .wy-hidden-btn > div,
    .wy-hidden-btn > div > div,
    .wy-hidden-btn [data-testid="stButton"],
    .wy-hidden-btn button {
        height: 0 !important;
        min-height: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
        border: none !important;
        overflow: hidden !important;
    }
    </style>
    """, unsafe_allow_html=True)

    for i, block in enumerate(blocks):
        tag  = block["tag"]
        text = block["text"]

        # ── Modo edición activo para este bloque ──────────────────────────────
        if editing == i:
            st.markdown("---")
            is_short = tag in ("h1", "h2", "h3", "h4")
            new_val  = st.text_area(
                f"Editando <{tag}>",
                value  = text,
                height = 80 if is_short else 200,
                key    = f"edit_area_{i}",
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Guardar", key=f"save_block_{i}", use_container_width=True):
                    st.session_state["blocks"][i]["text"] = new_val
                    st.session_state["blocks"][i]["raw"]  = new_val
                    st.session_state["editing_idx"] = None
                    _rebuild_content()
                    st.rerun()
            with c2:
                if st.button("❌ Cancelar", key=f"cancel_block_{i}", use_container_width=True):
                    st.session_state["editing_idx"] = None
                    st.rerun()
            st.markdown("---")
            _render_image_thumbnails(i)
            continue

        # ── Vista normal: doble clic activa la edición ────────────────────────
        # Renderizamos un botón invisible de ancho completo sobre el contenido.
        # El truco: st.button con CSS que lo hace transparente y de tamaño cero,
        # y el contenido HTML real se muestra debajo. El usuario ve el HTML,
        # el botón invisible captura el doble clic via un wrapper JS.
        hint = '<span class="wy-hint">✏ doble clic para editar</span>'

        if tag == "h1":
            st.markdown(f'<div class="wy-block"><div class="wy-h1">{text}{hint}</div></div>', unsafe_allow_html=True)
        elif tag == "h2":
            st.markdown(f'<div class="wy-block"><div class="wy-h2">{text}{hint}</div></div>', unsafe_allow_html=True)
        elif tag == "h3":
            st.markdown(f'<div class="wy-block"><div class="wy-h3">{text}{hint}</div></div>', unsafe_allow_html=True)
        elif tag == "h4":
            st.markdown(f'<div class="wy-block"><div class="wy-h4">{text}{hint}</div></div>', unsafe_allow_html=True)
        elif block.get("is_list"):
            st.markdown(f'<div class="wy-block">{text}{hint}</div>', unsafe_allow_html=True)
        elif tag == "blockquote":
            st.markdown(f'<div class="wy-block"><div class="wy-bq">{text}{hint}</div></div>', unsafe_allow_html=True)
        elif tag == "pre":
            st.code(text)
        else:
            st.markdown(f'<div class="wy-block"><p class="wy-p">{text}{hint}</p></div>', unsafe_allow_html=True)

        # Botón invisible — oculto por CSS global (.wy-hidden-btn)
        st.markdown('<div class="wy-hidden-btn">', unsafe_allow_html=True)
        if st.button("·", key=f"dbl_{i}", use_container_width=True):
            st.session_state["editing_idx"] = i
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        # JS: escucha doble clic en el bloque y hace click en el botón oculto
        components.html(f"""
        <script>
        (function() {{
            var BLOCK_IDX = {i};
            function init() {{
                var parent = window.parent.document;
                var blocks = parent.querySelectorAll('.wy-block');
                var block  = blocks[BLOCK_IDX];
                if (!block) {{ setTimeout(init, 200); return; }}
                block.addEventListener('dblclick', function(e) {{
                    e.preventDefault(); e.stopPropagation();
                    // Buscar todos los botones invisibles (texto '·')
                    var allBtns = Array.from(parent.querySelectorAll('button'));
                    var hiddenBtns = allBtns.filter(function(b) {{
                        return b.textContent.trim() === '·';
                    }});
                    if (hiddenBtns[BLOCK_IDX]) hiddenBtns[BLOCK_IDX].click();
                }});
            }}
            init();
        }})();
        </script>
        """, height=0)


def _render_image_thumbnails(after_block_idx: int):
    for img in st.session_state["images"]:
        if img.get("after_block") == after_block_idx:
            src = img.get("src", "")
            alt = img.get("alt", "sin alt")
            if not src:
                continue
            if src.startswith("[LOCAL]"):
                local_path = src.replace("[LOCAL] ", "").strip()
                if Path(local_path).exists():
                    st.image(local_path, caption=f"🖼️ {alt}", width=280)
                else:
                    st.caption(f"📁 `{local_path}`")
            else:
                try:
                    st.image(src, caption=f"🖼️ {alt}", width=280)
                except Exception:
                    st.caption(f"🖼️ {src}")


# ─────────────────────────────────────────────────────────────────────────────
# Panel de imágenes
# ─────────────────────────────────────────────────────────────────────────────

def _heading_options() -> list[tuple[int, str]]:
    blocks  = st.session_state.get("blocks", [])
    options = [(-1, "Al inicio del artículo")]
    for b in blocks:
        if b["tag"] in ("h1", "h2", "h3"):
            snippet = b["text"][:48]
            options.append((b["idx"], f"Despues de <{b['tag']}> {snippet}"))
    options.append((99999, "Al final del artículo"))
    return options


def _image_panel():
    st.subheader("🖼️ Imágenes")
    images = st.session_state["images"]

    with st.expander("Agregar imagen", expanded=len(images) == 0):
        add_mode = st.radio(
            "Origen",
            ["Subir archivo", "URL externa"],
            horizontal=True,
            key="img_mode",
        )

        src_final = ""
        img_name  = ""

        if add_mode == "Subir archivo":
            uploaded = st.file_uploader(
                "Selecciona imagen",
                type=["jpg", "jpeg", "png", "gif", "webp"],
                key="img_upload",
            )
            if uploaded:
                ext       = Path(uploaded.name).suffix
                safe_name = f"img_{uuid.uuid4().hex[:8]}{ext}"
                dest      = IMAGES_DIR / safe_name
                dest.write_bytes(uploaded.read())
                src_final = str(dest)
                img_name  = uploaded.name
                st.image(str(dest), width=180)
        else:
            url_in = st.text_input(
                "URL de la imagen",
                placeholder="https://ejemplo.com/foto.jpg",
                key="img_url",
            )
            if url_in:
                src_final = url_in
                img_name  = url_in.split("/")[-1]
                try:
                    st.image(url_in, width=180)
                except Exception:
                    st.caption(url_in)

        alt_text     = st.text_input("Texto alt (SEO)", key="img_alt")
        caption_text = st.text_input("Pie de imagen (opcional)", key="img_caption")

        opts       = _heading_options()
        opt_labels = [lbl for _, lbl in opts]
        opt_idx    = st.selectbox(
            "Insertar despues de...",
            range(len(opt_labels)),
            format_func=lambda i: opt_labels[i],
            key="img_pos",
        )
        after_block = opts[opt_idx][0]

        if st.button("Agregar al artículo", disabled=not src_final, key="btn_add_img"):
            new_img = {
                "id":          str(uuid.uuid4()),
                "src":         src_final,
                "alt":         alt_text,
                "caption":     caption_text,
                "after_block": after_block,
                "marker":      f"<!-- img:{uuid.uuid4().hex[:6]} -->",
                "name":        img_name,
            }
            st.session_state["images"].append(new_img)
            st.success(f"Imagen añadida — posición: {opt_labels[opt_idx]}")
            st.rerun()

    if images:
        st.caption(f"{len(images)} imagen(es) en el artículo")
        for idx, img in enumerate(images):
            c1, c2 = st.columns([0.85, 0.15])
            with c1:
                name = img.get("name") or img.get("src", "")[:40]
                alt  = img.get("alt", "sin alt")
                st.markdown(f"**{idx+1}.** `{name}`  \n_alt: {alt}_")
            with c2:
                if st.button("🗑️", key=f"del_img_{idx}", help="Quitar imagen"):
                    st.session_state["images"].pop(idx)
                    st.rerun()
            st.divider()
    else:
        st.info("Agrega 1 o más imágenes para poder publicar en WordPress.")


# ─────────────────────────────────────────────────────────────────────────────
# Metadatos SEO
# ─────────────────────────────────────────────────────────────────────────────

def _meta_section():
    data = st.session_state["draft_data"]
    st.subheader("📋 Metadatos SEO")

    new_title = st.text_input(
        "Título del post",
        value=data.get("title", ""),
        key="meta_title",
    )
    new_kw = st.text_input(
        "Focus keyword",
        value=data.get("focus_keyword", ""),
        key="meta_kw",
    )
    new_meta = st.text_area(
        "Meta description (max 160 caracteres)",
        value=data.get("meta_description", ""),
        height=80,
        key="meta_desc",
        max_chars=160,
    )

    st.session_state["draft_data"]["title"]            = new_title
    st.session_state["draft_data"]["focus_keyword"]    = new_kw
    st.session_state["draft_data"]["meta_description"] = new_meta


# ─────────────────────────────────────────────────────────────────────────────
# Publicar en WordPress
# ─────────────────────────────────────────────────────────────────────────────

def _publish_to_wp(filepath: Path):
    from models.post_draft import PostDraft, PostType
    from core.wp_client    import WPClient

    data   = st.session_state["draft_data"]
    images = st.session_state["images"]
    blocks = st.session_state["blocks"]

    # Reconstruir HTML con marcadores de imagen intercalados
    markers_by_block: dict[int, list[str]] = {}
    for img in images:
        ab = img.get("after_block", 99999)
        markers_by_block.setdefault(ab, []).append(img.get("marker", ""))

    html_parts = []
    for m in markers_by_block.get(-1, []):
        html_parts.append(m)

    for b in blocks:
        tag = b["tag"]
        if b.get("is_list"):
            html_parts.append(b["text"])
        else:
            html_parts.append(f"<{tag}>{b['text']}</{tag}>")
        for m in markers_by_block.get(b["idx"], []):
            html_parts.append(m)

    for m in markers_by_block.get(99999, []):
        html_parts.append(m)

    content_with_markers = "\n".join(html_parts)

    try:
        pt = PostType(data["post_type"])
    except (KeyError, ValueError):
        pt = list(PostType)[0]

    draft = PostDraft(
        post_type        = pt,
        title            = data.get("title", ""),
        content          = content_with_markers,
        meta_description = data.get("meta_description", ""),
        focus_keyword    = data.get("focus_keyword", ""),
        affiliate_url    = data.get("affiliate_url") or None,
        images           = images,
    )

    with st.spinner("Enviando a WordPress..."):
        try:
            wp  = WPClient.from_env()
            wid = wp.create_draft(draft)
            data["wp_post_id"] = draft.wp_post_id
            data["images"]     = images
            _save_draft(filepath, data)
            if IS_LIVE:
                base     = os.getenv("WP_BASE_URL", "")
                edit_url = f"{base}/wp-admin/post.php?post={wid}&action=edit"
                st.success(
                    f"Borrador creado en WordPress — ID **{wid}**\n\n"
                    f"[Abrir en WP Admin]({edit_url})"
                )
            else:
                st.success(f"[Simulado] Draft enviado — ID simulado: **{wid}**")
        except Exception as exc:
            st.error(f"Error al publicar: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# PÁGINA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def main():
    st.title("✏️ Editor de Borrador")

    all_files = sorted(
        [f.name for f in DRAFTS_DIR.glob("draft_*.json")],
        reverse=True,
    )

    if not all_files:
        st.warning("No hay borradores. Genera uno desde la página principal.")
        if st.button("Ir a Inicio"):
            st.switch_page("app.py")
        return

    # ── Leer el archivo solicitado via query_param ────────────────────────────
    # NUNCA usar selectbox con key persistente: Streamlit cached value
    # siempre gana sobre el index= calculado, causando que siempre
    # se abra el último archivo seleccionado manualmente.
    query_file = st.query_params.get("file", "")

    # Si viene un archivo válido por query_param, usarlo directamente
    if query_file and query_file in all_files:
        selected_file = query_file
    else:
        # Sin query_param: mostrar lista de botones para elegir
        st.info("Selecciona un borrador para editar:")
        for fname in all_files:
            label = _nice_label(fname, DRAFTS_DIR)
            if st.button(label, key=f"pick_{fname}", use_container_width=True):
                st.query_params["file"] = fname
                st.rerun()
        return

    if not selected_file:
        return

    filepath   = DRAFTS_DIR / selected_file
    if not filepath.exists():
        st.error(f"Archivo no encontrado: `{selected_file}`")
        st.query_params.clear()
        if st.button("Volver al historial"):
            st.switch_page("pages/historial.py")
        return

    draft_data = _load_draft(filepath)
    _init_state(draft_data, selected_file)

    col_main, col_side = st.columns([0.67, 0.33])

    with col_main:
        ptype = draft_data.get("post_type", "")
        icon  = TYPE_ICONS.get(ptype, "📄")
        st.caption(f"{icon} `{ptype}` · `{selected_file}`")
        st.divider()
        _meta_section()
        st.divider()
        st.subheader("📝 Contenido del artículo")
        st.caption("Haz clic en el lápiz junto a cada elemento para editarlo.")
        _render_blocks()

    with col_side:
        _image_panel()

        st.divider()
        st.subheader("Acciones")

        images = st.session_state["images"]

        if st.button("💾 Guardar borrador", use_container_width=True, type="secondary"):
            _rebuild_content()
            d = st.session_state["draft_data"]
            d["images"] = images
            _save_draft(filepath, d)

        st.markdown(" ")

        has_images = len(images) > 0
        wp_label   = "🚀 Publicar en WordPress" if IS_LIVE else "🚀 Enviar a WP (simulado)"
        if st.button(
            wp_label,
            disabled            = not has_images,
            help                = "" if has_images else "Agrega 1 o más imágenes para publicar",
            use_container_width = True,
            type                = "primary",
        ):
            _publish_to_wp(filepath)

        if not has_images:
            st.caption("Agrega al menos 1 imagen para habilitar el botón de publicar.")

        st.divider()

        if IS_LIVE:
            st.success("WP_MODE=live — WordPress real")
        else:
            st.info("WP_MODE=simulated — Solo guarda localmente")

        st.divider()

        with st.expander("Eliminar borrador"):
            st.warning("Esta acción no se puede deshacer.")
            if st.button("Eliminar permanentemente", type="primary"):
                filepath.unlink(missing_ok=True)
                st.session_state.pop("_loaded_file", None)
                st.query_params.clear()
                st.success("Borrador eliminado.")
                st.switch_page("pages/historial.py")

        st.divider()
        if st.button("Volver al historial", use_container_width=True):
            st.query_params.clear()
            st.switch_page("pages/historial.py")


main()
