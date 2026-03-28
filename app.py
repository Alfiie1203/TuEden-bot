"""
app.py  -  Blog Content Generator - Interfaz Flask
============================================================================
Como ejecutar:
    python app.py
    (abre tu navegador en http://localhost:5000)

Primera vez: se crea users.json con usuarios por defecto.
  - luis / admin123  (admin)
  - alejandra / alejandra123  (psicologa)
  - angela / angela123  (medico)
Cambia las contrasenas desde el panel /admin
"""
from __future__ import annotations

# Usar el almacén de certificados del sistema operativo (necesario en redes
# corporativas con proxies de inspección TLS como Zscaler).
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # En entornos sin truststore (p.ej. Linux/servidor) funciona sin esto

import json
import os
import queue
import re
import threading
import uuid
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

import requests as req_lib
from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, redirect, render_template,
    request, send_file, session, stream_with_context, url_for,
)
from requests.auth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())
app.permanent_session_lifetime = timedelta(days=7)

# -- Directorios -------------------------------------------------------------------
DRAFTS_DIR = Path("drafts_output")
IMAGES_DIR = DRAFTS_DIR / "images"
LOG_PATH   = Path("logs/generation_log.jsonl")
USERS_FILE = Path("users.json")
ENV_FILE   = Path(".env")

# -- Singleton: TokenManager -------------------------------------------------------
_token_manager = None
_tm_lock = threading.Lock()


def get_token_manager():
    global _token_manager
    if _token_manager is None:
        with _tm_lock:
            if _token_manager is None:
                try:
                    from core.token_manager import TokenManager
                    _token_manager = TokenManager.from_env()
                except Exception:
                    pass
    return _token_manager


# -- Colas de progreso para generacion asincrona -----------------------------------
_progress_queues: dict[str, queue.Queue] = {}


# -- Helpers modo ------------------------------------------------------------------
def _get_modes() -> tuple[bool, bool]:
    mock_mode   = os.getenv("GEMINI_MOCK_MODE", "true").lower() in ("true", "1", "yes")
    simulate_wp = os.getenv("WP_MODE", "simulated").lower() != "live"
    return mock_mode, simulate_wp


def _find_draft_file(wp_post_id, post_type: str) -> str | None:
    if not DRAFTS_DIR.exists():
        return None
    target = DRAFTS_DIR / f"draft_{wp_post_id}_{post_type}.json"
    if target.exists():
        return target.name
    for f in DRAFTS_DIR.glob(f"draft_{wp_post_id}_*.json"):
        return f.name
    return None


def _safe_draft_path(filename: str) -> Path | None:
    """Valida que el filename este dentro de DRAFTS_DIR (evita path traversal)."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    base = DRAFTS_DIR.resolve()
    safe = (DRAFTS_DIR / Path(filename).name).resolve()
    if not str(safe).startswith(str(base)):
        return None
    return safe


# ==================================================================================
# GESTION DE USUARIOS
# ==================================================================================

def _init_users_file() -> None:
    """Crea users.json inicial desde credenciales del .env."""
    users = {
        "luis": {
            "password_hash":   generate_password_hash("admin123"),
            "role":            "admin",
            "display_name":    "Luis",
            "wp_username":     os.getenv("WP_USERNAME", "Luis"),
            "wp_app_password": os.getenv("WP_APP_PASSWORD", ""),
            "topic_categories": None,
        },
        "alejandra": {
            "password_hash":   generate_password_hash("alejandra123"),
            "role":            "psicologa",
            "display_name":    "Alejandra",
            "wp_username":     os.getenv("WP_USERNAME_ALEJANDRA", "alejandra"),
            "wp_app_password": os.getenv("WP_APP_PASSWORD_ALEJANDRA", ""),
            "topic_categories": ["psicologia_relacionados", "psicologia_no_relacionados"],
        },
        "angela": {
            "password_hash":   generate_password_hash("angela123"),
            "role":            "medico",
            "display_name":    "Angela",
            "wp_username":     os.getenv("WP_USERNAME_ANGELA", "angela"),
            "wp_app_password": os.getenv("WP_APP_PASSWORD_ANGELA", ""),
            "topic_categories": ["medicina_relacionados", "medicina_no_relacionados"],
        },
    }
    save_users(users)


def load_users() -> dict:
    if not USERS_FILE.exists():
        _init_users_file()
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_users(users: dict) -> None:
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")


def get_current_user() -> dict | None:
    username = session.get("username")
    if not username:
        return None
    return load_users().get(username)


# -- Auth decorators ---------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("username"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "No autenticado", "redirect": "/login"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"error": "No autenticado"}), 401
            return redirect(url_for("login_page"))
        if user.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Acceso denegado"}), 403
            return render_template("403.html", mock_mode=False, simulate_wp=False), 403
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_current_user():
    return {"current_user": get_current_user()}


# -- .env file helpers -------------------------------------------------------------
def _read_env_lines() -> list[str]:
    if ENV_FILE.exists():
        return ENV_FILE.read_text(encoding="utf-8").splitlines()
    return []


def _write_env_lines(lines: list[str]) -> None:
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    load_dotenv(override=True)


# ==================================================================================
# AUTENTICACION
# ==================================================================================

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if session.get("username"):
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        users    = load_users()
        user     = users.get(username)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session.permanent = True
            session["username"] = username
            return redirect(url_for("index"))
        error = "Usuario o contrasena incorrectos"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ==================================================================================
# RUTAS DE PAGINAS
# ==================================================================================

@app.route("/")
@login_required
def index():
    mock_mode, simulate_wp = _get_modes()
    return render_template(
        "index.html",
        mock_mode=mock_mode,
        simulate_wp=simulate_wp,
        wp_url=os.getenv("WP_BASE_URL", ""),
    )


@app.route("/historial")
@login_required
def historial():
    mock_mode, simulate_wp = _get_modes()
    return render_template("historial.html", mock_mode=mock_mode, simulate_wp=simulate_wp)


@app.route("/topicos")
@login_required
def topicos():
    mock_mode, simulate_wp = _get_modes()
    return render_template("topicos.html", mock_mode=mock_mode, simulate_wp=simulate_wp)


@app.route("/borrador")
@login_required
def borrador():
    mock_mode, simulate_wp = _get_modes()
    filename = request.args.get("file", "")
    return render_template(
        "borrador.html",
        mock_mode=mock_mode,
        simulate_wp=simulate_wp,
        filename=filename,
    )


@app.route("/admin")
@admin_required
def admin_page():
    mock_mode, simulate_wp = _get_modes()
    return render_template("admin.html", mock_mode=mock_mode, simulate_wp=simulate_wp)


# ==================================================================================
# API - TOKENS
# ==================================================================================

@app.get("/api/tokens")
@login_required
def api_tokens():
    mock_mode, _ = _get_modes()
    try:
        from core.token_manager import FREE_TIER_RPD, TOKENS_PER_BLOG_EST
        tm = get_token_manager()
        if tm is None:
            return jsonify({"error": "TokenManager no disponible", "mock_mode": mock_mode})
        summary = tm.get_summary()
        summary.update({
            "mock_mode":       mock_mode,
            "free_tier_rpd":   FREE_TIER_RPD,
            "tokens_per_blog": TOKENS_PER_BLOG_EST,
        })
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e), "mock_mode": mock_mode})


@app.post("/api/tokens/rotar")
@login_required
def api_tokens_rotar():
    mock_mode, _ = _get_modes()
    if mock_mode:
        return jsonify({"ok": False, "msg": "Mock mode activo"})
    tm = get_token_manager()
    if not tm:
        return jsonify({"ok": False, "msg": "No hay TokenManager"})
    rotated = tm.rotate(reason="manual-gui")
    if rotated:
        return jsonify({"ok": True, "alias": tm.active_key.alias})
    return jsonify({"ok": False, "msg": "No hay mas claves disponibles"})


@app.post("/api/tokens/activar")
@login_required
def api_tokens_activar():
    data  = request.get_json() or {}
    alias = data.get("alias", "")
    tm = get_token_manager()
    if tm and alias:
        tm.set_active_key(alias)
        return jsonify({"ok": True})
    return jsonify({"ok": False})


# ==================================================================================
# API - GENERACION DE BORRADORES
# ==================================================================================

@app.post("/api/generar")
@login_required
def api_generar():
    global _token_manager
    data         = request.get_json() or {}
    user_input   = data.get("topico", "").strip()
    focus        = data.get("focus", "").strip()
    gen_mode     = data.get("mode", "auto")
    reviewer     = data.get("reviewer", "")
    gemini_model = data.get("gemini_model", "gemini-2.5-flash")

    if not user_input:
        return jsonify({"error": "Falta el topico"}), 400

    task_id = str(uuid.uuid4())
    q       = queue.Queue()
    _progress_queues[task_id] = q
    _current_username = (get_current_user() or {}).get("username", "")
    _current_badge    = (get_current_user() or {}).get("professional_badge", "")

    def run():
        global _token_manager
        try:
            from core.orchestrator import ContentOrchestrator
            tm = get_token_manager()

            def progress_cb(step: int, total: int, message: str):
                q.put({"type": "progress", "step": step, "total": total, "message": message})

            orchestrator = ContentOrchestrator.from_env(
                progress_cb=progress_cb,
                token_manager=tm,
                gemini_model=gemini_model,
            )
            drafts = orchestrator.run(user_input, mode=gen_mode, focus=focus, reviewer=reviewer, username=_current_username, badge_html=_current_badge)
            _token_manager = orchestrator.gemini.token_manager

            result = []
            for draft in drafts:
                df = draft.draft_file or _find_draft_file(draft.wp_post_id, draft.post_type)
                result.append({
                    "post_type":        str(draft.post_type),
                    "title":            draft.title,
                    "focus_keyword":    draft.focus_keyword,
                    "meta_description": draft.meta_description[:100],
                    "wp_post_id":       draft.wp_post_id,
                    "draft_file":       df,
                    "is_error":         draft.title.startswith("[ERROR]"),
                    "error_msg":        draft.content[:300] if draft.title.startswith("[ERROR]") else "",
                })
            q.put({"type": "done", "drafts": result, "topic": user_input})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"task_id": task_id})


@app.get("/api/progreso/<task_id>")
@login_required
def api_progreso(task_id):
    """Server-Sent Events - stream de progreso de generacion."""
    q = _progress_queues.get(task_id)

    if q is None:
        def not_found():
            yield 'data: {"type":"error","message":"Tarea no encontrada"}\n\n'
        return Response(not_found(), mimetype="text/event-stream")

    def stream():
        while True:
            msg = q.get()
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            if msg.get("type") in ("done", "error"):
                _progress_queues.pop(task_id, None)
                return

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ==================================================================================
# API - TOPICOS DEL DIA
# ==================================================================================

@app.post("/api/topicos/cargar")
@login_required
def api_topicos_cargar():
    global _token_manager
    data      = request.get_json() or {}
    force     = data.get("force", False)
    mock_mode, _ = _get_modes()
    try:
        from core.topic_discovery import get_topics
        from core.gemini_client   import GeminiClient
        tm     = get_token_manager()
        gemini = GeminiClient(token_manager=tm, mock_mode=mock_mode)
        topics = get_topics(gemini, force_refresh=force)
        _token_manager = gemini.token_manager
        # Filtrar por rol de usuario
        user    = get_current_user()
        allowed = user.get("topic_categories") if user else None
        if allowed and isinstance(topics, dict):
            topics = {k: v for k, v in topics.items() if k == "fecha" or k in allowed}
        return jsonify({"ok": True, "data": topics, "from_cache": not force})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/topicos/sugerir")
@login_required
def api_topicos_sugerir():
    global _token_manager
    data      = request.get_json() or {}
    topics    = data.get("topics", [])
    mock_mode, _ = _get_modes()
    try:
        from core.post_type_advisor import suggest_post_structure
        from core.gemini_client     import GeminiClient
        tm     = get_token_manager()
        gemini = GeminiClient(token_manager=tm, mock_mode=mock_mode)
        sugs   = suggest_post_structure(gemini, topics)
        _token_manager = gemini.token_manager
        return jsonify({"ok": True, "suggestions": [s.to_dict() for s in sugs]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/topicos/generar")
@login_required
def api_topicos_generar():
    global _token_manager
    data          = request.get_json() or {}
    topics        = data.get("topics", [])
    edited_titles = data.get("edited_titles", {})
    reviewer      = data.get("reviewer", "")
    gemini_model  = data.get("gemini_model", "gemini-2.5-flash")
    focus_global  = data.get("focus_global", "")

    if not topics:
        return jsonify({"error": "Sin topicos seleccionados"}), 400

    task_id = str(uuid.uuid4())
    q       = queue.Queue()
    _progress_queues[task_id] = q
    _current_username = (get_current_user() or {}).get("username", "")
    _current_badge    = (get_current_user() or {}).get("professional_badge", "")

    def run():
        global _token_manager
        try:
            from core.orchestrator import ContentOrchestrator
            tm           = get_token_manager()
            total_topics = len(topics)
            all_results  = []

            for t_idx, topic in enumerate(topics):
                q.put({"type": "topic_start", "topic": topic, "idx": t_idx, "total": total_topics})
                ct = {
                    pt: edited_titles.get(f"{topic}:{pt}", "")
                    for pt in ("opinion", "listicle", "howto")
                }
                ct = {k: v for k, v in ct.items() if v.strip()}

                def make_cb(idx):
                    def cb(step, total, msg):
                        q.put({"type": "progress", "idx": idx, "step": step, "total": total, "message": msg})
                    return cb

                try:
                    orch = ContentOrchestrator.from_env(
                        progress_cb=make_cb(t_idx),
                        token_manager=tm,
                        gemini_model=gemini_model,
                    )
                    drafts = orch.run(
                        topic,
                        mode="libre",
                        focus=focus_global,
                        reviewer=reviewer,
                        custom_titles=ct or None,
                        username=_current_username,
                        badge_html=_current_badge,
                    )
                    _token_manager = orch.gemini.token_manager
                    tm = _token_manager
                    topic_drafts = []
                    for d in drafts:
                        df = d.draft_file or _find_draft_file(d.wp_post_id, d.post_type)
                        topic_drafts.append({
                            "post_type":     str(d.post_type),
                            "title":         d.title,
                            "focus_keyword": d.focus_keyword,
                            "wp_post_id":    d.wp_post_id,
                            "draft_file":    df,
                            "is_error":      d.title.startswith("[ERROR]"),
                        })
                    all_results.append({"topic": topic, "drafts": topic_drafts})
                    q.put({"type": "topic_done", "topic": topic, "idx": t_idx, "drafts": topic_drafts})
                except Exception as e:
                    q.put({"type": "topic_error", "topic": topic, "idx": t_idx, "message": str(e)})
                    all_results.append({"topic": topic, "drafts": [], "error": str(e)})

            q.put({"type": "done", "results": all_results})
        except Exception as e:
            q.put({"type": "error", "message": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"task_id": task_id})


# ==================================================================================
# API - BORRADORES
# ==================================================================================

@app.get("/api/borradores")
@login_required
def api_borradores():
    if not DRAFTS_DIR.exists():
        return jsonify([])
    files = []
    for f in sorted(DRAFTS_DIR.glob("draft_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            files.append({
                "filename":   f.name,
                "title":      d.get("title", f.name),
                "post_type":  d.get("post_type", ""),
                "created_at": d.get("created_at", ""),
            })
        except Exception:
            files.append({"filename": f.name, "title": f.name, "post_type": "", "created_at": ""})
    return jsonify(files)


@app.get("/api/borrador/<path:filename>")
@login_required
def api_borrador_get(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    return jsonify(json.loads(safe.read_text(encoding="utf-8")))


@app.post("/api/borrador/<path:filename>/guardar")
@login_required
def api_borrador_guardar(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    data = request.get_json()
    if not data:
        return jsonify({"error": "Sin datos"}), 400
    safe.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True})


@app.post("/api/borrador/<path:filename>/eliminar")
@login_required
def api_borrador_eliminar(filename):
    safe = _safe_draft_path(filename)
    if safe and safe.exists():
        safe.unlink()
    return jsonify({"ok": True})


@app.route("/api/borradores/todos", methods=["DELETE"])
@admin_required
def api_borradores_borrar_todos():
    """Borra todos los archivos de borrador en drafts_output/ (solo admin)."""
    try:
        eliminados = 0
        if DRAFTS_DIR.exists():
            for f in DRAFTS_DIR.glob("draft_*.json"):
                f.unlink()
                eliminados += 1
        return jsonify({"ok": True, "eliminados": eliminados})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/borrador/<path:filename>/publicar")
@login_required
def api_borrador_publicar(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    draft_data     = json.loads(safe.read_text(encoding="utf-8"))
    _, simulate_wp = _get_modes()
    body           = request.get_json() or {}
    images         = body.get("images",     draft_data.get("images", []))
    categories     = body.get("categories", draft_data.get("categories", []))
    tags           = body.get("tags",       draft_data.get("tags", []))
    try:
        from core.wp_client    import WordPressClient
        from models.post_draft import PostDraft, PostType
        try:
            pt = PostType(draft_data.get("post_type", ""))
        except (KeyError, ValueError):
            import models.post_draft as _pm
            pt = list(_pm.PostType)[0]

        post_draft = PostDraft(
            post_type        = pt,
            title            = draft_data.get("title", ""),
            content          = draft_data.get("content", ""),
            meta_description = draft_data.get("meta_description", ""),
            focus_keyword    = draft_data.get("focus_keyword", ""),
            affiliate_url    = draft_data.get("affiliate_url") or None,
            images           = images,
            categories       = categories,
            tags             = tags,
        )
        if simulate_wp:
            wp = WordPressClient(simulate=True)
        else:
            # Usar credenciales del usuario autenticado
            user        = get_current_user()
            wp_username = user.get("wp_username", "") if user else ""
            wp_password = user.get("wp_app_password", "") if user else ""
            base_url    = os.getenv("WP_BASE_URL", "").rstrip("/")
            if wp_username and wp_password and base_url:
                wp = WordPressClient(
                    simulate=False,
                    base_url=base_url,
                    username=wp_username,
                    app_password=wp_password,
                )
            else:
                author_key = session.get("username", "luis")
                wp = WordPressClient.from_env(user_key=author_key)

            # Asignar categorías y etiquetas si el borrador las tiene vacías
            if not categories or not tags:
                try:
                    from requests.auth import HTTPBasicAuth
                    from core.wp_taxonomy import assign_taxonomy
                    from core.gemini_client import GeminiClient
                    _tax_auth = HTTPBasicAuth(wp_username or os.getenv("WP_USERNAME", ""),
                                             wp_password or os.getenv("WP_APP_PASSWORD", ""))
                    _gem      = GeminiClient()
                    _cats, _tags, _kws = assign_taxonomy(
                        gemini        = _gem,
                        base_url      = base_url,
                        auth          = _tax_auth,
                        title         = draft_data.get("title", ""),
                        content       = draft_data.get("content", ""),
                        post_type     = draft_data.get("post_type", ""),
                        focus_keyword = draft_data.get("focus_keyword", ""),
                    )
                    if not categories: categories = _cats
                    if not tags:       tags       = _tags
                    # Actualizar el PostDraft con taxonomy y keywords resueltas
                    post_draft.categories   = categories
                    post_draft.tags         = tags
                    post_draft.seo_keywords = _kws
                except Exception as _tax_exc:
                    logger.warning(f"[Publicar] No se pudo resolver taxonomy: {_tax_exc}")

            # Bloquear publicación si no hay categorías asignadas
            if not post_draft.categories:
                return jsonify({
                    "error": (
                        "⚠️ El artículo no tiene categorías asignadas. "
                        "Usa el botón \"Auto-clasificar con IA\" antes de publicar."
                    )
                }), 422

        _pub_user     = get_current_user()
        _badge_html   = (_pub_user or {}).get("professional_badge", "")
        if _badge_html and "professional-review-badge" not in post_draft.content:
            # Insertar el badge DESPUÉS del primer párrafo para que la focus keyword
            # esté en el primer párrafo visible y AIOSEO lo detecte correctamente.
            _first_p_end = post_draft.content.find("</p>")
            if _first_p_end != -1:
                _insert_at = _first_p_end + len("</p>")
                post_draft.content = (
                    post_draft.content[:_insert_at]
                    + "\n\n" + _badge_html + "\n\n"
                    + post_draft.content[_insert_at:]
                )
            else:
                # Fallback: no hay <p>, añadir al inicio
                post_draft.content = _badge_html + "\n\n" + post_draft.content

        wid = wp.create_draft(post_draft)
        if not simulate_wp:
            safe.unlink(missing_ok=True)
            wp_url = os.getenv("WP_BASE_URL", "").rstrip("/")
            return jsonify({
                "ok": True,
                "wp_post_id": wid,
                "edit_url":   f"{wp_url}/wp-admin/post.php?post={wid}&action=edit",
                "deleted":    True,
            })
        return jsonify({"ok": True, "wp_post_id": wid, "simulated": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/borrador/<path:filename>/descargar")
@login_required
def api_borrador_descargar(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    return send_file(safe, as_attachment=True, download_name=safe.name)


@app.post("/api/borrador/<path:filename>/imagen")
@login_required
def api_borrador_imagen(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    if "file" not in request.files:
        return jsonify({"error": "Sin archivo"}), 400
    file    = request.files["file"]
    ext     = Path(file.filename or "img.jpg").suffix.lower()
    allowed = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    if ext not in allowed:
        return jsonify({"error": "Tipo de archivo no permitido"}), 400
    safe_name = f"img_{uuid.uuid4().hex[:8]}{ext}"
    dest      = IMAGES_DIR / safe_name
    file.save(str(dest))
    return jsonify({"ok": True, "src": f"[LOCAL] {dest}", "local_name": safe_name, "name": file.filename})


@app.post("/api/borrador/<path:filename>/autoclasificar")
@login_required
def api_borrador_autoclasificar(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    draft_data = json.loads(safe.read_text(encoding="utf-8"))
    try:
        from core.wp_taxonomy   import assign_taxonomy
        from core.gemini_client import GeminiClient
        base_url = os.getenv("WP_BASE_URL", "").rstrip("/")
        wp_user  = os.getenv("WP_USERNAME", "")
        wp_pass  = os.getenv("WP_APP_PASSWORD", "")
        if not (base_url and wp_user and wp_pass):
            return jsonify({"error": "Credenciales WP admin no configuradas en .env"}), 400
        auth   = HTTPBasicAuth(wp_user, wp_pass)
        gemini = GeminiClient()
        cat_ids, tag_ids, kw_strings = assign_taxonomy(
            gemini=gemini, base_url=base_url, auth=auth,
            title         = draft_data.get("title", ""),
            content       = draft_data.get("content", ""),
            post_type     = draft_data.get("post_type", ""),
            focus_keyword = draft_data.get("focus_keyword", ""),
        )
        return jsonify({"ok": True, "categories": cat_ids, "tags": tag_ids, "seo_keywords": kw_strings})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/imagen/<path:img_name>")
def serve_image(img_name):
    """Sirve imagenes locales desde drafts_output/images/."""
    base = IMAGES_DIR.resolve()
    safe = (IMAGES_DIR / Path(img_name).name).resolve()
    if not str(safe).startswith(str(base)):
        return jsonify({"error": "Acceso denegado"}), 403
    if not safe.exists():
        return jsonify({"error": "No encontrada"}), 404
    return send_file(safe)


# ==================================================================================
# API - HISTORIAL
# ==================================================================================

@app.get("/api/historial")
@login_required
def api_historial():
    if not LOG_PATH.exists():
        return jsonify([])
    entries = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    if not e.get("draft_file"):
                        wp_id = e.get("wp_post_id")
                        pt    = e.get("post_type", "")
                        if wp_id:
                            e["draft_file"] = _find_draft_file(wp_id, pt) or ""
                    df = e.get("draft_file", "")
                    e["draft_exists"] = bool(df) and (DRAFTS_DIR / Path(df).name).exists()
                    entries.append(e)
                except json.JSONDecodeError:
                    pass
    return jsonify(list(reversed(entries)))


@app.post("/api/historial/recuperar")
@login_required
def api_historial_recuperar():
    data      = request.get_json() or {}
    wp_id     = data.get("wp_post_id")
    post_type = data.get("post_type", "")
    entry     = data.get("entry", {})
    base_url  = os.getenv("WP_BASE_URL", "").rstrip("/")
    # Usar credenciales del usuario actual
    user         = get_current_user()
    username     = user.get("wp_username", "")     if user else os.getenv("WP_USERNAME", "")
    app_password = user.get("wp_app_password", "") if user else os.getenv("WP_APP_PASSWORD", "")
    if not (base_url and username and app_password):
        return jsonify({"error": "Credenciales WP no configuradas"}), 400
    try:
        resp = req_lib.get(
            f"{base_url}/wp-json/wp/v2/posts/{wp_id}",
            auth=HTTPBasicAuth(username, app_password),
            timeout=20,
        )
        resp.raise_for_status()
        wp_data = resp.json()
    except Exception as e:
        return jsonify({"error": f"No se pudo recuperar desde WP: {e}"}), 500

    title     = wp_data.get("title",   {}).get("rendered", entry.get("title", ""))
    content   = wp_data.get("content", {}).get("rendered", "")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"draft_{wp_id}_{post_type}_{timestamp}.json"
    filepath  = DRAFTS_DIR / filename
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "sim_id": wp_id, "wp_post_id": wp_id,
        "status": wp_data.get("status", "draft"),
        "created_at": entry.get("timestamp", datetime.now().isoformat()),
        "post_type": post_type, "title": title, "content": content,
        "meta_description": "", "focus_keyword": entry.get("focus_keyword", ""),
        "affiliate_url": entry.get("affiliate_url", ""),
        "ai_generated": True, "images": [], "image_prompts": {},
    }
    filepath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "filename": filename})


@app.route("/api/historial", methods=["DELETE"])
@admin_required
def api_historial_borrar():
    """Borra todo el historial de generación (solo admin)."""
    try:
        if LOG_PATH.exists():
            LOG_PATH.write_text("", encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ==================================================================================
# API - ADMIN: ESTADÍSTICAS POR USUARIO
# ==================================================================================

@app.get("/api/admin/stats")
@admin_required
def api_admin_stats():
    """Devuelve estadísticas de generación por usuario."""
    users = load_users()

    # Mapeo reviewer → username para entradas antiguas sin username
    reviewer_to_user = {}
    for uname, udata in users.items():
        role = udata.get("role", "")
        if role == "medico":
            reviewer_to_user["Médico"] = uname
        elif role == "psicologa":
            reviewer_to_user["Psicólogo"] = uname

    # Inicializar stats por usuario
    stats: dict[str, dict] = {}
    for uname, udata in users.items():
        stats[uname] = {
            "username":         uname,
            "display_name":     udata.get("display_name", uname),
            "role":             udata.get("role", ""),
            "posts_generados":  0,
            "tokens_usados":    0,
            "borradores_disco": 0,
            "posts_publicados": 0,
            "ultima_actividad": None,
            "tipos": {},
        }

    # Leer log
    if LOG_PATH.exists():
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Resolver a qué usuario corresponde
                uname = e.get("username", "").strip()
                if not uname:
                    reviewer = e.get("reviewer", "")
                    uname = reviewer_to_user.get(reviewer, "")
                if not uname:
                    # Fallback: primer admin
                    uname = next((u for u, d in users.items() if d.get("role") == "admin"), "")
                if uname not in stats:
                    continue

                s = stats[uname]
                s["posts_generados"] += 1
                s["tokens_usados"]   += e.get("tokens_used", 0) or 0

                # Publicados = modo real
                if e.get("wp_mode") == "real":
                    s["posts_publicados"] += 1

                # Contar archivos en disco
                df = e.get("draft_file", "")
                if df and (DRAFTS_DIR / Path(df).name).exists():
                    s["borradores_disco"] += 1

                # Tipos de post
                pt = e.get("post_type", "otro")
                s["tipos"][pt] = s["tipos"].get(pt, 0) + 1

                # Última actividad
                ts = e.get("timestamp", "")
                if ts and (not s["ultima_actividad"] or ts > s["ultima_actividad"]):
                    s["ultima_actividad"] = ts

    return jsonify({"ok": True, "stats": list(stats.values())})


# ==================================================================================
# API - ADMIN: USUARIOS
# ==================================================================================

@app.get("/api/admin/users")
@admin_required
def api_admin_users_get():
    users = load_users()
    safe  = {u: {k: v for k, v in d.items() if k != "password_hash"} for u, d in users.items()}
    return jsonify(safe)


@app.post("/api/admin/users")
@admin_required
def api_admin_users_create():
    data     = request.get_json() or {}
    username = data.get("username", "").strip().lower()
    if not username or not re.match(r'^[a-z0-9_]+$', username):
        return jsonify({"error": "Username invalido (solo letras, numeros y _)"}), 400
    users = load_users()
    if username in users:
        return jsonify({"error": "Usuario ya existe"}), 409
    pwd = data.get("password", "").strip()
    if not pwd:
        return jsonify({"error": "La contrasena no puede estar vacia"}), 400
    users[username] = {
        "password_hash":    generate_password_hash(pwd),
        "role":             data.get("role", "editor"),
        "display_name":     data.get("display_name", username.title()),
        "wp_username":      data.get("wp_username", ""),
        "wp_app_password":  data.get("wp_app_password", ""),
        "topic_categories": data.get("topic_categories") or None,
        "professional_badge": data.get("professional_badge", ""),
    }
    save_users(users)
    return jsonify({"ok": True})


@app.patch("/api/admin/users/<username>")
@admin_required
def api_admin_users_update(username):
    users = load_users()
    if username not in users:
        return jsonify({"error": "Usuario no encontrado"}), 404
    data = request.get_json() or {}
    u    = users[username]
    for field in ("role", "display_name", "wp_username", "wp_app_password", "topic_categories", "professional_badge"):
        if field in data:
            u[field] = data[field] or None if field == "topic_categories" else data[field]
    if data.get("password"):
        u["password_hash"] = generate_password_hash(data["password"])
    save_users(users)
    return jsonify({"ok": True})


@app.delete("/api/admin/users/<username>")
@admin_required
def api_admin_users_delete(username):
    if username == "luis":
        return jsonify({"error": "No se puede eliminar al administrador principal"}), 403
    users = load_users()
    users.pop(username, None)
    save_users(users)
    return jsonify({"ok": True})


# ==================================================================================
# API - ADMIN: CLAVES GEMINI
# ==================================================================================

@app.get("/api/admin/gemini-keys")
@admin_required
def api_admin_gemini_keys_get():
    lines = _read_env_lines()
    keys  = []
    for line in lines:
        m = re.match(r'^GEMINI_API_KEY_(\d+)=(.*)$', line)
        if m:
            val    = m.group(2).strip()
            masked = (val[:8] + "****" + val[-4:]) if len(val) > 12 else ("****" if val else "vacia")
            keys.append({"n": int(m.group(1)), "masked": masked, "empty": not val})
    return jsonify(keys)


@app.post("/api/admin/gemini-keys")
@admin_required
def api_admin_gemini_keys_add():
    global _token_manager
    data    = request.get_json() or {}
    new_key = data.get("key", "").strip()
    if not new_key:
        return jsonify({"error": "Clave vacia"}), 400
    lines    = _read_env_lines()
    max_n    = 0
    last_idx = len(lines)
    for i, line in enumerate(lines):
        m = re.match(r'^GEMINI_API_KEY_(\d+)=', line)
        if m:
            max_n    = max(max_n, int(m.group(1)))
            last_idx = i + 1
    new_n = max_n + 1
    lines.insert(last_idx, f"GEMINI_API_KEY_{new_n}={new_key}")
    _write_env_lines(lines)
    _token_manager = None
    return jsonify({"ok": True, "n": new_n})


@app.delete("/api/admin/gemini-keys/<int:n>")
@admin_required
def api_admin_gemini_keys_delete(n):
    global _token_manager
    lines     = _read_env_lines()
    new_lines = [l for l in lines if not re.match(rf'^GEMINI_API_KEY_{n}=', l)]
    counter, renumbered = 1, []
    for line in new_lines:
        m = re.match(r'^GEMINI_API_KEY_(\d+)=(.*)', line)
        if m:
            renumbered.append(f"GEMINI_API_KEY_{counter}={m.group(2)}")
            counter += 1
        else:
            renumbered.append(line)
    _write_env_lines(renumbered)
    _token_manager = None
    return jsonify({"ok": True})


# ==================================================================================
# MAIN
# ==================================================================================

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)
