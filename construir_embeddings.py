"""
Radar DG - construir_embeddings.py  (paso 2 de 3 del buscador por embeddings)
==============================================================================
Convierte cada foto del catalogo en un "embedding": una lista de numeros que
representa COMO SE VE el zapato. Dos zapatos de silueta parecida quedan cerca
en ese espacio numerico aunque cambien el fondo, la luz y el angulo -- que es
exactamente donde falla el metodo anterior (phash), que compara patrones de
pixeles y por eso trata una foto real de feria como si fuera otra imagen
distinta a la del catalogo.

Casos reales que motivaron esto (medidos, no supuestos):
  - Subiendo la MISMA foto del catalogo, el SKU correcto salia 1o de 1527.
  - Con una foto real del mismo zapato (mesa, luz calida, 7 grados), caia al
    puesto 374 -- y a la IA solo le llegan los primeros ~12-30, asi que nunca
    lo veia.

Que indexa:
  1. Las 4 vistas por SKU que bajo descargar_vistas.py (carpeta "vistas").
  2. Ademas, la miniatura del catalogo (thumb_b64) de los SKUs que NO estan en
     ese Excel -- por ejemplo los de Base Presapica (BAIZHEN, LADY PAU, etc.).
     Asi el indice cubre TODO el catalogo, no solo los 1,324 del Excel.

Costo: voyage-multimodal-3.5 regala 150 mil millones de pixeles por cuenta.
Las ~5,300 fotos a 512 px suman ~1,000 millones, o sea que esto entra holgado
en el tramo gratuito.

Es reanudable: guarda cada tanto y al volver a correrlo saltea lo ya hecho.

Antes de correrlo:
    pip install voyageai
    $env:VOYAGE_API_KEY = "tu-clave-de-voyage"

Uso:
    python construir_embeddings.py vistas catalog_index.json embeddings_index.npz
    (opcional) --sample 40   -> procesa solo 40 fotos, para probar
"""
import os
import sys
import json
import base64
import io
import time
import argparse

import numpy as np
from PIL import Image

MODELO = "voyage-multimodal-3.5"
LOTE = 32          # imagenes por llamada (el limite duro es 1000 inputs /
                   # 320.000 tokens por request; 32 deja margen de sobra y
                   # hace que un fallo de red cueste poco)
LADO_MAX = 512

# Si la cuenta de Voyage NO tiene metodo de pago cargado, los limites son
# 3 requests/minuto y 10.000 tokens/minuto. Cada foto a 512 px son ~350
# tokens, asi que un lote de 32 (~11.200 tokens) ya se pasa y falla todo.
# Con --gratis el script se acomoda a esos limites: lotes chicos y una pausa
# entre llamadas. Tarda mucho mas, pero funciona sin cargar tarjeta.
LOTE_GRATIS = 20       # ~7.000 tokens, entra en los 10.000 TPM
PAUSA_GRATIS = 21.0    # segundos entre llamadas (3 por minuto)


def cargar_parcial(path):
    """Devuelve (claves_ya_hechas, lista_claves, lista_vectores)."""
    if not os.path.exists(path):
        return set(), [], []
    d = np.load(path, allow_pickle=True)
    claves = list(d["claves"])
    vecs = list(d["vectores"])
    return set(claves), claves, vecs


def guardar(path, claves, vecs):
    np.savez_compressed(path,
                        claves=np.array(claves, dtype=object),
                        vectores=np.array(vecs, dtype=np.float32))


def reunir_imagenes(carpeta_vistas, path_catalogo):
    """Devuelve lista de (clave, PIL.Image). clave = 'SKU__vista' o 'SKU__cat'."""
    items = []
    skus_con_vista = set()

    if os.path.isdir(carpeta_vistas):
        for nombre in sorted(os.listdir(carpeta_vistas)):
            if not nombre.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            clave = os.path.splitext(nombre)[0]
            sku = clave.split("__")[0]
            skus_con_vista.add(sku)
            items.append((clave, os.path.join(carpeta_vistas, nombre)))

    # Completar con la miniatura del catalogo para los SKUs sin vistas bajadas
    # (los de Base Presapica y cualquiera que no este en el Excel de vistas).
    with open(path_catalogo, encoding="utf-8") as f:
        catalogo = json.load(f)
    for r in catalogo:
        sku = r.get("sku")
        if not sku or sku in skus_con_vista or not r.get("thumb_b64"):
            continue
        items.append((f"{sku}__cat", ("b64", r["thumb_b64"])))

    return items


def abrir(origen):
    if isinstance(origen, tuple) and origen[0] == "b64":
        im = Image.open(io.BytesIO(base64.b64decode(origen[1])))
    else:
        im = Image.open(origen)
    im = im.convert("RGB")
    im.thumbnail((LADO_MAX, LADO_MAX))
    return im


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("carpeta_vistas", help="carpeta con las fotos que bajo descargar_vistas.py")
    ap.add_argument("catalogo", help="catalog_index.json (para completar los SKUs sin vistas)")
    ap.add_argument("salida", help="archivo de indice a generar, ej. embeddings_index.npz")
    ap.add_argument("--sample", type=int, default=None, help="procesar solo N fotos (para probar)")
    ap.add_argument("--gratis", action="store_true",
                     help="modo cuenta SIN metodo de pago: lotes chicos y pausas para "
                          "respetar 3 requests/min y 10.000 tokens/min. Mucho mas lento.")
    ap.add_argument("--lote", type=int, default=None, help="imagenes por llamada (avanzado)")
    ap.add_argument("--pausa", type=float, default=None, help="segundos entre llamadas (avanzado)")
    args = ap.parse_args()

    lote_n = args.lote or (LOTE_GRATIS if args.gratis else LOTE)
    pausa = args.pausa if args.pausa is not None else (PAUSA_GRATIS if args.gratis else 0.0)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import voyageai

    items = reunir_imagenes(args.carpeta_vistas, args.catalogo)
    hechas, claves, vecs = cargar_parcial(args.salida)
    pendientes = [it for it in items if it[0] not in hechas]

    print(f"Fotos totales a indexar: {len(items)}")
    print(f"Ya indexadas: {len(hechas)} | pendientes: {len(pendientes)}")

    if args.sample:
        pendientes = pendientes[:args.sample]
    if not pendientes:
        print("No hay nada pendiente. El indice ya esta completo.")
        return

    n_lotes = (len(pendientes) + lote_n - 1) // lote_n
    print(f"Procesando {len(pendientes)} en esta corrida "
          f"({n_lotes} lotes de {lote_n}, pausa {pausa}s)...")
    if args.gratis:
        mins = int(n_lotes * pausa / 60) + 1
        print(f"Modo --gratis: va a tardar aproximadamente {mins} minutos. "
              f"Es reanudable, podes cortarlo y seguir despues.")
    print()
    vo = voyageai.Client()  # toma VOYAGE_API_KEY del entorno

    procesadas, errores = 0, 0
    for i_lote, inicio in enumerate(range(0, len(pendientes), lote_n)):
        lote = pendientes[inicio:inicio + lote_n]
        imgs, ks = [], []
        for clave, origen in lote:
            try:
                imgs.append([abrir(origen)])
                ks.append(clave)
            except Exception as e:
                errores += 1
                print(f"  no pude abrir {clave}: {e}")
        if not imgs:
            continue

        # Reintento con espera creciente: el limite de velocidad de Voyage se
        # mide por minuto, asi que esperar y volver a intentar casi siempre
        # resuelve, en vez de dar el lote por perdido.
        ok = False
        for intento in range(4):
            try:
                res = vo.multimodal_embed(imgs, model=MODELO, input_type="document")
                for k, v in zip(ks, res.embeddings):
                    claves.append(k)
                    vecs.append(np.asarray(v, dtype=np.float32))
                procesadas += len(ks)
                ok = True
                break
            except Exception as e:
                es_limite = "rate" in type(e).__name__.lower() or "rate limit" in str(e).lower()
                if intento < 3 and es_limite:
                    espera = 25 * (intento + 1)
                    print(f"  limite de velocidad alcanzado, esperando {espera}s "
                          f"(intento {intento+2}/4)...")
                    time.sleep(espera)
                    continue
                errores += len(ks)
                print(f"  ERROR en el lote {inicio}-{inicio+len(lote)}: {e}")
                break

        if ok and pausa:
            time.sleep(pausa)

        if i_lote % 10 == 0 and procesadas:
            guardar(args.salida, claves, vecs)
            print(f"  [{procesadas}/{len(pendientes)}] guardado parcial en {args.salida}")

    guardar(args.salida, claves, vecs)
    print(f"\nListo. Indexadas en esta corrida: {procesadas}. Errores: {errores}.")
    print(f"Indice: {args.salida} ({len(claves)} vectores de {len(vecs[0]) if vecs else 0} dimensiones)")
    if errores:
        print("Volve a correr el mismo comando para reintentar las que fallaron.")


if __name__ == "__main__":
    main()
