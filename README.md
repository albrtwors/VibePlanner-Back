# This is a test project for an university assignment
```
git clone https://github.com/albrtwors/VibePlanner-Back.git


pip install -r requirements.txt

## Configuración

Las variables de entorno están documentadas en `.env.example`. Copialo y completá
los valores que necesites:

```bash
cp .env.example .env
```

Si `DATABASE_URL` queda vacío, la app usa el SQLite local (`database.db`).

## Migraciones (Alembic)

```bash
source .venv/bin/activate
alembic upgrade head      # aplica las migraciones pendientes
alembic current           # revisión aplicada
alembic revision --autogenerate -m "mensaje"   # genera la migración desde models.py
alembic check             # detecta diferencias entre modelos y base
```

## Datos iniciales (seed)

```bash
source .venv/bin/activate
flask --app app seed
```

Es idempotente (se puede correr varias veces sin duplicar). Crea usuarios, géneros,
autores, canciones, inventario, cancioneros y eventos de ejemplo.

| Correo | Rol | Contraseña |
| --- | --- | --- |
| admin@vibeplanner.com | admin | vibeplanner123 |
| coordinador@vibeplanner.com | coordinator | vibeplanner123 |
| operador@vibeplanner.com | operator | vibeplanner123 |
| pendiente@vibeplanner.com | operator (sin verificar) | vibeplanner123 |

## Autenticación

El registro es de dos pasos: la cuenta se crea, se envía un código de 6 dígitos al
correo y recién después de verificarlo se puede iniciar sesión.

| Endpoint | Qué hace |
| --- | --- |
| `POST /api/auth/register` | Crea la cuenta y manda el código de verificación |
| `POST /api/auth/verify-email` | Valida el código y abre sesión (cookie `vibe_token`) |
| `POST /api/auth/resend-verification` | Reenvía el código (cooldown de 60 s) |
| `POST /api/auth/forgot-password` | Manda el código de recuperación |
| `POST /api/auth/verify-code` | Valida el código de recuperación sin consumirlo |
| `POST /api/auth/reset-password` | Cambia la contraseña consumiendo el código |

Los códigos expiran según `AUTH_CODE_TTL_MINUTES` (15 por defecto) y admiten
`AUTH_CODE_MAX_ATTEMPTS` intentos.

Si no hay credenciales de `GMAIL_USER` / `GMAIL_APP_PASSWORD`, la app entra en modo
desarrollo: el código se imprime en la consola y se devuelve en la respuesta
(`dev_code`) para poder probar el flujo completo sin SMTP.

