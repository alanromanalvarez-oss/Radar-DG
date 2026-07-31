"""
Radar DG - clasificar_catalogo_con_ddg.py  (v13, Etapa A "a la Toño")
==============================================================================
Por que hace falta: la preseleccion de candidatos de la app compara fotos
usando hash perceptual (phash/colorhash) -- una comparacion de pixeles, no de
significado. Se verifico con un caso real (un taco negro cerrado, punta
puntiaguda) que NINGUNO de los candidatos realmente relevantes del catalogo
quedaba entre los primeros 15 por hash -- el top 15 lo llenaban sandalias,
tenis y flats, categorias que no compiten para nada con un taco cerrado. El
hash de imagen puede confundir siluetas distintas si comparten fondo/luz/
angulo parecidos.

Este script corre Claude UNA SOLA VEZ por cada SKU del catalogo (con foto) y
le pide que lo clasifique con el mismo criterio y el mismo DDG que se usa
para la foto nueva del comprador -- categoria_identificada + vector_dg (8
dimensiones: ocasion_de_uso, altura, silueta, punta, estilo_visual, color,
tipo_de_outfit, precio_percibido). El resultado queda guardado en
catalog_index.json como "categoria_ia" y "vector_dg_ia" por SKU.

Una vez que el catalogo tiene esto, la app (ver candidatos_por_vector_ddg en
app.py) puede preseleccionar candidatos filtrando por estos campos
estructurados (categoria + silueta + altura), que es mucho mas confiable que
el hash de pixeles -- y lo suma (no reemplaza) a la preseleccion por hash que
ya existia, asi que no hay riesgo de perder nada que ya funcionaba.

Es un trabajo de UNA SOLA VEZ (no se repite en cada analisis de un
comprador en la feria) -- corre en tu computadora o donde tengas la
ANTHROPIC_API_KEY, no en la app en vivo.

PROBAR ANTES DE GASTAR EN EL CATALOGO COMPLETO (~1527 fotos con imagen):
    export ANTHROPIC_API_KEY="tu-clave"
    python clasificar_catalogo_con_ddg.py catalog_index.json ddg.json --sample 20

Mira el resultado (te va a imprimir cada SKU con su categoria_ia y vector_dg_ia
a medida que corre) y confirma que tiene sentido antes de correr el resto.

Correr sobre TODO el catalogo (es reanudable -- si lo cortas con Ctrl+C o se
corta la conexion, la proxima corrida salta los SKUs que ya tengan
vector_dg_ia guardado, asi que no se vuelve a pagar por lo ya hecho):
    python clasificar_catalogo_con_ddg.py catalog_index.json ddg.json

Tambien se puede cortar en tandas explicitas (por si el entorno donde corres
esto tiene un limite de tiempo por comando):
    python clasificar_catalogo_con_ddg.py catalog_index.json ddg.json --start 0 --end 200
    python clasificar_catalogo_con_ddg.py catalog_index.json ddg.json --start 200 --end 400
    ... etc (se puede repetir el mismo rango sin costo extra: si un SKU ya
    quedo clasificado, se saltea).
"""
import sys
import json
import argparse

import anthropic

MODEL = "claude-sonnet-5"


def construir_tool(categorias_validas, dimensiones):
    props = {d: {"type": "string"} for d in dimensiones}
    return {
        "name": "clasificar_muestra",
        "description": "Clasifica una foto de calzado del catalogo segun el DDG.",
        "input_schema": {
            "type": "object",
            "properties": {
                "categoria_identificada": {"type": "string", "enum": categorias_validas},
                "vector_dg": {
                    "type": "object",
                    "properties": props,
                    "required": dimensiones,
                },
            },
            "required": ["categoria_identificada", "vector_dg"],
        },
    }


def construir_system_prompt(ddg):
    return f"""Sos el mismo sistema de Radar DG que clasifica fotos nuevas en la
feria (SAPICA) para Dorothy Gaynor. Ahora estas clasificando UNA foto que ya
esta en el catalogo -- usa EXACTAMENTE el mismo criterio que usarias con una
foto nueva de un comprador, mirando la imagen con atencion.

Trabaja SIEMPRE con el Diccionario de Decisiones Dorothy Gaynor (DDG) de abajo.
No inventes valores fuera de las listas del DDG.

DDG:
{json.dumps(ddg, ensure_ascii=False, indent=2)}

Identifica categoria_identificada por la silueta real que ves en la foto --
NO te fies del nombre de categoria que ya tenga cargado el catalogo, puede
estar mal cargado o no reflejar lo que se ve (ej. un producto cargado como
"Confort" que en realidad es una "Zapatilla" de taco medio-alto). Elegi
siempre una de la lista cerrada de categorias del DDG.

Devolve vector_dg con las 8 dimensiones del DDG, ninguna otra."""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("catalogo", help="ruta a catalog_index.json")
    ap.add_argument("ddg", help="ruta a ddg.json")
    ap.add_argument("--sample", type=int, default=None,
                     help="clasificar solo los primeros N SKUs sin clasificar (para probar)")
    ap.add_argument("--start", type=int, default=0, help="indice de inicio (dentro de los SKUs sin clasificar)")
    ap.add_argument("--end", type=int, default=None, help="indice de fin (exclusivo)")
    args = ap.parse_args()

    with open(args.catalogo, encoding="utf-8") as f:
        catalogo = json.load(f)
    with open(args.ddg, encoding="utf-8") as f:
        ddg = json.load(f)

    categorias_validas = sorted(set(
        ddg.get("categorias_cubiertas", []) + ddg.get("categorias_sin_definir_en_ddg", [])
    ))
    dimensiones = list(ddg["dimensiones"].keys())
    tool = construir_tool(categorias_validas, dimensiones)
    system_prompt = construir_system_prompt(ddg)

    client = anthropic.Anthropic()  # toma ANTHROPIC_API_KEY del entorno

    pendientes = [r for r in catalogo if r.get("thumb_b64") and not r.get("vector_dg_ia")]
    ya_clasificados = len(catalogo) - len(pendientes) - sum(1 for r in catalogo if not r.get("thumb_b64"))
    print(f"Total en catalogo: {len(catalogo)} | ya clasificados: {ya_clasificados} | "
          f"sin foto (no se pueden clasificar): {sum(1 for r in catalogo if not r.get('thumb_b64'))} | "
          f"pendientes: {len(pendientes)}")

    if args.sample:
        tanda = pendientes[:args.sample]
    else:
        fin = args.end if args.end is not None else len(pendientes)
        tanda = pendientes[args.start:fin]

    print(f"Clasificando {len(tanda)} SKUs en esta corrida...\n")

    ok, errores = 0, 0
    for i, r in enumerate(tanda):
        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=400,
                temperature=0,
                system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                tools=[tool],
                tool_choice={"type": "tool", "name": "clasificar_muestra"},
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"SKU {r['sku']} (categoria cargada en el catalogo: "
                                                  f"{r.get('categoria')}):"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                      "data": r["thumb_b64"]}},
                    ],
                }],
            )
            encontrado = False
            for block in msg.content:
                if block.type == "tool_use":
                    r["categoria_ia"] = block.input["categoria_identificada"]
                    r["vector_dg_ia"] = block.input["vector_dg"]
                    ok += 1
                    encontrado = True
                    print(f"  [{i+1}/{len(tanda)}] {r['sku']}: categoria_ia={r['categoria_ia']!r} "
                          f"(catalogo decia {r.get('categoria')!r}) | vector_dg_ia={r['vector_dg_ia']}")
                    break
            if not encontrado:
                print(f"  [{i+1}/{len(tanda)}] {r['sku']}: el modelo no devolvio la herramienta esperada")
                errores += 1
        except Exception as e:
            print(f"  [{i+1}/{len(tanda)}] {r['sku']}: ERROR -- {e}")
            errores += 1

        if (i + 1) % 25 == 0:
            with open(args.catalogo, "w", encoding="utf-8") as f:
                json.dump(catalogo, f, ensure_ascii=False)
            print(f"  ... guardado parcial en {args.catalogo}")

    with open(args.catalogo, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False)

    print(f"\nListo. Clasificados en esta corrida: {ok}. Errores: {errores}.")
    print(f"Guardado en {args.catalogo}. Volve a correr el script (sin --sample/--start/--end, "
          f"o con el rango que falte) para seguir con los que quedaron pendientes.")


if __name__ == "__main__":
    main()
