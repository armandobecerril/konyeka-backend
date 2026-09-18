def get_origin_account_vault(rfc: str):
    return {
        "rfc": rfc,
        "account_status": "Activa",
        "sat_connection": "Pendiente de validación real",
        "xmls_available": 0,
        "xmls_downloaded": 0,
        "last_sync": None,
        "modules": [
            {
                "name": "XML Emitidos",
                "status": "Simulado"
            },
            {
                "name": "XML Recibidos",
                "status": "Simulado"
            },
            {
                "name": "Conciliación fiscal",
                "status": "Próximo"
            },
            {
                "name": "Alertas fiscales IA",
                "status": "Próximo"
            }
        ]
    }