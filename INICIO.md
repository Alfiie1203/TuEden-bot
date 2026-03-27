# ✅ INICIO.md — Estado del Proyecto y Próximos Pasos
> Última actualización: 27 de marzo de 2026 · **Migrado de Streamlit → Flask**

---

## 📦 Estado Actual: Archivos Creados

### Estructura completa del proyecto

```
proyecto/
│
├── ✅ app.py                        ← Aplicación Flask (servidor web + API REST)
├── ✅ requirements.txt              ← Todas las dependencias Python
├── ✅ .env.example                  ← Plantilla de variables de entorno
├── ✅ .gitignore                    ← Excluye .env, venv, drafts_output, logs
├── ✅ proyecto.md                   ← Documentación de arquitectura completa
├── ✅ INICIO.md                     ← Este archivo
│
├── core/
│   ├── ✅ __init__.py
│   ├── ✅ orchestrator.py           ← Orquestador: 1 input → 3 borradores → WP
│   ├── ✅ gemini_client.py          ← Wrapper Gemini API con mock, retry y rotación
│   ├── ✅ token_manager.py          ← Pool de API Keys: tracking, rotación, persistencia
│   ├── ✅ wp_client.py              ← Cliente WP modo SIMULADO + modo REAL
│   ├── ✅ prompt_templates.py       ← 3 plantillas de prompt (Comparativa/Guía/Reseña)
│   └── ✅ amazon_parser.py          ← Extrae nombre de producto desde URLs Amazon
│
├── templates/                       ← Plantillas Jinja2 (HTML con Bootstrap 5)
│   ├── ✅ base.html                 ← Layout base con navbar, sidebar tokens (offcanvas)
│   ├── ✅ index.html                ← Página principal: formulario de generación
│   ├── ✅ historial.html            ← Historial de generaciones con filtros
│   ├── ✅ topicos.html              ← Descubrimiento de tópicos del día
│   └── ✅ borrador.html             ← Editor WYSIWYG de borradores
│
├── models/
│   ├── ✅ __init__.py
│   └── ✅ post_draft.py             ← Modelo Pydantic PostDraft + PostType enum
│
├── tests/
│   ├── ✅ __init__.py
│   ├── ✅ test_amazon_parser.py     ← Tests del parser de URLs Amazon
│   └── ✅ test_wp_client.py         ← Tests del cliente WP simulado
│
├── logs/
│   └── token_usage.json            ← Se crea automáticamente (persiste contadores de tokens)
└── drafts_output/                   ← Se crea automáticamente en modo simulado
```

---

## 🚦 Checklist de Puesta en Marcha

### FASE 1 — Setup Local (Puedes hacerlo HOY)

- [ ] **Instalar dependencias** (Python global, sin entorno virtual)
  ```cmd
  pip install -r requirements.txt
  ```
  Esto instala: **Flask** (servidor web), Gemini SDK, Pydantic, Loguru y demás.

- [ ] **Crear tu archivo `.env`** (copiar desde `.env.example`)
  ```cmd
  copy .env.example .env
  ```

- [ ] **Verificar modo simulado y mock activos** en `.env`:
  ```dotenv
  GEMINI_MOCK_MODE=true
  WP_MODE=simulated
  ```
  Con estos valores la app funciona al 100% **sin gastar tokens y sin WordPress**.

- [ ] **Ejecutar los tests**
  ```cmd
  pytest tests/ -v
  ```
  Todos deben pasar (no requieren API Key ni WordPress).

- [ ] **Lanzar la aplicación**
  ```cmd
  python app.py
  ```
  Abre tu navegador en: http://localhost:5000

- [ ] **Prueba de humo en modo simulado**
  - Escribe un tópico: `auriculares inalámbricos Sony WH-1000XM5`
  - Haz clic en "Generar 3 Borradores"
  - Verifica que se crean 3 archivos `.json` en `drafts_output/`
  - Revisa el historial en el panel derecho de la GUI

---

### FASE 2 — Conectar Gemini API en Modo Real

- [ ] **Obtener API Keys gratuitas de Gemini** (una por proyecto de Google Cloud):
  → Ve a: https://aistudio.google.com/app/apikey
  → Crea un proyecto → Genera API Key → Cópiala en `.env`
  → Repite para `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, etc.

- [ ] **Editar `.env`** con tus claves y desactivar mock:
  ```dotenv
  GEMINI_API_KEY_1=AIzaSy...tu_clave_1
  GEMINI_API_KEY_2=AIzaSy...tu_clave_2
  GEMINI_MOCK_MODE=false
  WP_MODE=simulated
  ```

- [ ] **Probar generación real**: La GUI usará Gemini y rotará entre claves automáticamente
- [ ] **Revisar el panel de tokens** en el sidebar izquierdo de la GUI
- [ ] **Revisar los JSON en `drafts_output/`** para validar calidad del contenido
- [ ] **Ajustar los prompts** en `core/prompt_templates.py` según tu nicho/estilo

---

### FASE 3 — Instalar y Configurar WordPress

- [ ] **Elegir hosting para WordPress** (opciones recomendadas):
  | Opción | Costo aprox. | Ideal para |
  |--------|-------------|------------|
  | SiteGround | ~$3-6/mes | Producción rápida |
  | Hostinger | ~$2-4/mes | Económico |
  | InfinityFree / Byet | Gratis | Solo pruebas |
  | Local WP (app) | Gratis | Desarrollo local en tu PC |

- [ ] **Instalar WordPress** (tu hosting lo hace en 1 clic, o usa Local WP para pruebas locales)

- [ ] **Instalar plugins esenciales en WordPress**:
  - [ ] **Yoast SEO** (gratis) → para meta descriptions y keywords
  - [ ] **Advanced Custom Fields** (gratis) → para campos de afiliado
  - [ ] *(Opcional)* **WP Rocket** → caché y performance

- [ ] **Registrar los meta fields** en WordPress
  Copiar el código PHP de `proyecto.md` → sección 8 → al `functions.php` del tema hijo.

  ```php
  // En: Apariencia → Editor de temas → functions.php (tema hijo)
  function register_affiliate_meta_fields() { ... }
  add_action('init', 'register_affiliate_meta_fields');
  ```

- [ ] **Crear Application Password en WordPress**
  `WP Admin → Usuarios → Tu Perfil → Application Passwords → Add New`
  → Nombre: "Content Generator Bot" → Copiar contraseña

---

### FASE 4 — Conectar el Sistema a WordPress Real

- [ ] **Actualizar `.env`** con los datos de WordPress:
  ```dotenv
  WP_MODE=live
  WP_BASE_URL=https://tu-blog.com
  WP_USERNAME=tu_usuario
  WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
  ```

- [ ] **Probar la conexión** desde Python:
  ```cmd
  python -c "from dotenv import load_dotenv; load_dotenv(); from core.wp_client import WordPressClient; c=WordPressClient.from_env(); print('Conexion:', c.test_connection())"
  ```
  Debe imprimir: `Conexion: True`

- [ ] **Generar el primer post real** desde la GUI
  - Verificar que aparezca como BORRADOR en `WP Admin → Entradas`
  - Revisar que los meta fields (keyword, meta description, affiliate_url) estén presentes

- [ ] **Revisar y publicar manualmente** el primer borrador desde WordPress Admin

---

### FASE 5 — Monetización y SEO

- [ ] **Crear cuenta en Amazon Afiliados** (Amazon Associates):
  → https://afiliados.amazon.es/
  → Obtener tu tag personal (ej. `tu-blog-21`)

- [ ] **Actualizar `.env`**: `AMAZON_AFFILIATE_TAG=tu-tag-21`

- [ ] **Crear cuenta en Google AdSense**:
  → https://adsense.google.com/
  → Esperar aprobación (requiere dominio propio con contenido)

- [ ] **Verificar atributos en links de afiliado**:
  En el tema de WordPress, confirmar que los CTAs generados incluyan:
  ```html
  rel="nofollow sponsored"
  ```

- [ ] **Configurar Google Search Console** para indexación:
  → https://search.google.com/search-console/

---

### FASE 6 — Optimizaciones Futuras (Backlog)

- [ ] Añadir generación de imágenes con Gemini Vision o DALL·E para los posts
- [ ] Implementar modo asíncrono (generar los 3 posts en paralelo para mayor velocidad)
- [ ] Añadir soporte para categorías y tags de WordPress en la GUI
- [ ] Integrar Google Trends para sugerir tópicos con alto volumen de búsqueda
- [ ] Añadir programación de publicación automática (cron job) post-aprobación
- [ ] Panel de métricas: posts publicados, keywords cubiertas, clicks en afiliados

---

## 🔑 Variables de Entorno — Referencia Rápida

| Variable | Descripción | Requerida en Fase |
|---|---|---|
| `GEMINI_API_KEY_1` | Primera API Key de Gemini (gratuita) | Fase 2 |
| `GEMINI_API_KEY_2` | Segunda API Key (rotación automática) | Fase 2 |
| `GEMINI_API_KEY_3` | Tercera API Key (rotación automática) | Fase 2 |
| `GEMINI_MOCK_MODE` | `true` = sin API, `false` = real | Fase 1 |
| `WP_MODE` | `simulated` = sin WP, `live` = WP real | Fase 1 |
| `WP_BASE_URL` | URL de tu WordPress (con https) | Fase 4 |
| `WP_USERNAME` | Usuario administrador de WP | Fase 4 |
| `WP_APP_PASSWORD` | Application Password de WP | Fase 4 |
| `AMAZON_AFFILIATE_TAG` | Tu tag de Amazon Afiliados | Fase 5 |

---

## 📊 Sistema de Gestión de Tokens (core/token_manager.py)

### Límites del tier GRATUITO de Gemini 1.5 Flash

| Límite | Valor | Impacto real |
|--------|-------|-------------|
| Requests por minuto (RPM) | 15 | Espera automática entre posts |
| Tokens por minuto (TPM) | 1,000,000 | Prácticamente ilimitado |
| **Requests por día (RPD)** | **1,500** | El límite que más importa |
| Costo | $0.00 | Tier gratuito |

### Capacidad estimada por pool de claves

Fórmula: `Blogs/día = (1,500 req × N claves) ÷ 3 req/blog`

| Claves | Blogs/día | Blogs/mes |
|--------|-----------|-----------|
| 1 clave | 500 | 15,000 |
| 2 claves | 1,000 | 30,000 |
| 3 claves | 1,500 | 45,000 |

> Nota: Cada blog = 3 posts × ~2,000 tokens c/u = ~6,000 tokens totales

### Cómo funciona la rotación automática

```
Llamada a Gemini con Clave 1
        ↓
¿Error 429 / cuota agotada?
    NO → registra tokens y continúa con Clave 1
    SÍ → TokenManager.rotate() → Clave 2
              ↓
        ¿Clave 2 disponible?
            SÍ → reintenta con Clave 2
            NO → prueba Clave 3, Clave 4...
                      ↓
                ¿Ninguna disponible?
                    → Error claro al usuario
```

### Persistencia del estado

El uso de tokens se guarda en `logs/token_usage.json` y **sobrevive reinicios** de la app.
Al inicio del día siguiente los contadores diarios se resetean automáticamente.

```json
{
  "active_idx": 0,
  "keys": [
    {
      "alias": "Clave 1",
      "key_preview": "AIzaSy..._4x7z",
      "today_requests": 45,
      "today_tokens": 92340,
      "total_requests": 312,
      "total_tokens": 643200
    }
  ]
}
```

### Panel de tokens en la GUI (Sidebar)

El panel de tokens (offcanvas lateral de Flask) muestra en tiempo real:
- ▶ Clave activa actualmente en uso
- Barra de progreso por clave (% del límite diario usado)
- Requests y tokens consumidos hoy / histórico
- Estimación de blogs restantes por clave y por pool total
- Botón de rotación manual de clave
- Estimador mensual de capacidad total

### Para añadir una nueva clave gratuita

1. Ve a https://aistudio.google.com/app/apikey
2. Crea un **nuevo proyecto de Google Cloud** (cada proyecto tiene su propio límite)
3. Genera API Key en ese proyecto
4. Añade al `.env`:
   ```dotenv
   GEMINI_API_KEY_4=AIzaSy...tu_nueva_clave
   ```
5. Reinicia la app — se detecta automáticamente



## 🐛 Solución de Problemas Comunes

| Problema | Causa probable | Solución |
|---|---|---|
| `ModuleNotFoundError: No module named 'flask'` | Flask no instalado | Ejecutar `pip install -r requirements.txt` |
| `No se encontraron API Keys` | Faltan `GEMINI_API_KEY_1..N` en `.env` | Añadir al menos `GEMINI_API_KEY_1=...` o activar `GEMINI_MOCK_MODE=true` |
| `Error 429 / ResourceExhausted` | Cuota diaria agotada en esa clave | El sistema rota automáticamente; si todas están agotadas, esperar al día siguiente |
| `401 Unauthorized` en WordPress | Application Password mal copiada | Regenerar en WP Admin → Perfil |
| `JSONDecodeError` al parsear Gemini | Modelo no devolvió JSON válido | El cliente reintenta 3 veces automáticamente; si persiste, revisa el prompt |
| Los drafts simulados no aparecen | `drafts_output/` no existe | Se crea automáticamente al generar el primer post |
| WordPress no guarda los meta fields | `show_in_rest` no está en `true` | Verificar el código en `functions.php` |
| El sidebar no muestra tokens | `logs/token_usage.json` corrupto | Borrar el archivo — se recrea solo al reiniciar la app |
| `Todos los tokens del pool agotados` | Múltiples claves al límite de RPD | Agregar más claves al `.env` (`GEMINI_API_KEY_4=...`) o esperar medianoche |

---

*Blog Content Generator v2.0 · Human-in-the-Loop · Gemini + WordPress · Flask*
