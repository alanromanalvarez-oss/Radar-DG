"""
Radar DG - reemplazar_fotos_alta_res.py
==============================================================================
Reemplaza el thumb_b64 (y recalcula phash/colorhash) de las entradas del
catalogo cuyo SKU coincide EXACTAMENTE con el nombre de archivo (sin
extension) de una carpeta de fotos en mejor resolucion.

Por que hace falta: las miniaturas que ya tiene el catalogo para los items
de "Base Presapica.xlsx" (China SS27 / Presapica) se extrajeron incrustadas
del Excel, y quedaron chicas (~220x140px, bastante comprimidas). Alan
comparti una carpeta ("Imagenes Base") con las mismas fotos pero en mucha
mejor resolucion (ej. 1280x960) -- una fuente mas limpia deberia ayudar a
que el hash perceptual (phash/colorhash) sea mas preciso para esos items.

No agrega SKUs nuevos y no toca nada mas del catalogo -- si el nombre de
archivo no coincide con ningun SKU ya existente, se informa y se saltea (no
inventa una entrada nueva). El thumb final se vuelve a comprimir al mismo
ancho (~220px) que usa el resto del catalogo, para no disparar el tamaño de
catalog_index.json ni el costo de mandar estas imagenes a Claude en cada
analisis -- lo que gana en calidad es partir de una fuente mas nitida, no
guardar una imagen mas pesada.

Uso:
    python reemplazar_fotos_alta_res.py "Imagenes Base" catalog_index.json
"""
import sys
import os
import json
import base64
import io

import numpy as np
from PIL import Image
import imagehash

ANCHO_THUMB = 220  # mismo ancho que usan las miniaturas del resto del catalogo


def auto_crop_contenido(im: Image.Image, margen_pct=0.04, umbral_dist=28, franja_borde_pct=0.03):
    """Identica a la de app.py -- recorta al contenido real, estimando el
    color de fondo desde los bordes (no asume que sea blanco)."""
    arr = np.array(im.convert("RGB")).astype(np.int16)
    h, w = arr.shape[:2]
    fb = max(2, int(min(h, w) * franja_borde_pct))
    borde = np.concatenate([
        arr[:fb, :, :].reshape(-1, 3), arr[-fb:, :, :].reshape(-1, 3),
        arr[:, :fb, :].reshape(-1, 3), arr[:, -fb:, :].reshape(-1, 3),
    ])
    color_fondo = np.median(borde, axis=0)
    dist_al_fondo = np.sqrt(((arr - color_fondo) ** 2).sum(axis=2))
    contenido = dist_al_fondo > umbral_dist
    ys, xs = np.where(contenido)
    if len(xs) == 0:
        return im.convert("RGB")
    w2, h2 = im.size
    mx, my = int(w2 * margen_pct), int(h2 * margen_pct)
    x0 = max(0, xs.min() - mx)
    y0 = max(0, ys.min() - my)
    x1 = min(w2, xs.max() + mx)
    y1 = min(h2, ys.max() + my)
    return im.convert("RGB").crop((x0, y0, x1, y1))


def main():
    if len(sys.argv) < 3:
        print('Uso: python reemplazar_fotos_alta_res.py "carpeta_de_fotos" catalog_index.json')
        sys.exit(1)
    carpeta, path_catalogo = sys.argv[1], sys.argv[2]

    with open(path_catalogo, encoding="utf-8") as f:
        catalogo = json.load(f)
    por_sku = {r["sku"]: r for r in catalogo}

    archivos = [f for f in os.listdir(carpeta) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    reemplazados, no_encontrados, errores = 0, [], 0

    for nombre in sorted(archivos):
        sku = os.path.splitext(nombre)[0]
        r = por_sku.get(sku)
        if not r:
            no_encontrados.append(nombre)
            continue
        try:
            im = Image.open(os.path.join(carpeta, nombre)).convert("RGB")
            im.thumbnail((ANCHO_THUMB, ANCHO_THUMB * 3))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            r["thumb_b64"] = base64.b64encode(buf.getvalue()).decode()

            im_c = auto_crop_contenido(im)
            r["phash"] = str(imagehash.phash(im_c, hash_size=16))
            r["colorhash"] = str(imagehash.colorhash(im_c, binbits=3))
            reemplazados += 1
        except Exception as e:
            print(f"  error en {sku}: {e}")
            errores += 1

    with open(path_catalogo, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False)

    print(f"Reemplazados con foto de mejor resolucion: {reemplazados}")
    print(f"Archivos de la carpeta sin SKU coincidente en el catalogo (no se tocaron): {len(no_encontrados)}")
    if no_encontrados:
        for n in no_encontrados[:30]:
            print("  ", n)
    print(f"Errores: {errores}")
    print(f"Total de SKUs en el catalogo: {len(catalogo)}")


if __name__ == "__main__":
    main()
