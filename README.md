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

## Revisión con contadores del 23-oct-2026 (rama `feature/informe-23oct`)

Backlog completo: `../REU_23OCT26/BACKLOG_KONYEKA_23OCT26.md`.

```bash
alembic upgrade head                      # 0008 monto_pago/moneda de pagos, 0009 bancos
pip install -r requirements.txt           # agrega openpyxl (estados de cuenta .xlsx)
python -m scripts.backfill_combustibles   # marca facturas directas de gasolinera (151015xx)
python -m pytest tests                    # parsers, motor de cruce, moneda de pagos
```

| Método | Ruta | Qué hace |
| --- | --- | --- |
| GET | `/clientes/{id}/cfdis?meses=2026-08,2026-09&grupo=pagos&orden=total&dir=asc` | Meses acumulables, sub-pestaña (facturas/pagos/nomina/traslados) y orden por cualquier columna |
| GET | `/clientes/{id}/cfdis/conteos` | Contadores de las sub-pestañas |
| GET | `/clientes/{id}/cfdis/{cfdi}/detalle` | Conceptos, impuestos, pagos, relacionados, nómina y combustible (leído del XML) |
| GET | `/clientes/{id}/conciliacion/facturas/{factura}/pagos` | REPs y movimientos bancarios aplicados a una factura PPD |
| GET | `/clientes/{id}/combustibles/reporte` · `/export?formato=detalle_csv\|diot_csv\|diot_txt` | Combustibles por estación (insumo DIOT) |
| GET/POST | `/clientes/{id}/bancos/cuentas` | Cuentas bancarias del cliente |
| POST | `/clientes/{id}/bancos/estados` (multipart: cuenta_id, periodo, archivo) | Carga CSV/XLSX/PDF del mes y concilia |
| POST | `/clientes/{id}/bancos/conciliar?meses=` | Re-corre el motor de cruce |
| GET | `/clientes/{id}/bancos/movimientos` · `/resumen` · `/export` | Revisión, deducibilidad y CSV |
| GET/POST/PUT | `/bancos/movimientos/{m}/candidatos` · `/vincular` · `/clasificacion` · `/bancos/vinculos/{v}/{aprobar\|rechazar\|desvincular}` | Revisión manual |

La moneda de los CFDI tipo **P** ahora sale de `MonedaP`/`MonedaDR` (el encabezado siempre
trae `XXX`); los registros viejos se corrigen solos al abrir la bóveda (`backfill_pagos_moneda`).
