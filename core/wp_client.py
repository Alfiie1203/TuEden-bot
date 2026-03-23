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
            )
            logger.info(f"✅ WordPress MODO REAL → {kwargs['base_url']}")

    @classmethod
    def from_env(cls) -> "WordPressClient":
        """
        Construye el cliente leyendo la configuración desde variables de entorno.
        Acepta tanto WP_MODE ('simulated'/'live') como WP_SIMULATE ('true'/'false').
        WP_MODE tiene prioridad si está definida.
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
        return cls(
            simulate     = False,
            base_url     = os.environ["WP_BASE_URL"],
            username     = os.environ["WP_USERNAME"],
            app_password = os.environ["WP_APP_PASSWORD"],
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

    def __init__(self, base_url: str, username: str, app_password: str):
        self.base_url = base_url.rstrip("/")
        self.auth     = HTTPBasicAuth(username, app_password)
        self.headers  = {"Content-Type": "application/json"}
        self._api     = f"{self.base_url}/wp-json/wp/v2"

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
            "title":   draft.title,
            "content": content_with_images,
            "status":  "draft",              # ← NUNCA se publica automáticamente
            "meta": {
                "_yoast_wpseo_metadesc":  draft.meta_description,
                "_yoast_wpseo_focuskw":   draft.focus_keyword,
                "affiliate_url":          draft.affiliate_url or "",
                "post_type_label":        draft.post_type,
                "ai_generated":           "1",
            },
        }

        response = requests.post(
            endpoint,
            json    = payload,
            auth    = self.auth,
            headers = self.headers,
            timeout = 30,
        )

        if response.status_code == 401:
            raise PermissionError(
                "❌ Autenticación fallida en WordPress. "
                "Verifica WP_USERNAME y WP_APP_PASSWORD en .env"
            )

        response.raise_for_status()

        data  = response.json()
        wp_id = data["id"]
        edit_link = f"{self.base_url}/wp-admin/post.php?post={wp_id}&action=edit"
        logger.success(f"[REAL] Draft creado en WP → ID {wp_id} | {edit_link}")

        draft.wp_post_id = wp_id
        return wp_id

    def upload_media(self, file_path: str, alt_text: str = "") -> dict:
        """
        Sube un archivo de imagen a la Media Library de WordPress.

        Returns:
            dict con keys: id, url, alt  (para usar en el contenido HTML)
        """
        import mimetypes
        path = Path(file_path)
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"

        with open(path, "rb") as f:
            response = requests.post(
                f"{self._api}/media",
                auth    = self.auth,
                headers = {
                    "Content-Disposition": f'attachment; filename="{path.name}"',
                    "Content-Type":        mime,
                },
                data    = f,
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
        """
        content = draft.content
        images  = getattr(draft, "images", []) or []

        for img in images:
            marker   = img.get("marker", "")   # p.ej. "<!-- img:0 -->"
            src      = img.get("src", "")
            alt      = img.get("alt", "")
            caption  = img.get("caption", "")

            # Si es un path local → subirla a WP
            if src and not src.startswith("http") and Path(src).exists():
                try:
                    media = self.upload_media(src, alt_text=alt)
                    src   = media["url"]
                except Exception as exc:
                    logger.warning(f"[REAL] No se pudo subir imagen {src}: {exc}")
                    continue

            if not src:
                continue

            caption_html = f"<figcaption>{caption}</figcaption>" if caption else ""
            img_block = (
                f'\n<figure class="wp-block-image">'
                f'<img src="{src}" alt="{alt}" />'
                f'{caption_html}</figure>\n'
            )

            if marker and marker in content:
                content = content.replace(marker, img_block, 1)
            else:
                # Sin marcador → añadir al final
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
        except requests.RequestException as exc:
            logger.error(f"[REAL] No se pudo conectar a WordPress: {exc}")
            return False
