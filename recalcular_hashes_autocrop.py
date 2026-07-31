"""
Radar DG - recalcular phash/colorhash de todo el catalogo con auto-recorte
==============================================================================
Por que hace falta: se detecto un caso real (SKU D17240011620) donde el
zapato SI estaba en el catalogo, la foto nueva no estaba rotada ni nada raro,
y aun asi no aparecia como coincidencia. La causa: la foto nueva tenia mas
margen de fondo alrededor del zapato que la miniatura del catalogo (el
zapato se veia mas chico dentro del cuadro) -- el hash perceptual compara la
imagen completa, y ese "zoom" distinto alcanzaba para desviar bastante la
comparacion aunque el zapato fuera identico.

Este script recorta cada miniatura del catalogo a su contenido real (sin el
margen de fondo, estimando el color de fondo desde los bordes -- no asume
que sea blanco) antes de recalcular su phash/colorhash, para que quede en
pie de igualdad con como se procesa la foto nueva en la app (ver
auto_crop_contenido() en app.py, que usa EXACTAMENTE la misma logica).

Nota: se probo tambien segmentar con GrabCut (mas sofisticado, no depende de
ningun supuesto sobre el color de fondo) pero al validarlo contra el caso
real de arriba resulto MENOS preciso (el recuadro final queda con
proporciones distintas entre fotos que deberian dar igual), asi que se
descarto. Este metodo (estimar el color de fondo desde los bordes) es el que
esta probado y en uso.

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
