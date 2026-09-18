def simulate_sat_xml_download(rfc: str, start_date: str, end_date: str):
    return {
        "rfc": rfc,
        "period": {
            "start_date": start_date,
            "end_date": end_date
        },
        "status": "simulated",
        "message": "Descarga XML simulada correctamente",
        "xmls": [
            {
                "uuid": "SIM-XML-001",
                "type": "Emitido",
                "date": start_date,
                "total": 12500.00,
                "currency": "MXN"
            },
            {
                "uuid": "SIM-XML-002",
                "type": "Recibido",
                "date": end_date,
                "total": 8700.50,
                "currency": "MXN"
            }
        ]
    }