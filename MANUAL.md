# 📖 MANUAL DE USUARIO
## Blog Content Generator — Gemini + WordPress
> Versión 2.0 · Marzo 2026 · **Flask** (migrado desde Streamlit)

---

## ¿Qué hace este software?

Este sistema genera automáticamente **3 borradores de post** para tu blog de WordPress a partir de un solo input: un tópico o una URL de Amazon.

Para cada tópico produce:
1. **Post Comparativa** → "Los X mejores [producto] en [año]"
2. **Post Guía** → "Cómo elegir el mejor [producto]"
3. **Post Reseña SEO** → "Review: [producto] — ¿Vale la pena?"

Los posts se envían a WordPress como **borradores** (nunca se publican solos). Tú los revisas y decides qué publicar. Esto es lo que se llama **Human-in-the-Loop**.

---

## Requisitos previos

| Requisito | Versión mínima | Cómo verificar |
|---|---|---|
| Python | 3.10 o superior | `python --version` en cmd |
| pip | incluido con Python | `pip --version` |
| Conexión a internet | — | Para Gemini API y WordPress |

> **No necesitas** Docker, Node.js, ni ningún servidor adicional.

---

## Instalación (una sola vez)

### Paso 1 — Instalar dependencias Python

Abre una ventana de **cmd** en la carpeta del proyecto y ejecuta:

```cmd
pip install -r requirements.txt
```

Esto instala: **Flask** (servidor web), Gemini SDK, Pydantic, Loguru y el resto de librerías.

### Paso 2 — Crear el archivo de configuración

```cmd
copy .env.example .env
```

Abre el archivo `.env` con cualquier editor de texto (Bloc de notas, VS Code, Notepad++).

---

## Configuración del archivo `.env`

El archivo `.env` controla todo el comportamiento del sistema. Aquí está cada variable explicada:

### Bloque 1 — Claves de Gemini API

```dotenv
GEMINI_API_KEY_1=AIzaSy...tu_clave_aqui
GEMINI_API_KEY_2=
GEMINI_API_KEY_3=
```

- Puedes tener **de 1 a 9 claves**. Las vacías se ignoran automáticamente.
- Cuando una clave agota su límite diario, el sistema **rota a la siguiente sin interrupciones**.
- Cómo obtener una clave gratis → ver sección [Obtener API Key de Gemini](#obtener-api-key-de-gemini-gratis).

### Bloque 2 — Modo de generación

```dotenv
GEMINI_MOCK_MODE=true
```

| Valor | Comportamiento |
|---|---|
| `true` | Usa respuestas pre-fabricadas. **No consume tokens. Ideal para probar la app.** |
| `false` | Llama a Gemini API con tu clave real. Genera contenido real. |

### Bloque 3 — Modo WordPress

```dotenv
WP_MODE=simulated
```

| Valor | Comportamiento |
|---|---|
| `simulated` | Guarda los borradores como archivos JSON en la carpeta `drafts_output/`. No necesitas WordPress. |
| `live` | Envía los borradores directamente a tu WordPress como entradas en estado Borrador. |

### Bloque 4 — Credenciales WordPress (solo para `WP_MODE=live`)

```dotenv
WP_BASE_URL=https://tu-blog.com
WP_USERNAME=tu_usuario_admin
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

> El `WP_APP_PASSWORD` **no es tu contraseña normal de WordPress**. Es una "Application Password" especial que se genera desde el panel de administración. Ver sección [Configurar WordPress](#configurar-wordpress).

### Bloque 5 — Amazon Afiliados (opcional)

```dotenv
AMAZON_AFFILIATE_TAG=tu-tag-20
```

Si pegas una URL de Amazon como input, el sistema extrae el nombre del producto y añade tu tag de afiliado a los links generados. Si no tienes cuenta de afiliados aún, déjalo en blanco.

---

## Ejecutar la aplicación

### Comando de inicio

Desde la carpeta del proyecto en **cmd**:

```cmd
python app.py
```

Después de unos segundos verás:

```
 * Running on http://127.0.0.1:5000
 * Debug mode: on
```

Abre tu navegador en **http://localhost:5000**.

### Para detener la aplicación

En la ventana de cmd donde corre Streamlit, presiona `Ctrl + C`.

---

## La interfaz — Guía visual

### Panel izquierdo (Sidebar) — Gestión de Tokens

El sidebar muestra el estado de tus API Keys de Gemini en tiempo real:

```
┌─────────────────────────────────┐
│  🔑 Gestión de API Keys         │
│                                 │
│  Clave activa: Clave 1          │
│  ████████░░░░░░ 53%             │
│  Requests hoy: 795 / 1,500      │
│  Tokens hoy: 1,234,567          │
│  Blogs restantes: ~235          │
│                                 │
│  [▶ Clave 1] [  Clave 2]        │
│  [  Clave 3]                    │
│                                 │
│  [ Rotar clave manualmente ]    │
│                                 │
│  📊 Capacidad estimada          │
│  Hoy: ~235 blogs                │
│  Esta semana: ~1,645 blogs      │
│  Este mes: ~7,050 blogs         │
└─────────────────────────────────┘
```

- **Barra de progreso**: uso del límite diario de 1,500 requests. Se pone en rojo al llegar al 80%.
- **Rotar clave**: cambia manualmente a la siguiente clave disponible.
- **Activar Clave X**: fuerza el uso de una clave específica.

### Panel principal (derecha) — Generador

```
┌─────────────────────────────────────────────────────┐
│  📝 Tópico o URL de Amazon                          │
│  ┌─────────────────────────────────────────────────┐│
│  │ auriculares Sony WH-1000XM5                     ││
│  └─────────────────────────────────────────────────┘│
│                                                     │
│  [  🚀 Generar 3 Borradores  ]                      │
│                                                     │
│  ─────────────────────────────────────────────────  │
│  📋 Historial de borradores generados               │
│  • [23/03/2026 14:32] Sony WH-1000XM5 — 3 posts ✅  │
│  • [23/03/2026 11:05] Samsung Galaxy S25 — 3 posts ✅│
└─────────────────────────────────────────────────────┘
```

---

## Cómo generar tu primer contenido

### Opción A — Con un nombre de producto

1. Escribe el nombre del producto en el campo de texto, por ejemplo:
   ```
   auriculares inalámbricos Sony WH-1000XM5
   ```
2. Haz clic en **"Generar 3 Borradores"**
3. Espera unos segundos (la barra de progreso avanza post a post)
4. Los 3 borradores se guardan automáticamente

### Opción B — Con una URL de Amazon

1. Copia la URL completa del producto en Amazon, por ejemplo:
   ```
   https://www.amazon.es/Sony-WH-1000XM5-Auriculares/dp/B09XS7JWHH
   ```
2. Pégala en el campo de texto
3. El sistema extrae el nombre del producto automáticamente
4. Haz clic en **"Generar 3 Borradores"**

### ¿Dónde quedan los borradores?

| Modo | Ubicación |
|---|---|
| `WP_MODE=simulated` | Carpeta `drafts_output/` como archivos `.json` |
| `WP_MODE=live` | En tu WordPress → **Entradas → Borradores** |

En modo simulado, cada archivo JSON contiene:
- Título del post
- Contenido HTML completo
- Meta description y focus keyword (para SEO)
- URL de afiliado
- Tipo de post (comparativa / guia / resena_seo)
- Fecha y estado

---

## Obtener API Key de Gemini (gratis)

El tier gratuito de Gemini 1.5 Flash permite **1,500 requests por día sin costo**.

1. Ve a **https://aistudio.google.com/app/apikey**
2. Inicia sesión con tu cuenta de Google
3. Haz clic en **"Create API key"**
4. Selecciona o crea un proyecto de Google Cloud
5. Copia la clave generada (empieza con `AIzaSy...`)
6. Pégala en tu `.env` como `GEMINI_API_KEY_1=AIzaSy...`

### Cómo tener múltiples claves gratuitas

Cada **proyecto de Google Cloud** tiene su propio límite de 1,500 requests/día.

1. Ve a https://console.cloud.google.com/
2. Haz clic en el selector de proyecto (arriba a la izquierda)
3. Haz clic en **"Nuevo proyecto"** → ponle un nombre → Crear
4. Vuelve a https://aistudio.google.com/app/apikey
5. Crea una API Key en ese nuevo proyecto
6. Añádela a tu `.env` como `GEMINI_API_KEY_2=...`

Con 3 claves tienes **4,500 requests/día ≈ 1,500 blogs/día** de forma totalmente gratuita.

---

## Configurar WordPress

> Solo necesitas esta sección cuando estés listo para conectar a un WordPress real (`WP_MODE=live`).

### Paso 1 — Instalar WordPress

Opciones según tu situación:

| Situación | Recomendación |
|---|---|
| Solo quiero probar localmente | Instala **Local WP** (https://localwp.com/) — gratis, en tu PC |
| Quiero un blog real económico | Hostinger (~$2-4/mes) o SiteGround (~$3-6/mes) |
| Solo pruebas sin gastar nada | InfinityFree.net (hosting gratuito con limitaciones) |

### Paso 2 — Instalar plugins en WordPress

Desde **WP Admin → Plugins → Añadir nuevo**, instala:

| Plugin | Para qué sirve |
|---|---|
| **Yoast SEO** (gratis) | Habilita los campos de meta description y focus keyword que el bot rellena |
| **Advanced Custom Fields** (gratis) | Habilita campos personalizados como `affiliate_url` |

### Paso 3 — Crear Application Password

1. En WP Admin ve a **Usuarios → Tu Perfil**
2. Baja hasta la sección **"Application Passwords"**
3. En "New Application Password Name" escribe: `Content Generator Bot`
4. Haz clic en **"Add New Application Password"**
5. Copia la contraseña que aparece (formato: `xxxx xxxx xxxx xxxx xxxx xxxx`)
6. Pégala en tu `.env` como `WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx`

> ⚠️ Esta contraseña solo se muestra una vez. Si la pierdes, deberás revocarla y crear una nueva.

### Paso 4 — Registrar los campos personalizados

Para que el bot pueda guardar los campos `affiliate_url`, `focus_keyword` y `meta_description` en WordPress, necesitas añadir código a tu tema hijo.

En **WP Admin → Apariencia → Editor de archivos de tema**, edita `functions.php` y añade al final:

```php
function register_affiliate_meta_fields() {
    $fields = ['affiliate_url', 'focus_keyword', 'meta_description',
               'post_type_label', 'ai_generated'];
    foreach ($fields as $field) {
        register_post_meta('post', $field, [
            'show_in_rest' => true,
            'single'       => true,
            'type'         => 'string',
        ]);
    }
}
add_action('init', 'register_affiliate_meta_fields');
```

### Paso 5 — Verificar la conexión

Desde cmd en la carpeta del proyecto:

```cmd
python -c "from dotenv import load_dotenv; load_dotenv(); from core.wp_client import WordPressClient; c=WordPressClient.from_env(); print('Conexion:', c.test_connection())"
```

Debe imprimir `Conexion: True`. Si no, revisa las credenciales en `.env`.

---

## Límites y costos

### Gemini API — Tier Gratuito (Gemini 1.5 Flash)

| Límite | Valor |
|---|---|
| Requests por día (RPD) | **1,500** por clave |
| Requests por minuto (RPM) | 15 por clave |
| Tokens por minuto (TPM) | 1,000,000 |
| Costo mensual | **$0.00** |

### Cuánto contenido puedes generar

Cada blog completo (3 posts) consume ~3 requests y ~6,000 tokens.

| Claves gratuitas | Blogs por día | Blogs por mes |
|---|---|---|
| 1 clave | ~500 | ~15,000 |
| 2 claves | ~1,000 | ~30,000 |
| 3 claves | ~1,500 | ~45,000 |

### Cuándo se reinicia el límite diario

Los contadores de Gemini se reinician a **medianoche hora del Pacífico (UTC-8)**. El sistema detecta el cambio de día automáticamente y restablece los contadores.

---

## Preguntas frecuentes

**¿Puedo dejar la app corriendo todo el día?**
Sí. Streamlit es un servidor web ligero. Puedes dejarlo corriendo en segundo plano. El sistema gestiona las cuotas automáticamente.

**¿Los posts se publican solos?**
No. El sistema está diseñado para que **tú revises y publiques manualmente** cada post. Los borradores nunca se auto-publican.

**¿Qué pasa si Gemini devuelve una respuesta mala?**
El cliente reintenta automáticamente hasta 3 veces con espera exponencial (2s → 4s → 8s). Si los 3 intentos fallan, muestra un error claro en la GUI.

**¿Dónde se guardan los logs?**
En la carpeta `logs/`. El archivo `token_usage.json` guarda el historial de uso de tokens. Los logs de ejecución se muestran en la terminal donde corre Streamlit.

**¿Puedo usar esto con ChatGPT en lugar de Gemini?**
El sistema está construido específicamente para la API de Gemini. Para usar OpenAI habría que modificar `core/gemini_client.py`.

**¿Los borradores simulados se pueden subir a WordPress después?**
Sí. Los JSON en `drafts_output/` contienen toda la información necesaria. Puedes cambiar a `WP_MODE=live` y volver a generar el mismo tópico, o crear un script de importación.

---

## Solución de problemas

| Error | Qué significa | Solución |
|---|---|---|
| `No se encontraron API Keys` | `.env` no tiene `GEMINI_API_KEY_1` | Añade tu clave o activa `GEMINI_MOCK_MODE=true` |
| `Error 429 / ResourceExhausted` | Cuota diaria agotada | El sistema rota automáticamente. Si todas agotadas, esperar al día siguiente |
| `401 Unauthorized` (WordPress) | Credenciales incorrectas | Regenerar Application Password en WP Admin |
| `404 Not Found` (WordPress) | URL de WordPress mal configurada | Verificar `WP_BASE_URL` en `.env` (sin barra final) |
| `ModuleNotFoundError: No module named 'flask'` | Dependencias no instaladas | Ejecutar `pip install -r requirements.txt` |
| Los archivos JSON no aparecen | Carpeta `drafts_output/` no existe | Se crea sola al generar el primer post. Si no, créala manualmente |
| El panel muestra 0 tokens siempre | `GEMINI_MOCK_MODE=true` activo | En modo mock no se consumen tokens reales, el contador en 0 es correcto |
| `logs/token_usage.json` con error | Archivo corrupto | Bórralo manualmente — se recrea solo al reiniciar la app |
| La app no abre en el navegador | Puerto 5000 ocupado | Cambia el puerto en `app.py`: `app.run(port=5001)` |

---

## Estructura de carpetas

```
proyecto/
├── app.py                  ← Punto de entrada. Ejecutar con: python app.py
├── templates/              ← Plantillas HTML Jinja2 (Flask)
│   ├── base.html
│   ├── index.html
│   ├── historial.html
│   ├── topicos.html
│   └── borrador.html
├── requirements.txt        ← Dependencias Python
├── .env                    ← Tu configuración (NO subir a git)
├── .env.example            ← Plantilla de configuración (SÍ subir a git)
├── .gitignore
│
├── core/
│   ├── orchestrator.py     ← Cerebro del sistema: coordina todo el flujo
│   ├── gemini_client.py    ← Comunicación con Gemini API
│   ├── token_manager.py    ← Gestión de claves API y conteo de tokens
│   ├── wp_client.py        ← Comunicación con WordPress
│   ├── prompt_templates.py ← Los 3 prompts que se envían a Gemini
│   └── amazon_parser.py    ← Extrae nombre del producto desde URLs Amazon
│
├── models/
│   └── post_draft.py       ← Estructura de datos de un borrador de post
│
├── tests/
│   ├── test_amazon_parser.py
│   └── test_wp_client.py
│
├── logs/
│   └── token_usage.json    ← Historial de uso de tokens (se crea automáticamente)
│
└── drafts_output/          ← Borradores en modo simulado (se crea automáticamente)
    └── draft_001_comparativa_20260323_143201.json
```

---

## Flujo completo del sistema

```
  Usuario escribe un tópico o URL de Amazon
                    │
                    ▼
         amazon_parser.py (si es URL)
         Extrae nombre del producto
                    │
                    ▼
          orchestrator.py
          Para cada uno de los 3 tipos de post:
                    │
          ┌─────────┴──────────┐
          │                    │
          ▼                    ▼
   prompt_templates.py   token_manager.py
   Construye el prompt   Verifica cuota disponible
          │                    │
          └─────────┬──────────┘
                    │
                    ▼
           gemini_client.py
           Llama a Gemini API
           (o devuelve mock si MOCK_MODE=true)
                    │
                    ▼
           Parsea respuesta JSON
           → PostDraft (modelo Pydantic)
                    │
                    ▼
             wp_client.py
    ┌──────────────┴──────────────┐
    │                             │
    ▼                             ▼
WP_MODE=simulated          WP_MODE=live
Guarda JSON en             POST a WordPress
drafts_output/             REST API → Borrador
```

---

*Blog Content Generator v1.0 · Human-in-the-Loop · Gemini 1.5 Flash + WordPress*
