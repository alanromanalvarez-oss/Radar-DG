"""
Radar DG - recalcular phash/colorhash de todo el catalogo con auto-recorte
==============================================================================
Por que hace falta: se detecto un caso real (SKU D17240011620) donde el
zapato SI estaba en el catalogo, la foto nueva no estaba rotada ni nada raro,
y aun asi no aparecia como coincidencia. La causa: la foto nueva tenia mas
margen blanco alrededor del zapato que la miniatura del catalogo (el zapato
se veia mas chico dentro del cuadro) -- el hash perceptual compara la imagen
completa, y ese "zoom" distinto alcanzaba para desviar bastante la
comparacion aunque el zapato fuera identico.

Este script recorta cada miniatura del catalogo a su contenido real (sin el
margen blanco) antes de recalcular su phash/colorhash, para que quede en pie
de igualdad con como se procesa la foto nueva en la app (ver
auto_crop_contenido() en app.py, que se le aplica a la foto del comprador).

Probado en un caso real: la distancia entre la foto nueva y el zapato
correcto bajo de 124 a 40, y el SKU correcto paso del puesto #161 al #1 de
1527 en la busqueda.

No hace falta internet -- usa el thumb_b64 que cada entrada ya tiene
guardado, no vuelve a descargar ni re-leer el PDF/Excel original.

Uso:
    python recalcular_hashes_autocrop.py catalog_index.json
"""
import sys
import json
import base64
import io

import numpy as np
from PIL import Image
import imagehash


def auto_crop_contenido(im: Image.Image, umbral=245, margen_pct=0.04):
    """Identica a la de app.py -- recorta al contenido real, descartando el
    margen blanco/fondo alrededor."""
    arr = np.array(im.convert("RGB"))
    no_fondo = np.any(arr < umbral, axis=2)
    ys, xs = np.where(no_fondo)
    if len(xs) == 0:
        return im
    w, h = im.size
    mx, my = int(w * margen_pct), int(h * margen_pct)
    x0 = max(0, xs.min() - mx)
    y0 = max(0, ys.min() - my)
    x1 = min(w, xs.max() + mx)
    y1 = min(h, ys.max() + my)
    return im.crop((x0, y0, x1, y1))


def main():
    if len(sys.argv) < 2:
        print("Uso: python recalcular_hashes_autocrop.py catalog_index.json")
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        catalogo = json.load(f)

    procesados, sin_imagen, errores = 0, 0, 0
    for r in catalogo:
        thumb = r.get("thumb_b64")
        if not thumb:
            sin_imagen += 1
            continue
        try:
            im = Image.open(io.BytesIO(base64.b64decode(thumb))).convert("RGB")
            im_c = auto_crop_contenido(im)
            r["phash"] = str(imagehash.phash(im_c, hash_size=16))
            r["colorhash"] = str(imagehash.colorhash(im_c, binbits=3))
            procesados += 1
        except Exception as e:
            print(f"  no se pudo reprocesar {r.get('sku')}: {e}")
            errores += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False)

    print(f"Reprocesados con auto-recorte: {procesados}")
    print(f"Sin imagen (sin cambios): {sin_imagen}")
    print(f"Errores: {errores}")
    print(f"Total en {path}: {len(catalogo)}")


if __name__ == "__main__":
    main()
