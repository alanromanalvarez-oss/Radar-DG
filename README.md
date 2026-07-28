# Radar DG — guía de puesta en marcha

Esta carpeta ya tiene todo lo necesario para publicar Radar DG como un link
que tus compradores abren desde el celular en la feria. No hace falta saber
programar para seguir estos pasos, solo ir uno por uno.

Qué hay en la carpeta:

| Archivo | Qué es |
|---|---|
| `app.py` | La app que ven los compradores (cámara + reporte) |
| `catalog_index.json` | Los 1,321 SKUs del catálogo ya procesados (fotos, categoría, inventario) |
| `ddg.json` | El Diccionario de Decisiones Dorothy Gaynor |
| `index_catalog.py` | Para regenerar `catalog_index.json` cuando cambie el catálogo |
| `requirements.txt` | Lista de librerías que necesita la app |

---

## Paso 1 — Conseguir la clave de Anthropic (la IA que analiza las fotos)

1. Andá a **console.anthropic.com** y creá una cuenta (con tu mail de Dorothy Gaynor).
2. Cargá una tarjeta en "Billing" — se cobra por uso, no es una suscripción.
   Para este uso (analizar fotos en una feria) el costo es de centavos de
   dólar por foto, así que unos días de feria cuestan muy poco (unos
   pocos dólares en total, no cientos).
3. En el menú de la izquierda, andá a **API Keys** → **Create Key**.
4. Copiá la clave (empieza con `sk-ant-...`) y guardala en un lugar seguro
   (un gestor de contraseñas, o una nota privada). **No la compartas ni la
   subas a ningún lado pública.**

## Paso 2 — Subir el código a GitHub

1. Creá una cuenta gratis en **github.com** si no tenés una.
2. Creá un repositorio nuevo (botón "New repository"), por ejemplo
   `radar-dg`. Dejalo **privado** (no público), ya que adentro va tu catálogo.
3. Subí todos los archivos de esta carpeta a ese repositorio (podés
   arrastrarlos directo en la web de GitHub con "Add file" → "Upload files").

## Paso 3 — Publicar la app en Streamlit Community Cloud (gratis)

1. Andá a **share.streamlit.io** y entrá con tu cuenta de GitHub.
2. Botón **"New app"** → elegí el repositorio `radar-dg`, la rama `main`,
   y como archivo principal `app.py`.
3. Antes de hacer clic en "Deploy", andá a **"Advanced settings" → "Secrets"**
   y pegá esto (con tu clave real del Paso 1):
   ```
   ANTHROPIC_API_KEY = "sk-ant-tu-clave-real-aca"
   ```
4. Deploy. En un par de minutos te da un link público, algo como
   `https://radar-dg.streamlit.app`. **Ese es el link que le mandás a tus
   compradores.**

Cualquier persona con el link puede abrirlo desde el celular (Chrome o
Safari), sin instalar nada.

Tip: pegá ese link en un generador de códigos QR gratis (buscá "QR code
generator" en Google) e imprimí el QR para pegarlo en la cartelera del
stand o mandarlo por WhatsApp al grupo de compradores.

## Paso 4 — Probarla antes de la feria

Abrí el link vos mismo, elegí una categoría, sacale una foto a un zapato
cualquiera (incluso uno que ya esté en el catálogo) y confirmá que te
devuelve el reporte completo. Hacé esto uno o dos días antes de viajar a
la feria, no el mismo día.

## Paso 5 — Durante la feria

- Cada comprador abre el mismo link en su propio celular.
- Elige la categoría del producto, saca la foto, y en segundos le llega
  el reporte de las 6 secciones (vector DG, redundancias con fotos reales,
  huecos, índices, recomendación).
- Si confirma la compra, puede tocar "Marcar como comprada" — así, si otro
  comprador (u otra foto más tarde) se parece a esa misma muestra, el radar
  ya la va a tener en cuenta, aunque todavía no esté en el catálogo oficial.
- Al final del día, desde el menú lateral se puede descargar el historial
  de esa sesión en un archivo JSON (útil para revisar qué se aprobó).

**Importante:** cada comprador que abra la app en su propio celular arranca
con su propia sesión — las muestras que uno marque como "comprada" no las
ven automáticamente los demás compradores en tiempo real durante la feria
(cada celular tiene su propia memoria temporal). Si esto es un problema
para tu equipo (varios compradores viendo lo mismo en simultáneo), avisame
y lo resolvemos en la siguiente versión con una base compartida.

## Después de la feria — que el radar "aprenda"

Los JSON descargados durante la feria tienen las muestras aprobadas con su
Vector DG. Para la próxima temporada, esos productos que se terminen
comprando de verdad se pueden sumar a `catalog_index.json` corriendo de
nuevo `index_catalog.py` sobre el catálogo actualizado. Si querés, en la
siguiente sesión te ayudo a automatizar ese "merge" para que sea un solo
paso.

---

## Limitaciones que hay que tener presentes (honestas, no letra chica)

- **El DDG que tenemos hoy cubre 7 de 13 categorías** (falta Bota, Botín,
  Choclo, Mocasín, Ugg, Balerina) **y 7 de las 11 dimensiones** del Vector DG
  (faltan Comodidad, Construcción, Cliente objetivo, Rol del producto). La
  app avisa cuando falta info, pero cuanto antes completes el DDG, más
  confiables van a ser esas categorías.
- **"Precio percibido"** hoy lo infiere la IA mirando la foto (materiales,
  herrajes, terminaciones) — no viene de un dato real de costo/precio. Si
  tenés esa info en otro sistema, convendría cargarla en `catalog_index.json`
  para que sea exacta en vez de estimada.
- **La preselección de candidatos es por categoría declarada + forma/color**
  (no compara contra los 1,321 SKUs en cada foto, sino contra los más
  parecidos dentro de la misma categoría). Esto es rápido y barato, pero
  significa que confía en que el comprador eligió bien la categoría al
  sacar la foto.
- Las fotos del catálogo son de estudio (fondo blanco); las de la feria van
  a tener fondo de stand. Pedile a los compradores una foto de perfil, sobre
  una superficie lisa si es posible — mejora la precisión de la comparación.
- Este catálogo (`catalog_index.json`) es "modelos con inventario mayor a
  100" — si Dorothy Gaynor compara también contra carryovers, importación de
  China u otras bases que no estén en este PDF, hay que sumarlas como una
  fuente adicional al indexado.
