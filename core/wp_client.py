"""
wp_client.py
Cliente WordPress con MODO DUAL:

  · MODO SIMULADO (WP_SIMULATE=true en .env):
      No requiere WordPress instalado. Guarda los borradores como archivos
      JSON en la carpeta drafts_output/. Ideal para desarrollo y pruebas.

  · MODO REAL (WP_SIMULATE=false):
      Se conecta a WordPress mediante la REST API usando Application Passwords.
      Crea los posts en estado 'draft' con todos los meta fields de afiliado.

Para cambiar de modo basta con editar la variable WP_SIMULATE en el archivo .env.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import requests
from loguru import logger
from requests.auth import HTTPBasicAuth

from models.post_draft import PostDraft


# ---------------------------------------------------------------------------
# Cliente principal (fachada que delega en simulado o real)
# ---------------------------------------------------------------------------

class WordPressClient:
    """
    Fachada que decide automáticamente si usar el modo simulado o el real
    según la variable de entorno WP_SIMULATE.

    Uso:
        client = WordPressClient.from_env()
        wp_id  = client.create_draft(draft)
    """

    def __init__(self, simulate: bool = True, **kwargs):
        self.simulate = simulate
        if simulate:
            self._backend = _SimulatedWPClient(
                output_dir=kwargs.get("output_dir", "drafts_output")
            )
            logger.warning(
                "⚠️  WordPress en MODO SIMULADO. "
                "Los borradores se guardarán en 'drafts_output/'."
            )
        else:
            self._backend = _RealWPClient(
                base_url     = kwargs["base_url"],
                username     = kwargs["username"],
                app_password = kwargs["app_password"],
                output_dir   = kwargs.get("output_dir", "drafts_output"),
            )
            logger.info(f"✅ WordPress MODO REAL → {kwargs['base_url']}")

    @classmethod
    def from_env(cls, user_key: str = "luis") -> "WordPressClient":
        """
        Construye el cliente leyendo la configuración desde variables de entorno.
        Acepta tanto WP_MODE ('simulated'/'live') como WP_SIMULATE ('true'/'false').
        WP_MODE tiene prioridad si está definida.

        Args:
            user_key: Clave del usuario WP a usar para la autenticación.
                      "luis" (default/admin), "alejandra" (psicología), "angela" (medicina).
                      En modo simulado este parámetro se ignora.
        """
        wp_mode    = os.getenv("WP_MODE", "").strip().lower()
        wp_simulate = os.getenv("WP_SIMULATE", "true").strip().lower()

        # WP_MODE="simulated" o WP_MODE="live"  (variable preferida del .env.example)
        # WP_SIMULATE="true"/"false"             (compatibilidad hacia atrás)
        if wp_mode:
            simulate = wp_mode != "live"
        else:
            simulate = wp_simulate not in ("false", "0", "no")

        if simulate:
            return cls(simulate=True)

        # Modo real: cargar credenciales del usuario indicado
        from core.wp_author_router import get_user
        user_data = get_user(user_key)
        username     = user_data["username"]
        app_password = user_data["app_password"]

        if not username or not app_password:
            raise ValueError(
                f"Credenciales incompletas para el usuario '{user_key}'. "
                f"Verifica WP_USERNAME_{user_key.upper()} y "
                f"WP_APP_PASSWORD_{user_key.upper()} en el .env"
            )

        logger.info(f"[WPClient] Usando credenciales de '{user_key}' ({username})")
        return cls(
            simulate     = False,
            base_url     = os.environ["WP_BASE_URL"],
            username     = username,
            app_password = app_password,
        )

    def create_draft(self, draft: PostDraft) -> int | str:
        """
        Crea un borrador en WordPress (real o simulado).

        Returns:
            int  en modo real   (ID del post en WordPress)
            str  en modo simulado (nombre del archivo JSON generado)
        """
        return self._backend.create_draft(draft)

    def upload_media(self, file_path: str, alt_text: str = "") -> dict:
        """
        Sube/copia una imagen.
        En modo real → la sube a la Media Library de WP.
        En modo simulado → la copia a drafts_output/images/.

        Returns: {"id": ..., "url": ..., "alt": ...}
        """
        return self._backend.upload_media(file_path, alt_text=alt_text)

    def test_connection(self) -> bool:
        """Verifica la conexión con WordPress. Siempre True en modo simulado."""
        return self._backend.test_connection()

    @property
    def mode(self) -> str:
        return "simulado" if self.simulate else "real"


# ---------------------------------------------------------------------------
# Backend SIMULADO
# ---------------------------------------------------------------------------

class _SimulatedWPClient:
    """
    Simula WordPress guardando cada borrador como un archivo JSON independiente.
    Los archivos se organizan por fecha y tipo de post para fácil revisión.
    """

    def __init__(self, output_dir: str = "drafts_output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Contador en memoria para simular IDs de WordPress
        self._id_counter = self._read_last_id()

    def _read_last_id(self) -> int:
        """Lee el último ID simulado desde un archivo de estado."""
        id_file = self.output_dir / ".last_id"
        if id_file.exists():
            try:
                return int(id_file.read_text().strip())
            except ValueError:
                pass
        return 1000  # ID inicial simulado

    def _next_id(self) -> int:
        self._id_counter += 1
        (self.output_dir / ".last_id").write_text(str(self._id_counter))
        return self._id_counter

    def create_draft(self, draft: PostDraft) -> str:
        """Guarda el borrador como JSON y devuelve el nombre del archivo."""
        sim_id   = self._next_id()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"draft_{sim_id}_{draft.post_type}_{timestamp}.json"
        filepath  = self.output_dir / filename

        payload = {
            "sim_id":           sim_id,
            "status":           "draft",
            "created_at":       datetime.now().isoformat(),
            "post_type":        draft.post_type,
            "title":            draft.title,
            "content":          draft.content,
            "meta_description": draft.meta_description,
            "focus_keyword":    draft.focus_keyword,
            "affiliate_url":    draft.affiliate_url or "",
            "ai_generated":     True,
            "images":           getattr(draft, "images", []) or [],
            "image_prompts":    getattr(draft, "image_prompts", {}) or {},
            "categories":       getattr(draft, "categories", []) or [],
            "tags":             getattr(draft, "tags", []) or [],
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        logger.success(f"[SIMULADO] Draft guardado → {filepath}")
        # Devolvemos el ID numérico para consistencia con el modo real
        draft.wp_post_id = sim_id
        draft.draft_file = filename   # guardar nombre para enlace en GUI
        return sim_id

    def upload_media(self, file_path: str, alt_text: str = "") -> dict:
        """
        Simula la subida de un archivo de imagen.
        Copia el archivo a drafts_output/images/ y devuelve una URL local simulada.
        """
        images_dir = self.output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        src = Path(file_path)
        if src.exists():
            dest = images_dir / src.name
            import shutil
            shutil.copy2(src, dest)
            url = f"[LOCAL] {dest}"
            logger.info(f"[SIMULADO] Imagen copiada → {dest}")
        else:
            # Si es URL externa, se usa tal cual
            url = file_path

        media_id = self._next_id()
        return {"id": media_id, "url": url, "alt": alt_text}

    def test_connection(self) -> bool:
        logger.info("[SIMULADO] test_connection → OK (sin WordPress)")
        return True


# ---------------------------------------------------------------------------
# Backend REAL (WordPress REST API)
# ---------------------------------------------------------------------------

class _RealWPClient:
    """
    Se conecta a la REST API de WordPress usando Application Passwords.
    Requiere WordPress 5.6+ con la REST API habilitada.

    Setup en WordPress:
        Admin → Usuarios → Tu perfil
        → Application Passwords → "Add New" → copiar contraseña al .env
    """

    def __init__(self, base_url: str, username: str, app_password: str,
                 output_dir: str = "drafts_output"):
        self.base_url   = base_url.rstrip("/")
        self.auth       = HTTPBasicAuth(username, app_password)
        self.headers    = {"Content-Type": "application/json"}
        self._api       = f"{self.base_url}/wp-json/wp/v2"
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def create_draft(self, draft: PostDraft) -> int:
        """
        Crea un post en WordPress con estado 'draft'.
        Si draft.images contiene imágenes, las sube a la Media Library primero
        e inyecta las URLs en el contenido HTML.
        """
        endpoint = f"{self._api}/posts"

        # ── Subir imágenes y reemplazar marcadores en el contenido ───────────
        content_with_images = self._inject_images(draft)

        payload = {
            "title":      draft.title,
            "content":    content_with_images,
            "status":     "draft",              # ← NUNCA se publica automáticamente
            "categories": getattr(draft, "categories", []) or [],
            "tags":       getattr(draft, "tags", []) or [],
            "meta": {
                "_yoast_wpseo_metadesc":  draft.meta_description,
                "_yoast_wpseo_focuskw":   draft.focus_keyword,
                "affiliate_url":          draft.affiliate_url or "",
                "post_type_label":        draft.post_type,
                "ai_generated":           "1",
            },
        }

        try:
            response = requests.post(
                endpoint,
                json    = payload,
                auth    = self.auth,
                headers = self.headers,
                timeout = 30,
            )
        except requests.exceptions.ConnectionError as exc:
            raise ConnectionError(
                f"❌ No se puede conectar a WordPress ({self.base_url}). "
                f"Verifica que el servidor esté online y accesible desde esta máquina "
                f"(¿VPN activa? ¿dominio en DNS?). Detalle: {exc}"
            ) from exc
        except requests.exceptions.Timeout:
            raise TimeoutError(
                f"⏱️ WordPress no respondió en 30 segundos ({self.base_url}). "
                f"El servidor puede estar caído o sobrecargado."
            )

        if response.status_code == 401:
            raise PermissionError(
                "❌ Autenticación fallida en WordPress. "
                "Verifica WP_USERNAME y WP_APP_PASSWORD en .env"
            )

        if not response.ok:
            raise RuntimeError(
                f"❌ WordPress devolvió HTTP {response.status_code}. "
                f"Respuesta: {response.text[:300]}"
            )

        data  = response.json()
        wp_id = data["id"]
        edit_link = f"{self.base_url}/wp-admin/post.php?post={wp_id}&action=edit"
        logger.success(f"[REAL] Draft creado en WP → ID {wp_id} | {edit_link}")

        draft.wp_post_id = wp_id

        # Actualizar metadatos SEO en AIOSEO (si está instalado)
        self._update_aioseo_meta(
            wp_id,
            draft.meta_description,
            draft.focus_keyword,
            getattr(draft, "seo_keywords", []) or [],
        )

        # Guardar copia local JSON para el editor de borradores
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = f"draft_{wp_id}_{draft.post_type}_{timestamp}.json"
        filepath  = self.output_dir / filename
        payload_local = {
            "sim_id":           wp_id,
            "wp_post_id":       wp_id,
            "status":           "draft",
            "created_at":       datetime.now().isoformat(),
            "post_type":        str(draft.post_type),
            "title":            draft.title,
            "content":          draft.content,
            "meta_description": draft.meta_description,
            "focus_keyword":    draft.focus_keyword,
            "affiliate_url":    draft.affiliate_url or "",
            "ai_generated":     True,
            "images":           getattr(draft, "images", []) or [],
            "image_prompts":    getattr(draft, "image_prompts", {}) or {},
            "categories":       getattr(draft, "categories", []) or [],
            "tags":             getattr(draft, "tags", []) or [],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload_local, f, ensure_ascii=False, indent=2)
        draft.draft_file = filename
        logger.info(f"[REAL] Copia local guardada → {filepath}")

        return wp_id

    def _update_aioseo_meta(
        self,
        wp_id: int,
        meta_description: str,
        focus_keyword: str,
        seo_keywords: list[str] | None = None,
    ) -> None:
        """
        Actualiza los metadatos SEO en AIOSEO via su REST API (v4+).
        Envía la meta description y la única keyword de foco.
        Si AIOSEO no está instalado o activo simplemente registra un warning.
        """
        try:
            payload: dict = {"post_id": wp_id, "description": meta_description or ""}
            if focus_keyword:
                payload["keyphrases"] = {
                    "focus": {
                        "keyphrase": focus_keyword,
                        "active":    True,
                        "score":     0,
                        "analysis":  {},
                    }
                }
            resp = requests.post(
                f"{self.base_url}/wp-json/aioseo/v1/post",
                json    = payload,
                auth    = self.auth,
                headers = {"Content-Type": "application/json"},
                timeout = 15,
            )
            if resp.ok:
                logger.success(f"[REAL] AIOSEO meta actualizada → post {wp_id}")
            elif resp.status_code == 404:
                logger.warning("[REAL] AIOSEO REST API no encontrada (¿plugin activo y actualizado?)")
            else:
                logger.warning(f"[REAL] AIOSEO respondió HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            logger.warning(f"[REAL] No se pudo actualizar AIOSEO: {exc}")

    @staticmethod
    def _resize_image_if_needed(path: Path, max_px: int = 2560, quality: int = 85) -> tuple[bytes, str]:
        """
        Redimensiona la imagen si cualquier dimensión supera max_px y la convierte
        a JPEG para minimizar el tamaño. Devuelve (bytes_imagen, mime_type).
        Requiere Pillow; si no está instalado, devuelve el archivo original sin tocar.
        """
        try:
            from PIL import Image
            import io
            img = Image.open(path)
            # Convertir a RGB si es necesario (p.ej. PNG con transparencia)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            w, h = img.size
            if w > max_px or h > max_px:
                # Redimensionar manteniendo la proporción
                ratio = min(max_px / w, max_px / h)
                new_w, new_h = int(w * ratio), int(h * ratio)
                img = img.resize((new_w, new_h), Image.LANCZOS)
                logger.info(f"[REAL] Imagen redimensionada: {w}×{h} → {new_w}×{new_h}")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue(), "image/jpeg"
        except ImportError:
            logger.warning("[REAL] Pillow no instalado — subiendo imagen sin redimensionar")
            return path.read_bytes(), None
        except Exception as exc:
            logger.warning(f"[REAL] No se pudo redimensionar {path.name}: {exc} — subiendo original")
            return path.read_bytes(), None

    def upload_media(self, file_path: str, alt_text: str = "") -> dict:
        """
        Sube un archivo de imagen a la Media Library de WordPress.
        Redimensiona automáticamente si supera 2560 px en cualquier dimensión.

        Returns:
            dict con keys: id, url, alt  (para usar en el contenido HTML)
        """
        import mimetypes
        path = Path(file_path)

        # Redimensionar si es necesario
        img_bytes, resized_mime = self._resize_image_if_needed(path)
        if resized_mime:
            mime      = resized_mime
            upload_name = path.stem + ".jpg"
        else:
            mime      = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            upload_name = path.name

        response = requests.post(
                f"{self._api}/media",
                auth    = self.auth,
                headers = {
                    "Content-Disposition": f'attachment; filename="{upload_name}"',
                    "Content-Type":        mime,
                },
                data    = img_bytes,
                timeout = 60,
            )

        response.raise_for_status()
        data = response.json()
        media_url = data.get("source_url", "")
        media_id  = data.get("id", 0)

        if alt_text:
            # Actualizar el alt text via PATCH
            requests.post(
                f"{self._api}/media/{media_id}",
                auth    = self.auth,
                headers = {"Content-Type": "application/json"},
                json    = {"alt_text": alt_text},
                timeout = 15,
            )

        logger.success(f"[REAL] Imagen subida → {media_url}")
        return {"id": media_id, "url": media_url, "alt": alt_text}

    def _inject_images(self, draft: PostDraft) -> str:
        """
        Sube las imágenes del draft a WP Media Library e inyecta los bloques
        <figure> en el HTML después del marcador <!-- img:N -->.
        Si la imagen tiene src= (URL externa o path local) la sube primero.

        Maneja dos formatos de src:
          - Path local puro:  'drafts_output/images/img_xxx.png'
          - Prefijo [LOCAL]:  '[LOCAL] drafts_output\images\img_xxx.png'
            (generado por el cliente simulado; se extrae el path real)
        """
        content = draft.content
        images  = getattr(draft, "images", []) or []

        for img in images:
            marker   = img.get("marker", "")   # p.ej. "<!-- img:0 -->"
            src      = img.get("src", "")
            alt      = img.get("alt", "")
            caption  = img.get("caption", "")

            # Extraer el path real si lleva el prefijo [LOCAL] del cliente simulado
            local_prefix = "[LOCAL] "
            actual_path  = src
            if src.startswith(local_prefix):
                actual_path = src[len(local_prefix):]

            # Si es un path local que existe en disco → subirla a WP
            uploaded_url = ""
            if actual_path and not actual_path.startswith("http") and Path(actual_path).exists():
                try:
                    media        = self.upload_media(actual_path, alt_text=alt)
                    uploaded_url = media["url"]
                    logger.info(f"[REAL] Imagen subida a WP: {actual_path} → {uploaded_url}")
                except Exception as exc:
                    logger.warning(f"[REAL] No se pudo subir imagen {actual_path}: {exc}")
                    continue
            elif actual_path.startswith("http"):
                # URL pública ya válida – usar directamente
                uploaded_url = actual_path

            if not uploaded_url:
                continue

            caption_html = f"<figcaption>{caption}</figcaption>" if caption else ""
            img_block = (
                f'\n<figure class="wp-block-image">'
                f'<img src="{uploaded_url}" alt="{alt}" />'
                f'{caption_html}</figure>\n'
            )

            # Caso 1: el contenido todavía tiene el marcador original
            if marker and marker in content:
                content = content.replace(marker, img_block, 1)

            # Caso 2: el cliente simulado ya reemplazó el marcador con la ruta [LOCAL]
            # (tanto en src= como en data-mce-src= u otros atributos)
            elif src in content:
                # Reemplazar todas las apariciones del string [LOCAL] en el HTML
                content = content.replace(src, uploaded_url)

            else:
                # Sin referencia en el contenido → añadir al final
                content += img_block

        return content

    def test_connection(self) -> bool:
        """Hace un GET a la REST API para verificar conectividad y credenciales."""
        try:
            response = requests.get(
                f"{self._api}/users/me",
                auth    = self.auth,
                timeout = 10,
            )
            if response.status_code == 200:
                user = response.json().get("name", "?")
                logger.success(f"[REAL] Conectado a WordPress como: {user}")
                return True
            logger.error(f"[REAL] test_connection falló: HTTP {response.status_code}")
            return False
        except requests.exceptions.RequestException as exc:
            logger.error(f"[REAL] test_connection falló: {exc}")
            return False
