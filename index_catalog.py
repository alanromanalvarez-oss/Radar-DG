"""
Radar DG - indexado del catalogo
=================================
Corre esto UNA sola vez (y de nuevo cada vez que cambie el catalogo) para
convertir el PDF gigante en un archivo chico (catalog_index.json) que la
app usa para comparar contra las fotos nuevas.

No necesita clave de API: solo lee el PDF, guarda miniaturas y un "hash"
de forma/color de cada foto (para el preseleccionado rapido, antes de
mandarle candidatos a la IA).

Uso normal (todo el catalogo de una vez):
    python index_catalog.py "Modelos con inventario mayor a 100_URL_VISTAS_2 (AR).pdf"

Si tu computadora es lenta o el PDF es muy grande y se corta a la mitad,
podes correrlo por tramos (se van guardando en un archivo .jsonl que se
puede retomar donde quedo):
    python index_catalog.py catalogo.pdf --start 0 --end 300
    python index_catalog.py catalogo.pdf --start 300 --end 600
    ...
    python index_catalog.py catalogo.pdf --consolidar

Genera:
    catalog_index.json   (subir esto al repo de GitHub junto con el resto del codigo)
"""
import sys
import re
import json
import base64
import io
import argparse
import os
from pypdf import PdfReader
from PIL import Image
import imagehash

THUMB_SIZE = (220, 220)   # tamano de miniatura guardada (chico para que el json no pese demasiado)
JPEG_QUALITY = 70
JSONL_TMP = "catalog_index.partial.jsonl"

ROW_RE = re.compile(r"(\S+)\s+(\d+)\s+(\d+)\s+(\d+)%\s+(\S+)")


def normaliza_categoria(cat: str) -> str:
    """Arregla problemas de acentos que a veces quedan mal al extraer texto del PDF."""
    fix = {
        "BOTÃN": "BOTÍN", "BOTÃ\x8dN": "BOTÍN",
        "MOCASÃN": "MOCASÍN", "MOCASÃ\x8dN": "MOCASÍN",
    }
    return fix.get(cat, cat).title()


def record_from_page(page):
    """Devuelve un dict con los datos de una pagina, o None si la pagina no tiene
    el formato esperado (fila de producto + foto)."""
    text = page.extract_text() or ""
    flat = text.replace("\n", " ")
    m = ROW_RE.search(flat)
    if not m:
        return None
    sku, inv, vta, st, cat = m.groups()

    imgs = list(page.images)
    if not imgs:
        return None

    im = imgs[0].image.convert("RGB")

    phash = str(imagehash.phash(im, hash_size=16))       # forma / contorno
    colorhash = str(imagehash.colorhash(im, binbits=3))  # paleta de color

    thumb = im.copy()
    thumb.thumbnail(THUMB_SIZE)
    buf = io.BytesIO()
    thumb.save(buf, format="JPEG", quality=JPEG_QUALITY)
    thumb_b64 = base64.b64encode(buf.getvalue()).decode()

    return {
        "sku": sku,
        "categoria": normaliza_categoria(cat),
        "inventario": int(inv),
        "ventas": int(vta),
        "sell_through_pct": int(st),
        "phash": phash,
        "colorhash": colorhash,
        "thumb_b64": thumb_b64,
    }


def run_range(pdf_path: str, start: int, end: int):
    reader = PdfReader(pdf_path)
    n = len(reader.pages)
    end = min(end, n)
    print(f"Procesando paginas {start} a {end} (de {n} totales)")

    done_skus = set()
    if os.path.exists(JSONL_TMP):
        with open(JSONL_TMP, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    done_skus.add(json.loads(line)["sku"])

    written = 0
    with open(JSONL_TMP, "a", encoding="utf-8") as out:
        for i in range(start, end):
            rec = record_from_page(reader.pages[i])
            if rec is None:
                continue
            if rec["sku"] in done_skus:
                continue
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
            if written % 100 == 0:
                print(f"  ... {written} nuevos en este tramo")

    print(f"Tramo terminado. {written} SKUs nuevos agregados a {JSONL_TMP}")


def consolidar(out_path: str = "catalog_index.json"):
    records = {}
    with open(JSONL_TMP, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                records[rec["sku"]] = rec  # de-duplica por SKU

    records = list(records.values())
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)

    print(f"Listo. {len(records)} SKUs indexados -> {out_path}")
    cats = {}
    for r in records:
        cats[r["categoria"]] = cats.get(r["categoria"], 0) + 1
    for c, cnt in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"   {cnt:4d}  {c}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("pdf_path", nargs="?")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--end", type=int, default=None)
    p.add_argument("--consolidar", action="store_true")
    p.add_argument("--out", default="catalog_index.json")
    args = p.parse_args()

    if args.consolidar:
        consolidar(args.out)
        return

    if not args.pdf_path:
        p.error("falta la ruta al PDF del catalogo")

    reader = PdfReader(args.pdf_path)
    end = args.end if args.end is not None else len(reader.pages)
    run_range(args.pdf_path, args.start, end)

    if args.end is None:
        consolidar(args.out)


if __name__ == "__main__":
    main()
