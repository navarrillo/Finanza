# Autenticación de dos factores y sesiones

El acceso real exige usuario, contraseña y un código TOTP de seis dígitos o un código de recuperación sin usar. El autenticador funciona sin conexión; Finanza no usa SMS, correo, generadores QR externos ni proveedores de identidad. Cada instalación sigue teniendo un único usuario configurado mediante `APP_USER`.

## Actualizar una instalación existente con Docker

**Configura el autenticador antes de reiniciar la app actualizada.** MFA está activado por defecto. Si su archivo falta, es inválido o no es escribible, la app no arranca; si falla durante el funcionamiento, devuelve un error sin permitir acceso solo con contraseña. Las cookies anteriores a esta actualización dejan de ser válidas. No se migra ni se modifica `finanzas.db`.

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

- El mismo campo de login admite TOTP o un código de recuperación, con o sin guiones. Usuario, contraseña y código incorrectos producen el mismo error.
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

TOTP mejora la protección ante una contraseña robada, pero puede ser capturado mediante phishing en tiempo real. Las passkeys/WebAuthn serían un siguiente paso. No se incluyen passkeys, múltiples usuarios, dispositivos recordados ni recuperación remota en este cambio. Consulta también la [guía técnica en inglés](AUTHENTICATION.md), la [documentación de PyOTP](https://pyauth.github.io/pyotp/) y la [guía MFA de OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html).
