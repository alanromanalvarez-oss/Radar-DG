# Radar DG — guía de puesta en marcha (v7)

Esta carpeta tiene todo lo necesario para publicar Radar DG como un link que
tus compradores abren desde el celular en SAPICA. No hace falta saber
programar para seguir estos pasos, solo ir uno por uno.

**Si ya tenías una versión anterior desplegada:** solo necesitás repetir el
Paso 2 (subir estos archivos nuevos a GitHub) — no hace falta rehacer
Anthropic, Streamlit Cloud ni Google Sheets, siguen funcionando igual.

**Novedades de v7:**
- Se sumaron valores de referencia de "Bota" al DDG (silueta ahora distingue
  Caña corta / Caña media / Caña alta / Sobre la rodilla) — "Bota" ya no
  muestra el aviso de "faltan valores en el DDG".
- Se corrigió un bug real de la comparación por color (el hash de color
  nunca se estaba usando bien, siempre caía en un valor fijo) — ahora el
  color sí influye en encontrar candidatos parecidos.
- La preselección de candidatos ahora también suma, para cada candidato
  encontrado, sus "hermanos" del mismo modelo en otros colores (dato duro
  del catálogo, no una suposición visual) — probado con datos reales: antes
  encontraba ~48% de las variantes de color de un mismo modelo, ahora ~83%.
  Esto es lo que pediste: que aparezca la misma silueta aunque el color no
  coincida, y que los colores parecidos también salgan a relucir.
- Como contrapartida, cada análisis le manda a la IA un poco más de fotos de
  candidatos (antes 10 fijas, ahora entre ~12 y ~35 según el modelo) — puede
  tardar un poco más, pero da resultados más precisos. Si en la feria se
  siente lento, avisame y lo recalibramos.

**Novedades de v6 (siguen vigentes):**
- Ya no se elige categoría a mano: sacás la foto y la IA identifica la
  categoría sola por la silueta, buscando en **todo** el catálogo (antes
  comparaba solo dentro de la categoría elegida — por eso a veces mostraba
  matches sin ninguna similitud real).
- Botón **"🔄 Analizar otra"** para limpiar la foto y arrancar de nuevo, sin
  importar si la sacaste con la cámara o la subiste como archivo.
- Comparación más fina en silueta, altura/tacón, suela y punta: si esos
  puntos no coinciden, ya no se muestra como coincidencia aunque el color se
  parezca.
- Cada coincidencia ahora muestra también en qué otros colores existe ese
  mismo modelo en el catálogo.

Qué hay en la carpeta:

| Archivo | Qué es |
|---|---|
| `app.py` | La app que ven los compradores (cámara + reporte + base compartida) |
| `catalog_index.json` | ~1,615 referencias ya procesadas: catálogo vigente + China SS27 + Presapica + PRESS27 |
| `ddg.json` | El Diccionario de Decisiones Dorothy Gaynor (8 dimensiones activas) |
| `index_catalog.py` | Para reconstruir `catalog_index.json` desde cero (PDF viejo) si hiciera falta |
| `enriquecer_con_costos.py` | Suma costo/precio/margen/temporada desde el Excel de Vistas |
| `sumar_presapica.py` | Suma China SS27 + Presapica + PRESS27 desde Base Presapica.xlsx |
| `agregar_compras_previas.py` | Para sumar fotos de compras ya hechas, organizadas en carpetas |
| `requirements.txt` | Lista de librerías que necesita la app |

---

## Paso 1 — Conseguir la clave de Anthropic (la IA que analiza las fotos)

1. Andá a **console.anthropic.com** y creá una cuenta (con tu mail de Dorothy Gaynor).
2. Cargá una tarjeta en "Billing" — se cobra por uso, centavos de dólar por
   foto, unos pocos dólares en total para toda la feria.
3. En el menú de la izquierda, andá a **API Keys** → **Create Key**.
4. Copiá la clave (empieza con `sk-ant-...`) y guardala en un lugar seguro.

## Paso 2 — Subir el código a GitHub

1. Creá una cuenta gratis en **github.com** si no tenés una.
2. Creá un repositorio **privado** (por ejemplo `radar-dg`), ya que adentro
   va tu catálogo.
3. Subí todos los archivos de esta carpeta a ese repositorio (con GitHub
   Desktop: clonar → copiar el contenido de la carpeta adentro → commit → push).

## Paso 3 — Publicar la app en Streamlit Community Cloud (gratis)

1. Andá a **share.streamlit.io** y entrá con tu cuenta de GitHub.
2. **"Create app" → "Deploy a public app from GitHub"** → elegí el
   repositorio, rama `main`, archivo principal `app.py`.
3. Antes de "Deploy", andá a **"Advanced settings" → "Secrets"** y pegá
   (con tu clave real del Paso 1):
   ```
   ANTHROPIC_API_KEY = "sk-ant-tu-clave-real-aca"
   ```
4. Deploy. Te da un link público (`https://algo.streamlit.app`) — ese es el
   que le mandás a tus compradores.

Tip: convertí ese link en un código QR (buscá "QR code generator" en
Google) para pegarlo en el stand o mandarlo por WhatsApp.

## Paso 3-bis — Conectar la base compartida (Google Sheets) [NUEVO en v5]

Esto es lo que hace que **todas las fotos de todos los compradores** queden
en un solo lugar, con proveedor y costo, descargable en cualquier momento.
Son varios pasos pero se hacen una sola vez.

1. **Crear la planilla:** andá a **sheets.google.com**, creá una planilla
   nueva, llamala por ejemplo "Radar DG - SAPICA". Copiá el link de la
   barra de direcciones (algo como `https://docs.google.com/spreadsheets/d/ABC123.../edit`).

2. **Crear la cuenta de servicio (el "robot" que va a escribir en la planilla):**
   - Andá a **console.cloud.google.com** (con tu cuenta de Google) y creá un
     proyecto nuevo (arriba, "Select a project" → "New Project"), nombralo
     "radar-dg".
   - En el buscador de arriba escribí **"Google Sheets API"** → abrila →
     **"Enable"**.
   - En el menú lateral: **"APIs & Services" → "Credentials"** →
     **"+ Create Credentials" → "Service account"**.
   - Ponele un nombre (ej. "radar-dg-bot") y creala (podés dejar el resto
     por defecto, "Continue" → "Done").
   - En la lista de "Service Accounts", hacé clic en la que acabás de crear
     → pestaña **"Keys"** → **"Add Key" → "Create new key"** → tipo **JSON**
     → se descarga un archivo `.json` a tu computadora. **Guardalo, lo vas
     a necesitar en el paso 4.**
   - Copiá el email de esa cuenta de servicio (se ve arriba, termina en
     `...iam.gserviceaccount.com`).

3. **Darle acceso a la planilla:** volvé a tu Google Sheet, botón **"Share"**
   (arriba a la derecha), pegá el email de la cuenta de servicio (el que
   termina en `iam.gserviceaccount.com`) y dale permiso de **"Editor"**.

4. **Cargar las credenciales en Streamlit:** abrí el archivo `.json` que se
   descargó (con el Bloc de notas) y vas a ver algo así:
   ```json
   {
     "type": "service_account",
     "project_id": "radar-dg-xxxxx",
     "private_key_id": "...",
     "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
     "client_email": "radar-dg-bot@radar-dg-xxxxx.iam.gserviceaccount.com",
     "client_id": "...",
     "token_uri": "https://oauth2.googleapis.com/token"
   }
   ```
   Andá a tu app en Streamlit Cloud → **⋮ → Settings → Secrets**, y agregá
   (además de tu `ANTHROPIC_API_KEY` que ya estaba) esto, reemplazando cada
   valor por el de tu archivo `.json` real:
   ```toml
   GSHEET_URL = "https://docs.google.com/spreadsheets/d/ABC123.../edit"

   [gcp_service_account]
   type = "service_account"
   project_id = "radar-dg-xxxxx"
   private_key_id = "..."
   private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
   client_email = "radar-dg-bot@radar-dg-xxxxx.iam.gserviceaccount.com"
   client_id = "..."
   token_uri = "https://oauth2.googleapis.com/token"
   ```
   **Importante:** el `private_key` tiene que quedar entre comillas y con
   los `\n` tal cual vienen en el JSON (no los borres). Guardá — la app se
   reinicia sola.

5. Probalo: sacá una foto de prueba y fijate que aparezca una fila nueva en
   tu Google Sheet (hoja "Historial", se crea sola la primera vez).

Si preferís no hacer este paso ahora (por ejemplo, falta poco para viajar),
la app funciona igual sin él — simplemente cada análisis queda solo en el
celular de quien lo hizo, como en la v4, y te avisa en pantalla que la base
compartida no está conectada.

## Paso 4 — Probarla antes de la feria

Abrí el link vos mismo, completá categoría/proveedor/costo, sacale una
foto a un zapato cualquiera y confirmá que te devuelve el reporte. Hacé
esto uno o dos días antes de viajar, no el mismo día.

## Durante la feria

- Cada comprador abre el mismo link y saca (o sube) la foto de la muestra —
  ya no hace falta elegir categoría antes, la IA la identifica sola.
- En segundos ve: la categoría identificada, si es un **hueco o es
  redundante** (bien arriba, en un color), el vector DG en una sola línea,
  las coincidencias (con foto, inventario, venta, ST, costo, precio y otros
  colores disponibles de cada una), y la recomendación.
- Recién ahí, si quiere guardarla, completa nombre/proveedor/costo y toca
  **"Guardar"** — eso es lo único que queda pendiente después de ver el
  reporte.
- Para analizar otra muestra, toca **"🔄 Analizar otra"** — limpia la foto
  anterior (funciona igual si la sacaste con la cámara o si la subiste como
  archivo).
- Si decide comprarla, marca el check **"Ya la compré"** al guardar — a
  partir de ese momento, esa muestra la va a tener en cuenta el radar de
  **cualquier** comprador que fotografíe algo parecido, en cualquier celular.
- Desde la barra lateral, en cualquier momento, **"Descargar base completa
  (CSV)"** baja todo lo analizado hasta ese momento por todo el equipo.

## Velocidad — por qué a veces tarda y cómo mejorarla

- **La primera foto del día siempre es más lenta** (30-60 seg): Streamlit
  gratis "duerme" la app sin uso. Truco: que alguien la abra 5-10 minutos
  antes de arrancar a fotografiar.
- El motor ya está ajustado para velocidad: 8 candidatos por consulta (no
  12), respuestas cortas y concretas, instrucciones del DDG cacheadas, y
  las notas de inventario/costo/precio se muestran directo del catálogo
  (no se le piden a la IA, así no hay que esperarlas).
- Si sigue lento, se puede bajar más `N_CANDIDATOS` en `app.py` — avisame
  y lo ajustamos viendo tiempos reales.

## Sumar compras ya hechas antes de SAPICA

Si ya compraste muestras (otra feria, proveedor nacional) organizá las
fotos en carpetas por categoría y corré:
```
python agregar_compras_previas.py compras_previas/ catalog_index.json
```
Subí el `catalog_index.json` actualizado a GitHub (commit + push).

## Si el catálogo cambia (nuevos Excel de Dorothy Gaynor)

- Nuevo Excel de costos/precios/vistas → `python enriquecer_con_costos.py "Modelos con inventario mayor a 100_URL_VISTAS.xlsx" catalog_index.json`
- Nuevo Base Presapica (China / Presapica / PRESS27) → `python sumar_presapica.py "Base Presapica.xlsx" catalog_index.json`
- Subí el `catalog_index.json` resultante a GitHub cada vez.

## Después de la feria — que el radar "aprenda"

La base compartida de Google Sheets ya tiene, temporada tras temporada,
todo lo analizado y comprado. Los productos que se terminen confirmando se
pueden sumar al `catalog_index.json` de la próxima temporada — avisame
cuando llegue el momento y armamos ese script de "cierre de temporada".

---

## Limitaciones que hay que tener presentes (honestas, no letra chica)

- **El DDG cubre 8 de las ~14 categorías** del catálogo (Botín, Choclo,
  Mocasín, Ugg, Balerina, Accesorios todavía sin valores de referencia;
  "Bota" se sumó en v7). La app avisa cuando falta, pero cuanto antes se
  complete el DDG para esas categorías, más confiable va a ser el análisis
  ahí.
- El Vector DG que ve el comprador ahora muestra **solo las 8 dimensiones
  que están definidas en el DDG** (se sacaron las 4 que no tenían valores,
  a pedido de Dorothy Gaynor).
- **Costo y precio ya son datos reales** (vienen del Excel de Dorothy
  Gaynor), no estimados por la IA — pero solo para los SKUs que traían esa
  info en el Excel. Los de "PRESS27" (importación en proceso) no tienen
  foto todavía, así que aparecen como referencia de proveedor/costo pero no
  compiten en la comparación visual.
- **La preselección de candidatos ahora es contra todo el catálogo** (forma +
  color, sobre los ~1,615 registros), y la categoría la identifica la IA a
  partir de la silueta — ya no depende de que el comprador elija bien una
  categoría de antemano. Si la IA identifica mal una categoría muy atípica o
  poco fotogénica, todavía puede pedirse una corrección manual en una
  próxima versión si hiciera falta.
- Las fotos del catálogo son de estudio (fondo blanco); las de la feria van
  a tener fondo de stand — pedile a los compradores una foto de perfil,
  sobre superficie lisa si es posible.
- La base compartida en Google Sheets guarda una miniatura chica de cada
  foto como texto (no como imagen visible directamente en la planilla) —
  sirve para reprocesar o auditar después, pero si querés fotos "clickeables"
  dentro de la misma planilla, es un paso extra (subirlas a Google Drive) que
  podemos sumar después si hace falta.
