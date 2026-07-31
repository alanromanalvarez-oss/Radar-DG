"""
Radar DG - app para compradores en feria (ej. SAPICA) -- v6
=============================================================
El comprador abre este link en el celular, saca (o sube) una foto de la
muestra -- YA NO elige categoria a mano, la IA la identifica sola por la
silueta y busca en TODO el catalogo -- y la app le devuelve un reporte
corto: categoria identificada, vector de decision, redundancias (con
foto, inventario, venta, ST, costo, precio y otros colores disponibles),
huecos y recomendacion. Recien despues del analisis se pide nombre,
proveedor y costo, solo si decide guardarlo.

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
N_CANDIDATOS = 8           # cuantos SKUs se preseleccionan por hash (forma+color) como
                           # "base" antes de expandir con su familia de colores (ver
                           # preseleccionar_candidatos). Se busca en TODO el catalogo, no
                           # solo dentro de una categoria elegida a mano.
MAX_POR_FAMILIA_DE_COLOR = 6   # de cada candidato base, cuantos "hermanos" del mismo
                               # modelo en otro color se suman igual (verificado con
                               # datos reales: sube de encontrar ~48% a ~75% de las
                               # variantes de color de un mismo modelo)
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


@st.cache_data
def logo_base64():
    """Logo de Dorothy Gaynor (wordmark blanco sobre fondo transparente) --
    se muestra sobre una franja oscura porque en blanco no se ve (el PNG es
    letras blancas con fondo transparente, pensado para fondo oscuro)."""
    try:
        with open("logo_dg.png", "rb") as f:
            return base64.b64encode(f.read()).decode()
    except FileNotFoundError:
        return None


def get_anthropic_client():
    api_key = st.secrets.get("ANTHROPIC_API_KEY", None)
    if not api_key:
        st.error("Falta la clave ANTHROPIC_API_KEY en Settings → Secrets (ver README).")
        st.stop()
    return anthropic.Anthropic(api_key=api_key)


CATALOGO = cargar_catalogo()
DDG = cargar_ddg()
DIMENSIONES_DDG = list(DDG.get("dimensiones", {}).keys())


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
ANGULOS_ROTACION = (-15, -10, -5, 0, 5, 10, 15)  # ver foto_a_hashes_multi


def foto_a_hashes(pil_img: Image.Image):
    im = pil_img.convert("RGB")
    return str(imagehash.phash(im, hash_size=16)), str(imagehash.colorhash(im, binbits=3))


def foto_a_hashes_multi(pil_img: Image.Image, angulos=ANGULOS_ROTACION):
    """Calcula el hash de la foto nueva en varios angulos de rotacion leve.

    Por que: las fotos del catalogo son de estudio, perfectamente derechas.
    Las que saca un comprador en la feria casi nunca lo estan -- un
    telefono en mano, un poco inclinado, es la norma. Se verifico con un
    caso real (el mismo SKU exacto, pero con la foto rotada ~15 grados y
    recortada de forma realista): comparando con un solo angulo, el SKU
    correcto caia del puesto #1 al puesto #1342 de 1527 -- practicamente
    invisible para la busqueda. Probando esta misma foto contra varios
    angulos y quedandose con la MEJOR distancia para cada candidato del
    catalogo, el SKU correcto volvia a aparecer en el puesto #1. El costo
    extra es minimo (unos pocos milisegundos de CPU local, no hace ninguna
    llamada de mas a la IA)."""
    im = pil_img.convert("RGB")
    out = []
    for ang in angulos:
        im_r = im.rotate(ang, expand=True, fillcolor=(255, 255, 255)) if ang else im
        out.append((str(imagehash.phash(im_r, hash_size=16)), str(imagehash.colorhash(im_r, binbits=3))))
    return out


def distancia_multi(hashes_nuevos, cand_phash: str, cand_colorhash: str) -> float:
    """Igual que distancia(), pero contra una lista de (phash, colorhash) de
    la foto nueva en varios angulos -- se queda con la mejor (menor)
    distancia de todas, para no perder el match por una foto apenas
    inclinada."""
    return min(distancia(ph, cand_phash) + 0.5 * distancia(ch, cand_colorhash) for ph, ch in hashes_nuevos)


def distancia(hash_a: str, hash_b: str) -> int:
    """Distancia de Hamming a nivel de bits entre dos hashes en hex.
    OJO: se compara el string hex directamente (no via imagehash.hex_to_hash),
    porque hex_to_hash asume un arreglo cuadrado y con colorhash (que no lo es)
    siempre tiraba excepcion -- en la practica, el color nunca influia en la
    preseleccion de candidatos. Con esta comparacion bit a bit funciona igual
    de bien para phash (verificado: da identico resultado que antes) y ademas
    funciona correctamente para colorhash."""
    try:
        return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
    except Exception:
        return 999


def thumb_b64(pil_img: Image.Image, size=(180, 180), quality=60) -> str:
    im = pil_img.convert("RGB")
    im.thumbnail(size)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def compradas_compartidas_como_candidatos():
    """Muestras que CUALQUIER comprador ya marco como compradas en esta feria,
    convertidas al mismo formato que los items del catalogo para poder compararlas.
    Se buscan en TODA la feria (no filtradas por categoria), igual que el resto."""
    out = []
    for fila in leer_historial_compartido():
        if str(fila.get("comprada", "")).strip().upper() != "SI":
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
            "categoria": fila.get("categoria", ""),
            "inventario": 0, "ventas": 0, "sell_through_pct": 0,
            "costo": fila.get("costo"), "precio": None,
            "phash": phash, "colorhash": colorhash,
            "thumb_b64": fila["foto_base64"],
            "fuente": "aprobada_en_feria",
        })
    return out


def preseleccionar_candidatos(hashes_nuevos, n=N_CANDIDATOS,
                               max_por_familia=MAX_POR_FAMILIA_DE_COLOR):
    """Busca los SKUs mas parecidos por forma/color en TODO el catalogo (no se
    le pide categoria al comprador -- la IA la identifica sola a partir de la
    foto). Ojo: algunos registros (ej. PRESS27, SKUs nuevos sin foto todavia)
    no tienen phash/colorhash -- los dejamos afuera de esta comparacion visual
    en vez de romper (antes esto tiraba KeyError).

    hashes_nuevos: lista de (phash, colorhash) de la foto nueva en varios
    angulos (ver foto_a_hashes_multi) -- se compara contra TODOS y se usa la
    mejor distancia, para no perder un match real solo porque la foto vino
    apenas inclinada (caso real verificado: sin esto, un SKU identico caia
    del puesto #1 al #1342 de 1527 con solo 15 grados de inclinacion).

    Paso 2 (expansion por familia de colores): el hash de imagen (phash+color)
    por si solo deja pasar bastantes "hermanos de color" del mismo modelo --
    verificado con datos reales del catalogo, la busqueda base sola encuentra
    ~48% de las variantes de color de un mismo modelo. Por eso, para cada uno
    de los candidatos base, sumamos tambien sus hermanos de color conocidos
    (mismo prefijo de SKU) -- asi la muestra "misma silueta, otro color" casi
    siempre queda incluida, aunque el color la hubiera sacado del top N por
    hash solo. Con esto el recall sube a ~75% en la misma prueba."""
    pool = [r for r in CATALOGO if r.get("phash") and r.get("colorhash")]
    pool += compradas_compartidas_como_candidatos()
    for r in pool:
        r["_dist"] = distancia_multi(hashes_nuevos, r["phash"], r["colorhash"])
    pool.sort(key=lambda r: r["_dist"])
    base = pool[:n]

    vistos = {r["sku"] for r in base}
    expandido = list(base)
    for r in base:
        if len(r["sku"]) < 4:
            continue
        prefijo = r["sku"][:-3]
        hermanas = [x for x in pool if x["sku"] != r["sku"] and x["sku"].startswith(prefijo)
                    and x["sku"] not in vistos]
        hermanas.sort(key=lambda h: h["_dist"])
        for h in hermanas[:max_por_familia]:
            vistos.add(h["sku"])
            h = dict(h)
            h["familia_color_de"] = r["sku"]
            expandido.append(h)
    return expandido


def familia_de_colores(sku: str, excluir: set):
    """Heuristica: en este catalogo el SKU = modelo base + 3 digitos de color
    (ej. D06950176501 y D06950176650 son el mismo modelo en 2 colores).
    Devuelve otros SKUs del mismo modelo base que existan en el catalogo."""
    if not sku or len(sku) < 4:
        return []
    base = sku[:-3]
    return sorted({r["sku"] for r in CATALOGO
                   if r["sku"] != sku and r["sku"].startswith(base) and r["sku"] not in excluir})


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
                "categoria_identificada": {
                    "type": "string",
                    "description": "La categoria de calzado que identificas en la foto (vos la determinas, "
                                    "el comprador NO la eligio de antemano). Usa un nombre corto y consistente "
                                    "(ej. 'Bota', 'Botín', 'Sandalia', 'Zapato', 'Mocasín', 'Tenis', 'Choclo', "
                                    "'Ugg', 'Balerina', 'Plataforma', 'Accesorio').",
                },
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
            "required": ["categoria_identificada", "vector_dg", "redundancias", "huecos", "indice_similitud_dg",
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

El comprador NO elige categoria de antemano: vos tenes que identificar la
categoria del calzado fotografiado a partir de la silueta (campo
categoria_identificada). La preseleccion de candidatos que se te muestra a
continuacion se hizo buscando en TODO el catalogo (no esta filtrada por
categoria), asi que vas a ver candidatos de categorias distintas mezclados --
es tu trabajo descartar los que no compiten realmente por la misma decision de
compra, sin importar que hayan quedado preseleccionados por parecido superficial
de color o textura.

REGLA DURA DE PRECISION ESTRUCTURAL (la mas importante de todas): dos muestras
NUNCA pueden considerarse similares o redundantes si difieren en estructura,
aunque compartan color, textura o material. Compara con atencion, en este orden
de importancia:
1. Silueta general (bota / botin / choclo / sandalia / plataforma / balerina / etc.)
2. Altura de caña / tacon (plano, con plataforma, tacon bajo/medio/alto, caña alta/baja)
3. Tipo de suela (plana, plataforma, con cuña, deportiva, etc.)
4. Forma de la punta (redonda, cuadrada, puntiaguda, abierta)
Si una muestra difiere claramente de un candidato en CUALQUIERA de estos 4
puntos (ej. un zapato de piso "Confort" vs una bota con caña alta, o una punta
cuadrada vs una punta puntiaguda), ese candidato NO es una redundancia real, sin
importar cuanto se parezca el color o el patron -- baja la similitud_pct
drasticamente (por debajo de {UMBRAL_REDUNDANCIA}) o no lo incluyas. El color y
la textura son el ULTIMO criterio de desempate, nunca el primero.

Algunos candidatos vienen etiquetados "MISMO MODELO en otro color que el SKU
X": eso significa que el catalogo confirma que es el mismo molde/silueta que
otro candidato, solo que en un color distinto -- NO es una coincidencia visual
aproximada, es un dato duro. Si la muestra nueva comparte silueta/altura/suela/
punta con ese modelo, tratalo como candidato real de redundancia (con similitud
alta) sin importar que el color de esa variante puntual no coincida -- lo que
importa es que el modelo (silueta) ya existe en el catalogo, en algun color.
Si ademas el color de esa variante SI se parece al de la muestra nueva, marcalo
como el match mas fuerte de ese modelo.

Devolve el reporte usando la herramienta reporte_radar_dg:
1. categoria_identificada: la categoria que identificas por la silueta.
2. vector_dg: las dimensiones del DDG.
3. redundancias: SOLO candidatos que realmente compiten por la misma decision de
   compra (misma silueta + misma altura/tacon + suela + punta compatibles). No
   fuerces coincidencias por color o textura parecidos.
4. huecos: una frase.
5. Los dos indices (0-100).
6. La recomendacion final con motivo (una frase, fundamentada en cobertura de
   decisiones, nunca "me gusta / no me gusta")."""


def image_block(pil_img: Image.Image, max_side=512):
    im = pil_img.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}


def analizar(client, foto_nueva: Image.Image, candidatos: list):
    content = [
        {"type": "text", "text": "Foto de la muestra nueva (identifica vos la categoria por la silueta, "
                                  "no fue elegida por el comprador):"},
        image_block(foto_nueva),
        {"type": "text", "text": f"Candidatos preseleccionados ({len(candidatos)}) buscando en TODO el "
                                  "catalogo, del mas al menos parecido por forma/color (pueden incluir "
                                  "categorias distintas a la de la muestra nueva, descarta las que no "
                                  "corresponda):"},
    ]
    for c in candidatos:
        etiqueta = f"SKU {c['sku']} | {c['categoria']}"
        if c.get("fuente"):
            etiqueta += f" | fuente: {c['fuente']}"
        if c.get("familia_color_de"):
            etiqueta += (f" | MISMO MODELO en otro color que el SKU {c['familia_color_de']} "
                         "(mismo molde/silueta confirmado por catalogo, no por parecido visual)")
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
_logo = logo_base64()
if _logo:
    st.markdown(
        f"""
        <div style="background:#1A1A1A; margin:-1rem -1rem 1.2rem -1rem; padding:22px 16px;
                    text-align:center; border-bottom:3px solid #9C6B3E;">
            <img src="data:image/png;base64,{_logo}" style="max-width:100%; height:38px;" />
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.title("Dorothy Gaynor")

st.markdown(
    "<p style='margin-top:-0.6rem; color:#9C6B3E; font-weight:600; letter-spacing:0.04em;'>"
    "🧭 RADAR DG</p>",
    unsafe_allow_html=True,
)
st.caption("Foto → ¿tengo algo así o es un hueco? Rápido y concreto.")

if not sheets_disponible():
    st.info("La base compartida (Google Sheets) todavía no está conectada — cada análisis "
             "solo queda en este celular por ahora. Ver README para conectarla.")

if "reset_ctr" not in st.session_state:
    st.session_state.reset_ctr = 0

col_reset1, col_reset2 = st.columns([3, 1])
with col_reset2:
    if st.button("🔄 Analizar otra"):
        st.session_state.reset_ctr += 1
        st.rerun()

foto = st.camera_input("Tomá la foto de la muestra", key=f"camara_{st.session_state.reset_ctr}")
if foto is None:
    foto = st.file_uploader("...o subí una foto", type=["jpg", "jpeg", "png"],
                             key=f"upload_{st.session_state.reset_ctr}")

if foto is not None:
    pil_img = Image.open(foto)
    with st.spinner("Analizando..."):
        hashes_nuevos = foto_a_hashes_multi(pil_img)
        candidatos = preseleccionar_candidatos(hashes_nuevos)
        client = get_anthropic_client()
        try:
            reporte = analizar(client, pil_img, candidatos)
        except Exception as e:
            st.error(f"No se pudo generar el reporte: {e}")
            st.stop()

    candidatos_por_sku = {c["sku"]: c for c in candidatos}
    vector = reporte["vector_dg"]
    categoria = reporte.get("categoria_identificada", "")
    cobertura = reporte["indice_cobertura_nueva_dg"]
    reco_key = reporte["recomendacion"]
    texto_reco = RECOMENDACIONES.get(reco_key, reco_key)
    if reco_key == "comprar_si_sustituye" and reporte.get("sku_a_sustituir"):
        texto_reco += f" ({reporte['sku_a_sustituir']})"

    st.image(pil_img, width=220)
    if categoria:
        etiqueta_cat = f"Categoría identificada: **{categoria}**"
        if categoria in DDG.get("categorias_sin_definir_en_ddg", []):
            etiqueta_cat += " ⚠️ (todavía no tiene valores de referencia en el DDG)"
        st.caption(etiqueta_cat)

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
                otros_colores = familia_de_colores(c["sku"], excluir=set(candidatos_por_sku.keys()))
                if otros_colores:
                    st.caption(f"🎨 También disponible en {len(otros_colores)} color(es) más: "
                               + ", ".join(otros_colores[:6])
                               + (" …" if len(otros_colores) > 6 else ""))

    # ---- guardado: recien aca se pide nombre / proveedor / costo ----
    st.markdown("---")
    st.markdown("**💾 Guardar en la base compartida**")
    with st.form(key="guardar_form", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        with col_a:
            comprador = st.text_input("Tu nombre (opcional)")
        with col_b:
            proveedor = st.text_input("Proveedor")
        col_c, col_d = st.columns(2)
        with col_c:
            costo = st.text_input("Costo (USD)")
        with col_d:
            ya_comprada = st.checkbox("Ya la compré")
        enviado = st.form_submit_button("Guardar")

    if enviado:
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
            "comprada": "SI" if ya_comprada else "",
            "foto_base64": thumb_b64(pil_img),
        }
        if guardar_en_sheet(fila):
            st.success("Guardada en la base compartida." + (
                " Marcada como comprada — ya la van a tener en cuenta todos los compradores."
                if ya_comprada else ""))
        else:
            st.info("No se pudo guardar en la base compartida (¿está conectado Google Sheets?). "
                    "El análisis de arriba sigue siendo válido igual.")


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
