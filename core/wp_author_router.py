"""
wp_author_router.py
Sistema de enrutamiento de autor para publicaciones WordPress multi-usuario.

Lógica de asignación:
  - Alejandra → temas de Psicología / Salud Mental
  - Angela    → temas de Medicina / Salud Física
  - Luis      → todo lo demás + administración

Las credenciales se leen del .env:
  WP_USERNAME / WP_APP_PASSWORD              (Luis — admin)
  WP_USERNAME_ALEJANDRA / WP_APP_PASSWORD_ALEJANDRA
  WP_USERNAME_ANGELA    / WP_APP_PASSWORD_ANGELA
"""
from __future__ import annotations

import os
import re

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Registro de usuarios
# ---------------------------------------------------------------------------

# Keywords que identifican temas de psicología
_KEYWORDS_ALEJANDRA: tuple[str, ...] = (
    "psicolog",
    "ansiedad",
    "depresión", "depresion",
    "estrés", "estres",
    "terapia", "terapeut",
    "emocion", "emoción",
    "mindfulness",
    "bienestar mental", "salud mental",
    "autoestima",
    "trauma",
    "cognitiv",
    "conducta",
    "motivac",
    "felicidad",
    "resiliencia",
    "inteligencia emocional",
    "duelo",
    "fobia",
    "trastorno",
    "meditacion", "meditación",
    "autosabotaje",
    "relaciones tóxicas", "relaciones toxicas",
)

# Keywords que identifican temas de medicina
_KEYWORDS_ANGELA: tuple[str, ...] = (
    "medicin", "médic", "medic",
    "enfermedad",
    "tratamiento",
    "síntoma", "sintoma",
    "diagnóstic", "diagnostic",
    "farmac",
    "clínica", "clinica",
    "patolog",
    "anatom",
    "fisiolog",
    "neurolog",
    "cardiolog",
    "dolor crónico", "dolor cronico",
    "cirugía", "cirugia",
    "hospital",
    "vacuna",
    "vitamina",
    "suplemento",
    "nutricion", "nutrición",
    "dietética", "dietetica",
    "obesidad",
    "diabetes",
    "hipertensión", "hipertension",
    "cancer", "cáncer",
    "inmunolog",
    "dermatolog",
    "oftalmolog",
    "ortopedia",
)


def _build_user_registry() -> dict[str, dict]:
    """Construye el diccionario de usuarios leyendo las variables de entorno."""
    return {
        "luis": {
            "label":        "Luis (Admin / General)",
            "icon":         "🧑‍💼",
            "username":     os.getenv("WP_USERNAME", "Luis"),
            "app_password": os.getenv("WP_APP_PASSWORD", ""),
            "description":  "Temas generales y administración",
        },
        "alejandra": {
            "label":        "Alejandra (Psicología)",
            "icon":         "🧠",
            "username":     os.getenv("WP_USERNAME_ALEJANDRA", "alejandra"),
            "app_password": os.getenv("WP_APP_PASSWORD_ALEJANDRA", ""),
            "description":  "Temas de psicología y salud mental",
        },
        "angela": {
            "label":        "Angela (Medicina)",
            "icon":         "🏥",
            "username":     os.getenv("WP_USERNAME_ANGELA", "angela"),
            "app_password": os.getenv("WP_APP_PASSWORD_ANGELA", ""),
            "description":  "Temas médicos y salud física",
        },
    }


def get_users() -> dict[str, dict]:
    """Devuelve el registro completo de usuarios (llamada fresca desde env)."""
    return _build_user_registry()


def get_user(user_key: str) -> dict:
    """
    Devuelve los datos de un usuario por su clave.
    Lanza KeyError si el usuario no existe.
    """
    users = get_users()
    if user_key not in users:
        raise KeyError(f"Usuario WP desconocido: '{user_key}'. Claves válidas: {list(users)}")
    return users[user_key]


# ---------------------------------------------------------------------------
# Auto-detección de autor según el contenido
# ---------------------------------------------------------------------------

def detect_author(title: str = "", content: str = "") -> str:
    """
    Analiza título y contenido para sugerir el autor más adecuado.

    Prioridad: Alejandra (psicología) > Angela (medicina) > Luis (default).

    Returns:
        Clave del usuario: "luis", "alejandra" o "angela".
    """
    text = (title + " " + content).lower()
    # Limpiar HTML básico
    text = re.sub(r"<[^>]+>", " ", text)

    score_alejandra = sum(1 for kw in _KEYWORDS_ALEJANDRA if kw in text)
    score_angela    = sum(1 for kw in _KEYWORDS_ANGELA    if kw in text)

    if score_alejandra == 0 and score_angela == 0:
        return "luis"

    if score_alejandra >= score_angela:
        return "alejandra"
    return "angela"


# ---------------------------------------------------------------------------
# Helpers de conveniencia
# ---------------------------------------------------------------------------

def user_options_for_selectbox() -> list[str]:
    """
    Devuelve la lista ordenada de claves de usuario para usar como
    opciones de un st.selectbox.
    Orden: luis, alejandra, angela.
    """
    return ["luis", "alejandra", "angela"]


def format_user_label(user_key: str) -> str:
    """Label legible con ícono para mostrar en la UI."""
    users = get_users()
    u = users.get(user_key, {})
    return f"{u.get('icon', '')} {u.get('label', user_key)}"
