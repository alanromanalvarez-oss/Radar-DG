"""
Radar DG - agregar compras ya hechas (antes de ir a SAPICA)
=============================================================
Si ya compraste muestras (en otra feria, a un proveedor nacional, etc.) y
queres que el radar las tenga en cuenta desde el primer dia -- para todos
los compradores, no solo en la sesion de uno -- usa este script para
sumarlas a catalog_index.json antes de subir el codigo a GitHub.

Como organizar las fotos:
  compras_previas/
    Sandalia/
      foto1.jpg
      foto2.jpg
    Tenis/
      foto3.jpg
    ...
  (una carpeta por categoria, el nombre de la carpeta = la categoria)

Uso:
    python agregar_compras_previas.py compras_previas/ catalog_index.json

Esto actualiza catalog_index.json sumando estas fotos con SKU
"YA-COMPRADO-<nombre de archivo>". Volve a subir el catalog_index.json
actualizado a GitHub (commit + push) para que quede disponible para todos.
"""
import sys
import json
import base64
import io
from pathlib import Path
from PIL import Image
import imagehash

THUMB_SIZE = (220, 220)
JPEG_QUALITY = 70


def procesar_carpeta(carpeta: Path):
    nuevos = []
    for cat_dir in sorted(p for p in carpeta.iterdir() if p.is_dir()):
        categoria = cat_dir.name
        for foto_path in sorted(cat_dir.glob("*")):
            if foto_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            try:
                im = Image.open(foto_path).convert("RGB")
            except Exception as e:
                print(f"  no pude abrir {foto_path}: {e}")
                continue

            phash = str(imagehash.phash(im, hash_size=16))
            colorhash = str(imagehash.colorhash(im, binbits=3))

            thumb = im.copy()
            thumb.thumbnail(THUMB_SIZE)
            buf = io.BytesIO()
            thumb.save(buf, format="JPEG", quality=JPEG_QUALITY)
            thumb_b64 = base64.b64encode(buf.getvalue()).decode()

            nuevos.append({
                "sku": f"YA-COMPRADO-{foto_path.stem}",
                "categoria": categoria,
                "inventario": 0,
                "ventas": 0,
                "sell_through_pct": 0,
                "phash": phash,
                "colorhash": colorhash,
                "thumb_b64": thumb_b64,
            })
            print(f"  + {categoria} / {foto_path.name}")
    return nuevos


def main():
    if len(sys.argv) < 3:
        print("Uso: python agregar_compras_previas.py <carpeta_con_fotos_por_categoria> <catalog_index.json>")
        sys.exit(1)

    carpeta = Path(sys.argv[1])
    index_path = Path(sys.argv[2])

    with open(index_path, encoding="utf-8") as f:
        catalogo = json.load(f)

    existentes = {r["sku"] for r in catalogo}
    nuevos = [r for r in procesar_carpeta(carpeta) if r["sku"] not in existentes]

    catalogo.extend(nuevos)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False)

    print(f"\nListo. Se agregaron {len(nuevos)} fotos de compras previas.")
    print(f"Total en catalog_index.json ahora: {len(catalogo)}")
    print("Recorda subir este archivo actualizado a GitHub (commit + push).")


if __name__ == "__main__":
    main()
