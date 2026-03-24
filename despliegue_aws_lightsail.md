# 🚀 Guía: Desplegar Streamlit + WordPress en AWS Lightsail

> **Proyecto**: Generador de contenido SEO (Streamlit + Gemini API) + Blog WordPress
> **Stack**: Python 3.11, Streamlit, Gemini API, WordPress, Apache, MySQL, Nginx
> **Fecha de referencia de precios**: Marzo 2026 — [Precios oficiales Lightsail](https://aws.amazon.com/es/lightsail/pricing/)

---

## 🏗️ Arquitectura Final

```
Internet
    │
    ▼
Lightsail (Ubuntu 22.04 — Plan $5/mes)
    │
    ├── Nginx (reverse proxy — puerto 80 / 443)
    │     ├── tudominio.com        → WordPress  (Apache en 127.0.0.1:8080)
    │     └── app.tudominio.com    → Streamlit  (127.0.0.1:8501)
    │
    ├── WordPress
    │     ├── Apache2 (puerto 8080 interno)
    │     ├── PHP 8.2
    │     └── MySQL 8.0
    │
    └── Streamlit App (Python 3.11 + venv)
```

---

## 📋 Índice

1. [Plan recomendado](#1-plan-recomendado)
2. [Crear cuenta AWS](#2-crear-cuenta-aws)
3. [Crear instancia Lightsail](#3-crear-instancia-lightsail)
4. [Conectarse al servidor por SSH](#4-conectarse-al-servidor-por-ssh)
5. [Instalar WordPress](#5-instalar-wordpress)
6. [Instalar Streamlit y subir el proyecto](#6-instalar-streamlit-y-subir-el-proyecto)
7. [Configurar variables de entorno](#7-configurar-variables-de-entorno)
8. [Instalar y configurar Nginx](#8-instalar-y-configurar-nginx)
9. [Ejecutar Streamlit como servicio permanente](#9-ejecutar-streamlit-como-servicio-permanente)
10. [Abrir puertos en el firewall de Lightsail](#10-abrir-puertos-en-el-firewall-de-lightsail)
11. [Finalizar instalación de WordPress](#11-finalizar-instalacion-de-wordpress)
12. [Mantenimiento y actualizaciones](#12-mantenimiento-y-actualizaciones)
13. [Costos estimados](#13-costos-estimados)
14. [Checklist final](#14-checklist-final)

---

## 1. Plan Elegido

Vas a usar el plan de **$5/mes** que entra dentro del nivel gratuito de AWS.

> ⚠️ **Nota importante**: Con WordPress (MySQL ~512 MB) + Streamlit (~300 MB) + sistema operativo en 1 GB de RAM,
> el servidor puede ir algo justo de memoria. Si en algún momento notas lentitud,
> puedes crear un snapshot y escalar al plan de $10/mes sin perder datos.

| Plan | RAM | vCPU | SSD | Precio | Gratis |
|------|-----|------|-----|--------|--------|
| **✅ $5/mes** ⭐ | **1 GB** | **2** | **40 GB** | **$5/mes** | **✅ 3 meses** |
| $10/mes | 2 GB | 2 | 60 GB | $10/mes | ✅ 3 meses |
| $20/mes | 4 GB | 2 | 80 GB | $20/mes | ✅ 3 meses |

### 🎁 Nivel Gratuito — Condiciones

- **3 meses gratis** en el plan de $5/mes de Linux/Unix con IPv4.
- Solo aplica a **un paquete por cuenta**.
- Aplica a cuentas que comenzaron a usar Lightsail desde el **8/7/2021**.
- Después de los 3 meses: **$5 USD/mes**.

> ⚠️ **Se requiere tarjeta de crédito** para crear la cuenta. AWS hace un cargo de verificación
> de **$1 USD** que se devuelve automáticamente en 3-5 días.

---

## 2. Crear Cuenta AWS

> ⚠️ **IMPORTANTE antes de empezar**:
> - Usa una **red móvil (hotspot)** — las redes corporativas bloquean el registro.
> - Usa un **email personal** (Gmail, Outlook, Hotmail) — no el corporativo.
> - Desactiva cualquier **VPN** que tengas activa.
> - Usa una tarjeta **Visa o Mastercard personal** con pagos internacionales habilitados.

### Paso 2.1 — Registro inicial

1. Ve a [https://aws.amazon.com/es/](https://aws.amazon.com/es/)
2. Haz clic en **"Crear una cuenta de AWS"**
3. Rellena:
   - **Dirección de email**: usa Gmail o Outlook personal
   - **Contraseña**: mínimo 8 caracteres, con mayúsculas, números y símbolos
   - **Nombre de la cuenta**: ej. `blog-seo-proyecto`
4. Haz clic en **"Continuar"**

### Paso 2.2 — Verificación de email

1. Revisa tu bandeja de entrada
2. Copia el código de 6 dígitos que llegó
3. Pégalo en la pantalla de AWS y haz clic en **"Verificar"**

### Paso 2.3 — Información de contacto

1. Selecciona **"Personal"** (no empresarial)
2. Rellena nombre completo, teléfono, país y dirección
3. Acepta el acuerdo de cliente de AWS
4. Haz clic en **"Continuar"**

### Paso 2.4 — Tarjeta de crédito

1. Introduce los datos de tu tarjeta Visa o Mastercard **personal**
2. El nombre debe coincidir exactamente con el de la tarjeta
3. Haz clic en **"Verificar y agregar"**

> ❌ **Si falla aquí**: desactiva VPN, cambia a hotspot móvil, intenta con otra tarjeta.
> Las tarjetas prepago y virtuales suelen ser rechazadas.

### Paso 2.5 — Verificación de identidad

1. Elige **"Mensaje de texto (SMS)"**
2. Selecciona tu país (+34 para España)
3. Introduce tu número de móvil
4. Haz clic en **"Enviar SMS"** e introduce el código recibido

### Paso 2.6 — Plan de soporte

1. Selecciona **"Básico — Gratuito"**
2. Haz clic en **"Finalizar registro"**
3. Recibirás un email de confirmación en unos minutos

### Paso 2.7 — Acceder a la consola

1. Ve a [https://console.aws.amazon.com](https://console.aws.amazon.com)
2. Inicia sesión con tu email y contraseña
3. En el buscador superior escribe **"Lightsail"** y haz clic en el resultado

---

## 3. Crear Instancia Lightsail

### Paso 3.1 — Nueva instancia

1. En el panel de Lightsail haz clic en **"Crear instancia"**
2. **Región**: `Europe (Ireland) — eu-west-1`
   > Más cercana a España → menor latencia para ti y tus usuarios
3. **Plataforma**: `Linux/Unix`
4. **Blueprint**: Selecciona `OS Only` → `Ubuntu 22.04 LTS`
   > ⚠️ No uses la imagen preconfigurada de WordPress — necesitamos instalar también Streamlit

### Paso 3.2 — Seleccionar plan

Selecciona el plan de **$5 USD/mes**:
```
✅ $5 USD/mes
   1 GB RAM | 2 vCPUs | 40 GB SSD | 2 TB transferencia
   [3 MESES GRATIS]
```

### Paso 3.3 — Clave SSH

1. Haz clic en **"Crear un par de claves nuevo"**
2. Dale un nombre: `llave-blog-seo`
3. Se descargará automáticamente el archivo `.pem`
4. Guárdalo en: `C:\Users\TuUsuario\.ssh\llave-blog-seo.pem`
5. ⚠️ **Guarda este archivo en un lugar seguro — si lo pierdes no podrás acceder al servidor**

### Paso 3.4 — Nombre y creación

1. **Nombre de la instancia**: `blog-seo-servidor`
2. Haz clic en **"Crear instancia"**
3. Espera ~2 minutos hasta que el estado sea **"En ejecución"** ✅

4. Una vez creada, haz clic en la instancia y anota la **IP pública** que aparece en la pantalla
   (ej: `3.250.45.123`). La usarás para conectarte por SSH y para acceder a la app.

> ℹ️ Sin IP estática, esta IP **puede cambiar** si reinicias el servidor.
> Para este proyecto es perfectamente válido — simplemente apunta la IP cada vez que reinicies.

---

## 4. Conectarse al Servidor por SSH

### Opción A — Desde el navegador (más fácil, recomendado para empezar)

1. En el panel de Lightsail, haz clic en tu instancia `blog-seo-servidor`
2. Haz clic en el botón naranja **"Conectar mediante SSH"**
3. Se abre una terminal directamente en el navegador ✅

### Opción B — Desde tu PC con Windows (CMD/PowerShell)

1. **Ajustar permisos del archivo `.pem`** (en PowerShell):
   ```powershell
   icacls "C:\Users\TuUsuario\.ssh\llave-blog-seo.pem" /inheritance:r /grant:r "%USERNAME%:R"
   ```

2. **Conectar**:
   ```powershell
   ssh -i "C:\Users\TuUsuario\.ssh\llave-blog-seo.pem" ubuntu@3.250.45.123
   ```
   Reemplaza `3.250.45.123` con tu IP estática real.

3. La primera vez preguntará si confías en el host → escribe `yes` y pulsa Enter.

---

## 5. Instalar WordPress

Ejecuta estos comandos **en orden** en la terminal SSH:

### Paso 5.1 — Actualizar el sistema

```bash
sudo apt update && sudo apt upgrade -y
```

### Paso 5.2 — Instalar Apache, PHP y MySQL

```bash
# Apache
sudo apt install apache2 -y
```

> ⚠️ **Ubuntu 22.04 no incluye PHP 8.2 por defecto.** Hay que añadir el repositorio externo de PHP antes de instalarlo:

```bash
# Añadir el repositorio PPA de PHP (Ondřej Surý)
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:ondrej/php -y
sudo apt update
```

```bash
# PHP 8.2 y todas las extensiones necesarias para WordPress
sudo apt install -y php8.2 php8.2-mysql php8.2-curl php8.2-gd \
  php8.2-mbstring php8.2-xml php8.2-xmlrpc php8.2-soap \
  php8.2-intl php8.2-zip php8.2-imagick

# Verificar que se instaló correctamente (debe mostrar PHP 8.2.x)
php -v

# Activar PHP 8.2 en Apache
sudo a2enmod php8.2
sudo systemctl restart apache2
```

```bash
# MySQL
sudo apt install mysql-server -y
```

### Paso 5.3 — Configurar la base de datos MySQL

```bash
sudo mysql
```

Dentro de MySQL, ejecuta estas líneas **una por una**:

```sql
CREATE DATABASE wordpress_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'wp_user'@'localhost' IDENTIFIED BY 'TuPasswordSegura123!';
GRANT ALL PRIVILEGES ON wordpress_db.* TO 'wp_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

> 📝 Anota el password elegido — lo necesitarás en el paso 5.6

### Paso 5.4 — Descargar e instalar WordPress

```bash
cd /tmp
wget https://wordpress.org/latest.tar.gz
tar -xzf latest.tar.gz
sudo mv wordpress /var/www/html/wordpress
sudo chown -R www-data:www-data /var/www/html/wordpress
sudo chmod -R 755 /var/www/html/wordpress
```

### Paso 5.5 — Mover Apache al puerto 8080

Nginx usará el puerto 80, así que Apache debe escuchar en el 8080:

```bash
sudo sed -i 's/Listen 80/Listen 8080/' /etc/apache2/ports.conf
```

### Paso 5.6 — Crear VirtualHost de Apache para WordPress

```bash
sudo tee /etc/apache2/sites-available/wordpress.conf > /dev/null <<'EOF'
<VirtualHost *:8080>
    ServerAdmin admin@tudominio.com
    DocumentRoot /var/www/html/wordpress
    ServerName tudominio.com
    ServerAlias www.tudominio.com

    <Directory /var/www/html/wordpress>
        Options FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>

    ErrorLog ${APACHE_LOG_DIR}/wordpress_error.log
    CustomLog ${APACHE_LOG_DIR}/wordpress_access.log combined
</VirtualHost>
EOF
```

```bash
sudo a2ensite wordpress.conf
sudo a2dissite 000-default.conf
sudo a2enmod rewrite
sudo systemctl restart apache2
```

### Paso 5.7 — Configurar wp-config.php

```bash
sudo cp /var/www/html/wordpress/wp-config-sample.php \
        /var/www/html/wordpress/wp-config.php

sudo sed -i "s/database_name_here/wordpress_db/" \
    /var/www/html/wordpress/wp-config.php
sudo sed -i "s/username_here/wp_user/" \
    /var/www/html/wordpress/wp-config.php
sudo sed -i "s/password_here/TuPasswordSegura123!/" \
    /var/www/html/wordpress/wp-config.php
```

### Paso 5.8 — Generar claves secretas de WordPress

```bash
# Obtener claves únicas
curl -s https://api.wordpress.org/secret-key/1.1/salt/
```

Copia el bloque que devuelve y añádelo al wp-config.php:
```bash
sudo nano /var/www/html/wordpress/wp-config.php
```
Busca las líneas `define('AUTH_KEY', 'put your unique phrase here');`
y reemplázalas con el bloque que copiaste. Guarda con `Ctrl+O` → `Ctrl+X`.

---

## 6. Instalar Streamlit y Subir el Proyecto

### Paso 6.1 — Instalar Python y dependencias del sistema

```bash
sudo apt install python3.11 python3.11-venv python3-pip git -y
```

### Paso 6.2 — Crear entorno virtual

```bash
cd ~
python3.11 -m venv venv_proyecto
source venv_proyecto/bin/activate
pip install --upgrade pip
```

### Paso 6.3 — Subir el proyecto

#### Opción A — Via SCP desde tu PC (Windows PowerShell)

Ejecuta esto en tu **PC local** (no en el servidor):

```powershell
scp -i "C:\Users\TuUsuario\.ssh\llave-blog-seo.pem" -r "C:\Users\Luis.RANGEL-GONZALEZ\OneDrive - Akkodis\Desktop\proyecto" ubuntu@3.250.45.123:/home/ubuntu/proyecto
```

#### Opción B — Via Git (si tienes el proyecto en GitHub)

```bash
cd ~
git clone https://github.com/tu-usuario/tu-repo.git proyecto
```

### Paso 6.4 — Instalar dependencias Python

```bash
cd ~/proyecto
source ~/venv_proyecto/bin/activate
pip install streamlit google-generativeai requests python-dotenv
```

Si tienes `requirements.txt`:
```bash
pip install -r requirements.txt
```

> 💡 **Si no tienes `requirements.txt`**, créalo con este contenido antes de subir el proyecto:
> ```
> streamlit
> google-generativeai
> requests
> python-dotenv
> ```

---

## 7. Configurar Variables de Entorno

Tu app usa claves de API de Gemini y credenciales de WordPress. **Nunca las subas en el código fuente**.

### Paso 7.1 — Crear el archivo `.env`

```bash
nano ~/proyecto/.env
```

Escribe dentro:
```env
GEMINI_API_KEY=tu_clave_gemini_aqui
GEMINI_API_KEY_2=segunda_clave_si_tienes
WP_URL=https://tudominio.com
WP_USER=tu_usuario_wp
WP_APP_PASSWORD=password_de_aplicacion_wp
```

Guarda con `Ctrl+O` → `Enter` → `Ctrl+X`.

### Paso 7.2 — Proteger el archivo

```bash
chmod 600 ~/proyecto/.env
echo ".env" >> ~/proyecto/.gitignore
```

---

## 8. Instalar y Configurar Nginx

Nginx actúa como **reverse proxy**: recibe las peticiones del puerto 80 y las redirige al servicio correcto (WordPress en 8080 o Streamlit en 8501).

### Paso 8.1 — Instalar Nginx

```bash
sudo apt install nginx -y
sudo systemctl enable nginx
sudo systemctl start nginx
```

### Paso 8.2 — Crear configuración de Nginx

```bash
sudo tee /etc/nginx/sites-available/blog-seo > /dev/null <<'EOF'
# WordPress — dominio principal
server {
    listen 80;
    server_name tudominio.com www.tudominio.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Streamlit — subdominio app
server {
    listen 80;
    server_name app.tudominio.com;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}
EOF
```

> 📝 Reemplaza `tudominio.com` y `app.tudominio.com` con tus dominios reales.

### Paso 8.3 — Activar la configuración

```bash
sudo ln -s /etc/nginx/sites-available/blog-seo /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t        # Verificar que no hay errores de sintaxis
sudo systemctl reload nginx
```

> 💡 **Sin dominio por ahora**: puedes acceder directamente por IP y puerto:
> - WordPress: `http://3.250.45.123:8080`
> - Streamlit: `http://3.250.45.123:8501`

---

## 9. Ejecutar Streamlit como Servicio Permanente

Para que Streamlit siga corriendo aunque cierres la terminal SSH:

### Paso 9.1 — Crear el servicio systemd

```bash
sudo tee /etc/systemd/system/streamlit-seo.service > /dev/null <<'EOF'
[Unit]
Description=Streamlit SEO App
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/TuEden-bot
Environment="PATH=/home/ubuntu/venv_proyecto/bin"
EnvironmentFile=/home/ubuntu/TuEden-bot/.env
ExecStart=/home/ubuntu/venv_proyecto/bin/streamlit run app.py \
    --server.port 8501 \
    --server.address 127.0.0.1 \
    --server.headless true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

### Paso 9.2 — Activar e iniciar

```bash
sudo systemctl daemon-reload
sudo systemctl enable streamlit-seo
sudo systemctl start streamlit-seo
```

### Paso 9.3 — Verificar que está corriendo

```bash
sudo systemctl status streamlit-seo
```

Deberías ver `Active: active (running)` ✅

### Comandos útiles

```bash
# Ver logs en tiempo real
sudo journalctl -u streamlit-seo -f

# Reiniciar (tras actualizar código)
sudo systemctl restart streamlit-seo

# Detener
sudo systemctl stop streamlit-seo
```

---

## 10. Abrir Puertos en el Firewall de Lightsail

Hay que permitir el tráfico web desde el panel de Lightsail:

1. En la consola de Lightsail → tu instancia → pestaña **"Redes"**
2. En **"Firewall de IPv4"**, añade estas reglas haciendo clic en **"Agregar regla"** para cada una:

| Protocolo | Puerto | Descripción |
|-----------|--------|-------------|
| TCP | 80 | HTTP (WordPress + Nginx) |
| TCP | 443 | HTTPS (SSL — cuando lo configures) |
| TCP | 8501 | Streamlit (acceso directo por IP) |
| TCP | 8080 | Apache/WordPress (acceso directo por IP) |

Para cada regla:
- Tipo: `Personalizado` → Protocolo: `TCP` → Puerto: el número
- Origen: `Cualquier lugar (0.0.0.0/0)`
- Clic en **"Guardar"**

---

## 11. Finalizar Instalación de WordPress

### Paso 11.1 — Asistente web de WordPress

1. Abre tu navegador y ve a:
   - Con dominio: `http://tudominio.com`
   - Sin dominio: `http://3.250.45.123:8080`

2. Selecciona el **idioma**: Español

3. Rellena los datos del sitio:
   - **Título del sitio**: el nombre de tu blog
   - **Nombre de usuario**: tu usuario admin (evita usar "admin")
   - **Contraseña**: una contraseña fuerte — guárdala bien
   - **Email**: tu email de contacto

4. Haz clic en **"Instalar WordPress"**

5. Accede al panel en `/wp-admin` con tus credenciales

### Paso 11.2 — Crear contraseña de aplicación para Streamlit

Para que tu app Streamlit pueda publicar posts en WordPress via la API REST:

1. En WordPress → **Usuarios** → tu usuario → desplázate hasta **"Contraseñas de aplicación"**
2. Nombre: `streamlit-seo-app`
3. Haz clic en **"Agregar nueva contraseña de aplicación"**
4. **Copia el password generado** — solo se muestra una vez
5. Actualiza el archivo `.env` en el servidor:
   ```bash
   nano ~/proyecto/.env
   ```
   Actualiza estas líneas:
   ```env
   WP_URL=http://tudominio.com
   WP_USER=tu_usuario_admin
   WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
   ```
6. Reinicia Streamlit:
   ```bash
   sudo systemctl restart streamlit-seo
   ```

---

## 12. Mantenimiento y Actualizaciones

### Actualizar el código de Streamlit (via SCP desde Windows)

Ejecuta esto en tu **PC local** (PowerShell), no en el servidor:

```powershell
scp -i "C:\Users\TuUsuario\.ssh\llave-blog-seo.pem" -r "C:\Users\Luis.RANGEL-GONZALEZ\OneDrive - Akkodis\Desktop\proyecto\." ubuntu@3.250.45.123:/home/ubuntu/proyecto/
```

Luego en el servidor reinicia el servicio:

```bash
sudo systemctl restart streamlit-seo
```

### Actualizar el código de Streamlit (via Git)

```bash
cd ~/proyecto
source ~/venv_proyecto/bin/activate
git pull
pip install -r requirements.txt
sudo systemctl restart streamlit-seo
```

### Actualizar WordPress

Desde el panel de administración `/wp-admin` → **Escritorio** → **Actualizaciones**.
WordPress avisará cuando haya actualizaciones de core, plugins o temas disponibles.

### Crear snapshot (copia de seguridad)

1. En Lightsail → tu instancia → pestaña **"Instantáneas"**
2. Haz clic en **"Crear instantánea"**
3. Costo: **$0.05 USD/GB/mes** (~$3/mes para 60 GB)

> 💡 Crea siempre una snapshot **antes de cualquier actualización importante** del servidor.

### Ver logs en tiempo real

```bash
# Logs de Streamlit
sudo journalctl -u streamlit-seo -f

# Logs de Nginx
sudo tail -f /var/log/nginx/error.log

# Logs de Apache / WordPress
sudo tail -f /var/log/apache2/wordpress_error.log

# Logs de MySQL
sudo tail -f /var/log/mysql/error.log
```

---

## 13. Costos Estimados

| Concepto | Costo mensual |
|----------|---------------|
| Instancia Linux $5/mes (1 GB RAM, IPv4) | **GRATIS** primeros 3 meses → luego **$5/mes** |
| IP pública (incluida en el plan) | **$0** |
| Instantáneas (~40 GB) | ~$2/mes |
| CDN (opcional) | **GRATIS** el primer año (50 GB) |
| Dominio (opcional, Route 53 o Namecheap) | ~$1/mes (~$12/año) |
| **Total mes 1-3** | **~$3/mes** |
| **Total mes 4+** | **~$8/mes** |

> ✅ **Costo estimado el primer año**: ~$63 USD total---

## 14. Checklist Final

### Cuenta AWS
- [ ] Crear cuenta desde red móvil (hotspot) con email personal
- [ ] Verificar email y número de teléfono
- [ ] Agregar tarjeta de crédito personal con pagos internacionales

### Instancia Lightsail
- [ ] Crear instancia Ubuntu 22.04 — plan **$5/mes** — Europa (Irlanda)
- [ ] Descargar y guardar el archivo `llave-blog-seo.pem` en lugar seguro
- [ ] Anotar la IP pública de la instancia

### Servidor — WordPress
- [ ] Conectar por SSH
- [ ] `sudo apt update && sudo apt upgrade -y`
- [ ] Instalar Apache, PHP 8.2 y MySQL
- [ ] Crear base de datos y usuario MySQL (`wordpress_db` / `wp_user`)
- [ ] Descargar WordPress en `/var/www/html/wordpress`
- [ ] Mover Apache al puerto 8080
- [ ] Crear VirtualHost en Apache
- [ ] Configurar `wp-config.php` con datos de la BD

### Servidor — Streamlit
- [ ] Instalar Python 3.11 y venv
- [ ] Subir el proyecto via SCP o Git
- [ ] Crear y activar entorno virtual (`venv_proyecto`)
- [ ] Instalar dependencias Python (`pip install -r requirements.txt`)
- [ ] Crear archivo `.env` con las API keys de Gemini y credenciales WP

### Servidor — Nginx
- [ ] Instalar Nginx
- [ ] Crear archivo `/etc/nginx/sites-available/blog-seo` con reverse proxy
- [ ] Activar sitio con `ln -s` y eliminar el default
- [ ] `sudo nginx -t` sin errores
- [ ] `sudo systemctl reload nginx`

### Servidor — Servicio Streamlit
- [ ] Crear `/etc/systemd/system/streamlit-seo.service`
- [ ] `sudo systemctl daemon-reload`
- [ ] `sudo systemctl enable streamlit-seo`
- [ ] `sudo systemctl start streamlit-seo`
- [ ] Verificar con `sudo systemctl status streamlit-seo` → `active (running)`

### Firewall Lightsail
- [ ] Abrir puerto **80** (HTTP)
- [ ] Abrir puerto **443** (HTTPS)
- [ ] Abrir puerto **8080** (WordPress/Apache directo)
- [ ] Abrir puerto **8501** (Streamlit directo)

### WordPress — Configuración final
- [ ] Completar asistente de instalación via `http://IP:8080`
- [ ] Crear contraseña de aplicación para Streamlit en `/wp-admin`
- [ ] Actualizar `.env` con `WP_APP_PASSWORD`
- [ ] `sudo systemctl restart streamlit-seo`

### Verificación final
- [ ] WordPress responde en `http://IP:8080` o `http://tudominio.com`
- [ ] Streamlit responde en `http://IP:8501` o `http://app.tudominio.com`
- [ ] La función de publicar post desde Streamlit → WordPress funciona
- [ ] Primera snapshot creada en Lightsail

---

*Guía generada para el proyecto en `C:\Users\Luis.RANGEL-GONZALEZ\OneDrive - Akkodis\Desktop\proyecto`*
*Referencia de precios: [https://aws.amazon.com/es/lightsail/pricing/](https://aws.amazon.com/es/lightsail/pricing/)*
