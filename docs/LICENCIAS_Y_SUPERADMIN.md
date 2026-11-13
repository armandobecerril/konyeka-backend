# Licencias, pagos y portal de superadmin

## Modelo
- **Despacho** = la cuenta que paga. Todos sus usuarios ven la misma bóveda y los mismos clientes; nadie ve lo de otro despacho.
- Un usuario pertenece a un solo despacho (`users.despacho_id`) con rol `dueno`, `admin` o `miembro`. Dueño y admin aprueban accesos y contratan.
- **Separación de datos**: `app/core/tenancy.py` es una dependencia global. Cualquier ruta con `{rfc_client_id}` o `{client_id}` verifica que el cliente sea del despacho del usuario (si no: 404). Con la licencia vencida el despacho queda en solo lectura (402 al escribir; dar de baja un cliente y guardar columnas siguen permitidos).
- Los usuarios y clientes que ya existían quedaron en el despacho **ISC-INTERNO** con licencia manual hasta 2099 (migración 0010).

## Paquetes (anuales, MXN + IVA)
| Código | Clientes (RFC) | Usuarios | Precio |
|---|---|---|---|
| prueba | hasta 3 | 1 | 14 días gratis |
| d10 | 1–10 | 1 | $4,490 |
| d20 | 11–20 | 2 | $7,490 |
| d50 | 21–50 | 3 | $13,990 |
| d50p | 51–100 (+10 por bloque) | 5 | $22,990 |
| usuario_extra | — | +1 | $1,290 |
| rfc_extra_10 | +10 (solo d50p) | — | $1,900 |

- Renovación: dentro de los 45 días antes de vencer; el año nuevo empieza al terminar el actual. Ahí se puede bajar de paquete.
- Subir de paquete o agregar usuarios a mitad del año: se cobra la diferencia anual × días restantes / 365.
- Los precios viven en la tabla `planes` (se pueden ajustar con SQL sin desplegar).

## Flujo de equipo (seats)
1. El nuevo usuario se registra con **«Me uno a mi equipo»** y el código del despacho (`KON-XXXXX`) o el correo del responsable.
2. El dueño y los admins reciben un correo con *Aprobar / Rechazar* (`/aprobar-acceso?token=…`, vence en 7 días). Deben iniciar sesión con su cuenta.
3. Al aprobar, el usuario ocupa un lugar. Sin lugar libre → se pide comprar un usuario adicional.
4. **Contador individual que ya pagó** y luego se integra a un despacho: desde *Mi licencia → ¿Te vas a integrar a otro despacho?* pide acceso marcando *Llevar mis clientes*. Al aprobarse, sus clientes y su bóveda se mueven al despacho destino (si caben en la licencia y no hay RFC duplicados), su despacho anterior se cierra y su licencia termina ese día. Los días pagados no usados se compensan manualmente (cupón o licencia manual desde el superadmin).

## Pagos (Stripe)
- La cuenta de Stripe es la de ISC, compartida con VEMI: cada pago lleva `metadata.product = "konyeka"` y el webhook ignora los demás productos.
- Checkout `mode=payment` (pago único anual, sin suscripción), precio calculado en el servidor, IVA incluido, descriptor `KONYEKA`.
- **El webhook es la fuente de verdad**: el regreso de Stripe no activa nada. Es idempotente (por id de evento), bloquea la orden, verifica monto/moneda y que el modo (prueba/real) coincida con `PAYMENTS_MODE`.
- Después del pago se lee la comisión real (balance transaction) para Finanzas.
- `PAYMENTS_MODE=mock` usa una pasarela simulada (`/checkout/mock/...`) que dispara el mismo evento. Nunca en producción.

### Alta en Stripe
1. Developers → Webhooks → *Add endpoint*: `https://<backend>/webhooks/stripe`
2. Eventos: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`, `charge.refunded`.
3. Copia el *Signing secret* a `STRIPE_WEBHOOK_SECRET`. Empieza con `PAYMENTS_MODE=stripe_test` y llaves `sk_test_`.
4. Tarjeta de prueba: 4242 4242 4242 4242.

## Cupones
- **Días gratis** (p. ej. 30 días): se canjean en *Mi licencia*; suman días al final de la licencia vigente con los límites del paquete elegido.
- **% o monto**: se aplican al pagar sobre el precio sin IVA (cobro mínimo $10). Se apartan 90 min durante el pago, se canjean con el webhook y se liberan si el pago expira o falla.
- Restricciones: paquetes, primera compra, máximo de canjes, por despacho, vigencia, correo asignado. Lotes con CSV.

## Portal de superadmin (`/admin`)
Finanzas (ingreso sin IVA, IVA, comisiones reales/estimadas, nube, margen, ARR, por vencer), Embudo (visita → compra, uso en prueba, activación, fuente, dispositivo, rescate), Cupones, Despachos (detalle y licencia manual), Correos (bandeja) y Bitácora.

### Acceso con Microsoft Entra ID + MFA
1. Entra ID → App registrations → *New registration* «KONYEKA Superadmin», single tenant (ISC).
   - Redirect URI (Web): `https://<frontend>/admin/auth/entra/callback` (y `http://localhost:3000/admin/auth/entra/callback` para desarrollo).
2. *App roles*: crea el de `infra/entra/app-roles.json` (valor `KONYEKA.SuperAdmin`).
3. *Token configuration*: agrega los optional claims de `infra/entra/optional-claims.json` (`amr`, `email`).
4. *Certificates & secrets*: crea un client secret.
5. Enterprise applications → la app → *Users and groups*: asigna el rol a tu cuenta. Activa *Assignment required*.
6. Conditional Access: política que **exija MFA** para esta app (el backend rechaza tokens sin `mfa` en `amr`).
7. Variables:
   - Frontend: `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`, `APP_BASE_URL`.
   - Backend: `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ADMIN_ALLOWLIST=tu@isc.mx`.

El backend verifica el id_token por su cuenta (firma JWKS del tenant, `iss`, `aud`, `tid`, `amr` con mfa, rol o grupo, lista permitida) y emite una sesión de admin de 8 h que no sirve como sesión de usuario.

## Correo (Azure Communication Services)
Sin `ACS_CONNECTION_STRING` los correos quedan en *Correos → En bandeja* (con sus enlaces). Para enviarlos: crea un recurso de Communication Services + Email, verifica el dominio `konyeka.mx` y usa `DoNotReply@konyeka.mx`.

## Pendiente (backlog)
- Emitir el CFDI de ISC al despacho que pidió factura (los datos fiscales ya se capturan en la orden).
- Recordatorios automáticos de vencimiento y de prueba por terminar.
