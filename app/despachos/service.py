"""Despachos, registro y lugares (seats).

Flujos:
1. Registro "nuevo despacho": crea usuario (dueño) + despacho + licencia de
   prueba. Correo de bienvenida con el código del despacho.
2. Registro "unirme a un despacho": crea el usuario SIN despacho y una
   solicitud de acceso; el dueño y los administradores reciben un correo con
   enlaces para aprobar o rechazar (deben iniciar sesión). Al aprobar, el
   usuario ocupa un lugar de la licencia (si no hay lugar, se pide comprar
   un usuario adicional).
3. Un contador que ya tiene su propio despacho (prueba o licencia individual
   pagada) también puede pedir unirse a otro: si tiene clientes, puede pedir
   "traer mis clientes"; al aprobarse, sus clientes (y su bóveda) se mueven
   al despacho destino, siempre que quepan en la licencia y no estén
   duplicados. Su despacho anterior queda inactivo. Los días pagados que le
   quedaban NO se reembolsan automáticamente (ISC puede compensarlos con un
   cupón desde el superadmin); esto se le advierte antes de enviar.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.admin.models import auditar
from app.billing import service as billing
from app.billing.models import Licencia
from app.core.security import hash_password
from app.despachos.models import ROL_ADMIN, ROL_DUENO, ROL_MIEMBRO, ROLES_GESTION, Despacho, SolicitudAcceso
from app.embudo import service as embudo
from app.notificaciones import service as correo
from app.rfc_clients.models import RfcClient
from app.users.models import User
from app.users.service import get_user_by_email

DIAS_SOLICITUD = 7
ALFABETO = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generar_codigo(db: Session) -> str:
    for _ in range(20):
        c = "KON-" + "".join(secrets.choice(ALFABETO) for _ in range(5))
        if not db.query(Despacho.id).filter(Despacho.codigo == c).first():
            return c
    raise HTTPException(500, "No se pudo generar el código del despacho.")


def despacho_de(db: Session, user: User) -> Despacho:
    if user.despacho_id is None:
        raise HTTPException(403, "Tu usuario todavía no pertenece a un despacho.")
    d = db.get(Despacho, user.despacho_id)
    if d is None:
        raise HTTPException(403, "Tu usuario todavía no pertenece a un despacho.")
    return d


def exigir_gestion(user: User) -> None:
    if user.despacho_id is None or user.rol_despacho not in ROLES_GESTION:
        raise HTTPException(403, "Solo el dueño o un administrador del despacho puede hacer esto.")


def _validar_password(password: str) -> None:
    if len(password or "") < 8:
        raise HTTPException(422, "La contraseña debe tener al menos 8 caracteres.")


def _clientes_activos(db: Session, despacho_id: int) -> int:
    return db.query(func.count(RfcClient.id)).filter(RfcClient.despacho_id == despacho_id, RfcClient.activo.is_(True)).scalar() or 0


# --------------------------------------------------------------------------
# Registro
# --------------------------------------------------------------------------

def crear_despacho(db: Session, user: User, nombre: str, origen: str | None = None) -> Despacho:
    nombre = (nombre or "").strip()
    if len(nombre) < 2:
        raise HTTPException(422, "Escribe el nombre de tu despacho.")
    d = Despacho(nombre=nombre[:160], codigo=generar_codigo(db), dueno_id=user.id, origen=(origen or None) and origen[:120])
    db.add(d)
    db.flush()
    user.despacho_id = d.id
    user.rol_despacho = ROL_DUENO
    billing.crear_prueba(db, d)
    return d


def registrar(db: Session, datos: dict) -> tuple[User, dict]:
    email = (datos.get("email") or "").strip().lower()
    nombre = (datos.get("nombre") or "").strip()
    if len(nombre) < 2:
        raise HTTPException(422, "Escribe tu nombre.")
    _validar_password(datos.get("password") or "")
    if get_user_by_email(db, email):
        raise HTTPException(409, "Ya existe una cuenta con ese correo. Inicia sesión.")
    modo = datos.get("modo") or "nuevo"
    user = User(email=email, nombre=nombre[:255], password_hash=hash_password(datos["password"]), rol="admin",
                telefono=(datos.get("telefono") or None))
    db.add(user)
    db.flush()
    origen = datos.get("utm_source") or None
    resultado: dict = {"modo": modo}
    if modo == "nuevo":
        d = crear_despacho(db, user, datos.get("despacho_nombre") or "", origen)
        resultado["despacho"] = d
    elif modo == "unirse":
        destino = _buscar_destino(db, datos.get("destino") or "")
        resultado["solicitud"] = _crear_solicitud(db, user, destino, datos.get("mensaje"), traer_clientes=False)
    else:
        raise HTTPException(422, "Modo de registro inválido.")

    embudo.registrar(db, "signup_completed", despacho_id=user.despacho_id, user_id=user.id, anonimo_id=datos.get("anonimo_id"),
                     utm={k: datos.get(f"utm_{k}") for k in ("source", "medium", "campaign")},
                     props={"modo": modo}, una_vez=True)
    if datos.get("anonimo_id"):
        embudo.coser_identidad(db, datos["anonimo_id"], despacho_id=user.despacho_id, user_id=user.id)
    if modo == "nuevo":
        correo.correo_bienvenida(db, user=user, despacho=resultado["despacho"], dias_prueba=_dias_prueba())
    auditar(db, f"user:{user.id}", f"registro.{modo}", user.despacho_id, {"email": email})
    db.commit()
    db.refresh(user)
    return user, resultado


def _dias_prueba() -> int:
    from app.core.config import settings

    return settings.PRUEBA_DIAS


# --------------------------------------------------------------------------
# Solicitudes de acceso
# --------------------------------------------------------------------------

def _buscar_destino(db: Session, destino: str) -> Despacho:
    destino = (destino or "").strip()
    if not destino:
        raise HTTPException(422, "Escribe el código del despacho o el correo de su responsable.")
    if "@" in destino:
        dueno = get_user_by_email(db, destino)
        if dueno is None or dueno.despacho_id is None or dueno.rol_despacho not in ROLES_GESTION:
            raise HTTPException(404, "No encontramos un despacho administrado por ese correo. Pídele a tu jefe el código del despacho.")
        d = db.get(Despacho, dueno.despacho_id)
    else:
        d = db.query(Despacho).filter(Despacho.codigo == destino.upper()).first()
    if d is None or not d.activo:
        raise HTTPException(404, "No encontramos ese despacho. Revisa el código (por ejemplo KON-7Q4M2).")
    return d


def _crear_solicitud(db: Session, user: User, destino: Despacho, mensaje: str | None, *, traer_clientes: bool) -> tuple[SolicitudAcceso, str]:
    if user.despacho_id == destino.id:
        raise HTTPException(409, "Ya perteneces a ese despacho.")
    db.query(SolicitudAcceso).filter(SolicitudAcceso.user_id == user.id, SolicitudAcceso.estado == "pendiente").update(
        {"estado": "cancelada", "resuelta_at": ahora()}, synchronize_session=False
    )
    token = secrets.token_urlsafe(32)
    s = SolicitudAcceso(
        despacho_id=destino.id, user_id=user.id, mensaje=(mensaje or "").strip()[:500] or None, token_hash=hash_token(token),
        estado="pendiente", expira_at=ahora() + timedelta(days=DIAS_SOLICITUD), traer_clientes=traer_clientes,
        despacho_origen_id=user.despacho_id,
    )
    db.add(s)
    db.flush()
    gestores = db.query(User).filter(User.despacho_id == destino.id, User.activo.is_(True), User.rol_despacho.in_(ROLES_GESTION)).all()
    for g in gestores:
        correo.correo_solicitud_acceso(db, destinatario=g.email, despacho=destino, solicitante=user, mensaje=s.mensaje, token=token)
    embudo.registrar(db, "join_requested", despacho_id=destino.id, user_id=user.id, props={"traer_clientes": traer_clientes})
    return s, token


def solicitar_union(db: Session, user: User, destino_txt: str, mensaje: str | None, traer_clientes: bool) -> SolicitudAcceso:
    destino = _buscar_destino(db, destino_txt)
    if user.despacho_id is not None:
        if user.rol_despacho != ROL_DUENO:
            raise HTTPException(409, "Ya perteneces a un despacho. Pide a su dueño que te dé de baja antes de unirte a otro.")
        otros = db.query(func.count(User.id)).filter(User.despacho_id == user.despacho_id, User.id != user.id, User.activo.is_(True)).scalar()
        if otros:
            raise HTTPException(409, "Tu despacho tiene más usuarios: transfiere la titularidad a alguien más antes de unirte a otro.")
        if _clientes_activos(db, user.despacho_id) and not traer_clientes:
            raise HTTPException(
                409, "Tu despacho tiene clientes. Marca «Llevar mis clientes» para moverlos al despacho al que te unes, o dalos de baja primero."
            )
    s, _ = _crear_solicitud(db, user, destino, mensaje, traer_clientes=traer_clientes and user.despacho_id is not None)
    auditar(db, f"user:{user.id}", "solicitud.creada", destino.id, {"solicitud": s.id, "traer_clientes": s.traer_clientes})
    db.commit()
    db.refresh(s)
    return s


def cancelar_mi_solicitud(db: Session, user: User) -> None:
    db.query(SolicitudAcceso).filter(SolicitudAcceso.user_id == user.id, SolicitudAcceso.estado == "pendiente").update(
        {"estado": "cancelada", "resuelta_at": ahora()}, synchronize_session=False
    )
    db.commit()


def mi_solicitud(db: Session, user: User) -> SolicitudAcceso | None:
    return (
        db.query(SolicitudAcceso)
        .filter(SolicitudAcceso.user_id == user.id)
        .order_by(SolicitudAcceso.created_at.desc(), SolicitudAcceso.id.desc())
        .first()
    )


def solicitud_por_token(db: Session, token: str) -> SolicitudAcceso:
    s = db.query(SolicitudAcceso).filter(SolicitudAcceso.token_hash == hash_token(token or "")).first()
    if s is None:
        raise HTTPException(404, "El enlace no es válido.")
    return s


def resolver(db: Session, aprobador: User, s: SolicitudAcceso, aprobar: bool) -> SolicitudAcceso:
    exigir_gestion(aprobador)
    if aprobador.despacho_id != s.despacho_id:
        raise HTTPException(403, "Esta solicitud es para otro despacho. Inicia sesión con la cuenta del responsable de ese despacho.")
    s = db.query(SolicitudAcceso).filter(SolicitudAcceso.id == s.id).with_for_update().one()
    if s.estado != "pendiente":
        raise HTTPException(409, f"Esta solicitud ya está {s.estado}.")
    if _aware(s.expira_at) <= ahora():
        s.estado = "cancelada"
        db.commit()
        raise HTTPException(410, "La solicitud venció. Pide a la persona que la envíe de nuevo.")
    destino = db.query(Despacho).filter(Despacho.id == s.despacho_id).with_for_update().one()
    solicitante = db.get(User, s.user_id)
    if not aprobar:
        s.estado, s.resuelta_por_id, s.resuelta_at = "rechazada", aprobador.id, ahora()
        correo.correo_solicitud_resuelta(db, solicitante=solicitante, despacho=destino, aprobada=False)
        auditar(db, f"user:{aprobador.id}", "solicitud.rechazada", destino.id, {"solicitud": s.id, "user": solicitante.id})
        db.commit()
        return s

    billing.exigir_cupo_usuario(db, destino)
    origen_id = solicitante.despacho_id
    if origen_id is not None and origen_id != s.despacho_origen_id:
        raise HTTPException(409, "La situación del solicitante cambió desde que envió la solicitud. Pídele que la envíe de nuevo.")
    if origen_id is not None:
        clientes = db.query(RfcClient).filter(RfcClient.despacho_id == origen_id).all()
        activos = [c for c in clientes if c.activo]
        if activos and not s.traer_clientes:
            raise HTTPException(409, "El solicitante tiene clientes en su despacho y no pidió traerlos.")
        if s.traer_clientes and clientes:
            rfcs_destino = {r for (r,) in db.query(RfcClient.rfc).filter(RfcClient.despacho_id == destino.id).all()}
            duplicados = sorted(c.rfc for c in clientes if c.rfc in rfcs_destino)
            if duplicados:
                raise HTTPException(
                    409, "Estos RFC ya existen en tu despacho: " + ", ".join(duplicados[:10])
                    + ". Pide al solicitante que los dé de baja o que no traiga sus clientes."
                )
            lic = billing.exigir_licencia_activa(db, destino.id)
            rfcs_max, _ = billing.limites(lic, destino)
            if billing.rfcs_activos(db, destino.id) + len(activos) > rfcs_max:
                raise HTTPException(
                    402, f"Con sus {len(activos)} clientes pasarías el límite de {rfcs_max} RFC de tu licencia. Sube de paquete primero."
                )
            for c in clientes:
                c.despacho_id = destino.id
        origen = db.get(Despacho, origen_id)
        if origen is not None:
            origen.activo = False
            if origen.dueno_id == solicitante.id:
                origen.dueno_id = None
            # La licencia del despacho que se cierra termina hoy.
            db.query(Licencia).filter(Licencia.despacho_id == origen.id, Licencia.vence > ahora()).update(
                {"vence": ahora()}, synchronize_session=False
            )
    solicitante.despacho_id = destino.id
    solicitante.rol_despacho = ROL_MIEMBRO
    s.estado, s.resuelta_por_id, s.resuelta_at = "aprobada", aprobador.id, ahora()
    correo.correo_solicitud_resuelta(db, solicitante=solicitante, despacho=destino, aprobada=True)
    embudo.registrar(db, "member_joined", despacho_id=destino.id, user_id=solicitante.id, props={"traer_clientes": s.traer_clientes})
    auditar(db, f"user:{aprobador.id}", "solicitud.aprobada", destino.id,
            {"solicitud": s.id, "user": solicitante.id, "despacho_origen": origen_id, "traer_clientes": s.traer_clientes})
    db.commit()
    return s


def pendientes(db: Session, despacho_id: int) -> list[tuple[SolicitudAcceso, User]]:
    return (
        db.query(SolicitudAcceso, User)
        .join(User, User.id == SolicitudAcceso.user_id)
        .filter(SolicitudAcceso.despacho_id == despacho_id, SolicitudAcceso.estado == "pendiente", SolicitudAcceso.expira_at > ahora())
        .order_by(SolicitudAcceso.created_at)
        .all()
    )


# --------------------------------------------------------------------------
# Miembros
# --------------------------------------------------------------------------

def miembros(db: Session, despacho_id: int) -> list[User]:
    return db.query(User).filter(User.despacho_id == despacho_id).order_by(User.nombre).all()


def cambiar_rol(db: Session, actor: User, user_id: int, rol: str) -> User:
    exigir_gestion(actor)
    if rol not in (ROL_DUENO, ROL_ADMIN, ROL_MIEMBRO):
        raise HTTPException(422, "Rol inválido.")
    u = db.get(User, user_id)
    if u is None or u.despacho_id != actor.despacho_id:
        raise HTTPException(404, "Usuario no encontrado.")
    d = db.get(Despacho, actor.despacho_id)
    if rol == ROL_DUENO or u.rol_despacho == ROL_DUENO:
        if actor.rol_despacho != ROL_DUENO:
            raise HTTPException(403, "Solo el dueño puede transferir la titularidad.")
        if rol != ROL_DUENO:
            raise HTTPException(422, "Para dejar de ser dueño, transfiere la titularidad a otro usuario.")
        actor.rol_despacho = ROL_ADMIN
        d.dueno_id = u.id
    u.rol_despacho = rol
    auditar(db, f"user:{actor.id}", "miembro.rol", d.id, {"user": u.id, "rol": rol})
    db.commit()
    db.refresh(u)
    return u


def quitar_miembro(db: Session, actor: User, user_id: int) -> None:
    exigir_gestion(actor)
    u = db.get(User, user_id)
    if u is None or u.despacho_id != actor.despacho_id:
        raise HTTPException(404, "Usuario no encontrado.")
    if u.rol_despacho == ROL_DUENO:
        raise HTTPException(422, "No puedes quitar al dueño del despacho.")
    if u.id == actor.id:
        raise HTTPException(422, "No puedes quitarte a ti mismo.")
    u.despacho_id = None
    u.rol_despacho = None
    auditar(db, f"user:{actor.id}", "miembro.baja", actor.despacho_id, {"user": u.id})
    db.commit()


def actualizar(db: Session, actor: User, datos: dict) -> Despacho:
    exigir_gestion(actor)
    d = despacho_de(db, actor)
    if datos.get("nombre") is not None:
        nombre = datos["nombre"].strip()
        if len(nombre) < 2:
            raise HTTPException(422, "Escribe el nombre del despacho.")
        d.nombre = nombre[:160]
    if datos.get("datos_fiscales") is not None:
        d.datos_fiscales = datos["datos_fiscales"]
    db.commit()
    db.refresh(d)
    return d
