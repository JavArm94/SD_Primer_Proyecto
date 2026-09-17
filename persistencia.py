import json


def archivo_de(mi_id):
    return f"estado_nodo_{mi_id}.json"


def guardar_estado(mi_id, mesas, solicitudes, puntajes):
    datos = {
        "mesas": {str(id_m): mesa for id_m, mesa in mesas.items()},
        "solicitudes": solicitudes,
        "puntajes": {str(id_n): puntaje for id_n, puntaje in puntajes.items()},
    }
    try:
        with open(archivo_de(mi_id), "w", encoding="utf-8") as f:
            json.dump(datos, f)
    except Exception as e:
        print(f"[persistencia] No se pudo guardar el respaldo local: {e}")


def cargar_estado(mi_id):
    try:
        with open(archivo_de(mi_id), "r", encoding="utf-8") as f:
            datos = json.load(f)
        return {
            "mesas": {int(id_m): mesa for id_m, mesa in datos["mesas"].items()},
            "solicitudes": datos["solicitudes"],
            "puntajes": {int(id_n): p for id_n, p in datos.get("puntajes", {}).items()},
        }
    except Exception:
        return None
