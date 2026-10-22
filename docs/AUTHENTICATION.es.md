# Autenticación de dos factores y sesiones

El acceso real exige usuario, contraseña y un código TOTP de seis dígitos o un código de recuperación sin usar. El autenticador funciona sin conexión; Finanza no usa SMS, correo, generadores QR externos ni proveedores de identidad. Cada instalación sigue teniendo un único usuario configurado mediante `APP_USER`.

## Actualizar una instalación existente con Docker

**Si actualizas desde una versión que solo pedía contraseña, configura el autenticador antes de reiniciar.** Si ya utilizas MFA, conserva el autenticador y los códigos: el archivo de autenticación se actualiza automáticamente como se explica más abajo. MFA está activado por defecto. Si su archivo falta, es inválido o no es escribible, la app no arranca; si falla durante el funcionamiento, devuelve un error sin permitir acceso solo con contraseña. Las cookies anteriores a esta actualización dejan de ser válidas. No se migra ni se modifica `finanzas.db`.

Conserva las credenciales y rutas financieras de tu `.env` y añade o ajusta:

```dotenv
DEMO_MODE=false
MFA_REQUIRED=true
AUTH_DIRECTORY=./private/auth
AUTH_DB_PATH=./private/auth/auth.db
COOKIE_SECURE=true
PUBLIC_ORIGIN=https://finanza.example.com
```

Sustituye el dominio por el tuyo. `AUTH_DIRECTORY` es el directorio del host; Compose fija `AUTH_DB_PATH=/auth/auth.db` dentro del contenedor. La ruta del ejemplo en `.env` sirve para las herramientas locales. No guardes este archivo en el directorio financiero ni en una carpeta pública. Si eliges otra ruta absoluta, adapta ambos valores y los comandos.

Desde la raíz del proyecto en el servidor Linux:

```bash
mkdir -p private/auth
sudo chown 10001:10001 private/auth
sudo chmod 700 private/auth
docker compose build
docker compose run --rm --no-deps finanzas python scripts/manage_auth.py setup --state /auth/auth.db
```

El último comando es interactivo. Añade manualmente la clave mostrada a tu autenticador como TOTP de seis dígitos y periodos de treinta segundos. Introduce un código actual para confirmar el alta. La URI `otpauth://` también permite usar herramientas locales compatibles; no la pegues en un generador QR de Internet.

Guarda los diez códigos de recuperación fuera del servidor y separados de la contraseña: solo se muestran una vez. Cada uno sustituye al código del autenticador durante un único login, pero **la contraseña sigue siendo obligatoria**.

Cuando termine el alta:

```bash
docker compose up -d --force-recreate
```

Espera al siguiente código del autenticador, porque el usado para confirmar el alta ya está consumido. Accede desde tu dominio HTTPS y comprueba ambos factores. Mantén el proxy inverso y el puerto de la app privado como indica el README.

## Sin Docker

Instala `requirements.txt` en tu entorno virtual. Para una instalación nueva, `python3 scripts/setup.py` crea las credenciales y una base financiera vacía sin sobrescribir archivos. En una instalación existente conserva `.env` y añade `MFA_REQUIRED=true`, `AUTH_DB_PATH=./private/auth/auth.db` y `DEMO_MODE=false`.

```bash
source .venv/bin/activate
python scripts/manage_auth.py setup --state ./private/auth/auth.db
```

El archivo nuevo se crea con permisos `600`. Ejecuta la app y la herramienta con el mismo usuario del sistema o configura permisos equivalentes. El CLI no carga `.env` por sí mismo: indica `--state` si cambias la ruta. `--account` solo cambia la etiqueta mostrada en el autenticador; no configura `APP_USER`. Carga `.env` y arranca Uvicorn como indica el README.

## Política de acceso

El primer paso no crea una sesión ni da acceso a datos: genera una cookie temporal HttpOnly firmada y un registro pendiente en el archivo de autenticación. Caduca en cinco minutos y permite como máximo cinco códigos incorrectos, además de los límites por dirección y globales. Puede haber como máximo treinta comprobaciones pendientes a la vez. El segundo paso consume ese registro y el código de forma atómica antes de crear la sesión. Recargar la página vuelve al formulario de contraseña.

Al actualizar desde la primera versión de MFA, el archivo de autenticación se actualiza automáticamente a la versión 2 para guardar estas comprobaciones pendientes. No necesitas volver a dar de alta el autenticador ni cambiar los códigos de recuperación. Las cookies de sesión anteriores requieren iniciar sesión otra vez; la base financiera conserva su esquema y contenido.

- Primero introduce usuario y contraseña y pulsa «Continuar». Después verás únicamente el campo TOTP. «Usar un código de recuperación» cambia el segundo formulario; admite códigos con o sin guiones. «Volver al inicio de sesión» cancela la comprobación pendiente. La contraseña no se conserva en el formulario ni se reenvía al verificar el código.
- Cada periodo TOTP y cada código de recuperación se consumen una sola vez, incluso con peticiones simultáneas o reinicios. Se tolera como máximo un periodo adyacente; mantén el reloj del servidor sincronizado.
- Hay un máximo de ocho fallos por dirección observada y treinta globales cada cinco minutos, contando contraseña y segundo factor. Los límites persisten con MFA. Un ataque puede bloquear temporalmente nuevos logins; reiniciar no elimina el límite.
- Las sesiones duran como máximo una hora y caducan tras quince minutos sin una petición autenticada a la API. La actividad no amplía la hora máxima. El navegador también oculta los datos tras quince minutos sin una respuesta autenticada correcta.
- Cerrar sesión elimina el registro persistente. Cambiar usuario, contraseña, secreto de sesión o modo MFA invalida las cookies firmadas. Rotar el autenticador elimina todas las sesiones registradas.
- Las cookies siguen siendo HttpOnly, SameSite Strict y Secure cuando `COOKIE_SECURE=true`. MFA no protege una cookie de sesión activa que ya haya sido robada.

Solo la demo HTTP local admite `MFA_REQUIRED=false`, con `DEMO_MODE=true`, `COOKIE_SECURE=false` y sin `PUBLIC_ORIGIN`. `setup.py --demo` genera esta configuración. Para una demo existente, añade los valores y crea `private/auth/` si falta; repetir setup crea el directorio sin sobrescribir `.env`. En ese modo los límites y las revocaciones siguen en memoria. No publiques la demo sin MFA en un dominio accesible desde Internet.

## Pérdida del móvil y administración

Puedes entrar con un código de recuperación sin usar y tu contraseña. Para sustituir el autenticador desde una terminal privada del servidor:

```bash
docker compose run --rm --no-deps finanzas python scripts/manage_auth.py rotate --state /auth/auth.db
```

Confirma el nuevo autenticador y guarda los nuevos códigos. La rotación invalida inmediatamente el autenticador anterior, los códigos antiguos y todas las sesiones; no requiere reinicio. Si pierdes también los códigos, necesitarás acceso administrativo al servidor para ejecutar esta operación. No existe una recuperación web solo con contraseña o correo. Protege también SSH.

Para cerrar todas las sesiones conservando el autenticador:

```bash
docker compose run --rm --no-deps finanzas python scripts/manage_auth.py revoke-sessions --state /auth/auth.db
```

Sin Docker, usa las mismas acciones mediante `python scripts/manage_auth.py` y la ruta local `--state`. Un alta fallida o interrumpida exige resolver el estado desde el servidor; no desactives MFA como alternativa en producción.

## Almacenamiento y límites

El archivo privado de autenticación contiene la clave TOTP, hashes de códigos de recuperación y sesiones, marcas de actividad y fallos con direcciones convertidas a hash. La clave TOTP necesita recuperarse para verificar códigos y está almacenada sin cifrado en este archivo protegido por permisos. Las copias de seguridad son igualmente sensibles. El volumen financiero conserva solo lectura; únicamente `/auth` admite escritura persistente.

Restaurar una copia antigua también restaura códigos consumidos y sesiones revocadas. **Detén la app, restaura, rota el autenticador y sus códigos, cambia `SESSION_SECRET` y vuelve a arrancar.** No uses clones de este estado en servidores independientes. Todos los procesos de la instalación deben compartir el mismo archivo en un sistema de archivos local; las réplicas distribuidas y volúmenes de red quedan fuera de este diseño.

## Passkeys

Una passkey es una credencial WebAuthn descubrible. El acceso no pide el nombre de usuario. El autenticador debe verificar a la persona (`userVerification=required`) y guardar una credencial residente. La contraseña y el TOTP siguen disponibles.

Solo puedes registrar una desde el navegador con la sesión abierta, y solo durante `WEBAUTHN_REAUTH_SECONDS` tras entrar con contraseña y autenticador o con otra passkey (300 por defecto; de 60 a 3600). En la barra lateral, **Passkeys** permite añadir, renombrar y eliminar. Eliminar una no cierra la sesión actual.

`PUBLIC_ORIGIN` es el origen exacto del navegador (esquema, host y puerto si no es el predeterminado, sin ruta). El identificador de la parte de confianza es el host de ese origen. `WEBAUTHN_RP_ID` solo hace falta para usar un dominio padre, por ejemplo `example.com` si el origen es `https://finanza.example.com`. Es un nombre de host, sin esquema, puerto ni ruta. Si no coincide con el host ni con un padre, la app no arranca. El navegador solo ofrece passkeys en HTTPS o en localhost.

Los retos duran dos minutos y se borran al usarlos. El contador de firmas no puede retroceder; si retrocede, el acceso se rechaza y el contador guardado no cambia. Los autenticadores que siempre informan cero se aceptan. Los fallos de passkey cuentan en el mismo límite por dirección y global que una contraseña incorrecta, y la respuesta es el mismo rechazo genérico.

Estas dos rutas no exigen sesión y son las que hay que permitir en una lista del proxy:

```text
POST /api/login/passkey/options
POST /api/login/passkey
```

La cookie, el tiempo de inactividad y el cierre de sesión son los mismos que con contraseña.

Para borrar todas las passkeys por SSH, sin cambiar el autenticador:

```bash
docker compose run --rm --no-deps finanzas python scripts/manage_auth.py revoke-passkeys --state /auth/auth.db
```

No hace falta una terminal que oculte la entrada. No toca la contraseña, el autenticador, los códigos de recuperación ni las sesiones abiertas. Una cookie robada sigue valiendo hasta que caduque o ejecutes `revoke-sessions`. Rotar el autenticador tampoco borra las passkeys. Si pierdes un dispositivo, ejecuta los dos comandos.

Sin Docker: `python scripts/manage_auth.py revoke-passkeys --state ./private/auth/auth.db`.

Al actualizar, el archivo de autenticación pasa solo a la versión 3. Conserva el autenticador y los códigos. El volumen `/auth` debe seguir siendo escribible; no hace falta otro volumen. Reconstruye la imagen para instalar el paquete `webauthn`. La base financiera no cambia.

TOTP sigue siendo el acceso de reserva y puede capturarse con phishing en tiempo real. No hay varios usuarios, dispositivos recordados ni recuperación remota. Consulta la [guía técnica en inglés](AUTHENTICATION.md), la [documentación de PyOTP](https://pyauth.github.io/pyotp/), [WebAuthn](https://www.w3.org/TR/webauthn-2/) y la [guía MFA de OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).
