"""despachos, seats, licencias anuales, órdenes/pagos (Stripe), cupones,
embudo de conversión, bandeja de correos y auditoría.

Todo lo que existía antes (usuarios y clientes) pasa a un despacho interno
de ISC con licencia sin vencimiento, para que nada deje de funcionar.

Revision ID: 0010_despachos_licencias
Revises: 0009_bancos
Create Date: 2026-11-13
"""
import sqlalchemy as sa

from alembic import op

revision = "0010_despachos_licencias"
down_revision = "0009_bancos"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)

# Precios anuales SIN IVA, en centavos de MXN (se pueden cambiar en la tabla planes).
PLANES = [
    # codigo, nombre, tipo, rfcs_min, rfcs_max, usuarios, precio, destacado, orden, descripcion
    ("prueba", "Prueba gratis", "prueba", 0, 3, 1, 0, False, 0, "14 días con hasta 3 RFC"),
    ("d10", "Despacho 10", "paquete", 1, 10, 1, 449000, False, 1, "Para el contador independiente"),
    ("d20", "Despacho 20", "paquete", 11, 20, 2, 749000, True, 2, "El más elegido por despachos pequeños"),
    ("d50", "Despacho 50", "paquete", 21, 50, 3, 1399000, False, 3, "Para despachos en crecimiento"),
    ("d50p", "Despacho 50+", "paquete", 51, 100, 5, 2299000, False, 4, "Hasta 100 RFC; más con bloques adicionales"),
    ("usuario_extra", "Usuario adicional", "addon", 0, 0, 1, 129000, False, 10, "Un lugar más para tu equipo"),
    ("rfc_extra_10", "Bloque de 10 RFC", "addon", 0, 10, 0, 190000, False, 11, "Solo para Despacho 50+"),
]


def upgrade():
    op.create_table(
        "despachos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("nombre", sa.String(160), nullable=False),
        sa.Column("codigo", sa.String(20), nullable=False),
        sa.Column("dueno_id", sa.Integer(), nullable=True),
        sa.Column("usuarios_extra", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rfc_extra_bloques", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("datos_fiscales", sa.JSON(), nullable=True),
        sa.Column("interno", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("origen", sa.String(120), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_index("ix_despachos_codigo", "despachos", ["codigo"], unique=True)

    op.add_column("users", sa.Column("despacho_id", sa.Integer(), sa.ForeignKey("despachos.id"), nullable=True))
    op.add_column("users", sa.Column("rol_despacho", sa.String(10), nullable=True))
    op.add_column("users", sa.Column("telefono", sa.String(20), nullable=True))
    op.create_index("ix_users_despacho_id", "users", ["despacho_id"])
    op.create_foreign_key("fk_despachos_dueno", "despachos", "users", ["dueno_id"], ["id"])

    op.create_table(
        "despacho_solicitudes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("despacho_id", sa.Integer(), sa.ForeignKey("despachos.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mensaje", sa.Text(), nullable=True),
        sa.Column("traer_clientes", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("despacho_origen_id", sa.Integer(), sa.ForeignKey("despachos.id"), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("estado", sa.String(20), nullable=False, server_default="pendiente"),
        sa.Column("expira_at", TS, nullable=False),
        sa.Column("resuelta_por_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resuelta_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_index("ix_despacho_solicitudes_despacho_id", "despacho_solicitudes", ["despacho_id"])
    op.create_index("ix_despacho_solicitudes_user_id", "despacho_solicitudes", ["user_id"])

    planes = op.create_table(
        "planes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("codigo", sa.String(30), nullable=False, unique=True),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("tipo", sa.String(10), nullable=False),
        sa.Column("rfcs_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rfcs_max", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usuarios_incluidos", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("precio_anual_centavos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("destacado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("orden", sa.Integer(), nullable=False, server_default="0"),
    )
    op.bulk_insert(
        planes,
        [
            dict(codigo=c, nombre=n, tipo=t, rfcs_min=a, rfcs_max=b, usuarios_incluidos=u,
                 precio_anual_centavos=p, destacado=d, orden=o, descripcion=desc, activo=True)
            for c, n, t, a, b, u, p, d, o, desc in PLANES
        ],
    )

    op.create_table(
        "cupones",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("codigo", sa.String(32), nullable=False, unique=True),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("tipo", sa.String(12), nullable=False),
        sa.Column("porcentaje", sa.Integer(), nullable=True),
        sa.Column("monto_centavos", sa.Integer(), nullable=True),
        sa.Column("dias", sa.Integer(), nullable=True),
        sa.Column("plan_codigo", sa.String(30), nullable=True),
        sa.Column("planes_aplicables", sa.JSON(), nullable=True),
        sa.Column("max_canjes", sa.Integer(), nullable=True),
        sa.Column("max_por_despacho", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("solo_primera_compra", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("vigente_desde", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("vigente_hasta", TS, nullable=True),
        sa.Column("campana", sa.String(60), nullable=True),
        sa.Column("lote", sa.String(36), nullable=True),
        sa.Column("email_asignado", sa.String(255), nullable=True),
        sa.Column("estado", sa.String(10), nullable=False, server_default="activo"),
        sa.Column("creado_por", sa.String(255), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_index("ix_cupones_campana", "cupones", ["campana"])
    op.create_index("ix_cupones_lote", "cupones", ["lote"])

    op.create_table(
        "ordenes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("despacho_id", sa.Integer(), sa.ForeignKey("despachos.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("tipo", sa.String(12), nullable=False),
        sa.Column("plan_codigo", sa.String(30), nullable=False),
        sa.Column("usuarios_extra", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rfc_extra_bloques", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("periodo_inicia", TS, nullable=False),
        sa.Column("periodo_vence", TS, nullable=False),
        sa.Column("lineas", sa.JSON(), nullable=False),
        sa.Column("subtotal_centavos", sa.Integer(), nullable=False),
        sa.Column("descuento_centavos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("iva_centavos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_centavos", sa.Integer(), nullable=False),
        sa.Column("moneda", sa.String(3), nullable=False, server_default="MXN"),
        sa.Column("cupon_id", sa.Integer(), sa.ForeignKey("cupones.id"), nullable=True),
        sa.Column("estado", sa.String(12), nullable=False, server_default="pendiente"),
        sa.Column("proveedor", sa.String(10), nullable=False),
        sa.Column("stripe_session_id", sa.String(255), nullable=True, unique=True),
        sa.Column("livemode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requiere_factura", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", TS, server_default=sa.func.now()),
        sa.Column("pagada_at", TS, nullable=True),
    )
    op.create_index("ix_ordenes_despacho_id", "ordenes", ["despacho_id"])
    op.create_index("ix_ordenes_created_at", "ordenes", ["created_at"])

    op.create_table(
        "licencias",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("despacho_id", sa.Integer(), sa.ForeignKey("despachos.id"), nullable=False),
        sa.Column("plan_codigo", sa.String(30), nullable=False),
        sa.Column("rfcs_max", sa.Integer(), nullable=False),
        sa.Column("usuarios_max", sa.Integer(), nullable=False),
        sa.Column("inicia", TS, nullable=False),
        sa.Column("vence", TS, nullable=False),
        sa.Column("origen", sa.String(10), nullable=False),
        sa.Column("orden_id", sa.String(36), sa.ForeignKey("ordenes.id"), nullable=True),
        sa.Column("cupon_id", sa.Integer(), sa.ForeignKey("cupones.id"), nullable=True),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_index("ix_licencias_despacho_id", "licencias", ["despacho_id"])
    op.create_index("ix_licencias_vence", "licencias", ["vence"])

    op.create_table(
        "pagos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("orden_id", sa.String(36), sa.ForeignKey("ordenes.id"), nullable=False),
        sa.Column("proveedor", sa.String(10), nullable=False),
        sa.Column("proveedor_pago_id", sa.String(255), nullable=False),
        sa.Column("monto_centavos", sa.Integer(), nullable=False),
        sa.Column("comision_centavos", sa.Integer(), nullable=True),
        sa.Column("neto_centavos", sa.Integer(), nullable=True),
        sa.Column("balance_transaction_id", sa.String(255), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_index("ix_pagos_orden_id", "pagos", ["orden_id"])
    op.create_index("ix_pagos_proveedor_pago_id", "pagos", ["proveedor_pago_id"])

    op.create_table(
        "webhook_eventos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("proveedor", sa.String(10), nullable=False),
        sa.Column("evento_id", sa.String(255), nullable=False, unique=True),
        sa.Column("tipo", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("procesado_at", TS, nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )

    op.create_table(
        "cupon_canjes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("cupon_id", sa.Integer(), sa.ForeignKey("cupones.id"), nullable=False),
        sa.Column("despacho_id", sa.Integer(), sa.ForeignKey("despachos.id"), nullable=False),
        sa.Column("orden_id", sa.String(36), nullable=True, unique=True),
        sa.Column("descuento_centavos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cobrado_centavos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estado", sa.String(10), nullable=False, server_default="apartado"),
        sa.Column("detalle", sa.JSON(), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
        sa.Column("canjeado_at", TS, nullable=True),
        sa.Column("liberado_at", TS, nullable=True),
    )
    op.create_index("ix_cupon_canjes_cupon_id", "cupon_canjes", ["cupon_id"])
    op.create_index("ix_cupon_canjes_despacho_id", "cupon_canjes", ["despacho_id"])

    op.create_table(
        "embudo_eventos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("paso", sa.String(40), nullable=False),
        sa.Column("anonimo_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("despacho_id", sa.Integer(), nullable=True),
        sa.Column("ruta", sa.String(255), nullable=True),
        sa.Column("utm_source", sa.String(120), nullable=True),
        sa.Column("utm_medium", sa.String(120), nullable=True),
        sa.Column("utm_campaign", sa.String(120), nullable=True),
        sa.Column("referrer_host", sa.String(255), nullable=True),
        sa.Column("dispositivo", sa.String(10), nullable=True),
        sa.Column("props", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ocurrio_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_embudo_paso_fecha", "embudo_eventos", ["paso", "ocurrio_at"])
    op.create_index("ix_embudo_despacho_fecha", "embudo_eventos", ["despacho_id", "ocurrio_at"])
    op.create_index("ix_embudo_eventos_anonimo_id", "embudo_eventos", ["anonimo_id"])

    op.create_table(
        "notificaciones",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("plantilla", sa.String(60), nullable=False),
        sa.Column("destinatario", sa.String(255), nullable=False),
        sa.Column("asunto", sa.String(255), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("html", sa.Text(), nullable=True),
        sa.Column("estado", sa.String(10), nullable=False, server_default="pendiente"),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("despacho_id", sa.Integer(), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
        sa.Column("enviado_at", TS, nullable=True),
    )
    op.create_index("ix_notificaciones_destinatario", "notificaciones", ["destinatario"])

    op.create_table(
        "auditoria",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("accion", sa.String(80), nullable=False),
        sa.Column("despacho_id", sa.Integer(), nullable=True),
        sa.Column("detalle", sa.JSON(), nullable=True),
        sa.Column("created_at", TS, server_default=sa.func.now()),
    )
    op.create_index("ix_auditoria_accion", "auditoria", ["accion"])
    op.create_index("ix_auditoria_despacho_id", "auditoria", ["despacho_id"])
    op.create_index("ix_auditoria_created_at", "auditoria", ["created_at"])

    # --- Lo existente pasa al despacho interno de ISC ---------------------
    op.add_column("rfc_clients", sa.Column("despacho_id", sa.Integer(), sa.ForeignKey("despachos.id"), nullable=True))
    op.execute(
        "INSERT INTO despachos (nombre, codigo, interno, origen) "
        "VALUES ('ISC · Intelligence Software Consulting', 'ISC-INTERNO', true, 'migracion')"
    )
    op.execute("UPDATE users SET despacho_id = (SELECT id FROM despachos WHERE codigo = 'ISC-INTERNO'), rol_despacho = 'admin'")
    op.execute(
        "UPDATE users SET rol_despacho = 'dueno' WHERE id = (SELECT min(id) FROM users)"
    )
    op.execute(
        "UPDATE despachos SET dueno_id = (SELECT min(id) FROM users) WHERE codigo = 'ISC-INTERNO'"
    )
    op.execute("UPDATE rfc_clients SET despacho_id = (SELECT id FROM despachos WHERE codigo = 'ISC-INTERNO')")
    op.execute(
        "INSERT INTO licencias (despacho_id, plan_codigo, rfcs_max, usuarios_max, inicia, vence, origen, nota) "
        "SELECT id, 'd50p', 100000, 1000, now(), '2099-12-31', 'manual', 'Despacho interno de ISC' "
        "FROM despachos WHERE codigo = 'ISC-INTERNO'"
    )
    op.alter_column("rfc_clients", "despacho_id", nullable=False)
    op.create_index("ix_rfc_clients_despacho_id", "rfc_clients", ["despacho_id"])
    op.drop_index("ix_rfc_clients_rfc", table_name="rfc_clients")
    op.create_index("ix_rfc_clients_rfc", "rfc_clients", ["rfc"])
    op.create_unique_constraint("uq_rfc_clients_despacho_rfc", "rfc_clients", ["despacho_id", "rfc"])


def downgrade():
    op.drop_constraint("uq_rfc_clients_despacho_rfc", "rfc_clients", type_="unique")
    op.drop_index("ix_rfc_clients_rfc", table_name="rfc_clients")
    op.create_index("ix_rfc_clients_rfc", "rfc_clients", ["rfc"], unique=True)
    op.drop_index("ix_rfc_clients_despacho_id", table_name="rfc_clients")
    op.drop_column("rfc_clients", "despacho_id")
    for tabla in ("auditoria", "notificaciones", "embudo_eventos", "cupon_canjes", "webhook_eventos",
                  "pagos", "licencias", "ordenes", "cupones", "planes", "despacho_solicitudes"):
        op.drop_table(tabla)
    op.drop_constraint("fk_despachos_dueno", "despachos", type_="foreignkey")
    op.drop_index("ix_users_despacho_id", table_name="users")
    op.drop_column("users", "telefono")
    op.drop_column("users", "rol_despacho")
    op.drop_column("users", "despacho_id")
    op.drop_table("despachos")
