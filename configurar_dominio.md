# ðŸŒ GuÃ­a: Migrar de IP a Dominio Propio

> Sigue esta guÃ­a cuando hayas comprado tu dominio. El servidor ya estÃ¡ funcionando con IP, solo hay que apuntar el dominio y actualizar la configuraciÃ³n.

---

## Resumen de cambios necesarios

| QuÃ© cambiar | DÃ³nde |
|-------------|-------|
| Apuntar el dominio al servidor | Panel del registrador de dominio |
| Nginx: `server_name` | Servidor SSH |
| WordPress: URL del sitio | Base de datos o wp-admin |
| Archivo `.env` del proyecto | Servidor SSH |
| SSL (HTTPS) con Certbot | Servidor SSH |

---

## Paso 1 â€” Apuntar el dominio al servidor

En el panel de tu registrador de dominio (Namecheap, GoDaddy, etc.), crea estos registros DNS:

| Tipo | Nombre | Valor |
|------|--------|-------|
| A | `@` | `13.38.47.68` |
| A | `www` | `13.38.47.68` |
| A | `app` | `13.38.47.68` â† para Streamlit |

> â³ Los cambios DNS pueden tardar entre 5 minutos y 48 horas en propagarse.
> Puedes verificar en: https://dnschecker.org

---

## Paso 2 â€” Actualizar Nginx con el dominio

Reemplaza la configuraciÃ³n actual (que usa `server_name _`) por una con tu dominio real. Sustituye `tudominio.com` por tu dominio:

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

Verifica y recarga Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## Paso 3 â€” Actualizar la URL de WordPress

WordPress guarda su propia URL en la base de datos. ActualÃ­zala:

```bash
sudo mysql
```

Dentro de MySQL:

```sql
UPDATE wordpress_db.wp_options SET option_value = 'http://tudominio.com' WHERE option_name = 'siteurl';
UPDATE wordpress_db.wp_options SET option_value = 'http://tudominio.com' WHERE option_name = 'home';
EXIT;
```

---

## Paso 4 â€” Actualizar el archivo `.env` del proyecto

```bash
nano /home/ubuntu/TuEden-bot/.env
```

Cambia la lÃ­nea:
```
WP_BASE_URL=https://tu-blog.com
```
por:
```
WP_BASE_URL=http://tudominio.com
```

Reinicia Streamlit:
```bash
sudo systemctl restart flask-app
```

---

## Paso 5 â€” Instalar SSL (HTTPS) con Certbot

Una vez que el DNS estÃ© propagado y puedas acceder por `http://tudominio.com`, instala el certificado SSL gratuito:

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d tudominio.com -d www.tudominio.com -d app.tudominio.com
```

Certbot actualiza Nginx automÃ¡ticamente y aÃ±ade la renovaciÃ³n automÃ¡tica.

Verifica que la renovaciÃ³n automÃ¡tica funciona:
```bash
sudo certbot renew --dry-run
```

---

## Paso 6 â€” Actualizar WordPress a HTTPS

DespuÃ©s de instalar SSL, actualiza la URL en la base de datos de `http` a `https`:

```bash
sudo mysql
```

```sql
UPDATE wordpress_db.wp_options SET option_value = 'https://tudominio.com' WHERE option_name = 'siteurl';
UPDATE wordpress_db.wp_options SET option_value = 'https://tudominio.com' WHERE option_name = 'home';
EXIT;
```

Y actualiza tambiÃ©n el `.env`:
```bash
nano /home/ubuntu/TuEden-bot/.env
```
```
WP_BASE_URL=https://tudominio.com
```

Reinicia Streamlit:
```bash
sudo systemctl restart flask-app
```

---

## Paso 7 â€” VerificaciÃ³n final

| URL | Debe mostrar |
|-----|-------------|
| `https://tudominio.com` | Blog WordPress âœ… |
| `https://www.tudominio.com` | Blog WordPress âœ… |
| `https://app.tudominio.com` | App Streamlit âœ… |
| `http://tudominio.com` | Redirige a HTTPS âœ… |

---

## Notas adicionales

- El acceso directo por IP (`http://13.38.47.68:8080`) dejarÃ¡ de funcionar una vez que Nginx tenga `server_name` con el dominio. Si quieres mantenerlo temporalmente, aÃ±ade un bloque `server` adicional con `listen 8080; server_name _;`.
- Guarda siempre una snapshot de Lightsail antes de hacer cambios importantes.

