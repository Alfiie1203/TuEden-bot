"""Diagnóstico: imágenes y taxonomy en drafts."""
import json, glob, re, os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# ── 1. Inspeccionar draft más reciente con imágenes ─────────────────────
drafts = sorted(glob.glob("drafts_output/draft_*.json"))
target = None
for f in reversed(drafts):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if d.get("images"):
        target = (f, d)
        break

if not target:
    print("No hay drafts con imágenes")
    sys.exit(0)

fname, data = target
print("=== DRAFT:", fname)
print("categories :", data.get("categories"))
print("tags       :", data.get("tags"))
imgs = data.get("images", [])
print("images count:", len(imgs))

for i, img in enumerate(imgs):
    src = img.get("src", "")
    local_prefix = "[LOCAL] "
    actual = src[len(local_prefix):] if src.startswith(local_prefix) else src
    exists = Path(actual).exists()
    print(f"\n  img[{i}]  src      = {src!r}")
    print(f"           actual   = {actual!r}")
    print(f"           existe   = {exists}")

content = data.get("content", "")
srcs_html = re.findall(r'src="([^"]+)"', content)
print("\nsrcs en HTML del content:", srcs_html[:6])

# ── 2. Test taxonomy (necesita WP en live) ────────────────────────────────
print("\n=== TEST TAXONOMY (assign_taxonomy) ===")
try:
    import truststore; truststore.inject_into_ssl()
except ImportError:
    pass

base_url = os.getenv("WP_BASE_URL", "").rstrip("/")
username = os.getenv("WP_USERNAME", "")
password = os.getenv("WP_APP_PASSWORD", "")
print(f"WP_BASE_URL : {base_url}")
print(f"WP_USERNAME : {username}")
print(f"WP_APP_PASSWORD set: {bool(password)}")

try:
    import requests
    from requests.auth import HTTPBasicAuth
    auth = HTTPBasicAuth(username, password)
    r = requests.get(f"{base_url}/wp-json/wp/v2/categories?per_page=10", auth=auth, timeout=10)
    print(f"categories endpoint HTTP {r.status_code}")
    if r.ok:
        cats = r.json()
        print("  Categorías WP:", [(c["id"], c["name"]) for c in cats])
    else:
        print("  Error:", r.text[:200])
except Exception as e:
    print("  FALLO:", e)

# ── 3. Test subida de imagen ──────────────────────────────────────────────
print("\n=== TEST UPLOAD IMAGEN ===")
if imgs:
    src = imgs[0].get("src", "")
    actual = src[len("[LOCAL] "):] if src.startswith("[LOCAL] ") else src
    if Path(actual).exists():
        print(f"Subiendo {actual} …")
        try:
            import mimetypes
            path = Path(actual)
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            with open(path, "rb") as f:
                resp = requests.post(
                    f"{base_url}/wp-json/wp/v2/media",
                    auth=auth,
                    headers={"Content-Disposition": f'attachment; filename="{path.name}"',
                             "Content-Type": mime},
                    data=f, timeout=60
                )
            print(f"  HTTP {resp.status_code}")
            if resp.ok:
                print("  URL:", resp.json().get("source_url"))
            else:
                print("  Error:", resp.text[:300])
        except Exception as e:
            print("  FALLO:", e)
    else:
        print(f"  Archivo no existe: {actual!r}")
        print("  Archivos en images/:", list(Path("drafts_output/images").glob("*.png"))[:5])
