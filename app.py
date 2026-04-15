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

import json
import os
import queue
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from html import unescape
from pathlib import Path

import requests as req_lib
from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, redirect, render_template,
    request, send_file, session, stream_with_context, url_for,
)
from requests.auth import HTTPBasicAuth
from werkzeug.security import check_password_hash, generate_password_hash

from core.wp_requests import wp_request

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
PROGRESS_DIR = Path("logs/progress_tasks")
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
_STREAM_POLL_INTERVAL_SECONDS = 1.0
_STREAM_KEEPALIVE_SECONDS = 5.0


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


def _progress_task_path(task_id: str) -> Path:
    return PROGRESS_DIR / f"{task_id}.jsonl"


def _init_progress_task(task_id: str) -> Path:
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    task_path = _progress_task_path(task_id)
    task_path.write_text("", encoding="utf-8")
    return task_path


def _append_progress_event(task_id: str, payload: dict) -> None:
    task_path = _progress_task_path(task_id)
    task_path.parent.mkdir(parents=True, exist_ok=True)
    with task_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _emit_progress_event(task_id: str, q: queue.Queue, payload: dict) -> None:
    q.put(payload)
    _append_progress_event(task_id, payload)


def _safe_draft_path(filename: str) -> Path | None:
    """Valida que el filename este dentro de DRAFTS_DIR (evita path traversal)."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    base = DRAFTS_DIR.resolve()
    safe = (DRAFTS_DIR / Path(filename).name).resolve()
    if not str(safe).startswith(str(base)):
        return None
    return safe


def _strip_html_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


_RELIGION_TOPIC_PATTERN = re.compile(
    r"\b("
    r"religi(?:on|ones|oso|osa|osos|osas)|iglesia|iglesias|dios|jesucristo|jesus|cristo|"
    r"biblia|catolic(?:o|a|os|as)|cristian(?:o|a|os|as)|evangelic(?:o|a|os|as)|"
    r"islam|musulm(?:an|ana|anes)|juda(?:ismo|ico|ica|icos|icas)|tora|coran|"
    r"bud(?:a|ismo|ista)|hindu(?:ismo|ista)|misa|misas|oracion|oraciones|templo|templos|"
    r"sacerdot(?:e|es|isa|isas)|pastor|pastores|fe\b|espiritualidad|culto|rezar"
    r")\b",
    re.IGNORECASE,
)

_ROLE_TOPIC_EXCLUSIONS: dict[str, set[str]] = {
    "psicologa": {"salud_nutricion", "estetica_autocuidado"},
    "medico": {"bienestar_emocional", "familia_educacion"},
}

_ROLE_TOPIC_VISIBILITY: dict[str, set[str]] = {
    "psicologa": {
        "psicologia_relacionados",
        "psicologia_no_relacionados",
        "bienestar_emocional",
        "familia_educacion",
    },
    "psicologo": {
        "psicologia_relacionados",
        "psicologia_no_relacionados",
        "bienestar_emocional",
        "familia_educacion",
    },
    "medico": {
        "medicina_relacionados",
        "medicina_no_relacionados",
        "salud_nutricion",
        "estetica_autocuidado",
    },
    "medica": {
        "medicina_relacionados",
        "medicina_no_relacionados",
        "salud_nutricion",
        "estetica_autocuidado",
    },
}

_TOPIC_POST_TYPES = ("opinion", "listicle", "howto")


def _topic_mentions_religion(topic: str) -> bool:
    return bool(_RELIGION_TOPIC_PATTERN.search(str(topic or "")))


def _sanitize_topic_list(raw_topics: list[str] | tuple[str, ...] | None) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    removed: list[str] = []
    seen: set[str] = set()
    for item in raw_topics or []:
        topic = str(item or "").strip()
        if not topic or topic in seen:
            continue
        seen.add(topic)
        if _topic_mentions_religion(topic):
            removed.append(topic)
            continue
        kept.append(topic)
    return kept, removed


def _sanitize_topics_payload(payload: dict | None) -> tuple[dict, list[str]]:
    safe_payload: dict = {}
    removed_topics: list[str] = []
    for key, value in (payload or {}).items():
        if key == "fecha":
            safe_payload[key] = value
            continue
        if key == "amazon_sugerencias":
            safe_payload[key] = value if isinstance(value, list) else []
            continue
        if isinstance(value, list):
            kept, removed = _sanitize_topic_list(value)
            safe_payload[key] = kept
            removed_topics.extend(removed)
    return safe_payload, removed_topics


def _apply_role_topic_visibility(topics: dict, user: dict | None) -> dict:
    if not isinstance(topics, dict):
        return topics

    role = (user or {}).get("role", "")
    allowed = _ROLE_TOPIC_VISIBILITY.get(role)
    if allowed is None:
        allowed = (user or {}).get("topic_categories")
    excluded = _ROLE_TOPIC_EXCLUSIONS.get(role, set())
    visible: dict = {}

    for key, value in topics.items():
        if key in {"fecha", "amazon_sugerencias"}:
            visible[key] = value
            continue
        if excluded and key in excluded:
            continue
        if allowed is None or key in allowed:
            visible[key] = value
    return visible


def _normalize_saved_suggestions(raw_suggestions: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    seen_topics: set[str] = set()
    for item in raw_suggestions or []:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topico", "")).strip()
        if not topic or topic in seen_topics or _topic_mentions_religion(topic):
            continue
        seen_topics.add(topic)
        normalized.append({
            "topico": topic,
            "evergreen": str(item.get("evergreen", "")).strip(),
            "listicle": str(item.get("listicle", "")).strip(),
            "howto": str(item.get("howto", "")).strip(),
        })
    return normalized


def _load_saved_topics_workspace(username: str) -> dict | None:
    user = load_users().get(username)
    state = (user or {}).get("topics_workspace")
    if not isinstance(state, dict):
        return None
    if state.get("fecha") != str(date.today()):
        return None
    return state


def _save_topics_workspace(username: str, state: dict) -> None:
    users = load_users()
    if username not in users:
        return
    users[username]["topics_workspace"] = state
    save_users(users)


def _get_wp_request_context() -> tuple[str, HTTPBasicAuth | None]:
    base_url = os.getenv("WP_BASE_URL", "").rstrip("/")
    user = get_current_user()
    username = user.get("wp_username", "") if user else os.getenv("WP_USERNAME", "")
    app_password = user.get("wp_app_password", "") if user else os.getenv("WP_APP_PASSWORD", "")
    auth = HTTPBasicAuth(username, app_password) if username and app_password else None
    return base_url, auth


def _wp_get_json(endpoint: str, *, params: dict | None = None):
    base_url, auth = _get_wp_request_context()
    if not base_url:
        raise ValueError("WP_BASE_URL no está configurado.")

    url = f"{base_url}{endpoint}"
    attempts = [auth] if auth else []
    if not attempts or attempts[-1] is not None:
        attempts.append(None)

    last_error: Exception | None = None
    for current_auth in attempts:
        try:
            response = wp_request("GET", url, params=params, auth=current_auth, timeout=20)
            response.raise_for_status()
            return response.json(), response.headers
        except Exception as exc:
            last_error = exc

    raise ValueError(f"No se pudo consultar WordPress: {last_error}")


def _fetch_published_wp_posts(limit: int = 30) -> list[dict]:
    safe_limit = max(1, min(int(limit), 50))
    posts, _ = _wp_get_json(
        "/wp-json/wp/v2/posts",
        params={
            "status": "publish",
            "per_page": safe_limit,
            "orderby": "date",
            "order": "desc",
            "_fields": "id,date,link,slug,title,excerpt",
        },
    )

    items = []
    for post in posts or []:
        title = _strip_html_tags((post.get("title") or {}).get("rendered", ""))
        if not title:
            title = f"Post {post.get('id', '')}".strip()
        items.append(
            {
                "id": post.get("id"),
                "title": title,
                "excerpt": _strip_html_tags((post.get("excerpt") or {}).get("rendered", "")),
                "date": post.get("date", ""),
                "link": post.get("link", ""),
                "slug": post.get("slug", ""),
            }
        )
    return items


def _fetch_wp_post_topic(post_id: int | str) -> dict:
    post, _ = _wp_get_json(
        f"/wp-json/wp/v2/posts/{int(post_id)}",
        params={"_fields": "id,date,link,slug,status,title,content,excerpt"},
    )
    title = _strip_html_tags((post.get("title") or {}).get("rendered", ""))
    excerpt = _strip_html_tags((post.get("excerpt") or {}).get("rendered", ""))
    content = _strip_html_tags((post.get("content") or {}).get("rendered", ""))[:3000]
    summary = excerpt or content[:320]
    topic_text = (
        f"Título del post publicado: {title}\n"
        f"Resumen útil: {summary}\n"
        f"Contenido base: {content}"
    )
    return {
        "id": post.get("id"),
        "title": title,
        "excerpt": excerpt,
        "date": post.get("date", ""),
        "link": post.get("link", ""),
        "slug": post.get("slug", ""),
        "topic_text": topic_text,
    }


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

    allowed_keys = {
        "style", "tone", "vocabulary", "examples", "sample", "compiled",
        "facts", "author_references",
    }
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
        changed = tm.set_active_key(alias)
        if changed:
            return jsonify({"ok": True, "alias": tm.active_key.alias})
        return jsonify({"ok": False, "error": f"No se pudo activar {alias}"}), 400
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
    _init_progress_task(task_id)
    _current_username = (get_current_user() or {}).get("username", "")
    _current_badge    = (get_current_user() or {}).get("professional_badge", "")
    _current_voice    = (get_current_user() or {}).get("voice_profile", {}).get("compiled", "")

    def run():
        global _token_manager
        try:
            from core.orchestrator import ContentOrchestrator
            tm = get_token_manager()

            def progress_cb(step: int, total: int, message: str):
                _emit_progress_event(task_id, q, {"type": "progress", "step": step, "total": total, "message": message})

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
            _emit_progress_event(task_id, q, {"type": "done", "drafts": result, "topic": user_input})
        except Exception as e:
            _emit_progress_event(task_id, q, {"type": "error", "message": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"task_id": task_id})


@app.get("/api/progreso/<task_id>")
@login_required
def api_progreso(task_id):
    """Server-Sent Events - stream de progreso de generacion."""
    q = _progress_queues.get(task_id)
    task_path = _progress_task_path(task_id)

    if q is None and not task_path.exists():
        def not_found():
            yield 'data: {"type":"error","message":"Tarea no encontrada"}\n\n'
        return Response(not_found(), mimetype="text/event-stream")

    def stream_from_file():
        last_sent_at = time.monotonic()
        yield "retry: 3000\n: stream-open\n\n"
        with task_path.open("r", encoding="utf-8") as f:
            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    last_sent_at = time.monotonic()
                    if msg.get("type") in ("done", "error"):
                        return
                    continue

                now = time.monotonic()
                if (now - last_sent_at) >= _STREAM_KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    last_sent_at = now
                time.sleep(_STREAM_POLL_INTERVAL_SECONDS)

    def stream():
        last_sent_at = time.monotonic()
        yield "retry: 3000\n: stream-open\n\n"
        while True:
            try:
                msg = q.get(timeout=_STREAM_POLL_INTERVAL_SECONDS)
            except queue.Empty:
                now = time.monotonic()
                if (now - last_sent_at) >= _STREAM_KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    last_sent_at = now
                continue
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            last_sent_at = time.monotonic()
            if msg.get("type") in ("done", "error"):
                _progress_queues.pop(task_id, None)
                return

    return Response(
        stream_with_context(stream() if q is not None else stream_from_file()),
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
        topics, removed_topics = _sanitize_topics_payload(topics)
        user    = get_current_user()
        topics = _apply_role_topic_visibility(topics, user)
        return jsonify({
            "ok": True,
            "data": topics,
            "from_cache": not force,
            "filtered_out": removed_topics,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/topicos/sugerir")
@login_required
def api_topicos_sugerir():
    global _token_manager
    data      = request.get_json() or {}
    topics, removed_topics = _sanitize_topic_list(data.get("topics", []))
    mock_mode, _ = _get_modes()
    if not topics:
        return jsonify({"ok": False, "error": "No hay tópicos válidos para sugerir títulos."})
    try:
        from core.post_type_advisor import suggest_post_structure
        from core.gemini_client     import GeminiClient
        tm     = get_token_manager()
        gemini = GeminiClient(token_manager=tm, mock_mode=mock_mode)
        sugs   = suggest_post_structure(gemini, topics)
        _token_manager = gemini.token_manager
        return jsonify({
            "ok": True,
            "suggestions": [s.to_dict() for s in sugs],
            "filtered_out": removed_topics,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/topicos/generar")
@login_required
def api_topicos_generar():
    global _token_manager
    data          = request.get_json() or {}
    topics, removed_topics = _sanitize_topic_list(data.get("topics", []))
    edited_titles = data.get("edited_titles", {})
    generation_plan = data.get("generation_plan", {}) if isinstance(data.get("generation_plan"), dict) else {}
    reviewer      = data.get("reviewer", "")
    gemini_model  = data.get("gemini_model", "gemini-2.5-flash")
    focus_global  = data.get("focus_global", "")

    if not topics:
        return jsonify({"error": "Sin topicos seleccionados"}), 400

    topic_jobs: list[dict] = []
    for topic in topics:
        raw_types = (generation_plan.get(topic) or {}).get("post_types", [])
        selected_types = [post_type for post_type in raw_types if post_type in _TOPIC_POST_TYPES]
        if not raw_types:
            selected_types = list(_TOPIC_POST_TYPES)
        if not selected_types:
            continue
        topic_jobs.append({"topic": topic, "post_types": selected_types})

    if not topic_jobs:
        return jsonify({"error": "No hay títulos activos para generar."}), 400

    task_id = str(uuid.uuid4())
    q       = queue.Queue()
    _progress_queues[task_id] = q
    _init_progress_task(task_id)
    _current_username = (get_current_user() or {}).get("username", "")
    _current_badge    = (get_current_user() or {}).get("professional_badge", "")
    _current_voice    = (get_current_user() or {}).get("voice_profile", {}).get("compiled", "")

    def run():
        global _token_manager
        try:
            from core.orchestrator import ContentOrchestrator
            tm           = get_token_manager()
            total_topics = len(topic_jobs)
            all_results  = []

            for t_idx, job in enumerate(topic_jobs):
                topic = job["topic"]
                selected_types = job["post_types"]
                _emit_progress_event(task_id, q, {"type": "topic_start", "topic": topic, "idx": t_idx, "total": total_topics})
                ct = {
                    pt: edited_titles.get(f"{topic}:{pt}", "")
                    for pt in selected_types
                }
                ct = {k: v for k, v in ct.items() if v.strip()}

                def make_cb(idx):
                    def cb(step, total, msg):
                        _emit_progress_event(task_id, q, {"type": "progress", "idx": idx, "step": step, "total": total, "message": msg})
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
                        selected_post_types=selected_types,
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
                    _emit_progress_event(task_id, q, {"type": "topic_done", "topic": topic, "idx": t_idx, "drafts": topic_drafts})
                except Exception as e:
                    _emit_progress_event(task_id, q, {"type": "topic_error", "topic": topic, "idx": t_idx, "message": str(e)})
                    all_results.append({"topic": topic, "drafts": [], "error": str(e)})

            _emit_progress_event(task_id, q, {"type": "done", "results": all_results})
        except Exception as e:
            _emit_progress_event(task_id, q, {"type": "error", "message": str(e)})

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"task_id": task_id, "filtered_out": removed_topics})


@app.get("/api/topicos/estado")
@login_required
def api_topicos_estado_get():
    username = session.get("username")
    if not username:
        return jsonify({"error": "No autenticado"}), 401

    state = _load_saved_topics_workspace(username)
    if not state:
        return jsonify({"ok": True, "state": None})

    topics_data, _ = _sanitize_topics_payload(state.get("topics_data") or {})
    state = {
        **state,
        "topics_data": _apply_role_topic_visibility(topics_data, get_current_user()),
        "suggestions": _normalize_saved_suggestions(state.get("suggestions") or []),
    }
    return jsonify({"ok": True, "state": state})


@app.put("/api/topicos/estado")
@login_required
def api_topicos_estado_put():
    payload = request.get_json() or {}
    username = session.get("username")
    if not username:
        return jsonify({"error": "No autenticado"}), 401

    topics_data, removed_topics = _sanitize_topics_payload(payload.get("topics_data") or {})
    topics_data = _apply_role_topic_visibility(topics_data, get_current_user())
    selected_topics, removed_selected = _sanitize_topic_list(payload.get("selected_topics", []))
    free_topic = str(payload.get("free_topic", "")).strip()
    if free_topic and _topic_mentions_religion(free_topic):
        free_topic = ""

    edited_titles = {
        str(key): str(value or "").strip()
        for key, value in (payload.get("edited_titles") or {}).items()
        if isinstance(key, str)
    }
    disabled_posts = [
        str(key) for key in (payload.get("disabled_posts") or [])
        if isinstance(key, str) and ":" in key
    ]

    state = {
        "fecha": str(date.today()),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "topics_data": topics_data,
        "selected_topics": selected_topics,
        "free_topic": free_topic,
        "suggestions": _normalize_saved_suggestions(payload.get("suggestions") or []),
        "edited_titles": edited_titles,
        "disabled_posts": disabled_posts,
        "reviewer": str(payload.get("reviewer", "")).strip(),
        "gemini_model": str(payload.get("gemini_model", "gemini-2.5-flash")).strip(),
        "focus_global": str(payload.get("focus_global", "")).strip(),
    }
    _save_topics_workspace(username, state)
    return jsonify({
        "ok": True,
        "saved_at": state["saved_at"],
        "filtered_out": list(dict.fromkeys(removed_topics + removed_selected)),
    })


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
    """Genera contenido social desde un borrador para TikTok o Instagram."""
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    body = request.get_json() or {}
    platform = (body.get("platform") or "tiktok").lower()
    selected_character_ids = _normalize_social_character_ids(body.get("selected_characters"))
    selected_hooks = _normalize_selected_hooks(body.get("selected_hooks"))
    if not selected_character_ids:
        return jsonify({"error": "Selecciona al menos un participante para este tema."}), 400
    if platform not in {"tiktok", "instagram"}:
        return jsonify({"error": "Plataforma no válida. Usa TikTok o Instagram."}), 400
    draft_data = json.loads(safe.read_text(encoding="utf-8"))
    social_cache = draft_data.get("social_cache") if isinstance(draft_data.get("social_cache"), dict) else {}
    cached_entry = social_cache.get(platform) if isinstance(social_cache.get(platform), dict) else None
    if cached_entry and isinstance(cached_entry.get("data"), dict):
        return jsonify(
            {
                "ok": True,
                "platform": platform,
                "data": cached_entry.get("data"),
                "content": json.dumps(cached_entry.get("data"), ensure_ascii=False, indent=2),
                "cached": True,
                "cache_meta": {
                    "generated_at": cached_entry.get("generated_at"),
                    "selected_characters": cached_entry.get("selected_characters", []),
                },
            }
        )
    title      = draft_data.get("title", "")
    focus_kw   = draft_data.get("focus_keyword", "")
    # Extracto del contenido (máx. 600 palabras) para no gastar demasiados tokens
    plain_content = " ".join(
        w for w in (draft_data.get("content", "")
                    .replace("<", " <").replace(">", "> ")
                    .split())
        if not w.startswith("<") and not w.startswith(">")
    )[:3000]
    topic_context = f"Título del borrador: {title}\nKeyword foco: {focus_kw}\nResumen útil: {plain_content}"
    mock_mode, _ = _get_modes()
    try:
        if platform == "tiktok":
            if mock_mode:
                result = _social_mock_response(
                    title or focus_kw or plain_content[:60],
                    selected_character_ids,
                )
                if selected_hooks:
                    selected_labels = {hook["tipo_de_angulo"] for hook in selected_hooks}
                    result["tiktok"]["opciones"] = [
                        option for option in result.get("tiktok", {}).get("opciones", [])
                        if option.get("tipo_de_angulo") in selected_labels
                    ]
                    result["tiktok"]["checklist"] = _build_tiktok_checklist_from_options(result["tiktok"]["opciones"])
            else:
                result = _generate_tiktok_2026_result(
                    topic_context=topic_context,
                    mode="borrador",
                    selected_character_ids=selected_character_ids,
                    selected_hooks=selected_hooks,
                )
            _validate_tiktok_2026_payload(
                result,
                selected_character_ids,
                expected_labels=[hook["tipo_de_angulo"] for hook in selected_hooks] if selected_hooks else None,
            )
        else:
            if mock_mode:
                result = _instagram_mock_response(topic_context, selected_character_ids)
            else:
                result = _generate_instagram_result(
                    topic_context=topic_context,
                    mode="borrador",
                    selected_character_ids=selected_character_ids,
                )
            _validate_instagram_payload(result, selected_character_ids)

        social_cache[platform] = {
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "selected_characters": selected_character_ids,
            "data": result,
        }
        draft_data["social_cache"] = social_cache
        safe.write_text(json.dumps(draft_data, ensure_ascii=False, indent=2), encoding="utf-8")
        return jsonify(
            {
                "ok": True,
                "platform": platform,
                "data": result,
                "content": json.dumps(result, ensure_ascii=False, indent=2),
                "cached": False,
            }
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
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
        resp = wp_request(
            "GET",
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
SOCIAL_PARTICIPANTS_FILE = Path("social_participants.json")
SOCIAL_RETENTION_DAYS = 30
SOCIAL_DIR.mkdir(parents=True, exist_ok=True)

TIKTOK_HOOK_LIBRARY = [
    {
        "label": "Resultado Express",
        "template": "Cómo logré [Resultado] en solo [X días] con este cambio simple.",
    },
    {
        "label": "Advertencia",
        "template": "Deja de hacer [Acción común] si no quieres seguir [Acción].",
    },
    {
        "label": "Hubiera Pagado",
        "template": "Hubiera pagado por saber esto antes de cumplir los [Edad/Etapa].",
    },
    {
        "label": "Error Oculto",
        "template": "¿Sabías que estás haciendo esto mal?",
    },
    {
        "label": "Verdad Incómoda",
        "template": "Lo que nadie te cuenta sobre [Tema popular/Industria].",
    },
    {
        "label": "Señales",
        "template": "X señales de que tu [Producto/Hábito] te está arruinando.",
    },
    {
        "label": "Hablemos Claro",
        "template": "Hablemos de la realidad de [Trabajo/Situación] que nadie te está contando.",
    },
    {
        "label": "POV",
        "template": "POV: Te das cuenta de que [Situación irónica o graciosa].",
    },
    {
        "label": "Fracaso",
        "template": "Mi mayor fracaso este año y lo que aprendí para que tú no lo repitas.",
    },
    {
        "label": "Dosis de Realidad",
        "template": "Dosis de realidad: no necesitas [Producto caro] para [Resultado].",
    },
]
TIKTOK_HOOK_LABELS = [item["label"] for item in TIKTOK_HOOK_LIBRARY]
TIKTOK_HOOK_TEMPLATES = [item["template"] for item in TIKTOK_HOOK_LIBRARY]
TIKTOK_HOOK_TEMPLATE_BY_LABEL = {
    item["label"]: item["template"] for item in TIKTOK_HOOK_LIBRARY
}
SOCIAL_DEFAULT_PARTICIPANTS = [
    {
        "id": "mama",
        "nombre": "Mama",
        "perfil": "30 años, psicologa",
        "apariencia_visual": "Mujer latina de 30 anos, cabello castano oscuro ondulado a los hombros, expresion serena, ropa neutra elegante en tonos arena y verde salvia.",
        "prompt_visual": "Personaje fijo Tu Eden. Mujer latina de 30 anos, psicologa, cabello castano oscuro ondulado a los hombros, rostro sereno, vestuario elegante en arena, marfil y verde salvia, bienestar premium, luz natural suave, editorial zen y profesional.",
    },
    {
        "id": "papa",
        "nombre": "Papa",
        "perfil": "30 años, ingeniero de sistemas",
        "apariencia_visual": "Hombre latino de 30 anos, cabello corto castano, barba sutil, look pulcro casual-profesional en beige, blanco roto y verde oliva suave.",
        "prompt_visual": "Personaje fijo Tu Eden. Hombre latino de 30 anos, ingeniero de sistemas, cabello corto castano, barba sutil, look casual-profesional pulcro en beige, blanco roto y verde oliva suave, atmosfera serena, editorial wellness corporativo.",
    },
    {
        "id": "hija",
        "nombre": "Hija",
        "perfil": "8 años",
        "apariencia_visual": "Nina latina de 8 anos, cabello largo oscuro, rasgos dulces, vestuario sencillo en tonos crema y verde claro.",
        "prompt_visual": "Personaje fijo Tu Eden. Nina latina de 8 anos, cabello largo oscuro, rasgos dulces, vestuario sencillo en tonos crema y verde claro, escena luminosa, sensibilidad familiar, coherencia visual serena.",
    },
]
TU_EDEN_INSTAGRAM_STYLE = (
    "Estilo visual fijo de Tu Eden: zen, calmado y profesional; editorial wellness premium; "
    "paleta verde salvia, arena, marfil y madera clara; luz natural suave; espacios limpios; "
    "composicion minimalista; atmosfera serena; confianza de empresa de salud y bienestar; "
    "sin estridencias, sin neon, sin look juvenil caotico."
)
INSTAGRAM_REQUIRED_KEYS = {
    "tipo_publicacion",
    "objetivo_editorial",
    "cantidad_imagenes",
    "justificacion_cantidad",
    "tamano_base",
    "estilo_visual_global",
    "hook_portada",
    "caption_principal",
    "caption_corto",
    "cta",
    "hashtags",
    "secuencia",
}
INSTAGRAM_REQUIRED_SLIDE_KEYS = {
    "orden",
    "rol_narrativo",
    "titulo_slide",
    "texto_slide",
    "relacion_con_blog",
    "continuidad_visual",
    "continuidad_textual",
    "tamano",
    "personajes",
    "prompt_imagen",
}
TIKTOK_REQUIRED_OPTION_KEYS = {
    "tipo_de_angulo",
    "plantilla_base",
    "gancho_texto",
    "promesa_valor",
    "duracion_segundos",
    "personajes",
    "instruccion_visual_inicio",
    "puntos_retencion",
    "guion_detallado",
    "plan_rodaje",
    "cta_engagement",
}
TIKTOK_HOOK_KEYS = {
    "tipo_de_angulo",
    "plantilla_base",
    "gancho_texto",
    "promesa_valor",
    "instruccion_visual_inicio",
}
TIKTOK_COMPACT_RETRY_SUFFIX = """

MODO COMPACTO OBLIGATORIO PARA ASEGURAR JSON COMPLETO:
- Mantén exactamente la misma estructura JSON y las mismas reglas.
- Genera 5 opciones completas, pero con redacción más compacta.
- Usa preferentemente 5 bloques en guion_detallado, no más, salvo necesidad real.
- promesa_valor: máximo 18 palabras.
- objetivo, visual, texto_pantalla, transicion, locacion, tono, ritmo_edicion, vestuario, props, musica_sfx: máximo 12 palabras.
- dialogo y mensaje: máximo 22 palabras.
- micro_transicion y rol_en_video: máximo 10 palabras.
- tomas_clave: frases muy breves.
- checklist: items y detalles breves.
- Devuelve solo JSON válido y completo.
"""


def _safe_social_path(filename: str) -> Path | None:
    base = SOCIAL_DIR.resolve()
    safe = (SOCIAL_DIR / Path(filename).name).resolve()
    if not str(safe).startswith(str(base)):
        return None
    return safe


def _prune_expired_social_drafts() -> None:
    cutoff = datetime.now() - timedelta(days=SOCIAL_RETENTION_DAYS)
    for path in SOCIAL_DIR.glob("social_*.json"):
        try:
            modified_at = datetime.fromtimestamp(path.stat().st_mtime)
            if modified_at < cutoff:
                path.unlink(missing_ok=True)
        except Exception:
            continue


def _safe_social_participant_id(raw_value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(raw_value or "").strip().lower()).strip("_")
    return value[:40]


def _load_social_participants() -> list[dict]:
    if not SOCIAL_PARTICIPANTS_FILE.exists():
        SOCIAL_PARTICIPANTS_FILE.write_text(
            json.dumps(SOCIAL_DEFAULT_PARTICIPANTS, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return [dict(item) for item in SOCIAL_DEFAULT_PARTICIPANTS]

    try:
        data = json.loads(SOCIAL_PARTICIPANTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = []

    participants = []
    seen = set()
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        participant_id = _safe_social_participant_id(item.get("id") or item.get("nombre"))
        nombre = str(item.get("nombre", "")).strip()
        perfil = str(item.get("perfil", "")).strip()
        apariencia_visual = str(item.get("apariencia_visual", "")).strip()
        prompt_visual = str(item.get("prompt_visual", "")).strip()
        if not participant_id or not nombre or not perfil or participant_id in seen:
            continue
        participants.append({
            "id": participant_id,
            "nombre": nombre,
            "perfil": perfil,
            "apariencia_visual": apariencia_visual,
            "prompt_visual": prompt_visual,
        })
        seen.add(participant_id)

    if participants:
        return participants
    return [dict(item) for item in SOCIAL_DEFAULT_PARTICIPANTS]


def _save_social_participants(participants: list[dict]) -> None:
    SOCIAL_PARTICIPANTS_FILE.write_text(
        json.dumps(participants, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _social_participant_map() -> dict[str, dict]:
    return {item["id"]: item for item in _load_social_participants()}


def _normalize_social_character_ids(selected_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen = set()
    participants_by_id = _social_participant_map()
    for raw_id in selected_ids or []:
        char_id = str(raw_id or "").strip().lower()
        if char_id in participants_by_id and char_id not in seen:
            normalized.append(char_id)
            seen.add(char_id)
    return normalized


def _selected_social_characters(selected_ids: list[str] | None) -> list[dict]:
    participants_by_id = _social_participant_map()
    return [participants_by_id[char_id] for char_id in _normalize_social_character_ids(selected_ids) if char_id in participants_by_id]


def _social_character_bank(selected_ids: list[str] | None, *, include_visual: bool = False) -> str:
    selected_characters = _selected_social_characters(selected_ids)
    lines = []
    for item in selected_characters:
        line = f'- {item["id"]}: {item["nombre"]}, {item["perfil"]}'
        if include_visual:
            visual = str(item.get("apariencia_visual", "")).strip()
            prompt_visual = str(item.get("prompt_visual", "")).strip()
            line += f'. Apariencia consistente: {visual or "Mantener look sereno, profesional y coherente con bienestar premium."}'
            line += f'. Prompt fijo del personaje: {prompt_visual or "Mantener la misma identidad visual y editorial en todas las publicaciones."}'
        lines.append(line)
    return "\n".join(lines)


def _resolve_social_generation_context(
    mode: str,
    topic: str,
    selected_post_id,
) -> tuple[str, str, dict | None]:
    selected_post_meta = None
    clean_topic = (topic or "").strip()

    if mode != "borrador" and not clean_topic:
        raise ValueError("Se requiere un tema o borrador.")
    if len(clean_topic) > 4000:
        raise ValueError("El texto de entrada es demasiado largo (máx 4000 caracteres).")

    if mode == "borrador":
        if selected_post_id not in (None, ""):
            selected_post_meta = _fetch_wp_post_topic(selected_post_id)
            clean_topic = selected_post_meta["title"]
            input_instruction = selected_post_meta["topic_text"]
        elif clean_topic:
            input_instruction = (
                "Analiza este borrador de blog y conviértelo en propuestas de vídeo para TikTok 2026, "
                "centradas en retención, conversación y ejecución visual inmediata:\n\nBORRADOR:\n" + clean_topic
            )
        else:
            raise ValueError("Selecciona un post publicado o pega un borrador.")
    else:
        input_instruction = (
            "Crea propuestas originales para TikTok 2026 buscando el hook más comercial, práctico, "
            "visual y retenible:\n\nTEMA: " + clean_topic
        )

    return clean_topic, input_instruction, selected_post_meta


def _build_tiktok_hook_candidates_prompt(topic: str, mode: str, selected_ids: list[str] | None = None) -> str:
    hook_bank = "\n".join(
        f'- {item["label"]}: "{item["template"]}"' for item in TIKTOK_HOOK_LIBRARY
    )
    selected_characters = _selected_social_characters(selected_ids)
    character_bank = "\n".join(
        f'- {item["id"]}: {item["nombre"]}, {item["perfil"]}' for item in selected_characters
    )
    return f"""Eres estratega senior de TikTok 2026 especializado en hooks de alta retención.

Entrada de trabajo ({mode}):
{topic}

Banco maestro de hooks autorizado:
{hook_bank}

Personajes disponibles para este tema:
{character_bank}

Objetivo:
- Genera exactamente 5 hooks candidatos para TikTok 2026.
- Deben salir de 5 plantillas distintas del banco maestro.
- Prioriza hooks con potencial de scroll stop y claridad comercial.
- No desarrolles el guion completo. Solo entrega la capa de decisión inicial.

Reglas:
- Devuelve solo JSON válido.
- Cada hook debe incluir: tipo_de_angulo, plantilla_base, gancho_texto, promesa_valor, instruccion_visual_inicio.
- tipo_de_angulo debe ser exactamente uno de estos valores: {', '.join(TIKTOK_HOOK_LABELS)}.
- plantilla_base debe ser exactamente la plantilla original elegida del banco maestro.
- gancho_texto debe ser la adaptación final sin placeholders.
- promesa_valor debe ser breve y concreta.
- instruccion_visual_inicio debe ser breve, visual y grabable.
- No repitas plantillas.

Formato exacto:
{{
  "hooks": [
    {{
      "tipo_de_angulo": "Resultado Express",
      "plantilla_base": "Cómo logré [Resultado] en solo [X días] con este cambio simple.",
      "gancho_texto": "...",
      "promesa_valor": "...",
      "instruccion_visual_inicio": "..."
    }}
  ]
}}
"""


def _normalize_selected_hooks(selected_hooks: list[dict] | None) -> list[dict]:
    normalized = []
    seen = set()
    for raw_hook in selected_hooks or []:
        if not isinstance(raw_hook, dict):
            continue
        label = str(raw_hook.get("tipo_de_angulo", "")).strip()
        if label not in TIKTOK_HOOK_LABELS or label in seen:
            continue
        gancho_texto = str(raw_hook.get("gancho_texto", "")).strip()
        promesa = str(raw_hook.get("promesa_valor", "")).strip()
        visual = str(raw_hook.get("instruccion_visual_inicio", "")).strip()
        if not (gancho_texto and promesa and visual):
            continue
        normalized.append({
            "tipo_de_angulo": label,
            "plantilla_base": TIKTOK_HOOK_TEMPLATE_BY_LABEL[label],
            "gancho_texto": gancho_texto,
            "promesa_valor": promesa,
            "instruccion_visual_inicio": visual,
        })
        seen.add(label)
    return normalized


def _validate_tiktok_hook_candidates(data: dict) -> None:
    hooks = data.get("hooks")
    if not isinstance(hooks, list) or len(hooks) != 5:
        raise ValueError("Se requieren exactamente 5 hooks candidatos.")

    used_labels = []
    for idx, hook in enumerate(hooks, start=1):
        if not isinstance(hook, dict):
            raise ValueError(f"El hook {idx} no es válido.")
        missing = [key for key in TIKTOK_HOOK_KEYS if key not in hook]
        if missing:
            raise ValueError(f"El hook {idx} no incluye: {', '.join(missing)}.")
        label = hook.get("tipo_de_angulo")
        if label not in TIKTOK_HOOK_LABELS:
            raise ValueError(f"El hook {idx} usa una plantilla no permitida: {label}.")
        plantilla = str(hook.get("plantilla_base", "")).strip()
        if TIKTOK_HOOK_TEMPLATE_BY_LABEL.get(label) != plantilla:
            raise ValueError(f"El hook {idx} no corresponde con su plantilla base.")
        for field in ("gancho_texto", "promesa_valor", "instruccion_visual_inicio"):
            value = str(hook.get(field, "")).strip()
            if not value:
                raise ValueError(f"El hook {idx} tiene {field} vacío.")
            if field == "gancho_texto" and ("[" in value or "]" in value):
                raise ValueError(f"El hook {idx} mantiene placeholders sin adaptar.")
        used_labels.append(label)

    if len(set(used_labels)) != 5:
        raise ValueError("Los hooks deben usar 5 plantillas distintas del banco maestro.")


def _generate_tiktok_hook_candidates_from_mock(topic: str, selected_ids: list[str]) -> dict:
    mock = _social_mock_response(topic, selected_ids)
    hooks = []
    for option in mock.get("tiktok", {}).get("opciones", []):
        hooks.append({
            "tipo_de_angulo": option.get("tipo_de_angulo"),
            "plantilla_base": option.get("plantilla_base"),
            "gancho_texto": option.get("gancho_texto"),
            "promesa_valor": option.get("promesa_valor"),
            "instruccion_visual_inicio": option.get("instruccion_visual_inicio"),
        })
    payload = {"hooks": hooks[:5]}
    _validate_tiktok_hook_candidates(payload)
    return payload


def _generate_tiktok_hook_candidates(topic_context: str, mode: str, selected_ids: list[str]) -> dict:
    from core.gemini_client import GeminiClient

    tm = get_token_manager()
    gemini = GeminiClient(token_manager=tm, mock_mode=False)
    prompt = _build_tiktok_hook_candidates_prompt(topic_context, mode, selected_ids)
    prompt_variants = [prompt, prompt + TIKTOK_COMPACT_RETRY_SUFFIX]
    last_error: Exception | None = None

    for attempt, prompt_variant in enumerate(prompt_variants, start=1):
        raw = gemini.call_raw(prompt_variant)
        try:
            parsed = _parse_social_json(raw)
            hooks = _normalize_selected_hooks(parsed.get("hooks"))
            payload = {"hooks": hooks}
            _validate_tiktok_hook_candidates(payload)
            return payload
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            app.logger.warning("TikTok hooks failed on attempt %s: %s", attempt, exc)

    raise ValueError(f"No se pudieron generar hooks TikTok válidos: {last_error}")


def _build_tiktok_2026_prompt(topic: str, mode: str, selected_ids: list[str] | None = None) -> str:
    hook_bank = "\n".join(
        f'- {item["label"]}: "{item["template"]}"' for item in TIKTOK_HOOK_LIBRARY
    )
    character_bank = _social_character_bank(selected_ids)
    return f"""Eres estratega senior de TikTok 2026 especializado en contenido de alta retención para salud, bienestar y estilo de vida.

Entrada de trabajo ({mode}):
{topic}

Objetivo:
- Genera exactamente 5 propuestas distintas de vídeo para TikTok 2026.
- Prioriza solo TikTok. No menciones ni planifiques Instagram, Facebook ni cross-posting.
- Cada propuesta debe estar diseñada para detener el scroll en los primeros 3 segundos, sostener la atención durante todo el vídeo y durar entre 40 y 70 segundos.

Banco maestro de hooks autorizado:
{hook_bank}

Personajes disponibles para este tema:
{character_bank}

Reglas obligatorias:
- Las 5 propuestas deben ser realmente distintas entre sí.
- Debes elegir exactamente 5 plantillas distintas del banco maestro: las 5 que mejor encajen con el tópico.
- No inventes fórmulas nuevas fuera del banco maestro.
- Nunca repitas la misma plantilla ni el mismo gancho con cambios mínimos.
- Solo puedes usar personajes del listado anterior. Si un personaje no está seleccionado, no puede aparecer en el guion, en la interacción ni en el plan de rodaje.
- El vídeo completo debe quedar resuelto de principio a fin: apertura, desarrollo, prueba/valor, cierre y CTA.
- Adapta cada hook al tema reemplazando los placeholders por detalles concretos del tópico. No dejes corchetes ni variables sin resolver.
- El campo tipo_de_angulo debe ser exactamente uno de estos valores: {', '.join(TIKTOK_HOOK_LABELS)}.
- El campo plantilla_base debe ser exactamente la plantilla original elegida del banco maestro, sin adaptar.
- El campo gancho_texto debe ser la versión adaptada y final de esa plantilla, optimizada para los primeros 3 segundos.
- El campo promesa_valor debe resumir en una frase qué gana la audiencia si ve el vídeo completo.
- El campo duracion_segundos debe ser un entero entre 40 y 70.
- El campo personajes debe ser una lista con los participantes reales del vídeo. Cada personaje debe incluir: id, nombre, perfil, rol_en_video.
- El campo instruccion_visual_inicio debe describir una acción física, edición o plano que detenga el scroll.
- El campo puntos_retencion debe contener exactamente 3 objetos, uno por cada bloque de 3 segundos iniciales.
- Cada objeto de puntos_retencion debe incluir: paso, segundos, mensaje, micro_transicion.
- El campo guion_detallado debe cubrir todo el vídeo en 5 a 8 bloques consecutivos. Cada bloque debe incluir: bloque, segundos, objetivo, personaje, visual, dialogo, texto_pantalla, transicion.
- El campo plan_rodaje debe incluir: locacion, tono, ritmo_edicion, vestuario, props, musica_sfx, tomas_clave.
- El CTA debe buscar conversación real: pedir opinión, experiencia, duda o caso concreto. Prohibido pedir solo likes, follows o shares.
- Escribe en español claro, directo y natural.
- Sé conciso para que el JSON completo quepa en una sola respuesta: prioriza frases cortas y 5 bloques de guion cuando sea posible.
- Devuelve solo JSON válido, sin markdown ni texto adicional.

Estructura obligatoria:
{{
    "tiktok": {{
        "opciones": [
            {{
                "tipo_de_angulo": "Resultado Express",
                "plantilla_base": "Cómo logré [Resultado] en solo [X días] con este cambio simple.",
                "gancho_texto": "...",
                "promesa_valor": "...",
                "duracion_segundos": 52,
                "personajes": [
                    {{"id": "mama", "nombre": "Mama", "perfil": "30 años, psicologa", "rol_en_video": "..."}}
                ],
                "instruccion_visual_inicio": "...",
                "puntos_retencion": [
                    {{"paso": 1, "segundos": "0-3", "mensaje": "...", "micro_transicion": "..."}},
                    {{"paso": 2, "segundos": "3-6", "mensaje": "...", "micro_transicion": "..."}},
                    {{"paso": 3, "segundos": "6-9", "mensaje": "...", "micro_transicion": "..."}}
                ],
                "guion_detallado": [
                    {{"bloque": 1, "segundos": "0-3", "objetivo": "...", "personaje": "Mama", "visual": "...", "dialogo": "...", "texto_pantalla": "...", "transicion": "..."}},
                    {{"bloque": 2, "segundos": "3-12", "objetivo": "...", "personaje": "Mama", "visual": "...", "dialogo": "...", "texto_pantalla": "...", "transicion": "..."}}
                ],
                "plan_rodaje": {{
                    "locacion": "...",
                    "tono": "...",
                    "ritmo_edicion": "...",
                    "vestuario": "...",
                    "props": "...",
                    "musica_sfx": "...",
                    "tomas_clave": ["...", "...", "..."]
                }},
                "cta_engagement": "..."
            }}
        ],
        "checklist": {{
            "preproduccion": [
                {{"item": "...", "detalle": "..."}},
                {{"item": "...", "detalle": "..."}},
                {{"item": "...", "detalle": "..."}}
            ],
            "grabacion": [
                {{"item": "...", "detalle": "..."}},
                {{"item": "...", "detalle": "..."}},
                {{"item": "...", "detalle": "..."}}
            ],
            "edicion": [
                {{"item": "...", "detalle": "..."}},
                {{"item": "...", "detalle": "..."}},
                {{"item": "...", "detalle": "..."}}
            ]
        }}
    }}
}}

Control de calidad:
1. Deben existir exactamente 5 objetos en tiktok.opciones.
2. Las 5 propuestas deben salir de 5 plantillas distintas del banco maestro.
3. Ninguna propuesta puede omitir campos.
4. Ningún string puede quedar vacío.
5. Cada propuesta debe durar entre 40 y 70 segundos.
6. Cada propuesta debe usar solo personajes seleccionados.
7. No dejes placeholders como [Resultado], [X días] o similares en gancho_texto.
8. No uses explicaciones genéricas; todo debe sonar accionable, visual y específico.
9. JSON perfectamente válido: sin comas finales y sin comentarios.
"""


def _build_instagram_prompt(topic: str, mode: str, selected_ids: list[str] | None = None) -> str:
        character_bank = _social_character_bank(selected_ids, include_visual=True)
        return f"""Eres director creativo senior de Instagram para Tu Eden, una empresa de bienestar, salud emocional y estilo de vida consciente.

Entrada de trabajo ({mode}):
{topic}

Estilo visual obligatorio de marca:
{TU_EDEN_INSTAGRAM_STYLE}

Personajes disponibles para mantener consistencia visual:
{character_bank}

Objetivo:
- Convierte el blog en una publicacion de Instagram de alto valor guardable.
- Decide si conviene "post_simple" o "carrusel".
- Decide la cantidad_imagenes ideal entre 1 y 8 segun la complejidad del tema.
- Si eliges carrusel, la secuencia debe tener un hilo narrativo claro de principio a fin.
- La salida debe estar pensada para feed de Instagram con tamano vertical 1080x1350 px.
- Cada prompt de imagen debe ser utilizable directamente en un generador de imagen IA.

Reglas obligatorias:
- Mantén SIEMPRE el mismo estilo visual de Tu Eden en todas las imágenes.
- Si reutilizas personajes, conserva rasgos, ropa base, energia y apariencia visual de forma consistente entre slides.
- Si un personaje tiene prompt fijo, debes respetarlo como identidad visual obligatoria y trasladarlo al prompt_imagen de cada slide donde aparezca.
- Usa solo personajes del listado anterior. No inventes personajes nuevos.
- La publicación debe apoyarse directamente en las ideas del blog, no ser genérica.
- El caption debe sonar profesional, cercano, sereno y muy claro.
- El tono debe ser zen, confiable y corporativo, sin parecer frío ni rígido.
- La secuencia debe estar optimizada para lectura rápida en Instagram.
- Cada slide debe indicar su rol narrativo: hook, problema, contexto, insight, paso, cierre o CTA.
- Cada slide debe incluir continuidad_visual y continuidad_textual para enlazar con el siguiente.
- Cada prompt_imagen debe mencionar el tamano 1080x1350 px y el estilo visual global.
- Devuelve solo JSON válido. No markdown. No texto fuera del JSON.

Formato exacto de salida:
{{
    "instagram": {{
        "tipo_publicacion": "carrusel",
        "objetivo_editorial": "...",
        "cantidad_imagenes": 5,
        "justificacion_cantidad": "...",
        "tamano_base": "1080x1350 px",
        "estilo_visual_global": "...",
        "hook_portada": "...",
        "caption_principal": "...",
        "caption_corto": "...",
        "cta": "...",
        "hashtags": ["#...", "#...", "#..."],
        "secuencia": [
            {{
                "orden": 1,
                "rol_narrativo": "hook",
                "titulo_slide": "...",
                "texto_slide": "...",
                "relacion_con_blog": "...",
                "continuidad_visual": "...",
                "continuidad_textual": "...",
                "tamano": "1080x1350 px",
                "personajes": [
                    {{"id": "mama", "nombre": "Mama", "perfil": "30 años, psicologa"}}
                ],
                "prompt_imagen": "..."
            }}
        ]
    }}
}}

Control de calidad:
1. cantidad_imagenes debe coincidir exactamente con el numero de objetos en secuencia.
2. Si tipo_publicacion es "post_simple", secuencia debe tener 1 slide.
3. Si tipo_publicacion es "carrusel", secuencia debe tener 2 a 8 slides.
4. Ningun string obligatorio puede quedar vacio.
5. hashtags debe contener entre 3 y 12 hashtags relevantes.
6. No uses estilos visuales opuestos al universo Tu Eden.
7. Todo debe quedar en espanol claro, salvo que dentro del prompt_imagen uses tecnicismos visuales necesarios.
8. JSON perfectamente valido.
"""


def _parse_social_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _generate_instagram_result(
    topic_context: str,
    mode: str,
    selected_character_ids: list[str],
) -> dict:
    from core.gemini_client import GeminiClient

    tm = get_token_manager()
    gemini = GeminiClient(token_manager=tm, mock_mode=False)
    prompt = _build_instagram_prompt(topic_context, mode, selected_character_ids)
    raw = gemini.call_raw(prompt)
    parsed = _parse_social_json(raw)
    _validate_instagram_payload(parsed, selected_character_ids)
    return parsed


def _validate_instagram_payload(data: dict, selected_ids: list[str] | None = None) -> None:
    instagram = data.get("instagram")
    if not isinstance(instagram, dict):
        raise ValueError("La respuesta debe incluir el bloque instagram.")

    missing = [key for key in INSTAGRAM_REQUIRED_KEYS if key not in instagram]
    if missing:
        raise ValueError(f"Instagram no incluye: {', '.join(missing)}.")

    for key in (
        "tipo_publicacion",
        "objetivo_editorial",
        "justificacion_cantidad",
        "tamano_base",
        "estilo_visual_global",
        "hook_portada",
        "caption_principal",
        "caption_corto",
        "cta",
    ):
        if not str(instagram.get(key, "")).strip():
            raise ValueError(f"Instagram tiene el campo {key} vacío.")

    tipo_publicacion = str(instagram.get("tipo_publicacion", "")).strip().lower()
    if tipo_publicacion not in {"post_simple", "carrusel"}:
        raise ValueError("tipo_publicacion debe ser post_simple o carrusel.")

    cantidad = instagram.get("cantidad_imagenes")
    if not isinstance(cantidad, int) or cantidad < 1 or cantidad > 8:
        raise ValueError("cantidad_imagenes debe estar entre 1 y 8.")

    hashtags = instagram.get("hashtags")
    if not isinstance(hashtags, list) or len(hashtags) < 3 or len(hashtags) > 12:
        raise ValueError("hashtags debe contener entre 3 y 12 elementos.")
    if any(not str(tag).strip() for tag in hashtags):
        raise ValueError("hashtags contiene valores vacíos.")

    secuencia = instagram.get("secuencia")
    if not isinstance(secuencia, list) or len(secuencia) != cantidad:
        raise ValueError("cantidad_imagenes no coincide con la secuencia.")
    if tipo_publicacion == "post_simple" and cantidad != 1:
        raise ValueError("post_simple debe tener exactamente 1 imagen.")
    if tipo_publicacion == "carrusel" and cantidad < 2:
        raise ValueError("carrusel debe tener al menos 2 imágenes.")

    allowed_characters = {item["id"]: item for item in _selected_social_characters(selected_ids)}
    for index, slide in enumerate(secuencia, start=1):
        if not isinstance(slide, dict):
            raise ValueError(f"El slide {index} no es válido.")
        missing_slide = [key for key in INSTAGRAM_REQUIRED_SLIDE_KEYS if key not in slide]
        if missing_slide:
            raise ValueError(f"El slide {index} no incluye: {', '.join(missing_slide)}.")
        for key in (
            "rol_narrativo",
            "titulo_slide",
            "texto_slide",
            "relacion_con_blog",
            "continuidad_visual",
            "continuidad_textual",
            "tamano",
            "prompt_imagen",
        ):
            if not str(slide.get(key, "")).strip():
                raise ValueError(f"El slide {index} tiene {key} vacío.")
        if slide.get("orden") != index:
            raise ValueError(f"El slide {index} debe conservar el orden secuencial.")
        personajes = slide.get("personajes")
        if not isinstance(personajes, list):
            raise ValueError(f"El slide {index} debe incluir una lista de personajes.")
        for personaje in personajes:
            if not isinstance(personaje, dict):
                raise ValueError(f"Un personaje del slide {index} no es válido.")
            for field in ("id", "nombre", "perfil"):
                if not str(personaje.get(field, "")).strip():
                    raise ValueError(f"Falta {field} en un personaje del slide {index}.")
            if allowed_characters and personaje.get("id") not in allowed_characters:
                raise ValueError(f"Instagram usa un personaje no seleccionado: {personaje.get('id')}.")


def _instagram_mock_response(topic: str, selected_ids: list[str] | None = None) -> dict:
    selected_characters = _selected_social_characters(selected_ids)
    lead = selected_characters[0]
    support = selected_characters[1] if len(selected_characters) > 1 else lead
    base_title = topic.split("\n", 1)[0].replace("Título del borrador:", "").strip() or "Bienestar consciente"
    return {
        "instagram": {
            "tipo_publicacion": "carrusel",
            "objetivo_editorial": "Convertir el blog en una pieza guardable, clara y visualmente consistente para Instagram.",
            "cantidad_imagenes": 5,
            "justificacion_cantidad": "El tema necesita una portada potente y cuatro slides para desarrollar problema, contexto, accion y cierre sin saturar.",
            "tamano_base": "1080x1350 px",
            "estilo_visual_global": TU_EDEN_INSTAGRAM_STYLE,
            "hook_portada": f"{base_title}: lo esencial para entenderlo y aplicarlo con calma.",
            "caption_principal": (
                f"{base_title}.\n\n"
                "En Tu Eden buscamos traducir ideas complejas en bienestar aplicable, con una estetica serena y profesional. "
                "Desliza, guarda esta guia y vuelve a ella cuando necesites una referencia clara.\n\n"
                "Que parte del tema sientes mas cercana hoy?"
            ),
            "caption_corto": f"Una guia visual de Tu Eden sobre {base_title.lower()}. Guarda este carrusel para releerlo.",
            "cta": "Guarda este carrusel y cuentanos en comentarios que idea te gustaria profundizar.",
            "hashtags": ["#TuEden", "#Bienestar", "#SaludMental", "#VidaConsciente", "#InstagramEducativo"],
            "secuencia": [
                {
                    "orden": 1,
                    "rol_narrativo": "hook",
                    "titulo_slide": "La idea central",
                    "texto_slide": f"Entiende {base_title.lower()} desde una mirada clara, serena y aplicable.",
                    "relacion_con_blog": "Resume la promesa principal del blog.",
                    "continuidad_visual": "Abrir con retrato editorial luminoso y tonos salvia, arena e ivory.",
                    "continuidad_textual": "Introduce el problema y prepara el contexto del siguiente slide.",
                    "tamano": "1080x1350 px",
                    "personajes": [{"id": lead["id"], "nombre": lead["nombre"], "perfil": lead["perfil"]}],
                    "prompt_imagen": (
                        f"Instagram vertical 1080x1350 px. Portada editorial para Tu Eden. {TU_EDEN_INSTAGRAM_STYLE} "
                        f"Personaje principal: {lead['nombre']}, {lead['perfil']}. Apariencia consistente: {lead.get('apariencia_visual') or 'look sereno y profesional'}. Prompt fijo: {lead.get('prompt_visual') or 'Mantener la misma identidad visual.'}. "
                        f"Escena de bienestar premium, composicion limpia, luz natural suave, gesto tranquilo, espacio minimalista, sin texto incrustado. Tema: {base_title}."
                    ),
                },
                {
                    "orden": 2,
                    "rol_narrativo": "contexto",
                    "titulo_slide": "Por que importa",
                    "texto_slide": "El blog explica el contexto real y por que este tema afecta decisiones, energia y bienestar diario.",
                    "relacion_con_blog": "Traduce la introduccion y la relevancia del articulo.",
                    "continuidad_visual": "Mantener misma paleta y misma direccion de luz; plano mas abierto.",
                    "continuidad_textual": "Pasa del gancho a la comprension del problema.",
                    "tamano": "1080x1350 px",
                    "personajes": [{"id": lead["id"], "nombre": lead["nombre"], "perfil": lead["perfil"]}],
                    "prompt_imagen": (
                        f"Instagram vertical 1080x1350 px. Segundo slide coherente con portada, mismo universo Tu Eden. {TU_EDEN_INSTAGRAM_STYLE} "
                        f"Mismo personaje {lead['nombre']} con apariencia consistente: {lead.get('apariencia_visual') or 'look sereno y profesional'}. Prompt fijo: {lead.get('prompt_visual') or 'Mantener la misma identidad visual.'}. "
                        f"Entorno corporativo-wellness elegante, gesto reflexivo, composicion limpia, profundidad suave, continuidad visual con slide 1."
                    ),
                },
                {
                    "orden": 3,
                    "rol_narrativo": "insight",
                    "titulo_slide": "La clave",
                    "texto_slide": "Aqui se condensa el insight mas util del blog para que se entienda en segundos.",
                    "relacion_con_blog": "Resume el argumento o aprendizaje central del articulo.",
                    "continuidad_visual": "Introducir detalle de manos, libreta o objeto wellness sin romper la estetica.",
                    "continuidad_textual": "Convierte la explicacion en una idea memorable.",
                    "tamano": "1080x1350 px",
                    "personajes": [{"id": support["id"], "nombre": support["nombre"], "perfil": support["perfil"]}],
                    "prompt_imagen": (
                        f"Instagram vertical 1080x1350 px. Slide 3 de carrusel Tu Eden. {TU_EDEN_INSTAGRAM_STYLE} "
                        f"Personaje: {support['nombre']}, {support['perfil']}. Apariencia consistente: {support.get('apariencia_visual') or 'look sereno y profesional'}. Prompt fijo: {support.get('prompt_visual') or 'Mantener la misma identidad visual.'}. "
                        "Plano editorial con detalle de manos, cuaderno o taza de te, ambiente ordenado, luz lateral suave, elegancia zen, sin texto en imagen."
                    ),
                },
                {
                    "orden": 4,
                    "rol_narrativo": "paso",
                    "titulo_slide": "Como aplicarlo",
                    "texto_slide": "El carrusel baja la idea a una accion concreta, posible y profesional.",
                    "relacion_con_blog": "Convierte la parte practica del blog en una accion simple.",
                    "continuidad_visual": "Escena serena de accion cotidiana, misma paleta y mismo vestuario base.",
                    "continuidad_textual": "Lleva del insight a la aplicacion directa.",
                    "tamano": "1080x1350 px",
                    "personajes": [{"id": lead["id"], "nombre": lead["nombre"], "perfil": lead["perfil"]}],
                    "prompt_imagen": (
                        f"Instagram vertical 1080x1350 px. Slide 4 coherente con la serie Tu Eden. {TU_EDEN_INSTAGRAM_STYLE} "
                        f"Mismo personaje {lead['nombre']} y misma apariencia: {lead.get('apariencia_visual') or 'look sereno y profesional'}. Prompt fijo: {lead.get('prompt_visual') or 'Mantener la misma identidad visual.'}. "
                        "Mostrar una accion simple y elegante en espacio luminoso, composicion vertical, atmosfera calmada, profesional y confiable."
                    ),
                },
                {
                    "orden": 5,
                    "rol_narrativo": "cta",
                    "titulo_slide": "Guardalo para volver",
                    "texto_slide": "Cierra con una invitacion suave a guardar, reflexionar y seguir aprendiendo con Tu Eden.",
                    "relacion_con_blog": "Recoge la conclusion y la transforma en accion de comunidad.",
                    "continuidad_visual": "Final luminoso, sensacion de calma y cierre editorial consistente.",
                    "continuidad_textual": "Cierra la secuencia y deja una accion clara.",
                    "tamano": "1080x1350 px",
                    "personajes": [{"id": lead["id"], "nombre": lead["nombre"], "perfil": lead["perfil"]}],
                    "prompt_imagen": (
                        f"Instagram vertical 1080x1350 px. Slide final del carrusel Tu Eden. {TU_EDEN_INSTAGRAM_STYLE} "
                        f"Mismo personaje {lead['nombre']} con apariencia consistente: {lead.get('apariencia_visual') or 'look sereno y profesional'}. Prompt fijo: {lead.get('prompt_visual') or 'Mantener la misma identidad visual.'}. "
                        "Plano final inspirador, luz dorada suave, interiores minimalistas con elementos botanicos discretos, sensacion de paz, profesionalidad y cierre elegante."
                    ),
                },
            ],
        }
    }


def _build_single_tiktok_option_prompt(
    topic: str,
    mode: str,
    selected_ids: list[str],
    approved_hook: dict,
    option_number: int,
) -> str:
    selected_characters = _selected_social_characters(selected_ids)
    character_bank = "\n".join(
        f'- {item["id"]}: {item["nombre"]}, {item["perfil"]}' for item in selected_characters
    )
    label = approved_hook["tipo_de_angulo"]
    plantilla = approved_hook["plantilla_base"]
    gancho = approved_hook["gancho_texto"]
    promesa = approved_hook["promesa_valor"]
    visual = approved_hook["instruccion_visual_inicio"]
    return f"""Eres estratega senior de TikTok 2026 especializado en alta retención.

Entrada de trabajo ({mode}):
{topic}

Tarea:
- Genera solo la opción #{option_number}.
- Debes desarrollar exactamente este hook ya aprobado por el usuario:
- tipo_de_angulo: {label}
- plantilla_base: {plantilla}
- gancho_texto: {gancho}
- promesa_valor: {promesa}
- instruccion_visual_inicio: {visual}
- El resultado debe ser ejecutable de principio a fin en 40 a 70 segundos.
- Usa solo estos personajes:
{character_bank}

Reglas:
- Devuelve exactamente un objeto JSON con la clave "opcion".
- Mantén exactamente tipo_de_angulo, plantilla_base y gancho_texto.
- Mantén la misma promesa de valor y la misma instrucción visual, salvo limpieza mínima de puntuación si fuera imprescindible.
- Usa 5 bloques de guion detallado, salvo necesidad excepcional.
- Sé muy concreto pero breve: frases cortas, sin relleno.
- CTA conversacional real, no pidas solo likes o follows.
- JSON válido, sin markdown ni comentarios.

Formato exacto:
{{
  "opcion": {{
    "tipo_de_angulo": "Resultado Express",
    "plantilla_base": "Cómo logré [Resultado] en solo [X días] con este cambio simple.",
    "gancho_texto": "...",
    "promesa_valor": "...",
    "duracion_segundos": 52,
    "personajes": [
      {{"id": "mama", "nombre": "Mama", "perfil": "30 años, psicologa", "rol_en_video": "..."}}
    ],
    "instruccion_visual_inicio": "...",
    "puntos_retencion": [
      {{"paso": 1, "segundos": "0-3", "mensaje": "...", "micro_transicion": "..."}},
      {{"paso": 2, "segundos": "3-6", "mensaje": "...", "micro_transicion": "..."}},
      {{"paso": 3, "segundos": "6-9", "mensaje": "...", "micro_transicion": "..."}}
    ],
    "guion_detallado": [
      {{"bloque": 1, "segundos": "0-3", "objetivo": "...", "personaje": "Mama", "visual": "...", "dialogo": "...", "texto_pantalla": "...", "transicion": "..."}},
      {{"bloque": 2, "segundos": "3-12", "objetivo": "...", "personaje": "Mama", "visual": "...", "dialogo": "...", "texto_pantalla": "...", "transicion": "..."}},
      {{"bloque": 3, "segundos": "12-24", "objetivo": "...", "personaje": "Mama", "visual": "...", "dialogo": "...", "texto_pantalla": "...", "transicion": "..."}},
      {{"bloque": 4, "segundos": "24-38", "objetivo": "...", "personaje": "Mama", "visual": "...", "dialogo": "...", "texto_pantalla": "...", "transicion": "..."}},
      {{"bloque": 5, "segundos": "38-52", "objetivo": "...", "personaje": "Mama", "visual": "...", "dialogo": "...", "texto_pantalla": "...", "transicion": "..."}}
    ],
    "plan_rodaje": {{
      "locacion": "...",
      "tono": "...",
      "ritmo_edicion": "...",
      "vestuario": "...",
      "props": "...",
      "musica_sfx": "...",
      "tomas_clave": ["...", "...", "..."]
    }},
    "cta_engagement": "..."
  }}
}}
"""


def _extract_tiktok_option_candidate(parsed: dict) -> dict:
    if isinstance(parsed, dict) and isinstance(parsed.get("opcion"), dict):
        return parsed["opcion"]
    if isinstance(parsed, dict):
        tiktok = parsed.get("tiktok")
        if isinstance(tiktok, dict):
            opciones = tiktok.get("opciones")
            if isinstance(opciones, list) and opciones:
                return opciones[0]
    raise ValueError("Gemini no devolvió una opción TikTok válida.")


def _normalize_tiktok_option_candidate(opcion: dict, selected_ids: list[str]) -> dict:
    normalized = dict(opcion or {})
    label = normalized.get("tipo_de_angulo")
    allowed_characters = _selected_social_characters(selected_ids)
    allowed_names = [item["nombre"] for item in allowed_characters]
    fallback_name = allowed_names[0] if allowed_names else "Mama"

    if label in TIKTOK_HOOK_TEMPLATE_BY_LABEL:
        normalized["plantilla_base"] = TIKTOK_HOOK_TEMPLATE_BY_LABEL[label]

    guion = normalized.get("guion_detallado") if isinstance(normalized.get("guion_detallado"), list) else []
    puntos = normalized.get("puntos_retencion")
    if not isinstance(puntos, list) or len(puntos) != 3:
        derived_points = []
        for index, bloque in enumerate(guion[:3], start=1):
            if not isinstance(bloque, dict):
                continue
            derived_points.append({
                "paso": index,
                "segundos": str(bloque.get("segundos", "")),
                "mensaje": str(bloque.get("objetivo") or bloque.get("dialogo") or "").strip(),
                "micro_transicion": str(bloque.get("transicion") or "Corte rápido").strip(),
            })
        normalized["puntos_retencion"] = derived_points
    else:
        repaired_points = []
        for index, punto in enumerate(puntos, start=1):
            if isinstance(punto, dict):
                repaired_points.append({
                    "paso": punto.get("paso") or index,
                    "segundos": str(punto.get("segundos") or (guion[index - 1].get("segundos") if len(guion) >= index and isinstance(guion[index - 1], dict) else "")),
                    "mensaje": str(punto.get("mensaje") or (guion[index - 1].get("objetivo") if len(guion) >= index and isinstance(guion[index - 1], dict) else "")).strip(),
                    "micro_transicion": str(punto.get("micro_transicion") or (guion[index - 1].get("transicion") if len(guion) >= index and isinstance(guion[index - 1], dict) else "Corte rápido")).strip(),
                })
        normalized["puntos_retencion"] = repaired_points

    repaired_guion = []
    for bloque in guion:
        if not isinstance(bloque, dict):
            continue
        personaje = str(bloque.get("personaje", "")).strip()
        resolved = next((name for name in allowed_names if name in personaje), "")
        bloque["personaje"] = resolved or fallback_name
        repaired_guion.append(bloque)
    normalized["guion_detallado"] = repaired_guion

    plan = normalized.get("plan_rodaje")
    if isinstance(plan, dict):
        tomas = plan.get("tomas_clave")
        if not isinstance(tomas, list) or len(tomas) < 3:
            derived_takes = []
            for bloque in guion[:3]:
                if isinstance(bloque, dict):
                    visual = str(bloque.get("visual", "")).strip()
                    if visual:
                        derived_takes.append(visual)
            if derived_takes:
                plan["tomas_clave"] = derived_takes[:3]
        normalized["plan_rodaje"] = plan

    return normalized


def _apply_approved_hook_to_option(opcion: dict, approved_hook: dict) -> dict:
    normalized = dict(opcion or {})
    for key in ("tipo_de_angulo", "plantilla_base", "gancho_texto", "promesa_valor", "instruccion_visual_inicio"):
        normalized[key] = approved_hook[key]
    return normalized


def _validate_tiktok_option_candidate(
    opcion: dict,
    selected_ids: list[str],
    remaining_labels: list[str],
) -> None:
    if not isinstance(opcion, dict):
        raise ValueError("La opción generada no es un objeto válido.")
    missing = [key for key in TIKTOK_REQUIRED_OPTION_KEYS if key not in opcion]
    if missing:
        raise ValueError(f"La opción generada no incluye: {', '.join(missing)}.")

    label = opcion.get("tipo_de_angulo")
    if label not in remaining_labels:
        raise ValueError(f"La opción generada usó una plantilla no permitida: {label}.")

    plantilla_base = str(opcion.get("plantilla_base", "")).strip()
    if TIKTOK_HOOK_TEMPLATE_BY_LABEL.get(label) != plantilla_base:
        raise ValueError("La opción generada no respeta la plantilla base elegida.")

    duration = opcion.get("duracion_segundos")
    if not isinstance(duration, int) or duration < 40 or duration > 70:
        raise ValueError("La opción generada no respeta la duración 40-70 segundos.")

    gancho = str(opcion.get("gancho_texto", "")).strip()
    if not gancho or "[" in gancho or "]" in gancho:
        raise ValueError("La opción generada dejó placeholders o gancho vacío.")

    allowed_characters = {item["nombre"]: item for item in _selected_social_characters(selected_ids)}
    personajes = opcion.get("personajes")
    if not isinstance(personajes, list) or not personajes:
        raise ValueError("La opción generada no incluye personajes válidos.")
    for personaje in personajes:
        if personaje.get("nombre") not in allowed_characters:
            raise ValueError(f"La opción generada usa un personaje no permitido: {personaje.get('nombre')}.")

    puntos = opcion.get("puntos_retencion")
    if not isinstance(puntos, list) or len(puntos) != 3:
        raise ValueError("La opción generada debe incluir exactamente 3 puntos de retención.")
    for punto in puntos:
        if not isinstance(punto, dict):
            raise ValueError("Un punto de retención no es válido.")
        for field in ("paso", "segundos", "mensaje", "micro_transicion"):
            if not str(punto.get(field, "")).strip():
                raise ValueError(f"Falta {field} en un punto de retención.")

    guion = opcion.get("guion_detallado")
    if not isinstance(guion, list) or len(guion) < 5 or len(guion) > 8:
        raise ValueError("La opción generada debe incluir entre 5 y 8 bloques de guion.")
    for bloque in guion:
        if not isinstance(bloque, dict):
            raise ValueError("Un bloque del guion no es válido.")
        for field in ("bloque", "segundos", "objetivo", "personaje", "visual", "dialogo", "texto_pantalla", "transicion"):
            if not str(bloque.get(field, "")).strip():
                raise ValueError(f"Falta {field} en un bloque del guion.")
        if bloque.get("personaje") not in allowed_characters:
            raise ValueError(f"El guion usa un personaje no permitido: {bloque.get('personaje')}.")

    plan = opcion.get("plan_rodaje")
    if not isinstance(plan, dict):
        raise ValueError("La opción generada debe incluir plan_rodaje.")
    for field in ("locacion", "tono", "ritmo_edicion", "vestuario", "props", "musica_sfx"):
        if not str(plan.get(field, "")).strip():
            raise ValueError(f"Falta plan_rodaje.{field}.")
    tomas = plan.get("tomas_clave")
    if not isinstance(tomas, list) or len(tomas) < 3:
        raise ValueError("La opción generada debe incluir al menos 3 tomas clave.")


def _build_tiktok_checklist_from_options(opciones: list[dict]) -> dict:
    first = opciones[0]
    plan = first.get("plan_rodaje", {})
    takes = plan.get("tomas_clave", [])
    return {
        "preproduccion": [
            {"item": "Definir hook principal", "detalle": str(first.get("gancho_texto", ""))[:120]},
            {"item": "Preparar locación y vestuario", "detalle": f"{plan.get('locacion', '')} | {plan.get('vestuario', '')}".strip(" |")},
            {"item": "Revisar props", "detalle": str(plan.get("props", ""))[:120]},
        ],
        "grabacion": [
            {"item": "Grabar apertura", "detalle": str(first.get("instruccion_visual_inicio", ""))[:120]},
            {"item": "Cubrir tomas clave", "detalle": str('; '.join(takes[:3]))[:120]},
            {"item": "Cerrar con CTA", "detalle": str(first.get("cta_engagement", ""))[:120]},
        ],
        "edicion": [
            {"item": "Aplicar ritmo", "detalle": str(plan.get("ritmo_edicion", ""))[:120]},
            {"item": "Insertar textos", "detalle": str(first.get("promesa_valor", ""))[:120]},
            {"item": "Añadir música y remates", "detalle": str(plan.get("musica_sfx", ""))[:120]},
        ],
    }


def _generate_tiktok_2026_result(
    topic_context: str,
    mode: str,
    selected_character_ids: list[str],
    selected_hooks: list[dict] | None = None,
) -> dict:
    from core.gemini_client import GeminiClient

    tm = get_token_manager()
    gemini = GeminiClient(token_manager=tm, mock_mode=False)
    approved_hooks = _normalize_selected_hooks(selected_hooks)
    if not approved_hooks:
        approved_hooks = _generate_tiktok_hook_candidates(topic_context, mode, selected_character_ids)["hooks"]
    opciones: list[dict] = []

    for option_number, approved_hook in enumerate(approved_hooks, start=1):
        option_prompt = _build_single_tiktok_option_prompt(
            topic=topic_context,
            mode=mode,
            selected_ids=selected_character_ids,
            approved_hook=approved_hook,
            option_number=option_number,
        )
        prompt_variants = [option_prompt, option_prompt + TIKTOK_COMPACT_RETRY_SUFFIX]
        last_error: Exception | None = None

        for attempt, prompt_variant in enumerate(prompt_variants, start=1):
            raw = gemini.call_raw(prompt_variant)
            try:
                parsed = _parse_social_json(raw)
                opcion = _apply_approved_hook_to_option(
                    _normalize_tiktok_option_candidate(
                        _extract_tiktok_option_candidate(parsed),
                        selected_character_ids,
                    ),
                    approved_hook,
                )
                _validate_tiktok_option_candidate(
                    opcion,
                    selected_character_ids,
                    [approved_hook["tipo_de_angulo"]],
                )
                opciones.append(opcion)
                last_error = None
                break
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                app.logger.warning(
                    "TikTok option %s failed on attempt %s: %s",
                    option_number,
                    attempt,
                    exc,
                )

        if last_error is not None:
            raise ValueError(f"No se pudo generar la opción TikTok #{option_number}: {last_error}")

    result = {
        "tiktok": {
            "opciones": opciones,
        }
    }
    _validate_tiktok_2026_payload(
        result,
        selected_character_ids,
        expected_labels=[hook["tipo_de_angulo"] for hook in approved_hooks],
    )
    return result


def _validate_tiktok_2026_payload(
    data: dict,
    selected_ids: list[str] | None = None,
    expected_labels: list[str] | None = None,
) -> None:
    tiktok = data.get("tiktok")
    if not isinstance(tiktok, dict):
        raise ValueError("La respuesta debe incluir el bloque tiktok.")

    opciones = tiktok.get("opciones")
    expected_count = len(expected_labels) if expected_labels else 5
    if not isinstance(opciones, list) or len(opciones) != expected_count:
        raise ValueError(f"TikTok 2026 requiere exactamente {expected_count} opciones de vídeo.")

    allowed_characters = {
        item["nombre"]: item for item in _selected_social_characters(selected_ids)
    }
    used_labels = []
    for idx, opcion in enumerate(opciones, start=1):
        if not isinstance(opcion, dict):
            raise ValueError(f"La opción {idx} no es un objeto válido.")
        missing = [key for key in TIKTOK_REQUIRED_OPTION_KEYS if key not in opcion]
        if missing:
            raise ValueError(f"La opción {idx} no incluye: {', '.join(missing)}.")

        label = opcion.get("tipo_de_angulo")
        if label not in TIKTOK_HOOK_LABELS:
            raise ValueError(f"La opción {idx} usa una plantilla no permitida: {label}.")
        used_labels.append(label)

        plantilla_base = str(opcion.get("plantilla_base", "")).strip()
        if plantilla_base not in TIKTOK_HOOK_TEMPLATES:
            raise ValueError(f"La opción {idx} no usa una plantilla base válida.")
        if TIKTOK_HOOK_TEMPLATE_BY_LABEL.get(label) != plantilla_base:
            raise ValueError(
                f"La opción {idx} no corresponde entre tipo_de_angulo y plantilla_base."
            )

        for key in ("gancho_texto", "instruccion_visual_inicio", "cta_engagement"):
            if not str(opcion.get(key, "")).strip():
                raise ValueError(f"La opción {idx} tiene el campo {key} vacío.")

        if not str(opcion.get("promesa_valor", "")).strip():
            raise ValueError(f"La opción {idx} tiene promesa_valor vacío.")

        duration = opcion.get("duracion_segundos")
        if not isinstance(duration, int) or duration < 40 or duration > 70:
            raise ValueError(f"La opción {idx} debe durar entre 40 y 70 segundos.")

        gancho = str(opcion.get("gancho_texto", "")).strip()
        if "[" in gancho or "]" in gancho:
            raise ValueError(f"La opción {idx} mantiene placeholders sin adaptar en gancho_texto.")

        personajes = opcion.get("personajes")
        if not isinstance(personajes, list) or not personajes:
            raise ValueError(f"La opción {idx} debe incluir al menos un personaje.")
        for char_idx, personaje in enumerate(personajes, start=1):
            if not isinstance(personaje, dict):
                raise ValueError(f"El personaje {char_idx} de la opción {idx} no es válido.")
            for field in ("id", "nombre", "perfil", "rol_en_video"):
                if not str(personaje.get(field, "")).strip():
                    raise ValueError(
                        f"El personaje {char_idx} de la opción {idx} no incluye {field}."
                    )
            if personaje.get("id") not in _social_participant_map():
                raise ValueError(f"La opción {idx} usa un personaje desconocido: {personaje.get('id')}.")
            if allowed_characters and personaje.get("nombre") not in allowed_characters:
                raise ValueError(f"La opción {idx} usa un personaje no seleccionado: {personaje.get('nombre')}.")

        puntos = opcion.get("puntos_retencion")
        if not isinstance(puntos, list) or len(puntos) != 3:
            raise ValueError(f"La opción {idx} debe tener 3 puntos de retención.")
        for point_idx, punto in enumerate(puntos, start=1):
            if not isinstance(punto, dict):
                raise ValueError(f"El punto {point_idx} de la opción {idx} no es válido.")
            for field in ("paso", "segundos", "mensaje", "micro_transicion"):
                if field not in punto or not str(punto.get(field, "")).strip():
                    raise ValueError(
                        f"El punto {point_idx} de la opción {idx} no incluye {field}."
                    )

        guion = opcion.get("guion_detallado")
        if not isinstance(guion, list) or len(guion) < 5 or len(guion) > 8:
            raise ValueError(f"La opción {idx} debe incluir entre 5 y 8 bloques de guion detallado.")
        for block_idx, bloque in enumerate(guion, start=1):
            if not isinstance(bloque, dict):
                raise ValueError(f"El bloque {block_idx} de la opción {idx} no es válido.")
            for field in ("bloque", "segundos", "objetivo", "personaje", "visual", "dialogo", "texto_pantalla", "transicion"):
                if field not in bloque or not str(bloque.get(field, "")).strip():
                    raise ValueError(
                        f"El bloque {block_idx} de la opción {idx} no incluye {field}."
                    )
            if allowed_characters and bloque.get("personaje") not in allowed_characters:
                raise ValueError(
                    f"El bloque {block_idx} de la opción {idx} usa un personaje no seleccionado: {bloque.get('personaje')}."
                )

        plan = opcion.get("plan_rodaje")
        if not isinstance(plan, dict):
            raise ValueError(f"La opción {idx} debe incluir plan_rodaje.")
        for field in ("locacion", "tono", "ritmo_edicion", "vestuario", "props", "musica_sfx"):
            if not str(plan.get(field, "")).strip():
                raise ValueError(f"La opción {idx} tiene plan_rodaje.{field} vacío.")
        tomas_clave = plan.get("tomas_clave")
        if not isinstance(tomas_clave, list) or len(tomas_clave) < 3:
            raise ValueError(f"La opción {idx} debe incluir al menos 3 tomas clave.")
        if any(not str(toma).strip() for toma in tomas_clave):
            raise ValueError(f"La opción {idx} tiene una toma clave vacía.")

    if expected_labels:
        if set(used_labels) != set(expected_labels):
            raise ValueError("Las opciones generadas no coinciden con los hooks seleccionados.")
    elif len(set(used_labels)) != 5:
        raise ValueError("Las 5 opciones deben usar 5 plantillas distintas del banco maestro.")

@app.route("/social")
@login_required
def social_page():
    return render_template("social.html")


@app.get("/api/social/historial")
@login_required
def api_social_historial():
    """Devuelve la lista de borradores de contenido social guardados."""
    _prune_expired_social_drafts()
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


@app.get("/api/social/wordpress-posts")
@login_required
def api_social_wordpress_posts():
    """Lista posts publicados en WordPress para reutilizarlos como tema social."""
    try:
        limit = request.args.get("limit", 30, type=int)
        return jsonify({"ok": True, "items": _fetch_published_wp_posts(limit)})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/social/participants")
@login_required
def api_social_participants_list():
    return jsonify({"ok": True, "items": _load_social_participants()})


@app.post("/api/social/participants")
@login_required
def api_social_participants_create():
    body = request.get_json(force=True) or {}
    nombre = str(body.get("nombre", "")).strip()
    perfil = str(body.get("perfil", "")).strip()
    apariencia_visual = str(body.get("apariencia_visual", "")).strip()
    prompt_visual = str(body.get("prompt_visual", "")).strip()
    participant_id = _safe_social_participant_id(body.get("id") or nombre)
    if not nombre or not perfil or not participant_id:
        return jsonify({"error": "Nombre y perfil son obligatorios."}), 400

    participants = _load_social_participants()
    if any(item["id"] == participant_id for item in participants):
        return jsonify({"error": "Ya existe un participante con ese identificador."}), 409

    participants.append({
        "id": participant_id,
        "nombre": nombre,
        "perfil": perfil,
        "apariencia_visual": apariencia_visual,
        "prompt_visual": prompt_visual,
    })
    _save_social_participants(participants)
    return jsonify({
        "ok": True,
        "item": {
            "id": participant_id,
            "nombre": nombre,
            "perfil": perfil,
            "apariencia_visual": apariencia_visual,
            "prompt_visual": prompt_visual,
        },
    })


@app.put("/api/social/participants/<participant_id>")
@login_required
def api_social_participants_update(participant_id):
    body = request.get_json(force=True) or {}
    nombre = str(body.get("nombre", "")).strip()
    perfil = str(body.get("perfil", "")).strip()
    has_appearance = "apariencia_visual" in body
    has_prompt = "prompt_visual" in body
    apariencia_visual = str(body.get("apariencia_visual", "")).strip()
    prompt_visual = str(body.get("prompt_visual", "")).strip()
    if not nombre or not perfil:
        return jsonify({"error": "Nombre y perfil son obligatorios."}), 400

    participants = _load_social_participants()
    updated = None
    for item in participants:
        if item["id"] == participant_id:
            item["nombre"] = nombre
            item["perfil"] = perfil
            if has_appearance:
                item["apariencia_visual"] = apariencia_visual
            if has_prompt:
                item["prompt_visual"] = prompt_visual
            updated = item
            break
    if not updated:
        return jsonify({"error": "Participante no encontrado."}), 404

    _save_social_participants(participants)
    return jsonify({"ok": True, "item": updated})


@app.delete("/api/social/participants/<participant_id>")
@login_required
def api_social_participants_delete(participant_id):
    participants = _load_social_participants()
    filtered = [item for item in participants if item["id"] != participant_id]
    if len(filtered) == len(participants):
        return jsonify({"error": "Participante no encontrado."}), 404
    _save_social_participants(filtered)
    return jsonify({"ok": True})


@app.get("/api/social/historial/<filename>")
@login_required
def api_social_borrador(filename):
    """Devuelve un borrador social guardado."""
    path = _safe_social_path(filename)
    if not path or not path.exists():
        return jsonify({"error": "No encontrado"}), 404
    return jsonify(json.loads(path.read_text(encoding="utf-8")))


@app.patch("/api/social/historial/<filename>")
@login_required
def api_social_borrador_update(filename):
    path = _safe_social_path(filename)
    if not path or not path.exists():
        return jsonify({"error": "No encontrado"}), 404

    body = request.get_json(force=True) or {}
    topic = str(body.get("topic", "")).strip()
    if not topic:
        return jsonify({"error": "El topic es obligatorio."}), 400

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("meta", {})["topic"] = topic[:120]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True})


@app.delete("/api/social/historial/<filename>")
@login_required
def api_social_borrador_delete(filename):
    path = _safe_social_path(filename)
    if not path or not path.exists():
        return jsonify({"error": "No encontrado"}), 404
    path.unlink(missing_ok=True)
    return jsonify({"ok": True})


@app.post("/api/social/hooks")
@login_required
def api_social_hooks():
    """Genera solo los 5 hooks candidatos para que el usuario elija."""
    data = request.get_json(force=True)
    mode = data.get("mode", "scratch")
    topic = (data.get("input") or "").strip()
    selected_post_id = data.get("selected_post_id")
    selected_character_ids = _normalize_social_character_ids(data.get("selected_characters"))
    if not selected_character_ids:
        return jsonify({"error": "Selecciona al menos un participante para este tema."}), 400

    try:
        resolved_topic, input_instruction, selected_post_meta = _resolve_social_generation_context(
            mode,
            topic,
            selected_post_id,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    mock_mode, _ = _get_modes()
    try:
        if mock_mode:
            hooks_payload = _generate_tiktok_hook_candidates_from_mock(resolved_topic, selected_character_ids)
        else:
            hooks_payload = _generate_tiktok_hook_candidates(input_instruction, mode, selected_character_ids)
    except ValueError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "ok": True,
        "data": {
            "hooks": hooks_payload["hooks"],
            "meta": {
                "topic": resolved_topic[:120],
                "mode": mode,
                "mock": mock_mode,
                "selected_characters": selected_character_ids,
                "source_post": selected_post_meta,
            },
        },
    })


@app.post("/api/social/generar")
@login_required
def api_social_generar():
    """Genera propuestas TikTok 2026 usando Gemini."""
    data = request.get_json(force=True)
    mode      = data.get("mode", "scratch")   # "borrador" | "scratch"
    topic     = (data.get("input") or "").strip()
    selected_post_id = data.get("selected_post_id")
    platforms = ["tiktok"]
    selected_character_ids = _normalize_social_character_ids(data.get("selected_characters"))
    selected_hooks = _normalize_selected_hooks(data.get("selected_hooks"))
    if not selected_character_ids:
        return jsonify({"error": "Selecciona al menos un participante para este tema."}), 400

    try:
        topic, input_instruction, selected_post_meta = _resolve_social_generation_context(
            mode,
            topic,
            selected_post_id,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    mock_mode, _ = _get_modes()

    if mock_mode:
        import time as _time
        _time.sleep(0.8)
        result = _social_mock_response(topic, selected_character_ids)
        if selected_hooks:
            selected_labels = {hook["tipo_de_angulo"] for hook in selected_hooks}
            result["tiktok"]["opciones"] = [
                option for option in result.get("tiktok", {}).get("opciones", [])
                if option.get("tipo_de_angulo") in selected_labels
            ]
            result["tiktok"]["checklist"] = _build_tiktok_checklist_from_options(result["tiktok"]["opciones"])
            _validate_tiktok_2026_payload(
                result,
                selected_character_ids,
                expected_labels=[hook["tipo_de_angulo"] for hook in selected_hooks],
            )
        else:
            _validate_tiktok_2026_payload(result, selected_character_ids)
    else:
        try:
            result = _generate_tiktok_2026_result(
                topic_context=input_instruction,
                mode=mode,
                selected_character_ids=selected_character_ids,
                selected_hooks=selected_hooks,
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    result.get("tiktok", {}).pop("checklist", None)

    # Guardar siempre el resultado con metadatos
    meta = {
        "mode":       mode,
        "mock":       mock_mode,
        "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms":  platforms,
        "selected_characters": selected_character_ids,
        "source_post": selected_post_meta,
        "selected_hooks": selected_hooks,
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


def _social_mock_response(topic: str, selected_ids: list[str] | None = None) -> dict:
    """Respuesta mock para TikTok 2026 usando hooks del banco maestro."""
    ts = topic[:30] if len(topic) > 30 else topic
    selected_characters = _selected_social_characters(selected_ids)
    lead_character = selected_characters[0]

    def build_cast(lead_role: str) -> list[dict]:
        cast = []
        for index, character in enumerate(selected_characters):
            if index == 0:
                role = lead_role
            elif character["id"] == "hija":
                role = "reaccion emocional y prueba visual"
            else:
                role = "apoyo, contraste y dinamica familiar"
            cast.append(
                {
                    "id": character["id"],
                    "nombre": character["nombre"],
                    "rol_en_video": role,
                }
            )
        return cast

    return {
        "tiktok": {
            "opciones": [
                {
                    "tipo_de_angulo": "Resultado Express",
                    "plantilla_base": "Cómo logré [Resultado] en solo [X días] con este cambio simple.",
                    "gancho_texto": f"Cómo logré disfrutar mejor {ts} en solo 3 días con este cambio simple.",
                    "promesa_valor": f"La audiencia entiende qué ajuste concreto aplicar para que {ts} salga mejor desde la próxima vez.",
                    "duracion_segundos": 51,
                    "personajes": build_cast(f"conduce el cambio simple y explica el resultado desde su experiencia como {lead_character['perfil']}") ,
                    "instruccion_visual_inicio": "Abre mostrando el resultado final a pantalla completa y corta a tu mano deteniendo un cronómetro en el segundo 1.",
                    "puntos_retencion": [
                        {"paso": 1, "segundos": "0-3", "mensaje": f"Enseña el antes y después real que conseguiste con {ts}.", "micro_transicion": "Zoom in duro al subtítulo con corte seco."},
                        {"paso": 2, "segundos": "3-6", "mensaje": "Aísla el cambio puntual que produjo el resultado más rápido.", "micro_transicion": "Jump cut con texto rojo y pequeño shake."},
                        {"paso": 3, "segundos": "6-9", "mensaje": "Remata con el beneficio concreto para quien quiera replicarlo hoy.", "micro_transicion": "Match cut al resultado con sonido click."}
                    ],
                    "guion_detallado": [
                        {"bloque": 1, "segundos": "0-3", "objetivo": "Frenar el scroll con el resultado final.", "personaje": lead_character["nombre"], "visual": f"Plano recurso del resultado ideal de {ts} y entrada inmediata a camara.", "dialogo": f"Asi logre que {ts} saliera mucho mejor en solo tres dias con un cambio simple.", "texto_pantalla": "Resultado real en 3 dias", "transicion": "Whip pan al rostro."},
                        {"bloque": 2, "segundos": "3-10", "objetivo": "Instalar el problema comun.", "personaje": lead_character["nombre"], "visual": "Plano medio explicando el error tipico con gesto claro de stop.", "dialogo": f"El fallo era intentar hacerlo igual que siempre, sin mirar el detalle que de verdad cambia {ts}.", "texto_pantalla": "El error que frena todo", "transicion": "Jump cut con zoom al gesto."},
                        {"bloque": 3, "segundos": "10-22", "objetivo": "Explicar el cambio simple.", "personaje": lead_character["nombre"], "visual": "Demostracion paso a paso del ajuste con plano de manos o accion concreta.", "dialogo": f"Lo que cambie fue esto: preparar primero la parte clave, observar la reaccion y corregir sobre la marcha en vez de improvisar.", "texto_pantalla": "Cambio simple", "transicion": "Corte a detalle de la accion."},
                        {"bloque": 4, "segundos": "22-34", "objetivo": "Añadir prueba social o familiar.", "personaje": selected_characters[-1]["nombre"], "visual": "Reaccion autentica al aplicar el cambio, sin posar.", "dialogo": "Cuando lo hicimos asi, la experiencia se noto enseguida: menos caos, mas disfrute y mucha mas atencion en lo importante.", "texto_pantalla": "Se nota al momento", "transicion": "Match cut a la reaccion."},
                        {"bloque": 5, "segundos": "34-45", "objetivo": "Bajar a instruccion concreta.", "personaje": lead_character["nombre"], "visual": "Plano frontal enumerando el mini paso a paso con dedos o texto sobreimpreso.", "dialogo": f"Si quieres replicarlo, haz tres cosas: define el objetivo, quita distracciones y deja que {ts} tenga un ritmo mas simple y consciente.", "texto_pantalla": "3 pasos para copiarlo", "transicion": "Flash de texto numerado."},
                        {"bloque": 6, "segundos": "45-51", "objetivo": "Cerrar con CTA util.", "personaje": lead_character["nombre"], "visual": "Plano cercano a camara, energia alta y gesto de invitacion.", "dialogo": "Dime que parte te cuesta mas y te digo que cambio simple probar primero en tu caso.", "texto_pantalla": "Te respondo tu caso", "transicion": "Freeze final con subtitulo."}
                    ],
                    "plan_rodaje": {
                        "locacion": "Espacio real donde ocurra la accion principal, con un antes y despues visible en pocos segundos.",
                        "tono": "Cercano, practico y con autoridad tranquila.",
                        "ritmo_edicion": "Agil, con cortes cada 2-4 segundos y refuerzo visual en cada idea clave.",
                        "vestuario": "Ropa cotidiana, limpia y coherente con una familia real; sin looks demasiado producidos.",
                        "props": f"Elemento central relacionado con {ts}, cronometro o movil para marcar el cambio, y apoyo visual del antes/despues.",
                        "musica_sfx": "Base ligera y dinamica, con whoosh suave en cambios y click de refuerzo en el beneficio.",
                        "tomas_clave": [
                            "Resultado final en el primer frame.",
                            "Detalle de la accion concreta que cambia el resultado.",
                            "Reaccion autentica del participante secundario."
                        ]
                    },
                    "cta_engagement": "Cuéntame qué resultado querrías lograr tú y te digo qué cambio simple probar primero."
                },
                {
                    "tipo_de_angulo": "Advertencia",
                    "plantilla_base": "Deja de hacer [Acción común] si no quieres seguir [Acción].",
                    "gancho_texto": f"Deja de improvisar con {ts} si no quieres seguir perdiéndote lo mejor de la experiencia.",
                    "promesa_valor": f"La audiencia detecta el error que sabotea {ts} y sale con una correccion clara para evitarlo.",
                    "duracion_segundos": 58,
                    "personajes": build_cast("detecta el error comun y lo corrige en vivo"),
                    "instruccion_visual_inicio": "Arranca con tres etiquetas rojas entrando una a una mientras niegas con la cabeza a cámara.",
                    "puntos_retencion": [
                        {"paso": 1, "segundos": "0-3", "mensaje": "Nombra la acción común que arruina el resultado aunque parezca inocente.", "micro_transicion": "Flash rojo y hard cut al gesto facial."},
                        {"paso": 2, "segundos": "3-6", "mensaje": "Sube la tensión con la consecuencia visible de seguir igual.", "micro_transicion": "Zoom digital al detalle problemático y pausa corta."},
                        {"paso": 3, "segundos": "6-9", "mensaje": "Corrige el hábito con una acción concreta que cualquiera pueda aplicar.", "micro_transicion": "Split screen antes/después con golpe sonoro."}
                    ],
                    "guion_detallado": [
                        {"bloque": 1, "segundos": "0-3", "objetivo": "Golpe de advertencia.", "personaje": lead_character["nombre"], "visual": "Plano frontal negando con la cabeza y error visible en pantalla.", "dialogo": f"Deja de improvisar con {ts} si no quieres cargarte la experiencia otra vez.", "texto_pantalla": "Deja de hacerlo asi", "transicion": "Hard cut con alarma suave."},
                        {"bloque": 2, "segundos": "3-11", "objetivo": "Mostrar el error real.", "personaje": lead_character["nombre"], "visual": "Plano detalle del error mientras se explica por encima.", "dialogo": f"La mayoria falla aqui porque quiere correr demasiado o meter demasiadas cosas a la vez con {ts}.", "texto_pantalla": "Error comun", "transicion": "Zoom al detalle."},
                        {"bloque": 3, "segundos": "11-24", "objetivo": "Explicar la consecuencia.", "personaje": selected_characters[-1]["nombre"], "visual": "Reaccion de frustracion o desconexion que refleje la consecuencia.", "dialogo": "Y lo peor es que no solo sale peor: tambien hace que la gente se canse antes, se despiste o deje de disfrutar.", "texto_pantalla": "Consecuencia real", "transicion": "Push-in a la reaccion."},
                        {"bloque": 4, "segundos": "24-38", "objetivo": "Enseñar la correccion practica.", "personaje": lead_character["nombre"], "visual": "Demostracion comparativa entre hacerlo mal y hacerlo bien.", "dialogo": f"Haz esto en cambio: baja el ritmo al inicio, marca una sola prioridad y construye {ts} sobre una accion clara desde el principio.", "texto_pantalla": "Haz esto en cambio", "transicion": "Split screen antes/despues."},
                        {"bloque": 5, "segundos": "38-50", "objetivo": "Cerrar con mini checklist.", "personaje": lead_character["nombre"], "visual": "Plano medio enumerando tres checks cortos.", "dialogo": "Si quieres saber si lo estas haciendo bien, revisa esto: se entiende rapido, nadie se pierde y el resultado mejora desde el minuto uno.", "texto_pantalla": "Checklist rapida", "transicion": "Texto en bullets."},
                        {"bloque": 6, "segundos": "50-58", "objetivo": "CTA conversacional.", "personaje": lead_character["nombre"], "visual": "Plano cercano con gesto de pregunta directa.", "dialogo": "Si te has visto en este error, escribeme cual es tu caso y te digo como corregirlo sin complicarte.", "texto_pantalla": "Te ayudo a corregirlo", "transicion": "Fade corto al final."}
                    ],
                    "plan_rodaje": {
                        "locacion": "Lugar donde el error pueda verse en primeros planos y compararse con la version corregida.",
                        "tono": "Directo, preventivo y con energia de alerta util.",
                        "ritmo_edicion": "Muy dinamico al inicio y mas explicativo desde la correccion.",
                        "vestuario": "Neutro y funcional; colores que permitan ver bien texto y gestos.",
                        "props": f"Elemento que muestre el error habitual en {ts}, etiquetas rojas y apoyo visual de checklist.",
                        "musica_sfx": "Beat tenso al inicio, con sonidos de freno y alivio al mostrar la correccion.",
                        "tomas_clave": [
                            "Macro del error real.",
                            "Comparativa error vs correccion.",
                            "Plano de reaccion del participante secundario."
                        ]
                    },
                    "cta_engagement": "Si te viste haciendo esto, escríbeme qué parte te cuesta más y te respondo con una alternativa realista."
                },
                {
                    "tipo_de_angulo": "Hubiera Pagado",
                    "plantilla_base": "Hubiera pagado por saber esto antes de cumplir los [Edad/Etapa].",
                    "gancho_texto": f"Hubiera pagado por saber esto antes de planear {ts} por primera vez.",
                    "promesa_valor": f"La audiencia recibe una leccion concreta que evita errores caros o frustrantes al organizar {ts}.",
                    "duracion_segundos": 47,
                    "personajes": build_cast("confiesa el aprendizaje y lo convierte en consejo util"),
                    "instruccion_visual_inicio": "Empieza con una toma handheld del fallo real antes de mirar a cámara y decir la verdad incómoda.",
                    "puntos_retencion": [
                        {"paso": 1, "segundos": "0-3", "mensaje": "Confiesa el error que te habría ahorrado tiempo, frustración o dinero.", "micro_transicion": "Corte documental sin música y sin estabilizar del todo."},
                        {"paso": 2, "segundos": "3-6", "mensaje": f"Conecta esa lección con el momento clave de preparar {ts}.", "micro_transicion": "Push-in lento al rostro con subtítulo blanco."},
                        {"paso": 3, "segundos": "6-9", "mensaje": "Deja un consejo simple que la audiencia pueda aplicar antes de cometer el mismo fallo.", "micro_transicion": "Jump cut a demostración simple con silencio breve."}
                    ],
                    "guion_detallado": [
                        {"bloque": 1, "segundos": "0-3", "objetivo": "Abrir con honestidad.", "personaje": lead_character["nombre"], "visual": "Plano handheld del fallo y mirada directa a camara.", "dialogo": f"Hubiera pagado por saber esto antes de planear {ts} por primera vez.", "texto_pantalla": "La leccion que me falto", "transicion": "Corte documental."},
                        {"bloque": 2, "segundos": "3-12", "objetivo": "Contar el error.", "personaje": lead_character["nombre"], "visual": "Recreacion corta del momento en que se cometio el fallo.", "dialogo": "Yo pensaba que hacerlo asi era suficiente, pero en realidad estaba complicando todo desde el primer paso.", "texto_pantalla": "Pensaba que estaba bien", "transicion": "Jump cut a recuerdo."},
                        {"bloque": 3, "segundos": "12-24", "objetivo": "Traducirlo en aprendizaje.", "personaje": lead_character["nombre"], "visual": "Plano medio explicando con calma y un gesto de insight.", "dialogo": f"Lo que aprendi es que {ts} funciona mucho mejor cuando priorizas una sola necesidad real y no intentas controlarlo todo de golpe.", "texto_pantalla": "La leccion real", "transicion": "Zoom suave."},
                        {"bloque": 4, "segundos": "24-36", "objetivo": "Poner ejemplo practico.", "personaje": selected_characters[-1]["nombre"], "visual": "Escena corta donde el consejo se aplica y mejora la dinamica.", "dialogo": "Cuando lo cambiamos, todo fue mas fluido, mas facil y mucho mas disfrutable para todos los que estaban ahi.", "texto_pantalla": "Se noto enseguida", "transicion": "Match cut a escena buena."},
                        {"bloque": 5, "segundos": "36-47", "objetivo": "Cerrar con consejo + CTA.", "personaje": lead_character["nombre"], "visual": "Plano cercano final con tono de confidencia.", "dialogo": "Si estas en esa etapa ahora mismo, dime donde te bloqueas y te comparto la leccion que a mi me habria ahorrado mas.", "texto_pantalla": "Te cuento que haria distinto", "transicion": "Cierre limpio con hold final."}
                    ],
                    "plan_rodaje": {
                        "locacion": "Entorno autentico donde el error y la correccion puedan verse sin decorado excesivo.",
                        "tono": "Honesto, cercano y vulnerable, pero siempre util.",
                        "ritmo_edicion": "Documental al principio y mas limpio conforme llega la solucion.",
                        "vestuario": "Natural, del dia a dia, sin estilismo artificial.",
                        "props": f"Objeto o situacion que simbolice el fallo cometido en {ts} y su correccion posterior.",
                        "musica_sfx": "Muy minima al principio, con entrada emocional suave en la resolucion.",
                        "tomas_clave": [
                            "Fallo real grabado con camara en mano.",
                            "Primer plano al contar la leccion.",
                            "Escena de correccion funcionando."
                        ]
                    },
                    "cta_engagement": "Dime en qué etapa te pilló más verde esto y te comparto qué habría hecho distinto desde el principio."
                },
                {
                    "tipo_de_angulo": "Señales",
                    "plantilla_base": "X señales de que tu [Producto/Hábito] te está arruinando.",
                    "gancho_texto": f"3 señales de que tu forma de preparar {ts} te está arruinando el plan.",
                    "promesa_valor": f"La audiencia aprende a detectar tres alertas claras para corregir {ts} antes de que salga mal.",
                    "duracion_segundos": 63,
                    "personajes": build_cast("detecta las señales y guia el diagnostico en pantalla"),
                    "instruccion_visual_inicio": "El primer frame es el resultado final ocupando toda la pantalla; después entra tu mano señalando el detalle con un whip pan.",
                    "puntos_retencion": [
                        {"paso": 1, "segundos": "0-3", "mensaje": "Lanza la primera señal visual como alerta inmediata.", "micro_transicion": "Whip pan del resultado a tu cara en el segundo 3."},
                        {"paso": 2, "segundos": "3-6", "mensaje": "Muestra la segunda señal en una situación cotidiana para que sea reconocible.", "micro_transicion": "Zoom al elemento protagonista con subtítulo anclado."},
                        {"paso": 3, "segundos": "6-9", "mensaje": "Cierra con la señal definitiva y la corrección más simple.", "micro_transicion": "Corte rápido a demostración física con sonido snap."}
                    ],
                    "guion_detallado": [
                        {"bloque": 1, "segundos": "0-3", "objetivo": "Abrir con lista util.", "personaje": lead_character["nombre"], "visual": "Primer frame del resultado malo y gesto de alerta en camara.", "dialogo": f"Tres señales de que tu forma de preparar {ts} te esta arruinando el plan.", "texto_pantalla": "3 señales claras", "transicion": "Whoosh con numero 1."},
                        {"bloque": 2, "segundos": "3-14", "objetivo": "Señal 1.", "personaje": lead_character["nombre"], "visual": "Mostrar el primer sintoma en contexto real.", "dialogo": "Señal uno: empiezas con demasiada prisa y nadie entiende que tiene que pasar primero.", "texto_pantalla": "Senal 1: vas demasiado rapido", "transicion": "Corte a prueba visual."},
                        {"bloque": 3, "segundos": "14-25", "objetivo": "Señal 2.", "personaje": selected_characters[-1]["nombre"], "visual": "Reaccion de confusion o desconexion durante la escena.", "dialogo": "Señal dos: la atencion se cae enseguida porque todo compite al mismo tiempo y no hay foco real.", "texto_pantalla": "Senal 2: se pierde la atencion", "transicion": "Zoom con subtitulo."},
                        {"bloque": 4, "segundos": "25-36", "objetivo": "Señal 3.", "personaje": lead_character["nombre"], "visual": "Plano detalle del tercer error mientras se enumera.", "dialogo": "Señal tres: acabas corrigiendo tarde, cuando ya ves frustracion o cansancio en lugar de disfrute.", "texto_pantalla": "Senal 3: corriges tarde", "transicion": "Flash al numero 3."},
                        {"bloque": 5, "segundos": "36-52", "objetivo": "Dar la correccion.", "personaje": lead_character["nombre"], "visual": "Comparativa entre mala ejecucion y version ordenada.", "dialogo": f"La solucion es simple: simplifica el inicio, marca un objetivo visible y deja que {ts} tenga una sola prioridad a la vez.", "texto_pantalla": "La correccion simple", "transicion": "Split screen."},
                        {"bloque": 6, "segundos": "52-63", "objetivo": "CTA y cierre.", "personaje": lead_character["nombre"], "visual": "Plano frontal final con mano abierta invitando a comentar.", "dialogo": "Escribeme cual de estas tres señales te pasa mas y te digo como corregirla sin montar un plan complicado.", "texto_pantalla": "Te digo como corregirla", "transicion": "Hold final con subtitulo."}
                    ],
                    "plan_rodaje": {
                        "locacion": "Entorno donde puedan mostrarse claramente las tres señales dentro de una misma rutina.",
                        "tono": "Didactico, agil y muy reconocible para familias reales.",
                        "ritmo_edicion": "Formato lista, con marcadores visuales de cada senal y comparativas rapidas.",
                        "vestuario": "Casual y coherente con una situacion cotidiana.",
                        "props": f"Cartelas o texto en pantalla para las 3 senales, y apoyo visual del error visible relacionado con {ts}.",
                        "musica_sfx": "Base ritmica con golpes cortos para cada numero y alivio al llegar a la solucion.",
                        "tomas_clave": [
                            "Resultado malo del primer frame.",
                            "Una toma por cada senal.",
                            "Comparativa final de la correccion."
                        ]
                    },
                    "cta_engagement": "Escríbeme cuál de estas señales te pasa y te digo cómo corregirla sin complicarte el plan."
                },
                {
                    "tipo_de_angulo": "Dosis de Realidad",
                    "plantilla_base": "Dosis de realidad: no necesitas [Producto caro] para [Resultado].",
                    "gancho_texto": f"Dosis de realidad: no necesitas gastar de más para disfrutar bien {ts}.",
                    "promesa_valor": f"La audiencia rompe una creencia cara y sale con una forma mas simple y realista de resolver {ts}.",
                    "duracion_segundos": 44,
                    "personajes": build_cast("derriba el mito y demuestra una alternativa suficiente"),
                    "instruccion_visual_inicio": "Empieza con el error en macro, tápalo con la mano en el segundo 1 y destápalo con texto de alerta ocupando media pantalla.",
                    "puntos_retencion": [
                        {"paso": 1, "segundos": "0-3", "mensaje": "Rompe una creencia cara o aparatosa que la gente da por obligatoria.", "micro_transicion": "Hard cut con sonido de freno y texto rojo."},
                        {"paso": 2, "segundos": "3-6", "mensaje": "Demuestra por qué el resultado depende más del enfoque que del gasto.", "micro_transicion": "Zoom out rápido a plano general con pausa tensa."},
                        {"paso": 3, "segundos": "6-9", "mensaje": "Cierra con la alternativa simple y suficiente para hacerlo bien.", "micro_transicion": "Corte a close-up del gesto correctivo con subtítulo amarillo."}
                    ],
                    "guion_detallado": [
                        {"bloque": 1, "segundos": "0-3", "objetivo": "Romper el mito caro.", "personaje": lead_character["nombre"], "visual": "Tapar con la mano el elemento caro o exagerado y destaparlo con texto de alerta.", "dialogo": f"Dosis de realidad: no necesitas gastar de mas para disfrutar bien {ts}.", "texto_pantalla": "No necesitas gastar de mas", "transicion": "Hard cut."},
                        {"bloque": 2, "segundos": "3-11", "objetivo": "Nombrar la creencia.", "personaje": lead_character["nombre"], "visual": "Plano medio señalando el mito o expectativa cara.", "dialogo": "Nos han vendido que si no haces esto a lo grande, no merece la pena. Y eso no es verdad.", "texto_pantalla": "Mito caro", "transicion": "Zoom con texto."},
                        {"bloque": 3, "segundos": "11-24", "objetivo": "Demostrar la alternativa.", "personaje": lead_character["nombre"], "visual": "Comparativa entre opcion cara y solucion simple pero efectiva.", "dialogo": f"Lo que realmente funciona con {ts} es tener claridad, ritmo y una experiencia sencilla que se pueda sostener, no un montaje perfecto.", "texto_pantalla": "Lo que si funciona", "transicion": "Split screen corto."},
                        {"bloque": 4, "segundos": "24-36", "objetivo": "Mostrar resultado real.", "personaje": selected_characters[-1]["nombre"], "visual": "Reaccion genuina disfrutando la alternativa simple.", "dialogo": "Cuando bajas el gasto inutil y subes la intencion, el resultado sigue estando ahi y se disfruta mucho mas.", "texto_pantalla": "Simple tambien funciona", "transicion": "Match cut al resultado."},
                        {"bloque": 5, "segundos": "36-44", "objetivo": "CTA util.", "personaje": lead_character["nombre"], "visual": "Plano cercano final con tono complice.", "dialogo": "Si quieres, dime en que te gastarias de mas y te digo que merece la pena y que no para este caso.", "texto_pantalla": "Te digo donde si gastar", "transicion": "Freeze final."}
                    ],
                    "plan_rodaje": {
                        "locacion": "Sitio donde pueda verse la opcion cara frente a la alternativa simple sin mover demasiada produccion.",
                        "tono": "Mito vs realidad, cercano y con sentido comun.",
                        "ritmo_edicion": "Rapido al plantear el mito y mas claro al enseñar la solucion.",
                        "vestuario": "Natural, familiar y sin estetica de anuncio.",
                        "props": f"Un elemento que represente gasto innecesario y otro que muestre la solucion sencilla para {ts}.",
                        "musica_sfx": "Beat ligero con sonido de freno al romper el mito y toque de alivio al mostrar la alternativa.",
                        "tomas_clave": [
                            "Elemento caro en primer plano.",
                            "Comparativa visual cara vs simple.",
                            "Resultado final con disfrute autentico."
                        ]
                    },
                    "cta_engagement": "Cuéntame qué gasto te genera más dudas y te digo qué sí merece la pena y qué no para este tema."
                }
            ],
            "checklist": {
                "preproduccion": [
                    {"item": "Elegir 5 plantillas distintas", "detalle": "Selecciona las 5 fórmulas del banco maestro que mejor encajan con el tópico antes de grabar."},
                    {"item": "Diseñar el primer frame", "detalle": "Cada propuesta necesita una imagen inicial que funcione sin audio y detenga el scroll sola."},
                    {"item": "Resolver los placeholders", "detalle": "Sustituye cada variable por situaciones, resultados o errores concretos del tema antes de pasar a grabación."}
                ],
                "grabacion": [
                    {"item": "Grabar dos versiones del hook", "detalle": "Haz una versión más agresiva y otra más natural para decidir cuál abre mejor el vídeo."},
                    {"item": "Meter acción física desde el segundo 0", "detalle": "Empieza señalando, tapando, enseñando o comparando algo; evita abrir hablando estático."},
                    {"item": "Cerrar pidiendo contexto real", "detalle": "El CTA debe pedir caso, experiencia o duda concreta, no interacción vacía."}
                ],
                "edicion": [
                    {"item": "Cambiar plano cada 2-3 segundos", "detalle": "Sostén la retención con cortes, acercamientos o overlays que acompañen cada bloque."},
                    {"item": "Subtitular la promesa principal", "detalle": "El gancho adaptado debe leerse completo o casi completo en pantalla en los primeros segundos."},
                    {"item": "Rematar con prueba o contraste", "detalle": "Cierra mostrando el resultado, el error o la corrección visual que valide la promesa del hook."}
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


@app.post("/api/editor/instagram")
@login_required
def api_editor_instagram():
    """
    Prepara la imagen editada para Instagram:
    - Sin límite de tamaño (calidad máxima).
    - Respeta el encuadre 1:1 enviado por el cliente.
    - Añade marca de agua del servidor.
    - Devuelve el PNG en base64 para descarga directa en el cliente.
    """
    import base64
    from io import BytesIO
    from PIL import Image

    data = request.get_json(force=True)
    image_b64 = data.get("image", "")
    filename = re.sub(r'[^a-zA-Z0-9_\-]', '_', data.get("filename", "instagram").strip()) or "instagram"

    try:
        header, b64data = image_b64.split(",", 1)
        img_bytes = base64.b64decode(b64data)
    except Exception:
        return jsonify({"error": "Imagen inválida"}), 400

    try:
        img = Image.open(BytesIO(img_bytes)).convert("RGBA")
    except Exception:
        return jsonify({"error": "No se pudo procesar la imagen"}), 400

    # Marca de agua (logo)
    wm_path = Path(__file__).parent / "static" / "pieDeFoto.png"
    if wm_path.exists():
        try:
            from PIL import ImageDraw as ID3, ImageChops
            wm_logo = Image.open(wm_path).convert("RGBA")
            wm_w = max(150, img.width // 8)
            wm_scale = wm_w / wm_logo.width
            wm_h = int(wm_logo.height * wm_scale)
            wm_logo = wm_logo.resize((wm_w, wm_h), Image.LANCZOS)
            mask = Image.new("L", (wm_w, wm_h), 0)
            mask_draw = ID3.Draw(mask)
            radius = int(min(wm_w, wm_h) * 0.25)
            mask_draw.rounded_rectangle([0, 0, wm_w - 1, wm_h - 1], radius=radius, fill=255)
            mask_draw.rectangle([wm_w - radius, 0, wm_w - 1, radius], fill=255)
            mask_draw.rectangle([wm_w - radius, wm_h - radius, wm_w - 1, wm_h - 1], fill=255)
            from PIL import ImageChops as IC2
            logo_alpha = wm_logo.getchannel("A")
            final_alpha = IC2.multiply(logo_alpha, mask)
            wm_logo.putalpha(final_alpha)
            x = img.width - wm_w
            y = img.height - wm_h - 10
            img.paste(wm_logo, (x, y), wm_logo)
        except Exception:
            pass

    # Guardar como PNG (sin pérdida, máxima calidad para Instagram)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    img_final = buf.getvalue()

    result_b64 = base64.b64encode(img_final).decode("ascii")
    size_kb = len(img_final) / 1024

    return jsonify({
        "ok": True,
        "image": f"data:image/png;base64,{result_b64}",
        "filename": f"{filename}.png",
        "size_kb": round(size_kb, 1),
        "dimensions": f"{img.width}×{img.height}",
    })


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
    from PIL import Image

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

    is_production = platform.system() == "Linux"
    if is_production:
        base_url, auth = _get_wp_request_context()
        if not base_url or auth is None:
            return jsonify({
                "error": "No hay credenciales de WordPress configuradas para subir a la biblioteca de medios."
            }), 500

        final_name = f"{filename}.jpg"
        try:
            response = req_lib.post(
                f"{base_url}/wp-json/wp/v2/media",
                auth=auth,
                headers={
                    "Content-Disposition": f'attachment; filename="{final_name}"',
                    "Content-Type": "image/jpeg",
                },
                data=img_final,
                timeout=45,
                verify=False,
            )
            response.raise_for_status()
            media = response.json() if response.content else {}
        except Exception as exc:
            return jsonify({"error": f"Error subiendo a WordPress Media Library: {exc}"}), 500

        media_url = str(media.get("source_url", "")).strip()
        if not media_url:
            return jsonify({
                "error": "WordPress no devolvió la URL del media; no se pudo confirmar el guardado en biblioteca."
            }), 500

        size_kb = len(img_final) / 1024
        return jsonify({
            "ok": True,
            "path": media_url,
            "size_kb": round(size_kb, 1),
            "filename": final_name,
            "wp_media_id": media.get("id"),
        })
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
