"""
app.py  â€”  Blog Content Generator â€” Interfaz Flask
============================================================================
CÃ³mo ejecutar:
    python app.py
    (abre tu navegador en http://localhost:5000)

Modos de operaciÃ³n:
    Â· SIMULADO (por defecto): no requiere WordPress ni API Key de Gemini.
                              Los borradores se guardan en drafts_output/ como JSON.
    Â· REAL:                   configura .env con GEMINI_API_KEY_1, _2... y
                              cambia GEMINI_MOCK_MODE=false y WP_MODE=live
"""
from __future__ import annotations

import json
import os
import queue
import threading
import uuid
from datetime import datetime
from pathlib import Path

import requests as req_lib
from dotenv import load_dotenv
from flask import (
    Flask, Response, jsonify, render_template,
    request, send_file, stream_with_context,
)
from requests.auth import HTTPBasicAuth

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())

# â”€â”€ Directorios â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
DRAFTS_DIR = Path("drafts_output")
IMAGES_DIR = DRAFTS_DIR / "images"
LOG_PATH   = Path("logs/generation_log.jsonl")

# â”€â”€ Singleton: TokenManager (app local de un solo usuario) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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


# â”€â”€ Colas de progreso para generaciÃ³n asÃ­ncrona â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_progress_queues: dict[str, queue.Queue] = {}


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
    """Valida que el filename estÃ© dentro de DRAFTS_DIR (evita path traversal)."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    base = DRAFTS_DIR.resolve()
    safe = (DRAFTS_DIR / Path(filename).name).resolve()
    if not str(safe).startswith(str(base)):
        return None
    return safe


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# RUTAS DE PÃGINAS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.route("/")
def index():
    mock_mode, simulate_wp = _get_modes()
    return render_template(
        "index.html",
        mock_mode=mock_mode,
        simulate_wp=simulate_wp,
        wp_url=os.getenv("WP_BASE_URL", ""),
    )


@app.route("/historial")
def historial():
    mock_mode, simulate_wp = _get_modes()
    return render_template("historial.html", mock_mode=mock_mode, simulate_wp=simulate_wp)


@app.route("/topicos")
def topicos():
    mock_mode, simulate_wp = _get_modes()
    return render_template("topicos.html", mock_mode=mock_mode, simulate_wp=simulate_wp)


@app.route("/borrador")
def borrador():
    mock_mode, simulate_wp = _get_modes()
    filename = request.args.get("file", "")
    return render_template(
        "borrador.html",
        mock_mode=mock_mode,
        simulate_wp=simulate_wp,
        filename=filename,
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# API â€” TOKENS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/api/tokens")
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
    return jsonify({"ok": False, "msg": "No hay mÃ¡s claves disponibles"})


@app.post("/api/tokens/activar")
def api_tokens_activar():
    data  = request.get_json() or {}
    alias = data.get("alias", "")
    tm = get_token_manager()
    if tm and alias:
        tm.set_active_key(alias)
        return jsonify({"ok": True})
    return jsonify({"ok": False})


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# API â€” GENERACIÃ“N DE BORRADORES
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.post("/api/generar")
def api_generar():
    global _token_manager
    data         = request.get_json() or {}
    user_input   = data.get("topico", "").strip()
    focus        = data.get("focus", "").strip()
    gen_mode     = data.get("mode", "auto")
    reviewer     = data.get("reviewer", "")
    gemini_model = data.get("gemini_model", "gemini-2.5-flash")

    if not user_input:
        return jsonify({"error": "Falta el tÃ³pico"}), 400

    task_id = str(uuid.uuid4())
    q       = queue.Queue()
    _progress_queues[task_id] = q

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
            drafts = orchestrator.run(user_input, mode=gen_mode, focus=focus, reviewer=reviewer)
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
def api_progreso(task_id):
    """Server-Sent Events â€” stream de progreso de generaciÃ³n."""
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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# API â€” TÃ“PICOS DEL DÃA
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.post("/api/topicos/cargar")
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
        return jsonify({"ok": True, "data": topics, "from_cache": not force})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.post("/api/topicos/sugerir")
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
def api_topicos_generar():
    global _token_manager
    data          = request.get_json() or {}
    topics        = data.get("topics", [])
    edited_titles = data.get("edited_titles", {})
    reviewer      = data.get("reviewer", "")
    gemini_model  = data.get("gemini_model", "gemini-2.5-flash")
    focus_global  = data.get("focus_global", "")

    if not topics:
        return jsonify({"error": "Sin tÃ³picos seleccionados"}), 400

    task_id = str(uuid.uuid4())
    q       = queue.Queue()
    _progress_queues[task_id] = q

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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# API â€” BORRADORES
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/api/borradores")
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
def api_borrador_get(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    return jsonify(json.loads(safe.read_text(encoding="utf-8")))


@app.post("/api/borrador/<path:filename>/guardar")
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
def api_borrador_eliminar(filename):
    safe = _safe_draft_path(filename)
    if safe and safe.exists():
        safe.unlink()
    return jsonify({"ok": True})


@app.post("/api/borrador/<path:filename>/publicar")
def api_borrador_publicar(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    draft_data  = json.loads(safe.read_text(encoding="utf-8"))
    _, simulate_wp = _get_modes()
    body        = request.get_json() or {}
    images      = body.get("images",     draft_data.get("images", []))
    categories  = body.get("categories", draft_data.get("categories", []))
    tags        = body.get("tags",        draft_data.get("tags", []))
    author_key  = body.get("author_key", "luis")
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
        wp  = WordPressClient.from_env(user_key=author_key) if not simulate_wp else WordPressClient(simulate=True)
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
def api_borrador_descargar(filename):
    safe = _safe_draft_path(filename)
    if safe is None or not safe.exists():
        return jsonify({"error": "No encontrado"}), 404
    return send_file(safe, as_attachment=True, download_name=safe.name)


@app.post("/api/borrador/<path:filename>/imagen")
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
            return jsonify({"error": "Credenciales WP no configuradas en .env"}), 400
        auth   = HTTPBasicAuth(wp_user, wp_pass)
        gemini = GeminiClient()
        cat_ids, tag_ids = assign_taxonomy(
            gemini=gemini, base_url=base_url, auth=auth,
            title=draft_data.get("title", ""),
            content=draft_data.get("content", ""),
            post_type=draft_data.get("post_type", ""),
        )
        return jsonify({"ok": True, "categories": cat_ids, "tags": tag_ids})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/imagen/<path:img_name>")
def serve_image(img_name):
    """Sirve imÃ¡genes locales desde drafts_output/images/."""
    base = IMAGES_DIR.resolve()
    safe = (IMAGES_DIR / Path(img_name).name).resolve()
    if not str(safe).startswith(str(base)):
        return jsonify({"error": "Acceso denegado"}), 403
    if not safe.exists():
        return jsonify({"error": "No encontrada"}), 404
    return send_file(safe)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# API â€” HISTORIAL
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.get("/api/historial")
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
def api_historial_recuperar():
    data      = request.get_json() or {}
    wp_id     = data.get("wp_post_id")
    post_type = data.get("post_type", "")
    entry     = data.get("entry", {})
    base_url     = os.getenv("WP_BASE_URL", "").rstrip("/")
    username     = os.getenv("WP_USERNAME", "")
    app_password = os.getenv("WP_APP_PASSWORD", "")
    if not (base_url and username and app_password):
        return jsonify({"error": "Credenciales WP no configuradas en .env"}), 400
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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MAIN
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False)

