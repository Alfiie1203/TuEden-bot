# ðŸš€ GuÃ­a: Desplegar Streamlit + WordPress en AWS Lightsail

> **Proyecto**: Generador de contenido SEO (Streamlit + Gemini API) + Blog WordPress
> **Stack**: Python 3.11, Streamlit, Gemini API, WordPress, Apache, MySQL, Nginx
> **Fecha de referencia de precios**: Marzo 2026 â€” [Precios oficiales Lightsail](https://aws.amazon.com/es/lightsail/pricing/)

---

## ðŸ—ï¸ Arquitectura Final

```
Internet
    â”‚
    â–¼
Lightsail (Ubuntu 22.04 â€” Plan $5/mes)
    â”‚
    â”œâ”€â”€ Nginx (reverse proxy â€” puerto 80 / 443)
    â”‚     â”œâ”€â”€ tudominio.com        â†’ WordPress  (Apache en 127.0.0.1:8080)
    â”‚     â””â”€â”€ app.tudominio.com    â†’ Streamlit  (127.0.0.1:5000)
    â”‚
    â”œâ”€â”€ WordPress
    â”‚     â”œâ”€â”€ Apache2 (puerto 8080 interno)
    â”‚     â”œâ”€â”€ PHP 8.2
    â”‚     â””â”€â”€ MySQL 8.0
    â”‚
    â””â”€â”€ Streamlit App (Python 3.11 + venv)
```

---

## ðŸ“‹ Ãndice

1. [Plan recomendado](#1-plan-recomendado)
2. [Crear cuenta AWS](#2-crear-cuenta-aws)
3. [Crear instancia Lightsail](#3-crear-instancia-lightsail)
4. [Conectarse al servidor por SSH](#4-conectarse-al-servidor-por-ssh)
5. [Instalar WordPress](#5-instalar-wordpress)
6. [Instalar Flask y subir el proyecto](#6-instalar-streamlit-y-subir-el-proyecto)
7. [Configurar variables de entorno](#7-configurar-variables-de-entorno)
8. [Instalar y configurar Nginx](#8-instalar-y-configurar-nginx)
9. [Ejecutar Flask como servicio permanente](#9-ejecutar-streamlit-como-servicio-permanente)
10. [Abrir puertos en el firewall de Lightsail](#10-abrir-puertos-en-el-firewall-de-lightsail)
11. [Finalizar instalaciÃ³n de WordPress](#11-finalizar-instalacion-de-wordpress)
12. [Mantenimiento y actualizaciones](#12-mantenimiento-y-actualizaciones)
13. [Costos estimados](#13-costos-estimados)
14. [Checklist final](#14-checklist-final)

---

## 1. Plan Elegido

Vas a usar el plan de **$5/mes** que entra dentro del nivel gratuito de AWS.

> âš ï¸ **Nota importante**: Con WordPress (MySQL ~512 MB) + Streamlit (~300 MB) + sistema operativo en 1 GB de RAM,
> el servidor puede ir algo justo de memoria. Si en algÃºn momento notas lentitud,
> puedes crear un snapshot y escalar al plan de $10/mes sin perder datos.

| Plan | RAM | vCPU | SSD | Precio | Gratis |
|------|-----|------|-----|--------|--------|
| **âœ… $5/mes** â­ | **1 GB** | **2** | **40 GB** | **$5/mes** | **âœ… 3 meses** |
| $10/mes | 2 GB | 2 | 60 GB | $10/mes | âœ… 3 meses |
| $20/mes | 4 GB | 2 | 80 GB | $20/mes | âœ… 3 meses |

### ðŸŽ Nivel Gratuito â€” Condiciones

- **3 meses gratis** en el plan de $5/mes de Linux/Unix con IPv4.
- Solo aplica a **un paquete por cuenta**.
- Aplica a cuentas que comenzaron a usar Lightsail desde el **8/7/2021**.
- DespuÃ©s de los 3 meses: **$5 USD/mes**.

> âš ï¸ **Se requiere tarjeta de crÃ©dito** para crear la cuenta. AWS hace un cargo de verificaciÃ³n
> de **$1 USD** que se devuelve automÃ¡ticamente en 3-5 dÃ­as.

---

## 2. Crear Cuenta AWS

> âš ï¸ **IMPORTANTE antes de empezar**:
> - Usa una **red mÃ³vil (hotspot)** â€” las redes corporativas bloquean el registro.
> - Usa un **email personal** (Gmail, Outlook, Hotmail) â€” no el corporativo.
> - Desactiva cualquier **VPN** que tengas activa.
> - Usa una tarjeta **Visa o Mastercard personal** con pagos internacionales habilitados.

### Paso 2.1 â€” Registro inicial

1. Ve a [https://aws.amazon.com/es/](https://aws.amazon.com/es/)
2. Haz clic en **"Crear una cuenta de AWS"**
3. Rellena:
   - **DirecciÃ³n de email**: usa Gmail o Outlook personal
   - **ContraseÃ±a**: mÃ­nimo 8 caracteres, con mayÃºsculas, nÃºmeros y sÃ­mbolos
   - **Nombre de la cuenta**: ej. `blog-seo-proyecto`
4. Haz clic en **"Continuar"**

### Paso 2.2 â€” VerificaciÃ³n de email

1. Revisa tu bandeja de entrada
2. Copia el cÃ³digo de 6 dÃ­gitos que llegÃ³
3. PÃ©galo en la pantalla de AWS y haz clic en **"Verificar"**

### Paso 2.3 â€” InformaciÃ³n de contacto

1. Selecciona **"Personal"** (no empresarial)
2. Rellena nombre completo, telÃ©fono, paÃ­s y direcciÃ³n
3. Acepta el acuerdo de cliente de AWS
4. Haz clic en **"Continuar"**

### Paso 2.4 â€” Tarjeta de crÃ©dito

1. Introduce los datos de tu tarjeta Visa o Mastercard **personal**
2. El nombre debe coincidir exactamente con el de la tarjeta
3. Haz clic en **"Verificar y agregar"**

> âŒ **Si falla aquÃ­**: desactiva VPN, cambia a hotspot mÃ³vil, intenta con otra tarjeta.
> Las tarjetas prepago y virtuales suelen ser rechazadas.

### Paso 2.5 â€” VerificaciÃ³n de identidad

1. Elige **"Mensaje de texto (SMS)"**
2. Selecciona tu paÃ­s (+34 para EspaÃ±a)
3. Introduce tu nÃºmero de mÃ³vil
4. Haz clic en **"Enviar SMS"** e introduce el cÃ³digo recibido

### Paso 2.6 â€” Plan de soporte

1. Selecciona **"BÃ¡sico â€” Gratuito"**
2. Haz clic en **"Finalizar registro"**
3. RecibirÃ¡s un email de confirmaciÃ³n en unos minutos

### Paso 2.7 â€” Acceder a la consola

1. Ve a [https://console.aws.amazon.com](https://console.aws.amazon.com)
2. Inicia sesiÃ³n con tu email y contraseÃ±a
3. En el buscador superior escribe **"Lightsail"** y haz clic en el resultado

---

## 3. Crear Instancia Lightsail

### Paso 3.1 â€” Nueva instancia

1. En el panel de Lightsail haz clic en **"Crear instancia"**
2. **RegiÃ³n**: `Europe (Ireland) â€” eu-west-1`
   > MÃ¡s cercana a EspaÃ±a â†’ menor latencia para ti y tus usuarios
3. **Plataforma**: `Linux/Unix`
4. **Blueprint**: Selecciona `OS Only` â†’ `Ubuntu 22.04 LTS`
   > âš ï¸ No uses la imagen preconfigurada de WordPress â€” necesitamos instalar tambiÃ©n Streamlit

### Paso 3.2 â€” Seleccionar plan

Selecciona el plan de **$5 USD/mes**:
```
âœ… $5 USD/mes
   1 GB RAM | 2 vCPUs | 40 GB SSD | 2 TB transferencia
   [3 MESES GRATIS]
```

### Paso 3.3 â€” Clave SSH

1. Haz clic en **"Crear un par de claves nuevo"**
2. Dale un nombre: `llave-blog-seo`
3. Se descargarÃ¡ automÃ¡ticamente el archivo `.pem`
4. GuÃ¡rdalo en: `C:\Users\TuUsuario\.ssh\llave-blog-seo.pem`
5. âš ï¸ **Guarda este archivo en un lugar seguro â€” si lo pierdes no podrÃ¡s acceder al servidor**

### Paso 3.4 â€” Nombre y creaciÃ³n

1. **Nombre de la instancia**: `blog-seo-servidor`
2. Haz clic en **"Crear instancia"**
3. Espera ~2 minutos hasta que el estado sea **"En ejecuciÃ³n"** âœ…

4. Una vez creada, haz clic en la instancia y anota la **IP pÃºblica** que aparece en la pantalla
   (ej: `3.250.45.123`). La usarÃ¡s para conectarte por SSH y para acceder a la app.

> â„¹ï¸ Sin IP estÃ¡tica, esta IP **puede cambiar** si reinicias el servidor.
> Para este proyecto es perfectamente vÃ¡lido â€” simplemente apunta la IP cada vez que reinicies.

---

## 4. Conectarse al Servidor por SSH

### OpciÃ³n A â€” Desde el navegador (mÃ¡s fÃ¡cil, recomendado para empezar)

1. En el panel de Lightsail, haz clic en tu instancia `blog-seo-servidor`
2. Haz clic en el botÃ³n naranja **"Conectar mediante SSH"**
3. Se abre una terminal directamente en el navegador âœ…

### OpciÃ³n B â€” Desde tu PC con Windows (CMD/PowerShell)

1. **Ajustar permisos del archivo `.pem`** (en PowerShell):
   ```powershell
   icacls "C:\Users\TuUsuario\.ssh\llave-blog-seo.pem" /inheritance:r /grant:r "%USERNAME%:R"
   ```

2. **Conectar**:
   ```powershell
   ssh -i "C:\Users\TuUsuario\.ssh\llave-blog-seo.pem" ubuntu@3.250.45.123
   ```
   Reemplaza `3.250.45.123` con tu IP estÃ¡tica real.

3. La primera vez preguntarÃ¡ si confÃ­as en el host â†’ escribe `yes` y pulsa Enter.

---

## 5. Instalar WordPress

Ejecuta estos comandos **en orden** en la terminal SSH:

### Paso 5.1 â€” Actualizar el sistema

```bash
sudo apt update && sudo apt upgrade -y
```

### Paso 5.2 â€” Instalar Apache, PHP y MySQL

```bash
# Apache
sudo apt install apache2 -y
```

> âš ï¸ **Ubuntu 22.04 no incluye PHP 8.2 por defecto.** Hay que aÃ±adir el repositorio externo de PHP antes de instalarlo:

```bash
# AÃ±adir el repositorio PPA de PHP (OndÅ™ej SurÃ½)
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:ondrej/php -y
sudo apt update
```

```bash
# PHP 8.2 y todas las extensiones necesarias para WordPress
sudo apt install -y php8.2 php8.2-mysql php8.2-curl php8.2-gd \
  php8.2-mbstring php8.2-xml php8.2-xmlrpc php8.2-soap \
  php8.2-intl php8.2-zip php8.2-imagick

# Verificar que se instalÃ³ correctamente (debe mostrar PHP 8.2.x)
php -v

# Activar PHP 8.2 en Apache
sudo a2enmod php8.2
sudo systemctl restart apache2
```

```bash
# MySQL
sudo apt install mysql-server -y
```

### Paso 5.3 â€” Configurar la base de datos MySQL

```bash
sudo mysql
```

Dentro de MySQL, ejecuta estas lÃ­neas **una por una**:

```sql
CREATE DATABASE wordpress_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'wp_user'@'localhost' IDENTIFIED BY 'TuPasswordSegura123!';
GRANT ALL PRIVILEGES ON wordpress_db.* TO 'wp_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

> ðŸ“ Anota el password elegido â€” lo necesitarÃ¡s en el paso 5.6

### Paso 5.4 â€” Descargar e instalar WordPress

```bash
cd /tmp
wget https://wordpress.org/latest.tar.gz
tar -xzf latest.tar.gz
sudo mv wordpress /var/www/html/wordpress
sudo chown -R www-data:www-data /var/www/html/wordpress
sudo chmod -R 755 /var/www/html/wordpress
```

### Paso 5.5 â€” Mover Apache al puerto 8080

Nginx usarÃ¡ el puerto 80, asÃ­ que Apache debe escuchar en el 8080:

```bash
sudo sed -i 's/Listen 80/Listen 8080/' /etc/apache2/ports.conf
```

### Paso 5.6 â€” Crear VirtualHost de Apache para WordPress

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

### Paso 5.7 â€” Configurar wp-config.php

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

### Paso 5.8 â€” Generar claves secretas de WordPress

```bash
# Obtener claves Ãºnicas
curl -s https://api.wordpress.org/secret-key/1.1/salt/
```

Copia el bloque que devuelve y aÃ±Ã¡delo al wp-config.php:
```bash
sudo nano /var/www/html/wordpress/wp-config.php
```
Busca las lÃ­neas `define('AUTH_KEY', 'put your unique phrase here');`
y reemplÃ¡zalas con el bloque que copiaste. Guarda con `Ctrl+O` â†’ `Ctrl+X`.

---

## 6. Instalar Flask y subir el Proyecto

### Paso 6.1 â€” Instalar Python y dependencias del sistema

```bash
sudo apt install python3.11 python3.11-venv python3-pip git -y
```

### Paso 6.2 â€” Crear entorno virtual

```bash
cd ~
python3.11 -m venv venv_proyecto
source venv_proyecto/bin/activate
pip install --upgrade pip
```

### Paso 6.3 â€” Subir el proyecto

#### OpciÃ³n A â€” Via SCP desde tu PC (Windows PowerShell)

Ejecuta esto en tu **PC local** (no en el servidor):

```powershell
scp -i "C:\Users\TuUsuario\.ssh\llave-blog-seo.pem" -r "C:\Users\Luis.RANGEL-GONZALEZ\OneDrive - Akkodis\Desktop\proyecto" ubuntu@3.250.45.123:/home/ubuntu/proyecto
```

#### OpciÃ³n B â€” Via Git (si tienes el proyecto en GitHub)

```bash
cd ~
git clone https://github.com/tu-usuario/tu-repo.git proyecto
```

### Paso 6.4 â€” Instalar dependencias Python

```bash
cd ~/proyecto
source ~/venv_proyecto/bin/activate
pip install flask google-generativeai requests python-dotenv
```

Si tienes `requirements.txt`:
```bash
pip install -r requirements.txt
```

> ðŸ’¡ **Si no tienes `requirements.txt`**, crÃ©alo con este contenido antes de subir el proyecto:
> ```
> streamlit
> google-generativeai
> requests
> python-dotenv
> ```

---

## 7. Configurar Variables de Entorno

Tu app usa claves de API de Gemini y credenciales de WordPress. **Nunca las subas en el cÃ³digo fuente**.

### Paso 7.1 â€” Crear el archivo `.env`

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

Guarda con `Ctrl+O` â†’ `Enter` â†’ `Ctrl+X`.

### Paso 7.2 â€” Proteger el archivo

```bash
chmod 600 ~/proyecto/.env
echo ".env" >> ~/proyecto/.gitignore
```

---

## 8. Instalar y Configurar Nginx

Nginx actÃºa como **reverse proxy**: recibe las peticiones del puerto 80 y las redirige al servicio correcto (WordPress en 8080 o Streamlit en 5000).

### Paso 8.1 â€” Instalar Nginx

```bash
sudo apt install nginx -y
sudo systemctl enable nginx
sudo systemctl start nginx
```

### Paso 8.2 â€” Crear configuraciÃ³n de Nginx

```bash
sudo tee /etc/nginx/sites-available/blog-seo > /dev/null <<'EOF'
# WordPress â€” dominio principal
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

# Streamlit â€” subdominio app
server {
    listen 80;
    server_name app.tudominio.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
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

> ðŸ“ Reemplaza `tudominio.com` y `app.tudominio.com` con tus dominios reales.

### Paso 8.3 â€” Activar la configuraciÃ³n

```bash
sudo ln -s /etc/nginx/sites-available/blog-seo /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t        # Verificar que no hay errores de sintaxis
sudo systemctl reload nginx
```

> ðŸ’¡ **Sin dominio por ahora**: puedes acceder directamente por IP y puerto:
> - WordPress: `http://3.250.45.123:8080`
> - Streamlit: `http://3.250.45.123:5000`

---

## 9. Ejecutar Flask como servicio Permanente

Para que Streamlit siga corriendo aunque cierres la terminal SSH:

### Paso 9.1 â€” Crear el servicio systemd

```bash
sudo tee /etc/systemd/system/flask-app.service > /dev/null <<'EOF'
[Unit]
Description=Streamlit SEO App
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/TuEden-bot
Environment="PATH=/home/ubuntu/venv_proyecto/bin"
EnvironmentFile=/home/ubuntu/TuEden-bot/.env
ExecStart=/home/ubuntu/venv_proyecto/bin/python app.py \
    --server.port 5000 \
    --server.address 127.0.0.1 \
    --server.headless true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

### Paso 9.2 â€” Activar e iniciar

```bash
sudo systemctl daemon-reload
sudo systemctl enable flask-app
sudo systemctl start flask-app
```

### Paso 9.3 â€” Verificar que estÃ¡ corriendo

```bash
sudo systemctl status flask-app
```

DeberÃ­as ver `Active: active (running)` âœ…

### Comandos Ãºtiles

```bash
# Ver logs en tiempo real
sudo journalctl -u flask-app -f

# Reiniciar (tras actualizar cÃ³digo)
sudo systemctl restart flask-app

# Detener
sudo systemctl stop flask-app
```

---

## 10. Abrir Puertos en el Firewall de Lightsail

Hay que permitir el trÃ¡fico web desde el panel de Lightsail:

1. En la consola de Lightsail â†’ tu instancia â†’ pestaÃ±a **"Redes"**
2. En **"Firewall de IPv4"**, aÃ±ade estas reglas haciendo clic en **"Agregar regla"** para cada una:

| Protocolo | Puerto | DescripciÃ³n |
|-----------|--------|-------------|
| TCP | 80 | HTTP (WordPress + Nginx) |
| TCP | 443 | HTTPS (SSL â€” cuando lo configures) |
| TCP | 5000 | Streamlit (acceso directo por IP) |
| TCP | 8080 | Apache/WordPress (acceso directo por IP) |

Para cada regla:
- Tipo: `Personalizado` â†’ Protocolo: `TCP` â†’ Puerto: el nÃºmero
- Origen: `Cualquier lugar (0.0.0.0/0)`
- Clic en **"Guardar"**

---

## 11. Finalizar InstalaciÃ³n de WordPress

### Paso 11.1 â€” Asistente web de WordPress

1. Abre tu navegador y ve a:
   - Con dominio: `http://tudominio.com`
   - Sin dominio: `http://3.250.45.123:8080`

2. Selecciona el **idioma**: EspaÃ±ol

3. Rellena los datos del sitio:
   - **TÃ­tulo del sitio**: el nombre de tu blog
   - **Nombre de usuario**: tu usuario admin (evita usar "admin")
   - **ContraseÃ±a**: una contraseÃ±a fuerte â€” guÃ¡rdala bien
   - **Email**: tu email de contacto

4. Haz clic en **"Instalar WordPress"**

5. Accede al panel en `/wp-admin` con tus credenciales

### Paso 11.2 â€” Crear contraseÃ±a de aplicaciÃ³n para Streamlit

Para que tu app Streamlit pueda publicar posts en WordPress via la API REST:

1. En WordPress â†’ **Usuarios** â†’ tu usuario â†’ desplÃ¡zate hasta **"ContraseÃ±as de aplicaciÃ³n"**
2. Nombre: `flask-app-app`
3. Haz clic en **"Agregar nueva contraseÃ±a de aplicaciÃ³n"**
4. **Copia el password generado** â€” solo se muestra una vez
5. Actualiza el archivo `.env` en el servidor:
   ```bash
   nano ~/proyecto/.env
   ```
   Actualiza estas lÃ­neas:
   ```env
   WP_URL=http://tudominio.com
   WP_USER=tu_usuario_admin
   WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
   ```
6. Reinicia Streamlit:
   ```bash
   sudo systemctl restart flask-app
   ```

---

## 12. Mantenimiento y Actualizaciones

### Actualizar el cÃ³digo de Streamlit (via SCP desde Windows)

Ejecuta esto en tu **PC local** (PowerShell), no en el servidor:

```powershell
scp -i "C:\Users\TuUsuario\.ssh\llave-blog-seo.pem" -r "C:\Users\Luis.RANGEL-GONZALEZ\OneDrive - Akkodis\Desktop\proyecto\." ubuntu@3.250.45.123:/home/ubuntu/proyecto/
```

Luego en el servidor reinicia el servicio:

```bash
sudo systemctl restart flask-app
```

### Actualizar el cÃ³digo de Streamlit (via Git)

```bash
cd ~/proyecto
source ~/venv_proyecto/bin/activate
git pull
pip install -r requirements.txt
sudo systemctl restart flask-app
```

### Actualizar WordPress

Desde el panel de administraciÃ³n `/wp-admin` â†’ **Escritorio** â†’ **Actualizaciones**.
WordPress avisarÃ¡ cuando haya actualizaciones de core, plugins o temas disponibles.

### Crear snapshot (copia de seguridad)

1. En Lightsail â†’ tu instancia â†’ pestaÃ±a **"InstantÃ¡neas"**
2. Haz clic en **"Crear instantÃ¡nea"**
3. Costo: **$0.05 USD/GB/mes** (~$3/mes para 60 GB)

> ðŸ’¡ Crea siempre una snapshot **antes de cualquier actualizaciÃ³n importante** del servidor.

### Ver logs en tiempo real

```bash
# Logs de Streamlit
sudo journalctl -u flask-app -f

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
| Instancia Linux $5/mes (1 GB RAM, IPv4) | **GRATIS** primeros 3 meses â†’ luego **$5/mes** |
| IP pÃºblica (incluida en el plan) | **$0** |
| InstantÃ¡neas (~40 GB) | ~$2/mes |
| CDN (opcional) | **GRATIS** el primer aÃ±o (50 GB) |
| Dominio (opcional, Route 53 o Namecheap) | ~$1/mes (~$12/aÃ±o) |
| **Total mes 1-3** | **~$3/mes** |
| **Total mes 4+** | **~$8/mes** |

> âœ… **Costo estimado el primer aÃ±o**: ~$63 USD total---

## 14. Checklist Final

### Cuenta AWS
- [ ] Crear cuenta desde red mÃ³vil (hotspot) con email personal
- [ ] Verificar email y nÃºmero de telÃ©fono
- [ ] Agregar tarjeta de crÃ©dito personal con pagos internacionales

### Instancia Lightsail
- [ ] Crear instancia Ubuntu 22.04 â€” plan **$5/mes** â€” Europa (Irlanda)
- [ ] Descargar y guardar el archivo `llave-blog-seo.pem` en lugar seguro
- [ ] Anotar la IP pÃºblica de la instancia

### Servidor â€” WordPress
- [ ] Conectar por SSH
- [ ] `sudo apt update && sudo apt upgrade -y`
- [ ] Instalar Apache, PHP 8.2 y MySQL
- [ ] Crear base de datos y usuario MySQL (`wordpress_db` / `wp_user`)
- [ ] Descargar WordPress en `/var/www/html/wordpress`
- [ ] Mover Apache al puerto 8080
- [ ] Crear VirtualHost en Apache
- [ ] Configurar `wp-config.php` con datos de la BD

### Servidor â€” Streamlit
- [ ] Instalar Python 3.11 y venv
- [ ] Subir el proyecto via SCP o Git
- [ ] Crear y activar entorno virtual (`venv_proyecto`)
- [ ] Instalar dependencias Python (`pip install -r requirements.txt`)
- [ ] Crear archivo `.env` con las API keys de Gemini y credenciales WP

### Servidor â€” Nginx
- [ ] Instalar Nginx
- [ ] Crear archivo `/etc/nginx/sites-available/blog-seo` con reverse proxy
- [ ] Activar sitio con `ln -s` y eliminar el default
- [ ] `sudo nginx -t` sin errores
- [ ] `sudo systemctl reload nginx`

### Servidor â€” Servicio Streamlit
- [ ] Crear `/etc/systemd/system/flask-app.service`
- [ ] `sudo systemctl daemon-reload`
- [ ] `sudo systemctl enable flask-app`
- [ ] `sudo systemctl start flask-app`
- [ ] Verificar con `sudo systemctl status flask-app` â†’ `active (running)`

### Firewall Lightsail
- [ ] Abrir puerto **80** (HTTP)
- [ ] Abrir puerto **443** (HTTPS)
- [ ] Abrir puerto **8080** (WordPress/Apache directo)
- [ ] Abrir puerto **5000** (Streamlit directo)

### WordPress â€” ConfiguraciÃ³n final
- [ ] Completar asistente de instalaciÃ³n via `http://IP:8080`
- [ ] Crear contraseÃ±a de aplicaciÃ³n para Streamlit en `/wp-admin`
- [ ] Actualizar `.env` con `WP_APP_PASSWORD`
- [ ] `sudo systemctl restart flask-app`

### VerificaciÃ³n final
- [ ] WordPress responde en `http://IP:8080` o `http://tudominio.com`
- [ ] Streamlit responde en `http://IP:5000` o `http://app.tudominio.com`
- [ ] La funciÃ³n de publicar post desde Streamlit â†’ WordPress funciona
- [ ] Primera snapshot creada en Lightsail

---

*GuÃ­a generada para el proyecto en `C:\Users\Luis.RANGEL-GONZALEZ\OneDrive - Akkodis\Desktop\proyecto`*
*Referencia de precios: [https://aws.amazon.com/es/lightsail/pricing/](https://aws.amazon.com/es/lightsail/pricing/)*

