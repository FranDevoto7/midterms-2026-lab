# Power, Politics & Policy · Midterms 2026

Laboratorio interactivo de ciencia política y políticas públicas sobre las elecciones de medio término de 2026. El sitio es un solo archivo (`index.html`), en inglés con selector EN | ES, y lee tres archivos opcionales de `data/` que se actualizan solos con GitHub Actions.

## Qué hay en el repositorio

| Ruta | Para qué sirve |
|---|---|
| `index.html` | El sitio completo, con estilos, datos y la capa en español incluidos. |
| `data/news.json` | Noticias del canal automático. Lo escribe el robot; no hace falta tocarlo. |
| `data/election.json` | Foto institucional (bancas de la Cámara y el Senado, fase). Se edita a mano. |
| `data/results.json` | Resultados: casos, Senado, gobernaciones y cambio neto en la Cámara. |
| `scripts/fetch_news.py` | Lee las fuentes RSS, filtra lo relevante y escribe `data/news.json`. |
| `scripts/news_config.json` | Fuentes, palabras clave y reglas de etiquetado. Acá agregás o sacás fuentes. |
| `scripts/record_result.py` | Carga un resultado desde el formulario de GitHub. |
| `scripts/validate.py` | Revisa los JSON antes de publicar, para que un error de tipeo no rompa el sitio. |
| `.github/workflows/update-site.yml` | El robot: noticias cada 2 horas, carga de resultados y publicación. |

## Publicarlo, paso a paso

1. **Crear el repositorio.** En github.com, botón **New repository**. Nombre sugerido: `midterms-2026-lab`. Elegí **Public** (en una cuenta gratuita, GitHub Pages sólo funciona con repositorios públicos). No marques ninguna opción de README, `.gitignore` ni licencia, así queda vacío.

2. **Subir los archivos.** Hay dos caminos:
   - **Con GitHub Desktop o git (recomendado).** Cloná el repositorio vacío, copiá adentro todo el contenido de esta carpeta (incluida la carpeta oculta `.github`), hacé commit y push a `main`. Con git:
     ```bash
     cd midterms-2026-lab
     git init -b main
     git add .
     git commit -m "Primera versión"
     git remote add origin https://github.com/TU-USUARIO/midterms-2026-lab.git
     git push -u origin main
     ```
   - **Desde el navegador.** En la página del repositorio vacío, entrá a **uploading an existing file** y arrastrá `index.html`, `README.md`, `.nojekyll` y las carpetas `data` y `scripts`. Confirmá con **Commit changes**. Las carpetas que empiezan con punto suelen quedar ocultas en el explorador de archivos, así que creá el workflow a mano: **Add file → Create new file**, escribí como nombre `.github/workflows/update-site.yml`, pegá el contenido de ese archivo y confirmá.

3. **Activar GitHub Pages con Actions.** En el repositorio: **Settings → Pages → Build and deployment → Source: GitHub Actions**.

4. **Dar permiso de escritura al robot.** **Settings → Actions → General**. En *Actions permissions*, dejá **Allow all actions**. En *Workflow permissions*, elegí **Read and write permissions** y guardá. Sin esto, el robot no puede guardar las noticias.

5. **Primera ejecución.** Pestaña **Actions**. Si GitHub pide habilitar los workflows, aceptá. Elegí **Update and deploy the site → Run workflow**, dejá *kind* en `news` y ejecutá. En uno o dos minutos tienen que verse en verde los dos trabajos, `build` y `deploy`.

6. **Abrir el sitio.** La dirección aparece en el paso `deploy` y en **Settings → Pages**: `https://TU-USUARIO.github.io/midterms-2026-lab/`. En **Daily brief** vas a ver el selector **Curated edition / Latest feed**. Desde el 4 de noviembre, el canal automático pasa a mostrarse primero.

## Cómo se actualiza

- **Noticias.** Cada hora el robot lee las fuentes de `scripts/news_config.json` (NPR, PBS, Fox News, CBS, NBC, The Hill, Politico, Ballotpedia y búsquedas de Google News). Descarta lo que no es política estadounidense, lo viejo y los duplicados. Etiqueta cada nota con los casos y conceptos del laboratorio y guarda hasta 8; el sitio muestra las 5 primeras. Nunca pone más de 2 notas del mismo medio.
- **Medidas de seguridad.** Una fuente caída se salta con un aviso amarillo en el registro. Si no aparece nada relevante, se conserva el `news.json` anterior. Si un JSON tiene un error, el paso de validación frena la publicación y deja el sitio como estaba.
- **Lo que hace el robot y lo que no.** Asigna señal 2 a lo que toca un caso y 1 al resto. La señal 3 («cambia la lectura») es un juicio editorial y queda para la edición seleccionada. Los resúmenes son extractos cortos con enlace al original.
- **Publicación.** Cada vez que hacés un cambio en `main` (por ejemplo, editás un archivo desde el navegador), el sitio se vuelve a publicar solo. GitHub Pages puede tardar hasta unos 10 minutos en mostrar la versión nueva por su caché.

## Cargar resultados la noche de la elección

**Actions → Update and deploy the site → Run workflow**, y completá:

| kind | key | value | Ejemplo |
|---|---|---|---|
| `senate` | código del estado | `D`, `R`, `I` o `clear` | `GA` · `D` |
| `governors` | código del estado | `D`, `R`, `I` o `clear` | `AZ` · `R` |
| `case` | id del caso | texto del resultado, o `clear` | `maine` · `Collins wins by 3 points` |
| `house_net_change` | (vacío) | número entero, negativo si pierde el partido del presidente | `-12` |

Ids de los casos: `michigan`, `maine`, `texas`, `pennsylvania`, `alaska`, `redistricting`, `reach`, `massachusetts`. Completá también *source* (por ejemplo, `AP`) y, si podés, *url*. El formulario rechaza los códigos que no tienen elección en 2026 y los valores inválidos.

El modo **Results** de Predictions toma estos datos y no deja pisarlos a mano. En el sitio, cada estado sin resultado cargado se puede seguir marcando manualmente.

Para la foto institucional (`data/election.json`), editá el archivo desde el navegador con el lápiz. Las bancas de la Cámara tienen que sumar 435 y las del Senado 100, si no, la validación lo avisa. Una `phase` posterior a la del calendario adelanta el sitio (por ejemplo, `post-election`); una anterior se ignora.

## Resultados sugeridos por el robot

Después del cierre de la votación, el robot revisa cada hora las noticias que trae. Si un titular dice que alguien ganó una carrera al Senado o a una gobernación, abre un aviso en la pestaña **Issues** (y GitHub te manda un mail). Abrilo, mirá el enlace y decidí:

- **Close issue**: confirma el resultado; en un par de minutos aparece en el sitio.
- **Close as not planned** (en la flechita al lado de *Close issue*): lo descarta y el robot no lo vuelve a sugerir.
- **Tu descripción**: si antes de cerrar escribís un comentario (por ejemplo, «Estado pivote donde Trump había apostado fuerte por el candidato republicano»), el botón pasa a decir *Close with comment* y ese texto aparece junto al resultado en la sección **Resultados** del sitio. En el formulario de *Run workflow* también hay un campo **note** para lo mismo.

Con el sitio abierto, los resultados nuevos y las noticias aparecen solos en menos de cinco minutos, sin recargar.

Cuando AP, o dos medios distintos, anuncian el mismo ganador, el robot lo **carga solo** y abre un aviso «Cargado: …» para avisarte; si está mal, cerralo como **Close as not planned** y se borra. Si hay un único titular de otro medio, o titulares que se contradicen, te pide confirmación como siempre. Cada resultado cargado así lleva la marca «Carga automática» en el sitio.

**Cámara:** cada hora el robot lee el recuento de la página de Wikipedia de las elecciones a la Cámara y carga el cambio neto del partido del presidente, marcado como provisorio con la cantidad de bancas definidas. Si cargás un número a mano con *Run workflow*, el robot lo respeta; para volver al modo automático, cargá `clear`.

Los resultados de los casos siguen cargándose con el formulario de **Run workflow**.

## Hitos históricos

Un hito es, por ejemplo, la primera mujer negra elegida gobernadora o la primera senadora indígena.

- **Con un comentario:** en el aviso de un resultado (aunque ya esté cerrado), escribí una línea que empiece con `Hito:` y tu texto. El resto del comentario sigue siendo la descripción.
- **Con el formulario:** volvé a correr *Run workflow* con el mismo ganador y en **note** escribí `Hito: tu texto`. Si además querés una descripción, separala con ` | `: `Hito: primera mujer negra gobernadora | Bottoms ganó en un estado que Trump había ganado`.
- **El robot te los sugiere:** cuando un titular dice que un ganador es «the first woman», «the first Black…» o que «makes history», abre un aviso «Posible hito: …». Escribí `Hito: …` y cerralo; si lo cerrás sin escribir nada, se usa el titular; si no corresponde, cerralo como *not planned*.

En el sitio, los hitos aparecen en **Hoy** («Hitos de esta elección»), con una marca en cada tarjeta y con el filtro **Hitos** de **Resultados**.

## Ajustes frecuentes

- **Frecuencia de las noticias.** En `.github/workflows/update-site.yml`, la línea `cron: "17 * * * *"` significa «cada hora, en el minuto 17». Para cada 2 horas: `"17 */2 * * *"`. Para cada 6 horas: `"17 */6 * * *"`.
- **Fuentes.** En `scripts/news_config.json`, lista `feeds`: cada fuente tiene `name`, `url` y `weight` (de 0 a 1). Para Google News se agrega `"google": true`.
- **Palabras clave.** En el mismo archivo, `cases` define qué nota corresponde a cada caso (`any` alcanza con una coincidencia; `all` exige además otra) y `concepts` define el etiquetado por concepto. Son expresiones regulares y no distinguen mayúsculas.

## Si algo no anda

- **La dirección da 404.** Esperá a que termine la primera ejecución y confirmá que *Source* sea **GitHub Actions** (paso 3).
- **Falla «Commit data changes» con un error 403.** Falta el permiso del paso 4.
- **Falla `deploy` por reglas del entorno.** La rama principal tiene que llamarse `main`.
- **El canal automático no aparece.** Abrí la última ejecución en **Actions → build → Fetch news**. Cada fuente figura como `ok` o con un aviso. Si una deja de responder, reemplazala o borrala en `news_config.json`.
- **Abierto como archivo local (`file://`), el sitio no lee `data/`.** Es esperable. Para probar en tu computadora:
  ```bash
  python3 scripts/fetch_news.py
  python3 -m http.server 8000
  ```
  y abrí `http://localhost:8000`.
- **Las ejecuciones programadas se frenan.** En repositorios públicos, GitHub las desactiva tras 60 días sin actividad. Se reactivan desde **Actions**.
- **Las ejecuciones programadas llegan tarde.** En momentos de mucha carga, GitHub puede demorarlas. **Run workflow** fuerza una al instante.

## Notas

Los títulos, extractos y nombres de los medios del canal automático quedan en inglés, que es como los publican las fuentes. En español, el análisis de cada nota (por qué importa, mecanismo y qué mirar) se traduce solo, porque sale del contenido del laboratorio. Algunas fuentes ofrecen sus feeds para uso personal y no comercial; este proyecto muestra sólo extractos breves con enlace al original.
