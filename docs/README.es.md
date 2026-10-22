# Finanza · Guía en español

Para empezar en dos comandos, consulta [el README principal](../README.md).

Dashboard privado en inglés y español para consultar una base SQLite existente. **La aplicación nunca crea, actualiza ni migra la base de datos financiera.** El estado de autenticación se guarda en un archivo privado independiente con permiso de escritura. Los scripts de inicialización y generación de datos ficticios son independientes del dashboard y rechazan sobrescribir archivos existentes. Los importadores también se ejecutan por separado.

FastAPI + SQLite de la biblioteca estándar + una SPA sin frameworks ni compilación: un único proceso, pocas dependencias y despliegue sencillo. HTML, CSS, JavaScript y gráficos SVG locales permiten una interfaz adaptable sin CDN, servicios externos ni telemetría.

## Estructura

```text
.
├── app/
│   ├── __init__.py
│   ├── main.py                 # API, login y cabecera de privacidad
│   ├── db.py                   # Conexión SQLite mode=ro y cambio histórico
│   ├── analytics.py            # Consultas de vistas, métricas y filtros
│   └── static/
│       ├── index.html
│       ├── app.js              # Las siete pantallas y gráficos SVG
│       ├── styles.css          # Escritorio, tablet y móvil
│       └── favicon.svg
├── finanzas_esquema.sql        # Esquema público canónico, sin cuentas ni movimientos
├── scripts/create_demo.py      # Generador independiente sobre ese esquema
├── tests/test_finanzas.py
├── data/demo.db                # Generado; excluido de Git y de Docker
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
├── Caddyfile.example
├── .env.example
├── .dockerignore
└── .gitignore
```

## Idiomas

El selector está disponible antes y después de iniciar sesión. Se detecta el idioma del navegador y se recuerda la elección localmente; el idioma de respaldo es inglés. Fechas, números, monedas, gráficos, controles accesibles y errores se traducen sin cambiar los datos originales de SQLite. Para añadir idiomas, consulta [la guía de internacionalización](INTERNATIONALIZATION.md).

## Ejecución local

Requiere Python 3.11 o superior (Docker utiliza 3.13). No necesita Node.js. Si `data/demo.db` ya existe en la entrega, omite la línea del generador.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/create_demo.py
cp .env.example .env
```

Edita `.env` y establece tus credenciales, o ejecuta `python3 scripts/setup.py --demo` para generarlas. El usuario predeterminado es `admin`. La aplicación no arranca si `APP_PASSWORD` o `SESSION_SECRET` está vacío, es demasiado corto (12 y 32 caracteres), tiene poca entropía o es un ejemplo de esta documentación. Para elegirlos a mano:

```bash
python -c 'import secrets; print(secrets.token_hex(32))'
```

Copia el resultado en `SESSION_SECRET`. El bloque siguiente muestra la forma del archivo para los datos ficticios. Los dos secretos son marcadores y la aplicación los rechaza:

```dotenv
APP_USER=admin
APP_PASSWORD=elige-una-clave-larga-y-privada
SESSION_SECRET=pega-aqui-el-secreto-aleatorio-generado
COOKIE_SECURE=false
DB_PATH=./data/demo.db
DB_DIRECTORY=./data
DB_FILENAME=demo.db
DEMO_MODE=true
```

Carga las variables y arranca (Uvicorn no lee `.env` por sí solo):

```bash
set -a
source .env
set +a
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --no-server-header
```

Abre [el dashboard local](http://127.0.0.1:8000). La sesión dura como máximo una hora; con MFA, caduca además tras quince minutos sin peticiones autenticadas. Con MFA, cerrar sesión elimina el registro persistente, por lo que una cookie copiada sigue revocada tras reiniciar. Cambiar la contraseña, el usuario o el secreto de sesión invalida las cookies existentes. El botón de actualización vuelve a leer SQLite sin caché. Las credenciales se configuran en el servidor; la interfaz no permite cambiarlas ni registrarse.

Cada instalación debe generar credenciales propias; no hay una contraseña de demo compartida. Ejecuta `python3 scripts/setup.py --demo` para crear los datos ficticios y un `.env` privado con credenciales aleatorias, sin sobrescribir archivos existentes. Para otra base ficticia utiliza `scripts/create_demo.py --output /ruta/nueva.db`.

## Autenticación de dos factores

**Antes del primer arranque con MFA en una instalación real, configura TOTP.** Si ya tienes MFA, conserva el autenticador y los códigos: el archivo de autenticación se actualiza solo, también para guardar passkeys. Sigue la [guía de activación, actualización y recuperación](AUTHENTICATION.es.md). Puedes entrar con contraseña y un código del autenticador (o de recuperación de un solo uso), o con una passkey registrada en este origen. La passkey sustituye a los dos pasos. El alta de TOTP sigue haciéndose desde una terminal privada del servidor.

El directorio de autenticación se monta con escritura y separado del volumen financiero, que conserva solo lectura. La app no arranca sin un estado de autenticación válido y escribible. La excepción sin MFA exige una demo HTTP local con `MFA_REQUIRED=false`, `DEMO_MODE=true`, `COOKIE_SECURE=false` y sin `PUBLIC_ORIGIN`.

## Conectar la base real

El contrato público es `finanzas_esquema.sql`: el esquema completo y un catálogo genérico de categorías, sin cuentas ni movimientos. Cada usuario mantiene su propia base privada. Para inicializar una nueva base usa `python3 scripts/init_db.py --output data/finanzas.db`; este comando independiente no sobrescribe archivos. Para generar un importador local con tu IA, utiliza [el prompt de importación](AI_IMPORT_PROMPT.md) y [el contrato de datos](DATA_CONTRACT.md).

```dotenv
DB_PATH=/ruta/absoluta/a/finanzas.db
DEMO_MODE=false
```

La base debe contener las vistas `v_transactions`, `v_monthly_cashflow` y `v_joint_transactions`, y las tablas del esquema indicado. Las tablas opcionales pueden estar vacías o faltar: se muestran como “Sin datos”. No es necesario ejecutar scripts de migración. Una definición incompatible de una vista o un tipo de cambio ausente produce un mensaje explícito; la app no modifica la base para corregirlo.

Cada petición abre una conexión `file:///…?mode=ro`, activa `PRAGMA query_only=ON` y lee en una transacción coherente. Cierra la conexión al terminar. No se usa `immutable=1`, porque otra herramienta actualiza el archivo. Las consultas filtran con parámetros; los nombres de las tablas opcionales proceden de una lista fija.

### Fuentes y reglas de cálculo

- **Resumen y evolución:** `v_monthly_cashflow` es la fuente de los importes mensuales por moneda. `net_expenses` se lee de la vista: en el SQL canónico **ya excluye puntuales**; inversión y activos no se suman al gasto. Los ingresos siguen la definición de la vista: entradas positivas excepto reembolsos, inversión, activos y repercutidos, excluyendo transferencias internas. No se restringen artificialmente a categorías `income`. Ahorro = ingresos − gasto neto, antes de aportaciones a inversión; tasa = ahorro / ingresos. Con ingresos cero se muestra `—`.
- **Moneda consolidada:** SEK al abrir; selector EUR/SEK. Si la moneda coincide, se usa el total de la vista mensual directamente. Un total mensual agregado no conserva las fechas necesarias para convertirlo exactamente. Para otra moneda, `analytics.measures` obtiene los pesos diarios de las líneas de `v_transactions`, comprueba que su suma concuerda con **cada** total de la vista mensual (tolerancia de 0,02 unidades), y convierte esas partes a su fecha. Los pesos reproducen las expresiones del SQL canónico exclusivamente para distribuir por fecha los totales de la vista. Si una futura versión de la vista emplea otra convención, la conciliación bloquea el cálculo, en lugar de sustituir la vista o aproximar con un tipo mensual. Las aportaciones a inversión y activos son flujos netos firmados, por lo que un reembolso reduce el indicador.
- **Cambio histórico:** `fx_rates`, último registro anterior o igual al movimiento; admite EUR→SEK y su inverso. Nunca usa un registro futuro, un tipo de hoy ni un servicio externo. Si no hay tipo previo, no ofrece resultados parciales. No hay un umbral de antigüedad: un hueco largo conserva el último tipo disponible igual que un fin de semana.
- **Reembolsos:** los positivos de clase `reimbursement` o marcados `is_pass_through=1` reducen gasto, no aumentan ingresos. Las salidas de anticipo siguen siendo gasto hasta su devolución. La fixture usa exactamente las vistas canónicas; las diferencias en futuras versiones se detectan al conciliar para convertir moneda.
- **Puntuales:** el SQL canónico excluye estas compras de `expenses` y `net_expenses`. Se respeta esa decisión: el gasto neto y el ahorro del dashboard son habituales. Los puntuales tienen un gráfico separado; se muestran también `gasto con puntuales = gasto neto + puntuales` y `ahorro tras puntuales = ahorro − puntuales`. Las **medias habituales** parten directamente de la vista, sin descontar los puntuales otra vez. La media cubre solo meses con datos dentro de los últimos doce meses; un mes vacío no se interpreta como un mes de vida sin gastos. La tasa media es la razón de las medias, no la media de porcentajes. El mes seleccionado se incluye y puede ser parcial.
- **Comparaciones:** mes elegido frente al anterior, junto a la media habitual. La variación relativa usa el valor absoluto del mes anterior como denominador; con base cero o sin datos no se muestra porcentaje. Para la tasa se muestran puntos porcentuales.
- **Categorías:** líneas de `v_transactions`, agrupadas por categoría padre / categoría / subcategoría y banco. Los reembolsos reducen su propia categoría; no se reasignan a una compra concreta sin una relación en la base. El total de la tabla incluye categorías negativas; el donut solo representa importes positivos y avisa cuando hay negativos. Los puntuales se excluyen por defecto, y un selector permite incluirlos en el desglose. El resumen siempre conserva el desglose habitual sin filtros de la pestaña de categorías.
- **Movimientos:** exclusivamente `v_transactions`; búsqueda literal en descripción y contraparte, fechas inclusivas, banco, categoría e importe **firmado en moneda original**. Sin rango de fechas se consulta todo el historial. La vista mantiene los splits como líneas. El recuento del servidor usa `COUNT(DISTINCT id)`. Paginación de 25 líneas, CSV de todas las líneas filtradas, con moneda original y consolidada. `Otros` se destaca; no se reclasifica desde aquí. El CSV usa UTF-8 con BOM, separador `;` y protección contra fórmulas en textos.
- **Cuenta común:** exclusivamente `v_joint_transactions`; entradas y gastos del mes elegido, saldos originales y hasta 200 líneas recientes. Las aportaciones propias se leen de la categoría personal `Gastos comunes`. Las entradas incluyen todas las entradas positivas de la cuenta común, sin atribuirlas automáticamente a un miembro de la pareja. Sus gastos nunca se suman al resumen personal.
- **Saldos:** una fila por movimiento real en `transactions` (sin duplicar los splits) y `balance_snapshots`; snapshots con prioridad en el mismo día. Los movimientos del mismo día se ordenan por `id` al no existir una hora en el contrato. Los saldos conservan la moneda de cada cuenta y la última fecha hasta el fin del mes seleccionado. La consolidación de saldos usa el cambio de la fecha de cada último saldo y avisa de que las fechas pueden diferir; no calcula posiciones, deuda ni un patrimonio neto completo.
- **Inversiones y presupuestos:** las aportaciones ya tienen indicador. `instruments`, `holding_snapshots`, `budgets`, `documents` y `recurring_series` muestran sus registros automáticamente cuando existan (primeros 200 por tabla, con contador total). Se muestran los registros de consulta con etiquetas en el idioma seleccionado. No se inventan rentabilidades sin precios o series históricas, ni se altera un presupuesto desde el dashboard. `documents` solo expone metadatos a la sesión autenticada, nunca permite descargar rutas del servidor.

## Docker en Linux / Hetzner

Docker Engine con Compose v2. Crea `.env` con credenciales nuevas y los valores adecuados:

```dotenv
APP_USER=admin
APP_PASSWORD=una-clave-privada-de-al-menos-12-caracteres
SESSION_SECRET=un-secreto-aleatorio-de-al-menos-32-caracteres
COOKIE_SECURE=true
PUBLIC_ORIGIN=https://finanzas.tudominio.com
DB_DIRECTORY=/srv/finanzas/data
DB_FILENAME=finanzas.db
DEMO_MODE=false
```

Los dos secretos del ejemplo son marcadores. Hay que sustituirlos (por ejemplo con `python3 scripts/setup.py`) antes de arrancar. Si una instalación ya los copió, al cambiarlos hay que volver a iniciar sesión: la clave de firma incluye la contraseña.

`DB_PATH` se fija **dentro del contenedor** a `/data/${DB_FILENAME}`. Para local se utiliza el `DB_PATH` de tu entorno. Asegúrate de que el directorio ya existe y de que el UID/GID `10001:10001` puede atravesarlo y leer la base. Concede solo lectura mediante grupo o ACL; evita permisos globales innecesarios. La herramienta importadora conserva sus propios permisos de escritura fuera del contenedor.

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 finanzas
```

El contenedor se ejecuta sin root, con sistema de archivos de solo lectura, capacidades eliminadas y `/tmp` temporal. El directorio de datos financieros completo se monta `read_only: true`; el directorio de autenticación `/auth` es el único volumen persistente escribible de la app. Esto permite ver reemplazos atómicos del archivo en futuras peticiones, y los archivos auxiliares WAL si existen. La imagen no contiene ninguna base ni `.env`.

El puerto se publica únicamente en `127.0.0.1:8000`. Con Caddy instalado **en el host**, adapta [Caddyfile.example](../Caddyfile.example):

```caddyfile
finanzas.tudominio.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

El dominio debe apuntar al servidor para que el proxy gestione HTTPS. La autenticación propia permanece activa. `COOKIE_SECURE=true` requiere HTTPS; usa `false` únicamente para HTTP local. `PUBLIC_ORIGIN` debe coincidir exactamente con el origen del navegador (esquema y host, sin ruta) para validar login y logout detrás del proxy. La imagen arranca Uvicorn con `--proxy-headers` y `--no-server-header`.

`FORWARDED_ALLOW_IPS` es la dirección desde la que el proxy se conecta a la app. No uses `*` ni una red que contenga todas las direcciones: Uvicorn aceptaría un `X-Forwarded-For` elegido por el cliente. Solo reescribe la dirección del cliente si el par TCP está en esa lista, y se queda con la dirección no confiable más cercana del encabezado, así que un prefijo enviado por el navegador no sustituye al cliente que vio el proxy. Cualquier otra conexión conserva su propia dirección, y falsificar `X-Forwarded-For` no abre otro cubo de intentos. El valor por defecto de Compose solo confía en loopback. Con el proxy en el host y la app en Docker, la conexión llega desde la dirección del puente que ve el contenedor, no desde loopback: configura esa dirección. Incluye el proxy, no las direcciones de los visitantes. Los intentos fallidos se limitan por dirección (8 cada cinco minutos) y con un tope global en la misma ventana (30; con MFA se guardan en el archivo de autenticación independiente). Rotar direcciones no elimina el tope.

Si el proxy corre en **otro contenedor**, loopback es ese contenedor. Conecta ambos a una red de Docker y usa `reverse_proxy finanzas:8000`; elimina `ports` si el host no necesita acceso. Conserva `PUBLIC_ORIGIN`.

Con MFA, cerrar sesión elimina el registro persistente: una cookie copiada sigue revocada tras reiniciar. La protección contra reutilizar TOTP, los códigos de recuperación y los límites de intentos también persisten en el archivo de autenticación independiente. Las sesiones duran como máximo una hora y caducan tras quince minutos sin peticiones autenticadas. Cambiar usuario, contraseña o secreto de sesión invalida las cookies existentes. Consulta la [guía de autenticación](AUTHENTICATION.es.md) para recuperar acceso, rotar el autenticador o revocar todas las sesiones.

Las respuestas incluyen `Permissions-Policy`, `Cross-Origin-Opener-Policy` y `Cross-Origin-Resource-Policy`. `Permissions-Policy` deja apagados cámara, micrófono, geolocalización y pagos, y permite `publickey-credentials-get` y `publickey-credentials-create` solo para este origen (`self`), que es lo que el navegador necesita para las passkeys. Si sustituyes esa cabecera en el proxy, conserva esas dos directivas. `Strict-Transport-Security: max-age=31536000` solo se envía con `COOKIE_SECURE=true`, así el HTTP local no cambia, y no incluye `includeSubDomains`. Un proxy como Caddy reenvía estas cabeceras y no añade HSTS salvo que lo configures. Deja cada política en la app, o sustituye ese campo en el proxy por un único valor. Añadir un segundo `Strict-Transport-Security` (la forma `+` de Caddy) deja dos políticas y el navegador aplica una. El proxy puede seguir enviando su propia cabecera `Server`; `--no-server-header` solo quita la de Uvicorn.

### Actualización mensual y WAL

El importador se crea y valida una vez por banco/formato mediante el [prompt de importación](AI_IMPORT_PROMPT.md). **El repositorio no incluye importadores específicos de bancos**: la herramienta generada debe entregar su comando exacto de ejecución. Reutilízala cada mes; si el banco cambia el formato, pide a tu asistente que la adapte.

1. Descarga los extractos en una carpeta ignorada por Git, por ejemplo `imports/2026-10/`. Prioriza CSV/XLSX/OFX frente a PDF.
2. Ejecuta tu importador sobre la misma base personal. Debe crear una copia de seguridad coherente antes de actualizar, añadir movimientos nuevos, omitir duplicados de extractos repetidos o solapados y conservar las clasificaciones manuales. No inicialices otra base cada mes.
3. Aporta tipos de cambio históricos EUR/SEK reales que cubran las fechas nuevas. Revisa filas rechazadas, duplicados dudosos y categorías `Otros`; concilia recuentos, totales y saldos con los extractos. Corrige clasificaciones mediante la herramienta de importación, ya que el dashboard solo lee.
4. Valida la base actualizada, ajustando la ruta si es necesario:

   ```bash
   python3 scripts/validate_db.py --db data/finanzas.db
   ```

   La validación comprueba el contrato de datos; no garantiza que los totales bancarios o las clasificaciones sean correctos.
5. Si importas en el servidor, termina la transacción de escritura antes de actualizar el dashboard. Si importas en tu ordenador, transfiere una copia coherente de SQLite al `DB_DIRECTORY` del servidor, valídala allí y publícala con el nombre `DB_FILENAME`, conservando el acceso de lectura del usuario del contenedor. Los extractos y la base no se suben a GitHub. Respeta las condiciones WAL indicadas a continuación.
6. Pulsa **↻** y selecciona el nuevo mes. Actualizar datos en la misma ruta no requiere reconstruir ni reiniciar Docker. Cambiar variables de entorno sí requiere recrear el contenedor.

La app abre de nuevo la base en cada petición. Después de importar datos, pulsa ↻. La herramienta importadora debe gestionar sus transacciones y la publicación de una versión coherente; la app no ejecuta checkpoints ni cambia el modo de journal.

En bases WAL, el lector de solo lectura puede necesitar los archivos `finanzas.db-wal` y `finanzas.db-shm` ya existentes y legibles. Si SQLite necesita crear un archivo auxiliar en el volumen de solo lectura, la petición fallará con un mensaje. Coordina una copia consistente o checkpoint **desde la herramienta que escribe**, o configura allí la entrega de una base consistente compatible con lectura. No copies únicamente el `.db` mientras haya cambios pendientes en su WAL. No uses `immutable=1` para eludirlo en una base que cambia. Véanse las condiciones de [SQLite para lectura de WAL](https://www.sqlite.org/wal.html#read_only_databases) y las opciones [URI de solo lectura](https://www.sqlite.org/uri.html).

### Privacidad

No hay analítica, píxeles, CDN, fuentes remotas ni consultas de tipos de cambio por red. Las únicas peticiones del navegador son al propio servidor. Instalar dependencias y construir Docker requiere acceso al registro de paquetes; ejecutar la app no. `healthz` es público y no consulta datos financieros. La API y la exportación requieren sesión, salvo el login (contraseña, verificación y passkey) y `/api/auth/config`. Esas rutas sin sesión están listadas en el README en inglés por si el proxy usa una lista de permitidos. Las respuestas usan `Cache-Control: no-store`, CSP, `Permissions-Policy`, COOP, CORP y protección contra inclusión en iframes. HSTS solo acompaña a `COOKIE_SECURE=true`. No se configura CORS. La cookie es HttpOnly y SameSite Strict; el login compara hashes de longitud fija, limita intentos por dirección y con un tope global, y cerrar sesión revoca el token. No se activa el registro de accesos de Uvicorn.

## Pruebas

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

Las pruebas usan exclusivamente bases temporales ficticias. Comprueban que no se altera el hash de la base al navegar/exportar y que SQLite rechaza UPDATE/DDL; login, cookies y límite de intentos; FX diario, inverso y fines de semana; conciliación contra la vista mensual; splits y recuentos; reembolsos, transferencias y cuenta común; exclusión de puntuales de las medias; filtros y exportación completa; seguridad de CSV; tablas vacías y aparición de futuros datos. Los gráficos se revisan en navegador con la demo.

La base ficticia es determinista, de enero de 2023 a octubre de 2026, con una segunda cuenta en SEK desde marzo de 2025 y bancos ficticios, EUR/SEK, cuenta común, splits, reembolsos, compra puntual de coche, depósito recuperable y un caso de devolución de inversión en las pruebas. El catálogo y las vistas proceden de `finanzas_esquema.sql`. No contiene información bancaria real.

## Verificación

La suite de pruebas automatizadas pasa y se ha validado la sintaxis de JavaScript. Se han revisado las siete pantallas en navegador, con los selectores de moneda, filtros, navegación móvil y ausencia de errores de consola. Las capturas utilizan exclusivamente datos ficticios: [escritorio](preview-desktop.png) y [móvil](preview-mobile.png).

`docker compose config --quiet` se ha validado. No se ha construido ni arrancado la imagen en este equipo porque no hay un daemon Docker en ejecución. La suite comprueba los estados sin datos y verifica que navegar o exportar no modifica el hash de la base. Valida cada importación privada con `scripts/validate_db.py` y concíliala con los extractos originales.
