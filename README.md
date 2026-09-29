# KONYEKA Backend

API en FastAPI para KONYEKA: catálogo de clientes (RFCs), autenticación real
y, más adelante, los conectores de descarga masiva del SAT (portal CIEC y
Web Service con e.firma).

## Estado actual (sprint 1)

- ✅ Autenticación real contra Postgres (`/accounts/login`, JWT).
- ✅ Catálogo de clientes (`/clientes`): alta, baja lógica, edición, búsqueda.
- 🔜 Descarga real del SAT con e.firma (`.cer`/`.key`/contraseña) — pendiente
  de sprint 2. `app/sat_portal.py`, `app/sat_xml.py` y `app/sat_portal_ciec/`
  siguen siendo simulaciones/pruebas, tal como estaban.

## Requisitos

- Python 3.11+ (recomendado; probado también con 3.10)
- Docker (para levantar Postgres localmente)

## Primeros pasos

```bash
# 1. Entorno virtual
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Variables de entorno
cp .env.example .env
# Edita .env: define ADMIN_PASSWORD y, en producción, un JWT_SECRET_KEY propio.

# 3. Base de datos local (Postgres en Docker)
docker compose up -d db

# 4. Migraciones
alembic upgrade head

# 5. Usuario administrador inicial
python -m scripts.seed_admin

# 6. Levantar la API
uvicorn app.main:app --reload --reload-dir app --port 8010
```

La API queda en `http://localhost:8000` (docs interactivas en `/docs`).

## Endpoints del sprint 1

| Método | Ruta              | Descripción                                   |
| ------ | ----------------- | ---------------------------------------------- |
| POST   | `/accounts/login` | Login (email + password) → JWT                |
| GET    | `/users/me`        | Usuario autenticado                            |
| GET    | `/clientes`        | Lista clientes (`q`, `solo_activos`, paginación) |
| POST   | `/clientes`        | Alta de cliente (RFC, razón social, régimen)   |
| GET    | `/clientes/{id}`   | Detalle de un cliente                          |
| PUT    | `/clientes/{id}`   | Edición parcial                                |
| DELETE | `/clientes/{id}`   | Baja lógica (marca `activo=false`)             |

Todos los endpoints de `/clientes` y `/users` requieren
`Authorization: Bearer <token>`.

## Migraciones (Alembic)

```bash
alembic revision -m "descripcion"   # nueva migración
alembic upgrade head                # aplicar
alembic downgrade -1                # revertir la última
```

## Notas de arquitectura

- ORM: SQLAlchemy 2.0 (estilo `Mapped`/`mapped_column`).
- Passwords: `bcrypt` (hash + salt), nunca en texto plano.
- Sesión: JWT firmado con `JWT_SECRET_KEY` (HS256), sin estado en servidor.
- La baja de clientes es **lógica** (`activo=false`), no se borra el registro,
  porque en sprints futuros el XML/CFDI descargado se relacionará con el
  cliente por `id`.
- En Azure, `DATABASE_URL` apuntará a Azure Database for PostgreSQL Flexible
  Server (mismo patrón que Unidad Radar); el backend se desplegará como su
  propio servicio (Azure Container Apps o App Service) — pendiente de
  configurar cuando pasemos a la fase de despliegue.
