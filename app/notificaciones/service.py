"""Envío de correos transaccionales (Azure Communication Services) con
bandeja de salida en BD. Sin ACS configurado, el correo queda con estado
"bandeja" y se puede ver en el portal de superadmin."""
import html as html_lib
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.notificaciones.models import Notificacion

logger = logging.getLogger(__name__)


def _html(titulo: str, parrafos: list[str], boton: tuple[str, str] | None = None, boton2: tuple[str, str] | None = None) -> str:
    cuerpo = "".join(f'<p style="margin:0 0 14px;line-height:1.55">{p}</p>' for p in parrafos)
    botones = ""
    for etiqueta, url, color in [(*b, c) for b, c in ((boton, "#006BFF"), (boton2, "#64748B")) if b]:
        botones += (
            f'<a href="{html_lib.escape(url)}" style="display:inline-block;margin:6px 8px 6px 0;padding:11px 20px;'
            f'border-radius:999px;background:{color};color:#fff;text-decoration:none;font-weight:600">{html_lib.escape(etiqueta)}</a>'
        )
    return (
        '<div style="font-family:Segoe UI,Arial,sans-serif;background:#F5FAFF;padding:24px">'
        '<div style="max-width:560px;margin:auto;background:#fff;border-radius:16px;padding:28px;color:#061A35">'
        f'<p style="font-weight:800;letter-spacing:.04em;color:#006BFF;margin:0 0 18px">KONYEKA</p>'
        f'<h1 style="font-size:20px;margin:0 0 16px">{html_lib.escape(titulo)}</h1>{cuerpo}{botones}'
        '<p style="margin:22px 0 0;font-size:12px;color:#64748B">Recibes este correo por tu cuenta en KONYEKA · Intelligence Software Consulting.</p>'
        "</div></div>"
    )


def enviar(
    db: Session,
    *,
    plantilla: str,
    destinatario: str,
    asunto: str,
    texto: str,
    html: str | None = None,
    despacho_id: int | None = None,
) -> Notificacion:
    """Guarda el correo y lo envía (si hay ACS). Nunca lanza: un correo que
    falla no debe tumbar la operación que lo originó."""
    n = Notificacion(
        plantilla=plantilla, destinatario=destinatario.lower().strip(), asunto=asunto[:255], texto=texto,
        html=html, despacho_id=despacho_id, estado="pendiente",
    )
    db.add(n)
    db.flush()
    if not settings.ACS_CONNECTION_STRING:
        n.estado = "bandeja"
        logger.info("Correo en bandeja (sin ACS): %s -> %s", plantilla, destinatario)
        return n
    try:
        from azure.communication.email import EmailClient

        cliente = EmailClient.from_connection_string(settings.ACS_CONNECTION_STRING)
        poller = cliente.begin_send(
            {
                "senderAddress": settings.ACS_SENDER,
                "recipients": {"to": [{"address": n.destinatario}]},
                "content": {"subject": n.asunto, "plainText": texto, **({"html": html} if html else {})},
            }
        )
        poller.result(timeout=30)
        n.estado = "enviado"
        n.enviado_at = datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("No se pudo enviar el correo %s a %s", plantilla, destinatario)
        n.estado = "error"
        n.error = f"{type(exc).__name__}: {exc}"[:2000]
    n.intentos += 1
    return n


# --- Plantillas -----------------------------------------------------------

def correo_solicitud_acceso(db: Session, *, destinatario: str, despacho, solicitante, mensaje: str | None, token: str) -> None:
    base = settings.APP_BASE_URL.rstrip("/")
    aprobar = f"{base}/aprobar-acceso?token={token}&accion=aprobar"
    rechazar = f"{base}/aprobar-acceso?token={token}&accion=rechazar"
    titulo = f"{solicitante.nombre} quiere unirse a {despacho.nombre}"
    parrafos = [
        f"<strong>{html_lib.escape(solicitante.nombre)}</strong> ({html_lib.escape(solicitante.email)}) pidió acceso a "
        f"la bóveda y los clientes de <strong>{html_lib.escape(despacho.nombre)}</strong> en KONYEKA.",
    ]
    if mensaje:
        parrafos.append(f"Su mensaje: «{html_lib.escape(mensaje)}»")
    parrafos.append("Si lo apruebas, ocupará uno de los usuarios de tu licencia. El enlace vence en 7 días.")
    texto = (
        f"{titulo}\n\n{solicitante.nombre} ({solicitante.email}) pidió acceso a {despacho.nombre}.\n"
        + (f"Mensaje: {mensaje}\n" if mensaje else "")
        + f"\nAprobar: {aprobar}\nRechazar: {rechazar}\n"
    )
    enviar(
        db, plantilla="solicitud_acceso", destinatario=destinatario, asunto=titulo, texto=texto,
        html=_html(titulo, parrafos, ("Aprobar acceso", aprobar), ("Rechazar", rechazar)), despacho_id=despacho.id,
    )


def correo_solicitud_resuelta(db: Session, *, solicitante, despacho, aprobada: bool) -> None:
    base = settings.APP_BASE_URL.rstrip("/")
    if aprobada:
        titulo = f"Ya tienes acceso a {despacho.nombre}"
        parrafos = [f"Tu acceso a <strong>{html_lib.escape(despacho.nombre)}</strong> fue aprobado. Ya puedes ver su bóveda y sus clientes."]
        boton = ("Entrar a KONYEKA", f"{base}/login")
    else:
        titulo = f"Tu solicitud a {despacho.nombre} no fue aprobada"
        parrafos = ["Si crees que es un error, habla con el responsable del despacho. También puedes contratar tu propia licencia."]
        boton = ("Ver paquetes", f"{base}/#precios")
    enviar(
        db, plantilla="solicitud_resuelta", destinatario=solicitante.email, asunto=titulo,
        texto=f"{titulo}\n\n{base}/login", html=_html(titulo, parrafos, boton), despacho_id=despacho.id,
    )


def correo_bienvenida(db: Session, *, user, despacho, dias_prueba: int) -> None:
    base = settings.APP_BASE_URL.rstrip("/")
    titulo = f"Bienvenido a KONYEKA, {user.nombre.split(' ')[0]}"
    parrafos = [
        f"Creaste el despacho <strong>{html_lib.escape(despacho.nombre)}</strong> con {dias_prueba} días de prueba y hasta 3 RFC.",
        "Primeros pasos: da de alta a tu primer cliente, sube su e.firma y descarga sus CFDI del SAT.",
        f"Para que tu equipo vea la misma bóveda, compárteles el código de tu despacho: <strong>{despacho.codigo}</strong>.",
    ]
    enviar(
        db, plantilla="bienvenida", destinatario=user.email, asunto=titulo,
        texto=f"{titulo}\n\nCódigo de tu despacho: {despacho.codigo}\n{base}/home",
        html=_html(titulo, parrafos, ("Entrar a KONYEKA", f"{base}/home")), despacho_id=despacho.id,
    )


def correo_pago_recibido(db: Session, *, destinatario: str, despacho, orden, plan_nombre: str) -> None:
    base = settings.APP_BASE_URL.rstrip("/")
    total = f"${orden.total_centavos / 100:,.2f} MXN"
    vence = orden.periodo_vence.strftime("%d/%m/%Y")
    titulo = f"Pago recibido: {plan_nombre}"
    parrafos = [
        f"Recibimos tu pago de <strong>{total}</strong> (IVA incluido) para <strong>{html_lib.escape(despacho.nombre)}</strong>.",
        f"Tu licencia queda vigente hasta el <strong>{vence}</strong>.",
        "Si pediste factura, te la enviaremos a este correo con los datos fiscales que capturaste.",
    ]
    enviar(
        db, plantilla="pago_recibido", destinatario=destinatario, asunto=titulo,
        texto=f"{titulo}\nTotal: {total}\nVigente hasta: {vence}\n{base}/home/licencia",
        html=_html(titulo, parrafos, ("Ver mi licencia", f"{base}/home/licencia")), despacho_id=despacho.id,
    )
