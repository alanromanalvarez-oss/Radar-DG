"""
Radar DG - app para compradores en feria (ej. SAPICA)
=======================================================
El comprador abre este link en el celular, saca (o sube) una foto de la
muestra, y la app le devuelve el reporte Radar DG completo: vector de
decision, redundancias con fotos reales, huecos, indices y recomendacion.

Requiere:
  - catalog_index.json  (generado una vez con index_catalog.py)
  - ddg.json             (el Diccionario de Decisiones Dorothy Gaynor)
  - una variable de entorno / secret ANTHROPIC_API_KEY
"""
import json
import base64
import io
import datetime as dt

import streamlit as st
from PIL import Image
import imagehash
import anthropic

MODEL = "claude-sonnet-5"
N_CANDIDATOS = 12          # cuantos SKUs parecidos (por hash) se le muestran a la IA
UMBRAL_REDUNDANCIA = 60    # % de similitud a partir del cual se considera "redundante"

RECOMENDACIONES = {
    "comprar": "🟢 Comprar",
    "comprar_si_sustituye": "🟡 Comprar únicamente si sustituye al SKU",
    "revisar_con_equipo": "🟠 Revisar con el equipo",
    "no_comprar": "🔴 No comprar",
}

st.set_page_config(page_title="Radar DG", page_icon="🧭", layout="centered")


# ------------------------------------------------------------------
# Carga de datos (una sola vez, cacheado)
# ------------------------------------------------------------------
@st.cache_data
def cargar_catalogo():
    with open("catalog_index.json", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def cargar_ddg():
    with open("ddg.json", encoding="utf-8") as f:
        return json.load(f)


def get_client():
    api_key = st.secrets.get("ANTHROPIC_API_KEY", None)
    if not api_key:
        st.error(
            "Falta la clave ANTHROPIC_API_KEY. Agregala en Settings → Secrets "
            "de Streamlit Cloud (ver README)."
        )
        st.stop()
    return anthropic.Anthropic(api_key=api_key)


CATALOGO = cargar_catalogo()
DDG = cargar_ddg()
CATEGORIAS = sorted({r["categoria"] for r in CATALOGO})

if "aprobados" not in st.session_state:
    st.session_state.aprobados = []      # muestras que el comprador confirmo comprar en esta feria
if "historial" not in st.session_state:
    st.session_state.historial = []      # todas las fotos analizadas (para exportar despues)


# ------------------------------------------------------------------
# Utilidades de similitud rapida (preseleccion antes de llamar a la IA)
# ------------------------------------------------------------------
def foto_a_hashes(pil_img: Image.Image):
    im = pil_img.convert("RGB")
    return str(imagehash.phash(im, hash_size=16)), str(imagehash.colorhash(im, binbits=3))


def distancia(hash_a: str, hash_b: str) -> int:
    try:
        return imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)
    except Exception:
        return 999


def thumb_b64(pil_img: Image.Image, size=(220, 220), quality=70) -> str:
    im = pil_img.convert("RGB")
    im.thumbnail(size)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def preseleccionar_candidatos(phash_nuevo, colorhash_nuevo, categoria, n=N_CANDIDATOS):
    pool = [r for r in CATALOGO if r["categoria"] == categoria] + st.session_state.aprobados
    for r in pool:
        r["_dist"] = distancia(phash_nuevo, r["phash"]) + 0.5 * distancia(colorhash_nuevo, r["colorhash"])
    pool.sort(key=lambda r: r["_dist"])
    return pool[:n]


# ------------------------------------------------------------------
# Llamada a Claude con tool-use forzado -> salida estructurada
# ------------------------------------------------------------------
REPORTE_TOOL = {
    "name": "reporte_radar_dg",
    "description": "Entrega el reporte Radar DG estructurado para la muestra fotografiada.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vector_dg": {
                "type": "object",
                "description": "Una entrada por cada dimension del DDG. Si la dimension no esta "
                                "definida en el DDG, usar el texto exacto 'no definido en DDG'.",
                "properties": {k: {"type": "string"} for k in [
                    "ocasion_de_uso", "altura", "silueta", "punta", "estilo_visual", "color",
                    "tipo_de_outfit", "precio_percibido", "nivel_de_comodidad",
                    "tipo_de_construccion", "cliente_objetivo", "rol_del_producto",
                ]},
                "required": ["ocasion_de_uso", "altura", "silueta", "punta", "estilo_visual",
                             "color", "tipo_de_outfit", "precio_percibido"],
            },
            "redundancias": {
                "type": "array",
                "description": "Solo SKUs de la lista de candidatos entregada, con similitud >= 40%.",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "similitud_pct": {"type": "integer"},
                        "diferencias": {"type": "string"},
                    },
                    "required": ["sku", "similitud_pct", "diferencias"],
                },
            },
            "huecos": {"type": "string", "description": "Que decision nueva cubre y cual ya estaba cubierta."},
            "indice_similitud_dg": {"type": "integer"},
            "indice_cobertura_nueva_dg": {"type": "integer"},
            "recomendacion": {
                "type": "string",
                "enum": ["comprar", "comprar_si_sustituye", "revisar_con_equipo", "no_comprar"],
            },
            "sku_a_sustituir": {"type": ["string", "null"]},
            "motivo": {"type": "string", "description": "Fundamentado en cobertura de decisiones, nunca 'me gusta/no me gusta'."},
        },
        "required": ["vector_dg", "redundancias", "huecos", "indice_similitud_dg",
                     "indice_cobertura_nueva_dg", "recomendacion", "motivo"],
    },
}


def construir_system_prompt():
    ddg_texto = json.dumps(DDG, ensure_ascii=False, indent=2)
    return f"""Eres el sistema de inteligencia de compras de Dorothy Gaynor (Radar DG).
Tu trabajo NO es decidir por gusto si un zapato es bonito. Tu trabajo es ayudar a
administrar la complejidad de compra de Dorothy Gaynor: maximizar la cobertura de
decisiones de compra con el menor numero posible de SKUs redundantes.

Trabaja SIEMPRE con el Diccionario de Decisiones Dorothy Gaynor (DDG) de abajo.
No inventes valores fuera de las listas del DDG: si una dimension no tiene valores
definidos, respondé exactamente "no definido en DDG" para esa dimension.

DDG:
{ddg_texto}

Se te va a mostrar una foto nueva (la muestra que el comprador fotografio en la
feria) y una lista corta de SKUs candidatos del catalogo vigente (los mas
parecidos por forma/color, preseleccionados automaticamente) junto con sus fotos
reales, categoria e inventario. Ojo: esta lista de candidatos NO es todo el
catalogo (son 1,321 SKUs en total); son solo los mas visualmente parecidos dentro
de la misma categoria, para que puedas evaluar redundancia real.

Analiza la foto nueva y devolve el reporte usando la herramienta reporte_radar_dg:
1. El vector DG del producto (las dimensiones del DDG).
2. Para cada candidato que realmente compita por la misma decision de compra,
   informa su SKU, % de similitud aproximado y las diferencias principales.
   No fuerces coincidencias: si un candidato no compite en la misma decision
   (por ejemplo, distinta altura de taco o categoria), no lo incluyas o dale
   una similitud baja.
3. Que huecos de decision cubre o no cubre esta muestra.
4. Los dos indices (0-100): Indice de Similitud DG e Indice de Cobertura Nueva DG.
5. La recomendacion final (comprar / comprar_si_sustituye / revisar_con_equipo /
   no_comprar) con motivo fundamentado en cobertura de decisiones."""


def image_block(pil_img: Image.Image, max_side=700):
    im = pil_img.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}


def analizar(client, foto_nueva: Image.Image, categoria: str, candidatos: list):
    content = [
        {"type": "text", "text": f"Foto de la muestra nueva (categoria declarada por el comprador: {categoria}):"},
        image_block(foto_nueva),
        {"type": "text", "text": f"Candidatos preseleccionados ({len(candidatos)}), del mas al menos parecido por forma/color:"},
    ]
    for c in candidatos:
        thumb_bytes = base64.b64decode(c["thumb_b64"])
        content.append({"type": "text", "text": f"SKU {c['sku']} | categoria {c['categoria']} | inventario {c['inventario']} unidades, ventas {c['ventas']} (ST {c['sell_through_pct']}%):"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": c["thumb_b64"]}})

    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=construir_system_prompt(),
        tools=[REPORTE_TOOL],
        tool_choice={"type": "tool", "name": "reporte_radar_dg"},
        messages=[{"role": "user", "content": content}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("El modelo no devolvio el reporte estructurado esperado.")


# ------------------------------------------------------------------
# Interfaz
# ------------------------------------------------------------------
st.title("🧭 Radar DG")
st.caption("Dorothy Gaynor · fotografiá la muestra y te digo si ya tenés algo así o si es un hueco de catálogo.")

categoria = st.selectbox("Categoría de la muestra", CATEGORIAS)
if categoria in DDG.get("categorias_sin_definir_en_ddg", []):
    st.warning(f"'{categoria}' todavía no tiene valores de referencia en el DDG — el análisis se hace igual, pero avisá al equipo para completarlo.")

foto = st.camera_input("Tomá la foto de la muestra") or st.file_uploader("...o subí una foto", type=["jpg", "jpeg", "png"])

if foto is not None:
    pil_img = Image.open(foto)
    with st.spinner("Analizando contra el catálogo vigente..."):
        phash_nuevo, colorhash_nuevo = foto_a_hashes(pil_img)
        candidatos = preseleccionar_candidatos(phash_nuevo, colorhash_nuevo, categoria)
        client = get_client()
        try:
            reporte = analizar(client, pil_img, categoria, candidatos)
        except Exception as e:
            st.error(f"No se pudo generar el reporte: {e}")
            st.stop()

    candidatos_por_sku = {c["sku"]: c for c in candidatos}

    st.image(pil_img, caption="Muestra fotografiada", width=260)

    st.subheader("1 · Vector DG del producto")
    vector = reporte["vector_dg"]
    st.table({k.replace("_", " ").capitalize(): [v] for k, v in vector.items()})

    st.subheader("2 y 3 · Comparación y redundancias")
    st.caption(f"Comparado contra {len(candidatos)} SKUs preseleccionados de la categoría {categoria} "
               f"(de {sum(1 for r in CATALOGO if r['categoria']==categoria)} en esa categoría, "
               f"{len(CATALOGO)} en todo el catálogo).")
    redund = sorted(reporte.get("redundancias", []), key=lambda r: -r["similitud_pct"])
    if not redund:
        st.info("Ningún candidato preseleccionado compite por la misma decisión de compra.")
    for r in redund:
        c = candidatos_por_sku.get(r["sku"])
        cols = st.columns([1, 3])
        with cols[0]:
            if c:
                st.image(base64.b64decode(c["thumb_b64"]), width=100)
        with cols[1]:
            st.markdown(f"**{r['sku']}** — {r['similitud_pct']}% similitud")
            st.progress(min(100, max(0, r["similitud_pct"])) / 100)
            st.caption(f"Diferencias: {r['diferencias']}")

    st.subheader("4 · Huecos")
    st.write(reporte.get("huecos", ""))

    st.subheader("5 · Índices")
    c1, c2 = st.columns(2)
    c1.metric("Índice de Similitud DG", f"{reporte['indice_similitud_dg']}/100")
    c2.metric("Índice de Cobertura Nueva DG", f"{reporte['indice_cobertura_nueva_dg']}/100")

    st.subheader("6 · Recomendación final")
    reco_key = reporte["recomendacion"]
    texto_reco = RECOMENDACIONES.get(reco_key, reco_key)
    if reco_key == "comprar_si_sustituye" and reporte.get("sku_a_sustituir"):
        texto_reco += f" **{reporte['sku_a_sustituir']}**"
    st.markdown(f"### {texto_reco}")
    st.write(reporte.get("motivo", ""))

    st.session_state.historial.append({
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "categoria": categoria,
        "reporte": reporte,
    })

    if st.button("✅ Marcar esta muestra como comprada (agregarla a la base de esta feria)"):
        st.session_state.aprobados.append({
            "sku": f"NUEVO-{dt.datetime.now().strftime('%H%M%S')}",
            "categoria": categoria,
            "inventario": 0, "ventas": 0, "sell_through_pct": 0,
            "phash": phash_nuevo, "colorhash": colorhash_nuevo,
            "thumb_b64": thumb_b64(pil_img),
            "vector_dg": vector,
        })
        st.success("Agregada. Las próximas fotos de esta feria también se van a comparar contra esta muestra.")


with st.sidebar:
    st.header("Feria en curso")
    st.write(f"Muestras analizadas: {len(st.session_state.historial)}")
    st.write(f"Marcadas como compradas: {len(st.session_state.aprobados)}")
    if st.session_state.historial:
        export = json.dumps(st.session_state.historial, ensure_ascii=False, indent=2)
        st.download_button("⬇️ Descargar historial de la feria (JSON)", export,
                            file_name=f"radar_dg_feria_{dt.date.today()}.json", mime="application/json")
    st.caption("Al terminar la feria, este archivo se puede usar para actualizar el catálogo "
               "de la próxima temporada (ver README).")
