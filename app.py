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

# --- SECRET KEY persistente -------------------------------------------------------
# Si FLASK_SECRET_KEY no está en .env la generamos UNA sola vez, la escribimos
# en .env y la usamos. Así los reinicios del servidor NO invalidan las sesiones.
_secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()
if not _secret_key:
    _secret_key = os.urandom(32).hex()
    _env_path = Path(".env")
    try:
        _current = _env_path.read_text(encoding="utf-8") if _env_path.exists() else ""
        _env_path.write_text(
            _current.rstrip() + f"\nFLASK_SECRET_KEY={_secret_key}\n",
            encoding="utf-8",
        )
        load_dotenv(override=True)  # recargar para que el resto del proceso la tenga
    except Exception:
        pass  # si no se puede escribir, usamos la generada en memoria (solo esta sesión)
app.secret_key = _secret_key
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
def inject_globals():
    mock_mode, simulate_wp = _get_modes()
    return {
        "current_user": get_current_user(),
        "mock_mode":    mock_mode,
        "simulate_wp":  simulate_wp,
    }


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
    return render_template("index.html", wp_url=os.getenv("WP_BASE_URL", ""))


@app.route("/historial")
@login_required
def historial():
    return render_template("historial.html")


@app.route("/topicos")
@login_required
def topicos():
    return render_template("topicos.html")


@app.route("/borrador")
@login_required
def borrador():
    return render_template(
        "borrador.html",
        filename=request.args.get("file", ""),
        wp_base_url=os.getenv("WP_BASE_URL", "").rstrip("/"),
    )


@app.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html")


# ==================================================================================
# GESTIÓN DE PROMPTS (solo admin)
# ==================================================================================

# Metadatos por prompt: label, descripción, variables requeridas y contexto de uso
_PROMPT_META: dict[str, dict] = {
    "BASE_CONTEXT": {
        "label":       "Contexto base — Modo Amazon",
        "description": "Reglas globales (estilo, SEO, gramática, formato) inyectadas al inicio de los 3 posts de Modo Amazon. Cualquier cambio aquí afecta a comparativa, guía y reseña.",
        "vars":        ["{topic}", "{today}", "{focus_block}", "{reviewer_block}", "{voice_block}", "{username}", "{badge_html}"],
        "used_in":     "Modo Amazon · los 3 posts",
    },
    "BASE_CONTEXT_LIBRE": {
        "label":       "Contexto base — Modo Libre",
        "description": "Reglas globales para los 3 posts de Modo Libre (sin producto). Define tono cercano, normas SEO y gramática. Es el prompt más influyente del modo libre.",
        "vars":        ["{topic}", "{today}", "{focus_block}", "{reviewer_block}", "{voice_block}", "{username}", "{badge_html}"],
        "used_in":     "Modo Libre · los 3 posts",
    },
    "PROMPT_OPINION": {
        "label":       "Post Libre · Artículo de Opinión",
        "description": "Genera el post 1/3: una reflexión o análisis personal/profesional sobre el tópico. Ideal para posicionar autoridad y generar debate.",
        "vars":        ["{title}"],
        "used_in":     "Modo Libre · Post 1 de 3",
    },
    "PROMPT_LISTICLE": {
        "label":       "Post Libre · Listicle / Top N",
        "description": "Genera el post 2/3: una lista numerada (Top 5, Los 7 mejores…). Alta viralidad y buen rendimiento en snippets de Google.",
        "vars":        ["{title}"],
        "used_in":     "Modo Libre · Post 2 de 3",
    },
    "PROMPT_HOWTO": {
        "label":       "Post Libre · Guía Paso a Paso",
        "description": "Genera el post 3/3: una guía práctica con pasos concretos. Alta intención de búsqueda y conversión.",
        "vars":        ["{title}"],
        "used_in":     "Modo Libre · Post 3 de 3",
    },
    "PROMPT_COMPARATIVA": {
        "label":       "Post Amazon · Comparativa",
        "description": "Genera el post 1/3 de Amazon: comparativa con tabla pros/contras y CTAs de afiliado. Ideal para búsquedas 'mejor X vs Y'.",
        "vars":        ["{title}", "{affiliate_url}"],
        "used_in":     "Modo Amazon · Post 1 de 3",
    },
    "PROMPT_GUIA": {
        "label":       "Post Amazon · Guía de Beneficios",
        "description": "Genera el post 2/3 de Amazon: guía educativa que presenta el producto de forma no invasiva, destacando beneficios y casos de uso.",
        "vars":        ["{title}", "{affiliate_url}"],
        "used_in":     "Modo Amazon · Post 2 de 3",
    },
    "PROMPT_RESENA_SEO": {
        "label":       "Post Amazon · Reseña SEO",
        "description": "Genera el post 3/3 de Amazon: reseña detallada con puntuación, pros/contras y CTA final. Muy efectiva para búsquedas 'review de X'.",
        "vars":        ["{title}", "{affiliate_url}"],
        "used_in":     "Modo Amazon · Post 3 de 3",
    },
    "_REVIEWER_Médico": {
        "label":       "Bloque Revisor · Médico",
        "description": "Se inserta al final del contexto base solo cuando el revisor seleccionado es Médico. Añade rigor clínico, citación de evidencias y nota de descargo médico.",
        "vars":        [],
        "used_in":     "Todos los posts — solo si revisor = Médico",
    },
    "_REVIEWER_Psicólogo": {
        "label":       "Bloque Revisor · Psicólogo",
        "description": "Se inserta cuando el revisor es Psicólogo. Añade lenguaje no estigmatizante, referencias a corrientes psicológicas y nota de descargo en salud mental.",
        "vars":        [],
        "used_in":     "Todos los posts — solo si revisor = Psicólogo",
    },
    "_REVIEWER_Editor": {
        "label":       "Bloque Revisor · Editor",
        "description": "Se inserta cuando el revisor es Editor. Enfoca a Gemini en claridad narrativa, fluidez y pirámide invertida, sin restricciones clínicas.",
        "vars":        [],
        "used_in":     "Todos los posts — solo si revisor = Editor",
    },
    "_VOICE_BLOCK": {
        "label":       "Bloque Voz del Autor",
        "description": "Se inserta si el usuario tiene configurado su perfil de voz. Replica el estilo personal, tono y vocabulario del autor en el texto generado por Gemini.",
        "vars":        ["{style}", "{tone}", "{vocabulary}", "{examples}", "{sample}"],
        "used_in":     "Todos los posts — solo si el usuario tiene perfil de voz",
    },
    "_FOCUS_BLOCK": {
        "label":       "Bloque Enfoque del Artículo",
        "description": "Se inserta solo cuando el usuario rellena el campo 'Enfoque' en el formulario. Fuerza a Gemini a ceñirse a ese ángulo sin desviarse.",
        "vars":        ["{focus}"],
        "used_in":     "Todos los posts — solo si se indica un enfoque",
    },
}

# Archivo donde se guardan los overrides editados por el admin
_PROMPTS_OVERRIDE_FILE = Path("prompts_override.json")


def _load_prompt_overrides() -> dict:
    if _PROMPTS_OVERRIDE_FILE.exists():
        try:
            return json.loads(_PROMPTS_OVERRIDE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_prompt_overrides(data: dict) -> None:
    _PROMPTS_OVERRIDE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _get_all_prompts() -> list[dict]:
    """Devuelve todos los prompts con su valor actual (override o default) y metadatos."""
    import core.prompt_templates as pt
    overrides = _load_prompt_overrides()
    result = []
    for key, meta in _PROMPT_META.items():
        if key.startswith("_REVIEWER_"):
            lang = key.replace("_REVIEWER_", "")
            default_val = pt._REVIEWER_BLOCKS.get(lang, "")
        elif key == "_VOICE_BLOCK":
            default_val = pt._VOICE_BLOCK
        elif key == "_FOCUS_BLOCK":
            default_val = pt._FOCUS_BLOCK
        else:
            default_val = getattr(pt, key, "")
        current_val = overrides.get(key, default_val)
        result.append({
            "key":         key,
            "label":       meta["label"],
            "description": meta["description"],
            "vars":        meta["vars"],
            "used_in":     meta["used_in"],
            "default":     default_val,
            "current":     current_val,
            "modified":    key in overrides,
        })
    return result


@app.route("/admin/prompts")
@admin_required
def prompts_page():
    return render_template("prompts.html", prompts=_get_all_prompts())


@app.get("/api/admin/prompts")
@admin_required
def api_prompts_get():
    return jsonify({"ok": True, "prompts": _get_all_prompts()})


@app.put("/api/admin/prompts/<key>")
@admin_required
def api_prompts_update(key):
    if key not in _PROMPT_META:
        return jsonify({"error": "Clave de prompt no válida"}), 400
    data = request.get_json() or {}
    new_text = data.get("value", "")
    overrides = _load_prompt_overrides()
    overrides[key] = new_text
    _save_prompt_overrides(overrides)
    # Aplicar en caliente al módulo cargado
    _apply_prompt_override(key, new_text)
    return jsonify({"ok": True})


@app.delete("/api/admin/prompts/<key>")
@admin_required
def api_prompts_reset(key):
    if key not in _PROMPT_META:
        return jsonify({"error": "Clave de prompt no válida"}), 400
    overrides = _load_prompt_overrides()
    overrides.pop(key, None)
    _save_prompt_overrides(overrides)
    # Restaurar el valor del módulo desde disco
    import importlib, core.prompt_templates as pt
    importlib.reload(pt)
    return jsonify({"ok": True})


def _apply_prompt_override(key: str, value: str) -> None:
    """Aplica un override directamente al módulo en memoria."""
    import core.prompt_templates as pt
    if key.startswith("_REVIEWER_"):
        lang = key.replace("_REVIEWER_", "")
        pt._REVIEWER_BLOCKS[lang] = value
    elif key == "_VOICE_BLOCK":
        pt._VOICE_BLOCK = value
    elif key == "_FOCUS_BLOCK":
        pt._FOCUS_BLOCK = value
    else:
        setattr(pt, key, value)


def _apply_all_overrides_on_startup() -> None:
    """Aplica al módulo todos los overrides guardados al iniciar el servidor."""
    for key, value in _load_prompt_overrides().items():
        try:
            _apply_prompt_override(key, value)
        except Exception:
            pass


_apply_all_overrides_on_startup()


# ==================================================================================
# API - TEMA
# ==================================================================================

@app.put("/api/tema")
@login_required
def api_tema_update():
    """Guarda la preferencia de tema (dark/light) del usuario."""
    data = request.get_json() or {}
    theme = data.get("theme", "dark")
    if theme not in ("dark", "light"):
        return jsonify({"error": "Tema no válido"}), 400
    username = session.get("username")
    if username:
        users = load_users()
        if username in users:
            users[username]["theme"] = theme
            save_users(users)
    return jsonify({"ok": True})


# ==================================================================================
# PERFIL DE VOZ DEL USUARIO
# ==================================================================================

@app.route("/perfil")
@login_required
def perfil_page():
    user = get_current_user()
    return render_template("perfil.html", voice=(user.get("voice_profile") or {}))


@app.put("/api/perfil")
@login_required
def api_perfil_update():
    """Guarda el perfil de voz del usuario autenticado."""
    data = request.get_json() or {}
    username = session.get("username")
    if not username:
        return jsonify({"error": "No autenticado"}), 401

    allowed_keys = {"style", "tone", "vocabulary", "examples", "sample", "compiled"}
    voice_data = {k: str(data.get(k, "")).strip() for k in allowed_keys}

    users = load_users()
    if username not in users:
        return jsonify({"error": "Usuario no encontrado"}), 404

    users[username]["voice_profile"] = voice_data
    save_users(users)
    return jsonify({"ok": True})


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
    _current_voice    = (get_current_user() or {}).get("voice_profile", {}).get("compiled", "")

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
            drafts = orchestrator.run(user_input, mode=gen_mode, focus=focus, reviewer=reviewer, username=_current_username, badge_html=_current_badge, voice_profile=_current_voice)
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
            try:
                msg = q.get(timeout=20)
            except queue.Empty:
                # Heartbeat para evitar que Gunicorn mate al worker por inactividad
                yield ": keepalive\n\n"
                continue
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
        # Filtrar por rol de usuario (las 4 categorías de bienestar son universales para todos los roles)
        user    = get_current_user()
        allowed = user.get("topic_categories") if user else None
        if allowed and isinstance(topics, dict):
            _universal = ["salud_nutricion", "bienestar_emocional", "familia_educacion", "estetica_autocuidado"]
            _allowed_full = list(allowed) + _universal
            topics = {k: v for k, v in topics.items() if k == "fecha" or k in _allowed_full}
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
    _current_voice    = (get_current_user() or {}).get("voice_profile", {}).get("compiled", "")

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
                        voice_profile=_current_voice,
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


@app.post("/api/borrador/<path:filename>/regenerar")
@login_required
def api_borrador_regenerar(filename):
    """Regenera el contenido de un borrador llamando de nuevo a Gemini."""
    global _token_manager
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "Borrador no encontrado"}), 404

    draft_data = json.loads(safe.read_text(encoding="utf-8"))
    mock_mode, _ = _get_modes()

    # Normalizar post_type (puede venir como "PostType.OPINION" o "opinion")
    raw_pt    = draft_data.get("post_type", "")
    post_type = raw_pt.lower().replace("posttype.", "").replace(" ", "_")

    title         = draft_data.get("title", "")
    focus_keyword = draft_data.get("focus_keyword", "")
    affiliate_url = draft_data.get("affiliate_url") or None

    # Determinar prompt_map según modo (amazon si hay affiliate_url)
    try:
        from core.prompt_templates import PROMPT_MAP, PROMPT_MAP_LIBRE
        prompt_map = PROMPT_MAP if affiliate_url else PROMPT_MAP_LIBRE
    except Exception as e:
        return jsonify({"error": f"Error cargando prompts: {e}"}), 500

    if post_type not in prompt_map:
        return jsonify({"error": f"Tipo de post '{post_type}' no reconocido para regenerar"}), 400

    # Construir focus forzando el mismo título
    focus = (
        f'El título del artículo DEBE SER EXACTAMENTE: "{title}". '
        f'Desarrolla el contenido para que encaje perfectamente con ese título. '
        f'Escribe un artículo COMPLETO con mínimo 600 palabras y al menos 4 secciones H2.'
    )
    if focus_keyword:
        focus += f' La focus_keyword es: "{focus_keyword}".'

    try:
        from core.gemini_client import GeminiClient
        from core.orchestrator import check_content_quality
        tm     = get_token_manager()
        gemini = GeminiClient(token_manager=tm, mock_mode=mock_mode)

        # Hasta 3 intentos para obtener contenido de calidad
        raw = None
        last_reason = ""
        for attempt in range(3):
            raw = gemini.generate_draft(
                post_type     = post_type,
                topic         = title,
                affiliate_url = affiliate_url,
                prompt_map    = prompt_map,
                focus         = focus if attempt == 0 else (
                    focus + f' (intento {attempt + 1}/3: el anterior fue {last_reason})'
                ),
            )
            ok, last_reason = check_content_quality(raw.get("content", ""))
            if ok:
                break

        _token_manager = gemini.token_manager

        # Actualizar solo el contenido y metadatos SEO; conservar imágenes, categorías, etc.
        draft_data["content"]          = raw.get("content", draft_data["content"])
        draft_data["title"]            = raw.get("title", title)
        draft_data["meta_description"] = raw.get("meta_description", draft_data.get("meta_description", ""))
        draft_data["focus_keyword"]    = raw.get("focus_keyword", focus_keyword)

        safe.write_text(json.dumps(draft_data, ensure_ascii=False, indent=2), encoding="utf-8")

        return jsonify({
            "ok":              True,
            "title":           draft_data["title"],
            "content":         draft_data["content"],
            "meta_description": draft_data["meta_description"],
            "focus_keyword":   draft_data["focus_keyword"],
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    force_update   = bool(body.get("force_update", False))
    force_create   = bool(body.get("force_create", False))

    # Bloquear re-publicación si ya fue publicado, salvo que el usuario lo fuerce
    if draft_data.get("wp_post_id") and not simulate_wp and not force_update and not force_create:
        return jsonify({
            "error": f"⚠️ Este borrador ya fue publicado en WordPress (ID: {draft_data['wp_post_id']}). No se puede publicar dos veces."
        }), 409
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
            # Insertar el badge AL FINAL del artículo
            post_draft.content = post_draft.content + "\n\n" + _badge_html

        existing_wp_id = draft_data.get("wp_post_id")

        # force_update → actualizar post existente en WP (sin crear duplicado)
        if force_update and existing_wp_id and not simulate_wp:
            wid = wp.update_draft(int(existing_wp_id), post_draft)
            draft_data["status"] = "published"
            safe.write_text(json.dumps(draft_data, ensure_ascii=False, indent=2), encoding="utf-8")
            wp_url = os.getenv("WP_BASE_URL", "").rstrip("/")
            return jsonify({
                "ok":         True,
                "wp_post_id": wid,
                "updated":    True,
                "edit_url":   f"{wp_url}/wp-admin/post.php?post={wid}&action=edit",
            })

        # force_create → ignorar wp_post_id anterior y crear un post nuevo en WP
        # (útil cuando el post fue eliminado de WP y hay que volver a subirlo)
        wid = wp.create_draft(post_draft)
        if not simulate_wp:
            # Conservar el borrador actualizado con el ID de WP (no eliminar)
            draft_data["wp_post_id"] = wid
            draft_data["status"]     = "published"
            safe.write_text(json.dumps(draft_data, ensure_ascii=False, indent=2), encoding="utf-8")
            wp_url = os.getenv("WP_BASE_URL", "").rstrip("/")
            return jsonify({
                "ok":        True,
                "wp_post_id": wid,
                "edit_url":   f"{wp_url}/wp-admin/post.php?post={wid}&action=edit",
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


@app.post("/api/borrador/<path:filename>/social")
@login_required
def api_borrador_social(filename):
    """Genera contenido optimizado para redes sociales usando Gemini."""
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    body     = request.get_json() or {}
    platform = body.get("platform", "").lower()
    if platform not in ("tiktok", "instagram", "facebook"):
        return jsonify({"error": "Plataforma no válida. Usa: tiktok, instagram, facebook"}), 400
    draft_data = json.loads(safe.read_text(encoding="utf-8"))
    title      = draft_data.get("title", "")
    focus_kw   = draft_data.get("focus_keyword", "")
    # Extracto del contenido (máx. 600 palabras) para no gastar demasiados tokens
    plain_content = " ".join(
        w for w in (draft_data.get("content", "")
                    .replace("<", " <").replace(">", "> ")
                    .split())
        if not w.startswith("<") and not w.startswith(">")
    )[:3000]
    platform_prompts = {
        "tiktok": (
            f"Escribe un guión viral para TikTok basado en este artículo de salud/bienestar.\n"
            f"Título del artículo: \"{title}\"\n"
            f"Palabra clave: \"{focus_kw}\"\n"
            f"Resumen del contenido: {plain_content}\n\n"
            f"El guión debe:\n"
            f"- Durar entre 45-60 segundos (aprox. 130-160 palabras habladas).\n"
            f"- Empezar con un GANCHO impactante (primeros 3 segundos) que genere curiosidad.\n"
            f"- Usar lenguaje directo y cercano (tuteo).\n"
            f"- Incluir indicaciones de escena entre corchetes: [PAUSA], [MOSTRAR TEXTO], [ZOOM IN], etc.\n"
            f"- Terminar con una llamada a la acción clara: comentar, seguir o guardar el vídeo.\n"
            f"- Sugerir 5 hashtags relevantes al final.\n"
            f"Escribe el guión completo en español de España."
        ),
        "instagram": (
            f"Escribe el copy completo para un post de Instagram basado en este artículo.\n"
            f"Título del artículo: \"{title}\"\n"
            f"Palabra clave: \"{focus_kw}\"\n"
            f"Resumen del contenido: {plain_content}\n\n"
            f"El copy debe incluir:\n"
            f"1. PRIMERA LÍNEA gancho (máx. 20 palabras, sin emojis al inicio).\n"
            f"2. Cuerpo: 3-5 párrafos cortos con insights del artículo, usando emojis estratégicos.\n"
            f"3. Llamada a la acción.\n"
            f"4. Bloque de 20-25 hashtags relevantes (mezcla de populares y nicho), separados por espacios.\n"
            f"5. Sugerencia de descripción para la imagen/carrusel (1-2 frases).\n"
            f"Escribe en español de España, tono empático y motivador."
        ),
        "facebook": (
            f"Escribe un post completo para Facebook basado en este artículo de salud/bienestar.\n"
            f"Título del artículo: \"{title}\"\n"
            f"Palabra clave: \"{focus_kw}\"\n"
            f"Resumen del contenido: {plain_content}\n\n"
            f"El post de Facebook debe:\n"
            f"- Tener entre 150-300 palabras (formato largo, Facebook lo permite).\n"
            f"- Empezar con una pregunta o dato sorprendente para generar interacción.\n"
            f"- Desarrollar los puntos clave del artículo de forma conversacional.\n"
            f"- Invitar a la comunidad a compartir su experiencia en los comentarios.\n"
            f"- Incluir 3-5 emojis distribuidos naturalmente.\n"
            f"- Terminar con el enlace al artículo (deja el placeholder: [URL DEL ARTÍCULO]).\n"
            f"- Añadir 5-8 hashtags relevantes al final.\n"
            f"Escribe en español de España, tono cálido y comunitario."
        ),
    }
    mock_mode, _ = _get_modes()
    try:
        from core.gemini_client import GeminiClient
        tm     = get_token_manager()
        gemini = GeminiClient(token_manager=tm, mock_mode=mock_mode)
        result = gemini.call_raw(platform_prompts[platform])
        return jsonify({"ok": True, "platform": platform, "content": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
                    draft_path = (DRAFTS_DIR / Path(df).name) if df else None
                    e["draft_exists"] = bool(draft_path) and draft_path.exists()
                    # Read status from the actual draft file
                    e["draft_status"]  = "draft"
                    e["draft_wp_id"]   = None
                    if e["draft_exists"]:
                        try:
                            _d = json.loads(draft_path.read_text(encoding="utf-8"))
                            e["draft_status"] = _d.get("status", "draft")
                            e["draft_wp_id"]  = _d.get("wp_post_id")
                        except Exception:
                            pass
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
# CREADOR DE CONTENIDO PARA REDES SOCIALES
# ==================================================================================
SOCIAL_DIR = Path("social_drafts")
SOCIAL_DIR.mkdir(parents=True, exist_ok=True)


def _safe_social_path(filename: str) -> Path | None:
    base = SOCIAL_DIR.resolve()
    safe = (SOCIAL_DIR / Path(filename).name).resolve()
    if not str(safe).startswith(str(base)):
        return None
    return safe


@app.route("/social")
@login_required
def social_page():
    return render_template("social.html")


@app.get("/api/social/historial")
@login_required
def api_social_historial():
    """Devuelve la lista de borradores de contenido social guardados."""
    files = []
    for f in sorted(SOCIAL_DIR.glob("social_*.json"), reverse=True):
        try:
            meta = json.loads(f.read_text(encoding="utf-8")).get("meta", {})
            files.append({
                "filename": f.name,
                "topic":    meta.get("topic", f.stem),
                "mode":     meta.get("mode", ""),
                "created_at": meta.get("created_at", ""),
            })
        except Exception:
            pass
    return jsonify(files[:50])


@app.get("/api/social/historial/<filename>")
@login_required
def api_social_borrador(filename):
    """Devuelve un borrador social guardado."""
    path = _safe_social_path(filename)
    if not path or not path.exists():
        return jsonify({"error": "No encontrado"}), 404
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


@app.post("/api/social/generar")
@login_required
def api_social_generar():
    """Genera contenido optimizado para TikTok, Instagram y Facebook usando Gemini."""
    data = request.get_json(force=True)
    mode      = data.get("mode", "scratch")   # "borrador" | "scratch"
    topic     = (data.get("input") or "").strip()
    platforms = data.get("platforms", ["tiktok", "instagram", "facebook"])

    if not topic:
        return jsonify({"error": "Se requiere un tema o borrador."}), 400
    if len(topic) > 4000:
        return jsonify({"error": "El texto de entrada es demasiado largo (máx 4000 caracteres)."}), 400

    if mode == "borrador":
        input_instruction = (
            "Analiza este borrador de blog y extrae los puntos más valiosos "
            "para adaptarlos a contenido viral en redes sociales:\n\nBORRADOR:\n" + topic
        )
    else:
        input_instruction = (
            "Crea contenido original para redes sociales buscando el ángulo "
            "más comercial, práctico y viral:\n\nTEMA: " + topic
        )

    prompt = f"""Eres un experto en marketing digital, producción audiovisual y estrategia de contenido viral.

{input_instruction}

Genera el siguiente contenido en formato JSON estrictamente válido. SOLO responde con el JSON, sin texto extra ni bloques markdown.

ESTRUCTURA REQUERIDA (completa todos los campos, sin omitir ninguno):
{{
  "tiktok": {{
    "hooks": [
      {{"tipo": "curiosidad", "texto": "Frase exacta dicha a cámara (máx 10 palabras)", "descripcion_visual": "Cómo grabarlo: plano, movimiento de cámara, expresión facial"}},
      {{"tipo": "fomo",       "texto": "Frase exacta dicha a cámara (máx 10 palabras)", "descripcion_visual": "Cómo grabarlo: plano, movimiento de cámara, expresión facial"}},
      {{"tipo": "beneficio",  "texto": "Frase exacta dicha a cámara (máx 10 palabras)", "descripcion_visual": "Cómo grabarlo: plano, movimiento de cámara, expresión facial"}}
    ],
    "guion": [
      {{
        "escena": 1,
        "segundos": "0-3",
        "tipo": "gancho",
        "toma": "Descripción técnica del plano: ángulo, encuadre, distancia a cámara",
        "dialogo": "TEXTO EXACTO a decir en voz alta, sin paréntesis ni indicaciones, como si lo leyeras",
        "texto_pantalla": "Frase corta que aparece como texto en pantalla (bold, contraste alto)",
        "nota_edicion": "Corte rápido / zoom automático / sonido de impacto al inicio"
      }},
      {{
        "escena": 2,
        "segundos": "3-15",
        "tipo": "retencion",
        "toma": "Descripción técnica del plano",
        "dialogo": "TEXTO EXACTO: presenta el problema o contexto. Genera tensión. Usa pausas dramáticas.",
        "texto_pantalla": "Texto refuerzo en pantalla",
        "nota_edicion": "Jump cuts cada 2-3 segundos. Música al 20%."
      }},
      {{
        "escena": 3,
        "segundos": "15-30",
        "tipo": "valor_1",
        "toma": "Descripción técnica del plano (puede ser b-roll, overhead, pantalla grabada)",
        "dialogo": "TEXTO EXACTO: explica el primer punto de valor con ejemplo concreto",
        "texto_pantalla": "PUNTO 1: [resumen en 5 palabras]",
        "nota_edicion": "Texto aparece al inicio de la escena. Corte a b-roll si hay demo."
      }},
      {{
        "escena": 4,
        "segundos": "30-45",
        "tipo": "valor_2",
        "toma": "Descripción técnica del plano",
        "dialogo": "TEXTO EXACTO: explica el segundo punto con dato o error común a evitar",
        "texto_pantalla": "PUNTO 2: [resumen en 5 palabras]",
        "nota_edicion": "Zoom sutil en la palabra clave. Subtítulos activados."
      }},
      {{
        "escena": 5,
        "segundos": "45-55",
        "tipo": "resultado",
        "toma": "Plano medio frontal, energía más alta, gestos expresivos",
        "dialogo": "TEXTO EXACTO: resume el beneficio clave y conecta con la vida del espectador",
        "texto_pantalla": "EL RESULTADO: [frase impactante]",
        "nota_edicion": "Música sube ligeramente. Ritmo de edición más rápido."
      }},
      {{
        "escena": 6,
        "segundos": "55-60",
        "tipo": "cta",
        "toma": "Plano medio, señalar pantalla o apuntar hacia abajo con el dedo",
        "dialogo": "TEXTO EXACTO del CTA: específico, que invite a guardar o comentar con una razón concreta",
        "texto_pantalla": "GUARDA ESTO 👇 / COMENTA SI TE PASÓ",
        "nota_edicion": "Pantalla congela 0.5s en último frame. Audio fade out."
      }}
    ],
    "checklist": {{
      "grabacion": [
        {{"item": "...", "detalle": "Descripción específica de cómo ejecutar este paso"}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}}
      ],
      "recursos": [
        {{"item": "...", "tipo": "prop/iluminacion/audio/locacion"}},
        {{"item": "...", "tipo": "..."}},
        {{"item": "...", "tipo": "..."}}
      ],
      "edicion": [
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}}
      ]
    }}
  }},
  "instagram": {{
    "slides": [
      {{"numero": 1, "tipo": "portada",    "titulo": "Titular magnético (máx 8 palabras)", "subtitulo": "Subtítulo complementario", "cuerpo": "", "estetica": "Paleta de colores + tipografía + elemento visual principal"}},
      {{"numero": 2, "tipo": "problema",   "titulo": "El problema real", "subtitulo": "", "cuerpo": "2-3 oraciones que describan el problema con el que el lector se identifica", "estetica": "..."}},
      {{"numero": 3, "tipo": "desarrollo", "titulo": "Paso / Consejo 1", "subtitulo": "", "cuerpo": "Explicación clara y accionable del primer punto. Una idea, un slide.", "estetica": "..."}},
      {{"numero": 4, "tipo": "desarrollo", "titulo": "Paso / Consejo 2", "subtitulo": "", "cuerpo": "Segundo punto con ejemplo o dato concreto.", "estetica": "..."}},
      {{"numero": 5, "tipo": "desarrollo", "titulo": "Paso / Consejo 3", "subtitulo": "", "cuerpo": "Tercer punto. Si aplica, incluye una mini-comparación o error a evitar.", "estetica": "..."}},
      {{"numero": 6, "tipo": "desarrollo", "titulo": "Paso / Consejo 4", "subtitulo": "", "cuerpo": "Cuarto punto práctico y diferenciador.", "estetica": "..."}},
      {{"numero": 7, "tipo": "resumen",    "titulo": "Quick Win ⚡", "subtitulo": "", "cuerpo": "Una sola acción que el lector puede ejecutar hoy mismo para ver resultados rápidos.", "estetica": "Fondo vibrante/diferenciado para que destaque del resto del carrusel"}},
      {{"numero": 8, "tipo": "cta",        "titulo": "¿Te fue útil?", "subtitulo": "", "cuerpo": "Instrucción clara: GUARDA + COMPARTE + COMENTA. Incluye qué comentar exactamente.", "estetica": "Color de marca, CTA visual bold"}}
    ],
    "hashtags": ["...", "...", "...", "...", "...", "...", "...", "...", "...", "..."],
    "checklist": {{
      "diseno": [
        {{"item": "...", "detalle": "Indicación visual específica de diseño o Canva para este slide"}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}}
      ],
      "recursos": [
        {{"item": "...", "tipo": "imagen/icono/color/tipografia"}},
        {{"item": "...", "tipo": "..."}},
        {{"item": "...", "tipo": "..."}}
      ],
      "publicacion": [
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}}
      ]
    }}
  }},
  "facebook": {{
    "post": "Post completo listo para copiar-pegar (200-350 palabras). Tono cercano, personal, narrativo. Usa saltos de línea para facilitar la lectura. Incluye emojis estratégicos. NO incluyas la pregunta de engagement aquí.",
    "pregunta_engagement": "Pregunta abierta específica al final para activar comentarios. Que sea fácil de responder.",
    "checklist": {{
      "contenido": [
        {{"item": "...", "detalle": "Verificación específica del post texto"}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}}
      ],
      "recursos": [
        {{"item": "...", "tipo": "imagen/video/gif/enlace"}},
        {{"item": "...", "tipo": "..."}}
      ],
      "publicacion": [
        {{"item": "...", "detalle": "Mejor horario, configuración de privacidad, etiquetas"}},
        {{"item": "...", "detalle": "..."}},
        {{"item": "...", "detalle": "..."}}
      ]
    }}
  }}
}}

REGLAS DE CALIDAD (obligatorias):
1. TikTok guion: el campo "dialogo" de cada escena debe ser TEXTO LITERAL para leer en voz alta, no descripciones entre paréntesis.
2. TikTok guion: las 6 escenas cubren el video completo de 60 segundos sin huecos.
3. Instagram slides: 8 slides exactos en el orden dado (portada > problema > x4 desarrollo > resumen > cta).
4. Facebook: el post NO termina con la pregunta (esa va en "pregunta_engagement" separada).
5. Todos los checklists: items concretos y ejecutables hoy, no genéricos.
6. Hashtags de Instagram: mezcla de 4 alto volumen (>500k) + 4 nicho específico (<100k) + 2 de marca/tendencia.
7. Todo en español neutro profesional (sin regionalismos).
8. JSON perfectamente válido: sin comas finales, sin saltos de línea dentro de strings (usar \\n si se necesita).
"""

    mock_mode, _ = _get_modes()

    if mock_mode:
        import time as _time
        _time.sleep(0.8)
        result = _social_mock_response(topic)
    else:
        try:
            tm = get_token_manager()
            if not tm or not tm.valid_keys_count:
                return jsonify({"error": "No hay API keys de Gemini configuradas."}), 503

            import google.generativeai as genai
            from core.gemini_client import _apply_proxy_from_env, _install_dns_patch
            _apply_proxy_from_env()
            _install_dns_patch()

            api_key = tm.get_active_key()
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                generation_config=genai.GenerationConfig(
                    temperature=0.8,
                    max_output_tokens=8192,
                ),
            )
            response = model.generate_content(prompt)
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
            result = json.loads(raw)

        except json.JSONDecodeError as e:
            return jsonify({"error": f"Gemini devolvió JSON inválido: {e}"}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Guardar siempre el resultado con metadatos
    meta = {
        "topic":      topic[:120],
        "mode":       mode,
        "mock":       mock_mode,
        "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms":  platforms,
    }
    result["meta"] = meta
    slug = re.sub(r"[^a-z0-9]+", "_", topic[:40].lower()).strip("_")
    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"social_{ts}_{slug}.json"
    try:
        (SOCIAL_DIR / filename).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass  # no bloquear si falla escritura

    return jsonify({"ok": True, "data": result, "saved_as": filename})


def _social_mock_response(topic: str) -> dict:
    """Respuesta mock detallada para el creador de contenido social."""
    t = topic[:60] if len(topic) > 60 else topic
    ts = topic[:30] if len(topic) > 30 else topic  # slug corto
    return {
        "tiktok": {
            "hooks": [
                {
                    "tipo": "curiosidad",
                    "texto": f"Lo que nadie te dice sobre {ts}...",
                    "descripcion_visual": "Zoom dramático de lejos a primer plano en 1 segundo. Expresión de sorpresa. Fondo liso o difuminado. Texto animado apareciendo letra a letra."
                },
                {
                    "tipo": "fomo",
                    "texto": f"El 90% hace {ts} mal. ¿Eres del 10%?",
                    "descripcion_visual": "Corte rápido: plano del error (tachado rojo en pantalla) → plano del método correcto (check verde). Energía alta, hablar rápido."
                },
                {
                    "tipo": "beneficio",
                    "texto": f"En 60 segundos dominas {ts}. Empieza.",
                    "descripcion_visual": "Temporizador en esquina superior. Plano medio frontal, cámara a nivel de ojos, ring light visible en pupilas. Señalar pantalla al decir 'empieza'."
                }
            ],
            "guion": [
                {
                    "escena": 1,
                    "segundos": "0-3",
                    "tipo": "gancho",
                    "toma": "Plano medio frontal. Cámara a nivel de ojos. Fondo liso o bokeh. Ring light activada. Grabación vertical 9:16.",
                    "dialogo": f"Para. Lo que nadie te explica sobre {t} es esto.",
                    "texto_pantalla": f"🚨 {ts.upper()}: EL ERROR #1",
                    "nota_edicion": "Zoom automático de 0.9x a 1.1x en el primer segundo. Sonido de 'whoosh' o campana al inicio. Corte seco al siguiente clip."
                },
                {
                    "escena": 2,
                    "segundos": "3-15",
                    "tipo": "retencion",
                    "toma": "Plano medio frontal. Mismo encuadre. Empezar a hablar más rápido. Gestos con manos activos.",
                    "dialogo": f"La mayoría se acerca a {t} sin saber esto: hay un paso que todo el mundo se salta. Y ese paso es exactamente el que hace la diferencia entre resultados reales y perder el tiempo. Te lo explico en 3 puntos.",
                    "texto_pantalla": "3 puntos que cambian todo 👇",
                    "nota_edicion": "Jump cut cada 2-3 segundos. Subtítulos automáticos activados al 100%. Música de fondo al 15%."
                },
                {
                    "escena": 3,
                    "segundos": "15-30",
                    "tipo": "valor_1",
                    "toma": "Plano medio o b-roll de manos mostrando el proceso / pantalla / objeto. Si es digital: grabación de pantalla con recuadro de webcam pequeño en esquina.",
                    "dialogo": f"Punto uno. El fundamento. Con {t}, lo primero que necesitas entender es EL POR QUÉ, no el cómo. Cuando entiendes por qué funciona, todo lo demás tiene sentido.",
                    "texto_pantalla": "PUNTO 1: Entiende el POR QUÉ primero",
                    "nota_edicion": "Texto aparece en pantalla 0.5s después de que empieces a hablar. Corte a b-roll si estás mostrando algo. Resalta la palabra clave con color."
                },
                {
                    "escena": 4,
                    "segundos": "30-45",
                    "tipo": "valor_2",
                    "toma": "Plano medio frontal. Levantar dos dedos al decir 'punto dos'. Mantener contacto visual con cámara.",
                    "dialogo": f"Punto dos. El error costoso. El 80% falla en {t} porque intenta hacer todo a la vez. La clave es enfocarse en una sola variable primero, medirla, y solo entonces sumar la siguiente.",
                    "texto_pantalla": "PUNTO 2: Una variable a la vez ✅",
                    "nota_edicion": "Zoom sutil en la palabra 'error'. Añadir ícono de X rojo cuando digas el error y ✅ verde cuando digas la solución."
                },
                {
                    "escena": 5,
                    "segundos": "45-55",
                    "tipo": "resultado",
                    "toma": "Plano medio frontal. Energía más alta. Sonreír. Inclinarse ligeramente hacia la cámara al dar el dato clave.",
                    "dialogo": f"Y el resultado cuando aplicas esto correctamente: en lugar de frustrarte con {t}, empiezas a ver progreso en días, no en meses. Porque estás trabajando con el sistema, no contra él.",
                    "texto_pantalla": "RESULTADO: Progreso en días 🚀",
                    "nota_edicion": "Transición de entrada con fade rápido. Música puede subir ligeramente al 25%. Ritmo más dinámico."
                },
                {
                    "escena": 6,
                    "segundos": "55-60",
                    "tipo": "cta",
                    "toma": "Plano medio. Señalar hacia abajo con el dedo índice al decir 'guarda'. Mantener la energía alta hasta el último segundo.",
                    "dialogo": f"Guarda este video ahora para cuando empieces. Y cuéntame en comentarios: ¿habías escuchado esto antes de {ts}?",
                    "texto_pantalla": "💾 GUARDA ESTO · COMENTA ABAJO",
                    "nota_edicion": "Último frame congela 0.5 segundos. Fade out de audio. Añadir sticker de 'guardar' o flecha apuntando abajo."
                }
            ],
            "checklist": {
                "grabacion": [
                    {"item": "Encuadre y cámara", "detalle": "Grabar en vertical 1080x1920 (9:16). Cámara a nivel de ojos, no desde abajo. Cara centrada en el tercio superior del cuadro."},
                    {"item": "Iluminación", "detalle": "Ring light frontal o ventana al lado. Sin sombras duras en el rostro. El fondo debe estar más oscuro que el primer plano."},
                    {"item": "Audio", "detalle": "Usar micrófono de solapa o airpods. Grabar en habitación silenciosa. Hacer prueba de audio de 10 segundos antes de la toma definitiva."},
                    {"item": "Energía y ritmo", "detalle": "Hablar un 20% más rápido de lo normal. Pausas cortas (menos de 0.5s). Gestos con manos visibles en cuadro para dinamismo."},
                    {"item": "Múltiples tomas", "detalle": "Grabar el gancho (escena 1) al menos 5 veces y elegir la mejor. Las demás escenas con 2-3 tomas. Siempre dejar 1 segundo de silencio antes de hablar."}
                ],
                "recursos": [
                    {"item": "Ring light o fuente de luz difusa", "tipo": "iluminacion"},
                    {"item": "Trípode o soporte de teléfono", "tipo": "equipo"},
                    {"item": f"Props relacionados con {ts} (si el contenido es físico)", "tipo": "prop"},
                    {"item": "Fondo liso, pared clean o bookshelf ordenado", "tipo": "locacion"},
                    {"item": "Música de fondo desde TikTok Sound Library (trending)", "tipo": "audio"}
                ],
                "edicion": [
                    {"item": "Subtítulos automáticos", "detalle": "Activar en CapCut o TikTok. Fuente bold, color blanco con sombra negra, tamaño grande (al menos 16% del ancho)."},
                    {"item": "Textos en pantalla", "detalle": "Añadir el texto de cada escena antes del diálogo correspondiente. Fuente bold, contraste alto. Duración: toda la escena."},
                    {"item": "Música de fondo", "detalle": "Usar trending sound de la semana o lo-fi motivacional. Volumen: 15-20% (no debe competir con la voz)."},
                    {"item": "Cortes y ritmo", "detalle": "Jump cut cada 2-3 segundos en las escenas 2-4. Eliminar silencios mayores a 0.3 segundos. Velocidad de reproducción: 1.05x en toda la edición."}
                ]
            }
        },
        "instagram": {
            "slides": [
                {
                    "numero": 1,
                    "tipo": "portada",
                    "titulo": f"Todo lo que necesitas saber sobre {ts}",
                    "subtitulo": "Y nadie te lo había explicado así",
                    "cuerpo": "",
                    "estetica": "Fondo degradado morado-azul oscuro. Tipografía Montserrat Bold blanca. Icono o emoji grande centrado sobre el título. Nombre de usuario en esquina inferior."
                },
                {
                    "numero": 2,
                    "tipo": "problema",
                    "titulo": "El problema real",
                    "subtitulo": "",
                    "cuerpo": f"La mayoría de personas que trabajan con {t} cometen el mismo error: van directo a la acción sin entender el sistema. El resultado es frustración, tiempo perdido y la sensación de que 'esto no es para mí'.",
                    "estetica": "Fondo oscuro (#1a1a2e). Texto blanco. Icono ⚠️ en ámbar visible en esquina superior. Línea divisoria de color bajo el título."
                },
                {
                    "numero": 3,
                    "tipo": "desarrollo",
                    "titulo": "01 · Entiende el fundamento",
                    "subtitulo": "",
                    "cuerpo": f"Antes de cualquier técnica, necesitas entender POR QUÉ funciona {t}. El 'por qué' es tu mapa. Sin mapa, todas las rutas se ven iguales. Dedica 20 minutos a entender la lógica antes de ejecutar.",
                    "estetica": "Número '01' grande como watermark de fondo (color muy claro). Contenido en tarjeta blanca superpuesta. Punto de color en el número = color del tema."
                },
                {
                    "numero": 4,
                    "tipo": "desarrollo",
                    "titulo": "02 · El error más costoso",
                    "subtitulo": "",
                    "cuerpo": f"Intentar hacer todo a la vez con {t} es la trampa más común. Enfócate en UNA variable, mídela durante 7 días y solo entonces agrega la siguiente. La consistencia late a la intensidad.",
                    "estetica": "Número '02'. Icono de ❌ rojo pequeño junto al error y ✅ verde junto a la solución. Layout limpio, espacio en blanco generoso."
                },
                {
                    "numero": 5,
                    "tipo": "desarrollo",
                    "titulo": "03 · La técnica que funciona",
                    "subtitulo": "",
                    "cuerpo": f"Los mejores resultados con {t} vienen de ciclos cortos: aplica → mide → ajusta → repite. No de sesiones largas y esporádicas. 15 minutos diarios consistentes > 3 horas una vez a la semana.",
                    "estetica": "Número '03'. Flecha circular de retroalimentación como elemento gráfico. Colores consistentes con slides anteriores."
                },
                {
                    "numero": 6,
                    "tipo": "desarrollo",
                    "titulo": "04 · Cómo medir tu progreso",
                    "subtitulo": "",
                    "cuerpo": f"Sin métricas no hay progreso real. Define tu indicador de éxito con {t} ANTES de empezar. ¿Qué cambia cuando funciona? Anótalo. Revísalo cada semana. Ajusta lo que no mueve esa métrica.",
                    "estetica": "Número '04'. Gráfica de barra ascendente simple como elemento decorativo. Fondo consistente."
                },
                {
                    "numero": 7,
                    "tipo": "resumen",
                    "titulo": "⚡ Quick Win — Hazlo hoy",
                    "subtitulo": "",
                    "cuerpo": f"Aplica SOLO el punto 01 durante 7 días. Dedica 20 minutos a entender el fundamento de {t} sin ejecutar todavía. Los resultados llegan a quien construye sobre bases sólidas, no a quien corre sin dirección.",
                    "estetica": "Fondo amarillo vibrante (#FFD700) o verde energético. Tipografía negra bold. Este slide debe verse completamente diferente al resto para detener el scroll."
                },
                {
                    "numero": 8,
                    "tipo": "cta",
                    "titulo": "¿Te fue útil esto? 👇",
                    "subtitulo": "",
                    "cuerpo": "💾 GUARDA este carrusel (lo vas a necesitar después)\n📤 COMPÁRTELO con alguien que esté luchando con esto\n💬 COMENTA: ¿cuál de los 4 puntos te resonó más?",
                    "estetica": "Fondo del color de marca. Tres iconos grandes para cada acción (guardar/compartir/comentar). Nombre de usuario visible. Foto de perfil en esquina."
                }
            ],
            "hashtags": [
                f"#{ts.replace(' ', '').lower()}",
                "#aprendizaje",
                "#crecimientopersonal",
                "#productividad",
                f"#{ts.replace(' ', '').lower()}tips",
                "#contenidodevalor",
                "#desarrollopersonal",
                "#habitos",
                "#mentoring",
                "#viralenespanol"
            ],
            "checklist": {
                "diseno": [
                    {"item": "Paleta de colores consistente", "detalle": "Elegir 2-3 colores y mantenerlos en TODOS los slides. Usar Canva 'Brand Kit' para guardarlos. El slide 7 puede romper la paleta intencionalmente."},
                    {"item": "Tipografía unificada", "detalle": "Título: Montserrat Bold o Poppins Bold (mínimo 40pt). Cuerpo: Open Sans Regular o Lato (mínimo 24pt). Máximo 2 familias tipográficas."},
                    {"item": "Elementos numéricos en slides 3-6", "detalle": "Número grande (200pt+) como watermark de fondo en transparencia 15-20%. Ayuda a la navegación visual del carrusel."},
                    {"item": "Portada llamativa (slide 1)", "detalle": "Debe detener el scroll. Probar con degradado vibrante, imagen de fondo con overlay, o ilustración. A/B test con 2 versiones si es posible."}
                ],
                "recursos": [
                    {"item": "Plantilla base en Canva", "tipo": "diseno"},
                    {"item": f"Imagen o ilustración relacionada con {ts}", "tipo": "imagen"},
                    {"item": "Paleta de colores en HEX definida", "tipo": "color"},
                    {"item": "Logo o @usuario para portada y CTA", "tipo": "marca"}
                ],
                "publicacion": [
                    {"item": "Cargar slides en orden exacto", "detalle": "Verificar en vista previa antes de publicar. El slide 1 es portada, el 8 es CTA."},
                    {"item": "Escribir pie de foto con gancho", "detalle": "Primera línea = gancho que obligue a leer más. No empieces con 'Hoy quiero hablarte de...'."},
                    {"item": "Incluir hashtags en primer comentario", "detalle": "Publicar el post y en los primeros 60 segundos añadir los hashtags en un comentario para mantener el pie de foto limpio."}
                ]
            }
        },
        "facebook": {
            "post": f"Hoy quiero ser honesto sobre algo que nadie dice abiertamente respecto a {t}.\n\nCuando empecé, pensé que era cuestión de seguir pasos. Que si hacía lo que decían los expertos, los resultados vendrían solos. Spoiler: no fue así.\n\nLo que descubrí después de mucho tiempo (y varios errores costosos) es que hay 4 principios que nadie te enseña porque nadie los redujo a algo concreto:\n\n✅ Principio 1: El fundamento lo es todo. Si no entiendes POR QUÉ algo funciona, no puedes adaptarlo cuando cambian las condiciones.\n\n✅ Principio 2: La consistencia supera a la intensidad. 15 minutos diarios durante 30 días producen más resultado que 8 horas un domingo.\n\n✅ Principio 3: El error más caro no es equivocarse, es no saber QUÉ medir. Sin métricas claras, cualquier esfuerzo es ruido.\n\n✅ Principio 4: Menos variables, más control. Enfócate en una sola cosa a la vez. Mídela. Ajusta. Después suma la siguiente.\n\nSi estás empezando con {t} hoy, mi consejo es: aplica solo el principio 1 esta semana. Antes de ejecutar cualquier cosa, entiende el sistema.",
            "pregunta_engagement": f"¿Cuál de estos 4 principios te hubiera ayudado más cuando empezaste con {t}? ¿O hay algo que agregarías tú a la lista? 👇 Me interesa mucho leer tu respuesta.",
            "checklist": {
                "contenido": [
                    {"item": "Revisar primera línea del post", "detalle": "La primera oración debe generar curiosidad o identificación inmediata. Evitar empezar con 'Hoy quiero...'. Probar con 'Lo que nadie dice sobre...' o una afirmación polarizante."},
                    {"item": "Verificar longitud", "detalle": "Entre 200-350 palabras. Facebook muestra solo las primeras 3 líneas antes del 'Ver más'. Esas 3 líneas deben ser el gancho perfecto."},
                    {"item": "Emojis estratégicos", "detalle": "Usar 1 emoji por párrafo como máximo. Antes de cada punto de la lista (✅, 🔑, ⚡). No abusar o perderá credibilidad."}
                ],
                "recursos": [
                    {"item": f"Imagen cuadrada 1:1 o 4:5 relacionada con {ts}", "tipo": "imagen"},
                    {"item": "Primera lámina del carrusel de Instagram (reutilizable)", "tipo": "imagen"},
                    {"item": "Video corto de TikTok (cross-posting si aplica)", "tipo": "video"}
                ],
                "publicacion": [
                    {"item": "Horario óptimo", "detalle": "Publicar entre 7-9am, 12-1pm o 7-9pm hora local. Martes, miércoles y jueves tienen mayor engagement promedio en Facebook."},
                    {"item": "Responder comentarios en la primera hora", "detalle": "El algoritmo de Facebook prioriza posts con actividad en los primeros 60 minutos. Responder todos los comentarios aunque sea con un '¡Gracias! ¿Y tú qué harías?'"},
                    {"item": "No añadir enlace externo en el texto", "detalle": "Facebook reduce el alcance de posts con URLs. Si necesitas compartir un enlace, ponlo en el primer comentario, no en el cuerpo del post."}
                ]
            }
        }
    }


# ==================================================================================
# EDITOR DE IMAGENES
# ==================================================================================

@app.route("/editor")
@login_required
def editor_page():
    return render_template("editor.html")


@app.post("/api/editor/quitar-fondo")
@login_required
def api_editor_quitar_fondo():
    """Elimina el fondo de una imagen usando rembg."""
    import base64
    from io import BytesIO
    try:
        from rembg import remove as rembg_remove
    except ImportError as ie:
        import sys
        return jsonify({"error": f"rembg no instalado ({sys.executable}): {ie}"}), 500

    data = request.get_json(force=True)
    image_b64 = data.get("image", "")

    try:
        header, b64data = image_b64.split(",", 1)
        img_bytes = base64.b64decode(b64data)
    except Exception:
        return jsonify({"error": "Imagen inválida"}), 400

    try:
        result_bytes = rembg_remove(img_bytes)
    except Exception as exc:
        return jsonify({"error": f"Error al procesar: {exc}"}), 500

    result_b64 = base64.b64encode(result_bytes).decode("ascii")
    return jsonify({
        "ok": True,
        "image": f"data:image/png;base64,{result_b64}",
    })


@app.post("/api/editor/guardar")
@login_required
def api_editor_guardar():
    """Guarda la imagen editada (JPEG, ≤2 MB, con marca de agua del servidor)."""
    import base64
    import platform
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont

    data = request.get_json(force=True)
    image_b64 = data.get("image", "")
    filename  = data.get("filename", "imagen").strip()

    # Validar filename
    filename = re.sub(r'[^a-zA-Z0-9_\-]', '_', filename)
    if not filename:
        return jsonify({"error": "Nombre de archivo inválido"}), 400

    # Decodificar base64
    try:
        header, b64data = image_b64.split(",", 1)
        img_bytes = base64.b64decode(b64data)
    except Exception:
        return jsonify({"error": "Imagen inválida"}), 400

    # Validar tamaño
    if len(img_bytes) > 2 * 1024 * 1024:
        return jsonify({"error": "La imagen supera 2 MB"}), 400

    # Abrir con Pillow y agregar marca de agua (logo) del servidor
    try:
        img = Image.open(BytesIO(img_bytes)).convert("RGBA")
    except Exception:
        return jsonify({"error": "No se pudo procesar la imagen"}), 400

    # Cargar logo watermark
    wm_path = Path(__file__).parent / "static" / "pieDeFoto.png"
    if wm_path.exists():
        try:
            from PIL import ImageDraw as ID2, ImageChops

            wm_logo = Image.open(wm_path).convert("RGBA")
            # Redimensionar logo a ~150px de ancho (horizontal)
            wm_w = 150
            wm_scale = wm_w / wm_logo.width
            wm_h = int(wm_logo.height * wm_scale)
            wm_logo = wm_logo.resize((wm_w, wm_h), Image.LANCZOS)

            # Crear máscara: solo esquinas izquierdas redondeadas
            mask = Image.new("L", (wm_w, wm_h), 0)
            mask_draw = ID2.Draw(mask)
            radius = int(min(wm_w, wm_h) * 0.25)
            # Dibujar rectángulo con todas las esquinas redondeadas
            mask_draw.rounded_rectangle([0, 0, wm_w - 1, wm_h - 1], radius=radius, fill=255)
            # Rellenar esquinas derechas (tapar el redondeo)
            mask_draw.rectangle([wm_w - radius, 0, wm_w - 1, radius], fill=255)
            mask_draw.rectangle([wm_w - radius, wm_h - radius, wm_w - 1, wm_h - 1], fill=255)

            # Combinar alfa original del logo con máscara (sin opacidad)
            logo_alpha = wm_logo.getchannel("A")
            final_alpha = ImageChops.multiply(logo_alpha, mask)
            wm_logo.putalpha(final_alpha)

            # Posicionar: borde derecho pegado al borde de la imagen, abajo
            x = img.width - wm_w
            y = img.height - wm_h - 10
            img.paste(wm_logo, (x, y), wm_logo)
        except Exception:
            pass  # Si falla el logo, guardar sin marca de agua

    # Convertir a RGB para JPEG
    img = img.convert("RGB")

    # Comprimir a JPEG ≤ 2 MB
    quality = 90
    while quality >= 10:
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= 2 * 1024 * 1024:
            break
        quality -= 5
    else:
        return jsonify({"error": "No se pudo comprimir a menos de 2 MB"}), 400

    img_final = buf.getvalue()

    # Determinar ruta de guardado
    now = datetime.now()
    year = str(now.year)
    month = f"{now.month:02d}"

    is_production = platform.system() == "Linux"
    if is_production:
        base_dir = Path("/var/www/html/wp-content/uploads") / year / month
    else:
        base_dir = Path(r"C:\Users\Luis.RANGEL-GONZALEZ\OneDrive - Akkodis\Desktop\fotos\editadas")

    base_dir.mkdir(parents=True, exist_ok=True)

    # Evitar sobreescritura
    final_name = f"{filename}.jpg"
    final_path = base_dir / final_name
    counter = 1
    while final_path.exists():
        final_name = f"{filename}_{counter}.jpg"
        final_path = base_dir / final_name
        counter += 1

    final_path.write_bytes(img_final)

    size_kb = len(img_final) / 1024
    return jsonify({
        "ok": True,
        "path": str(final_path),
        "size_kb": round(size_kb, 1),
        "filename": final_name,
    })


# ==================================================================================
# MAIN
# ==================================================================================

if __name__ == "__main__":
    app.run(debug=False, port=5000, use_reloader=False)
