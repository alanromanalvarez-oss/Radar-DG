"""
Radar DG - app para compradores en feria (ej. SAPICA) -- v5
=============================================================
El comprador abre este link en el celular, elige categoria, pone
proveedor y costo, saca (o sube) una foto de la muestra, y la app le
devuelve un reporte corto: vector de decision, redundancias (con foto,
inventario, venta, ST, costo y precio), huecos y recomendacion.

Cada foto analizada se guarda automaticamente en un Google Sheet
compartido (comprador, proveedor, costo, categoria, resultados) para que
TODOS los compradores vean la misma base, no solo su propio celular. Esa
base se puede descargar en cualquier momento desde la barra lateral.

Requiere (ver README.md):
  - catalog_index.json  (generado con index_catalog.py + enriquecer_con_costos.py + sumar_presapica.py)
  - ddg.json             (el Diccionario de Decisiones Dorothy Gaynor)
  - secrets: ANTHROPIC_API_KEY, GSHEET_URL, [gcp_service_account]
"""
import json
import base64
import io
import uuid
import datetime as dt

import streamlit as st
from PIL import Image
import imagehash
import anthropic

MODEL = "claude-sonnet-5"
N_CANDIDATOS = 8           # cuantos SKUs parecidos (por hash) se le muestran a la IA
UMBRAL_REDUNDANCIA = 40    # % de similitud minimo para mostrar un candidato como match

RECOMENDACIONES = {
    "comprar": "🟢 Comprar",
    "comprar_si_sustituye": "🟡 Comprar solo si sustituye",
    "revisar_con_equipo": "🟠 Revisar con el equipo",
    "no_comprar": "🔴 No comprar",
}

HOJA_HISTORIAL = "Historial"
COLUMNAS_SHEET = [
    "id", "timestamp", "comprador", "categoria", "proveedor", "costo",
    "ocasion_de_uso", "altura", "silueta", "punta", "estilo_visual", "color",
    "tipo_de_outfit", "precio_percibido",
    "top_sku_similar", "top_similitud_pct",
    "indice_similitud_dg", "indice_cobertura_nueva_dg",
    "recomendacion", "motivo", "comprada", "foto_base64",
]

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


def get_anthropic_client():
    api_key = st.secrets.get("ANTHROPIC_API_KEY", None)
    if not api_key:
        st.error("Falta la clave ANTHROPIC_API_KEY en Settings → Secrets (ver README).")
        st.stop()
    return anthropic.Anthropic(api_key=api_key)


CATALOGO = cargar_catalogo()
DDG = cargar_ddg()
DIMENSIONES_DDG = list(DDG.get("dimensiones", {}).keys())
CATEGORIAS = sorted({r["categoria"] for r in CATALOGO})


# ------------------------------------------------------------------
# Google Sheets: base compartida entre todos los compradores
# ------------------------------------------------------------------
def sheets_disponible() -> bool:
    return "GSHEET_URL" in st.secrets and "gcp_service_account" in st.secrets


@st.cache_resource
def get_gspread_client():
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]), scopes=scopes)
    return gspread.authorize(creds)


def get_hoja_historial():
    client = get_gspread_client()
    sh = client.open_by_url(st.secrets["GSHEET_URL"])
    try:
        ws = sh.worksheet(HOJA_HISTORIAL)
    except Exception:
        ws = sh.add_worksheet(title=HOJA_HISTORIAL, rows=2000, cols=len(COLUMNAS_SHEET))
    if ws.row_values(1) != COLUMNAS_SHEET:
        ws.update("A1", [COLUMNAS_SHEET])
    return ws


@st.cache_data(ttl=20)
def leer_historial_compartido():
    """Devuelve todas las filas del Sheet (cacheado 20s para no pegarle a la API en cada foto)."""
    if not sheets_disponible():
        return []
    try:
        ws = get_hoja_historial()
        return ws.get_all_records()
    except Exception as e:
        st.warning(f"No pude leer la base compartida ({e}). Sigo solo con el catálogo.")
        return []


def guardar_en_sheet(fila: dict) -> str:
    if not sheets_disponible():
        return None
    try:
        ws = get_hoja_historial()
        ws.append_row([fila.get(c, "") for c in COLUMNAS_SHEET], value_input_option="USER_ENTERED")
        leer_historial_compartido.clear()
        return fila["id"]
    except Exception as e:
        st.warning(f"No se pudo guardar en la base compartida ({e}). El análisis igual se muestra abajo.")
        return None


def marcar_comprada_en_sheet(fila_id: str):
    if not sheets_disponible():
        return
    try:
        ws = get_hoja_historial()
        cell = ws.find(fila_id)
        if cell:
            col_comprada = COLUMNAS_SHEET.index("comprada") + 1
            ws.update_cell(cell.row, col_comprada, "SI")
            leer_historial_compartido.clear()
    except Exception as e:
        st.warning(f"No se pudo marcar como comprada en la base compartida ({e}).")


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


def thumb_b64(pil_img: Image.Image, size=(180, 180), quality=60) -> str:
    im = pil_img.convert("RGB")
    im.thumbnail(size)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def compradas_compartidas_como_candidatos(categoria: str):
    """Muestras que CUALQUIER comprador ya marco como compradas en esta feria,
    convertidas al mismo formato que los items del catalogo para poder compararlas."""
    out = []
    for fila in leer_historial_compartido():
        if str(fila.get("comprada", "")).strip().upper() != "SI":
            continue
        if fila.get("categoria") != categoria:
            continue
        if not fila.get("foto_base64"):
            continue
        try:
            im = Image.open(io.BytesIO(base64.b64decode(fila["foto_base64"]))).convert("RGB")
            phash = str(imagehash.phash(im, hash_size=16))
            colorhash = str(imagehash.colorhash(im, binbits=3))
        except Exception:
            continue
        out.append({
            "sku": f"COMPRADA-EN-FERIA ({fila.get('comprador') or 'equipo'})",
            "categoria": categoria,
            "inventario": 0, "ventas": 0, "sell_through_pct": 0,
            "costo": fila.get("costo"), "precio": None,
            "phash": phash, "colorhash": colorhash,
            "thumb_b64": fila["foto_base64"],
            "fuente": "aprobada_en_feria",
        })
    return out


def preseleccionar_candidatos(phash_nuevo, colorhash_nuevo, categoria, n=N_CANDIDATOS):
    pool = [r for r in CATALOGO if r["categoria"] == categoria] + compradas_compartidas_como_candidatos(categoria)
    for r in pool:
        r["_dist"] = distancia(phash_nuevo, r["phash"]) + 0.5 * distancia(colorhash_nuevo, r["colorhash"])
    pool.sort(key=lambda r: r["_dist"])
    return pool[:n]


# ------------------------------------------------------------------
# Llamada a Claude con tool-use forzado -> salida estructurada
# ------------------------------------------------------------------
def construir_reporte_tool():
    props = {d: {"type": "string"} for d in DIMENSIONES_DDG}
    return {
        "name": "reporte_radar_dg",
        "description": "Entrega el reporte Radar DG estructurado para la muestra fotografiada.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vector_dg": {
                    "type": "object",
                    "description": "Una entrada por cada dimension del DDG (solo estas, ninguna otra).",
                    "properties": props,
                    "required": DIMENSIONES_DDG,
                },
                "redundancias": {
                    "type": "array",
                    "description": f"Solo SKUs de la lista de candidatos entregada, con similitud >= {UMBRAL_REDUNDANCIA}%.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                            "similitud_pct": {"type": "integer"},
                            "diferencia_clave": {"type": "string", "description": "UNA sola frase corta, la diferencia mas importante."},
                        },
                        "required": ["sku", "similitud_pct", "diferencia_clave"],
                    },
                },
                "huecos": {"type": "string", "description": "Una frase corta: que decision nueva cubre (o no) esta muestra."},
                "indice_similitud_dg": {"type": "integer"},
                "indice_cobertura_nueva_dg": {"type": "integer"},
                "recomendacion": {
                    "type": "string",
                    "enum": ["comprar", "comprar_si_sustituye", "revisar_con_equipo", "no_comprar"],
                },
                "sku_a_sustituir": {"type": ["string", "null"]},
                "motivo": {"type": "string", "description": "Una frase corta, fundamentada en cobertura de decisiones."},
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
decisiones de compra con el menor numero posible de SKUs redundantes (canibalizacion).

Estamos en plena feria (SAPICA), con miles de muestras posibles y un open-to-buy
limitado -- los compradores necesitan respuestas RAPIDAS y CONCRETAS, no ensayos.
Se breve en cada campo de texto (una frase corta, directo al punto).

Trabaja SIEMPRE con el Diccionario de Decisiones Dorothy Gaynor (DDG) de abajo.
El vector_dg debe tener EXACTAMENTE estas dimensiones, ninguna otra: {", ".join(DIMENSIONES_DDG)}.
No inventes valores fuera de las listas del DDG.

DDG:
{ddg_texto}

Se te va a mostrar una foto nueva (la muestra que el comprador fotografio en la
feria) y una lista corta de candidatos (los mas parecidos por forma/color,
preseleccionados automaticamente, algunos del catalogo vigente y otros ya
marcados como "comprados en esta feria" por el equipo). Esta lista NO es todo
el universo de referencia; son solo los mas visualmente parecidos dentro de la
misma categoria.

Devolve el reporte usando la herramienta reporte_radar_dg:
1. vector_dg: las dimensiones del DDG.
2. redundancias: SOLO candidatos que realmente compiten por la misma decision de
   compra (misma altura/silueta/ocasion). No fuerces coincidencias.
3. huecos: una frase.
4. Los dos indices (0-100).
5. La recomendacion final con motivo (una frase, fundamentada en cobertura de
   decisiones, nunca "me gusta / no me gusta")."""


def image_block(pil_img: Image.Image, max_side=512):
    im = pil_img.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}


def analizar(client, foto_nueva: Image.Image, categoria: str, candidatos: list):
    content = [
        {"type": "text", "text": f"Foto de la muestra nueva (categoria declarada: {categoria}):"},
        image_block(foto_nueva),
        {"type": "text", "text": f"Candidatos preseleccionados ({len(candidatos)}), del mas al menos parecido:"},
    ]
    for c in candidatos:
        etiqueta = f"SKU {c['sku']} | {c['categoria']}"
        if c.get("fuente"):
            etiqueta += f" | fuente: {c['fuente']}"
        content.append({"type": "text", "text": etiqueta})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": c["thumb_b64"]}})

    system_blocks = [{
        "type": "text",
        "text": construir_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }]

    msg = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        system=system_blocks,
        tools=[construir_reporte_tool()],
        tool_choice={"type": "tool", "name": "reporte_radar_dg"},
        messages=[{"role": "user", "content": content}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("El modelo no devolvio el reporte estructurado esperado.")


def fmt_moneda(v):
    if v is None or v == "":
        return "—"
    try:
        return f"${float(v):,.0f}"
    except (ValueError, TypeError):
        return str(v)


# ------------------------------------------------------------------
# Interfaz
# ------------------------------------------------------------------
st.title("🧭 Radar DG")
st.caption("Dorothy Gaynor · foto → ¿tengo algo así o es un hueco? Rápido y concreto.")

if not sheets_disponible():
    st.info("La base compartida (Google Sheets) todavía no está conectada — cada análisis "
             "solo queda en este celular por ahora. Ver README para conectarla.")

col_a, col_b = st.columns(2)
with col_a:
    categoria = st.selectbox("Categoría", CATEGORIAS)
with col_b:
    comprador = st.text_input("Tu nombre (opcional)")

col_c, col_d = st.columns(2)
with col_c:
    proveedor = st.text_input("Proveedor")
with col_d:
    costo = st.text_input("Costo (USD)")

if categoria in DDG.get("categorias_sin_definir_en_ddg", []):
    st.warning(f"'{categoria}' todavía no tiene valores de referencia en el DDG.")

foto = st.camera_input("Tomá la foto de la muestra") or st.file_uploader("...o subí una foto", type=["jpg", "jpeg", "png"])

if foto is not None:
    pil_img = Image.open(foto)
    with st.spinner("Analizando..."):
        phash_nuevo, colorhash_nuevo = foto_a_hashes(pil_img)
        candidatos = preseleccionar_candidatos(phash_nuevo, colorhash_nuevo, categoria)
        client = get_anthropic_client()
        try:
            reporte = analizar(client, pil_img, categoria, candidatos)
        except Exception as e:
            st.error(f"No se pudo generar el reporte: {e}")
            st.stop()

    candidatos_por_sku = {c["sku"]: c for c in candidatos}
    vector = reporte["vector_dg"]
    cobertura = reporte["indice_cobertura_nueva_dg"]
    reco_key = reporte["recomendacion"]
    texto_reco = RECOMENDACIONES.get(reco_key, reco_key)
    if reco_key == "comprar_si_sustituye" and reporte.get("sku_a_sustituir"):
        texto_reco += f" ({reporte['sku_a_sustituir']})"

    st.image(pil_img, width=220)

    if cobertura >= 60:
        st.success(f"**HUECO — {texto_reco}**  ·  Cobertura nueva: {cobertura}/100  ·  Similitud: {reporte['indice_similitud_dg']}/100")
    elif cobertura >= 35:
        st.warning(f"**PARCIAL — {texto_reco}**  ·  Cobertura nueva: {cobertura}/100  ·  Similitud: {reporte['indice_similitud_dg']}/100")
    else:
        st.error(f"**REDUNDANTE — {texto_reco}**  ·  Cobertura nueva: {cobertura}/100  ·  Similitud: {reporte['indice_similitud_dg']}/100")
    st.caption(f"{reporte.get('motivo','')} · {reporte.get('huecos','')}")

    # Vector DG en una sola linea, para leer rapido
    st.caption(" · ".join(f"**{k.replace('_',' ')}:** {v}" for k, v in vector.items()))

    redund = sorted(reporte.get("redundancias", []), key=lambda r: -r["similitud_pct"])
    if redund:
        st.markdown("**Coincidencias:**")
    for r in redund:
        c = candidatos_por_sku.get(r["sku"])
        cols = st.columns([1, 4])
        with cols[0]:
            if c:
                st.image(base64.b64decode(c["thumb_b64"]), width=80)
        with cols[1]:
            st.markdown(f"**{r['sku']}** — {r['similitud_pct']}% · {r['diferencia_clave']}")
            if c:
                nota = (f"Inv: {c.get('inventario', 0)} · Venta: {c.get('ventas', 0)} · "
                        f"ST: {c.get('sell_through_pct', 0)}% · Costo: {fmt_moneda(c.get('costo'))} · "
                        f"Precio: {fmt_moneda(c.get('precio'))}")
                st.caption(nota)

    # ---- guardado automatico en la base compartida ----
    fila_id = str(uuid.uuid4())[:8]
    fila = {
        "id": fila_id,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "comprador": comprador,
        "categoria": categoria,
        "proveedor": proveedor,
        "costo": costo,
        **{d: vector.get(d, "") for d in DIMENSIONES_DDG},
        "top_sku_similar": redund[0]["sku"] if redund else "",
        "top_similitud_pct": redund[0]["similitud_pct"] if redund else "",
        "indice_similitud_dg": reporte["indice_similitud_dg"],
        "indice_cobertura_nueva_dg": cobertura,
        "recomendacion": reco_key,
        "motivo": reporte.get("motivo", ""),
        "comprada": "",
        "foto_base64": thumb_b64(pil_img),
    }
    id_guardado = guardar_en_sheet(fila)

    if id_guardado and st.button("✅ Marcar esta muestra como COMPRADA"):
        marcar_comprada_en_sheet(id_guardado)
        st.success("Marcada. Ya la van a ver todos los compradores en sus próximas fotos.")


with st.sidebar:
    st.header("Feria en curso")
    if sheets_disponible():
        historial = leer_historial_compartido()
        st.write(f"Fotos analizadas (todo el equipo): {len(historial)}")
        st.write(f"Marcadas como compradas: {sum(1 for h in historial if str(h.get('comprada','')).upper()=='SI')}")
        if historial:
            import csv
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=COLUMNAS_SHEET)
            writer.writeheader()
            writer.writerows(historial)
            st.download_button("⬇️ Descargar base completa (CSV)", buf.getvalue(),
                                file_name=f"radar_dg_feria_{dt.date.today()}.csv", mime="text/csv")
        st.caption("Esta base la ven y la alimentan todos los compradores en tiempo real.")
    else:
        st.caption("Conectá Google Sheets (ver README) para compartir esta base entre todos los compradores.")
