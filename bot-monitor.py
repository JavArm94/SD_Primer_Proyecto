import time, os, sys
from datetime import datetime
import Pyro5.api
from constants import NODOS

NS_HOST = NODOS[sorted(NODOS)[0]][0] if len(sys.argv) == 1 else sys.argv[1]

def obtener_proxy_lectura():
    for id_n, (ip, puerto) in NODOS.items():
        try:
            proxy = Pyro5.api.Proxy(f"PYRO:nodo.{id_n}@{ip}:{puerto}")
            proxy._pyroTimeout = 1.0
            if not proxy.es_primario():
                return proxy
        except Exception:
            continue
            
    for id_n, (ip, puerto) in NODOS.items():
        try:
            proxy = Pyro5.api.Proxy(f"PYRO:nodo.{id_n}@{ip}:{puerto}")
            proxy._pyroTimeout = 1.0
            if proxy.es_primario():
                return proxy
        except Exception:
            continue
    return None

def limpiar_pantalla():
    os.system("cls" if os.name == "nt" else "clear")

def formatear_fila(tablero, inicio):
    celdas = [tablero[i] if tablero[i] != '-' else str(i + 1) for i in range(inicio, inicio+3)]
    return f" {celdas[0]} | {celdas[1]} | {celdas[2]} "

def monitor():
    print("Iniciando Monitor Global del Clúster...")
    while True:
        try:
            proxy = obtener_proxy_lectura()
            if not proxy:
                limpiar_pantalla()
                print("Buscando nodos vivos en el clúster...")
                time.sleep(2)
                continue
            
            res_lista = proxy.leer("LISTAR_MESAS", [])
            if not res_lista:
                continue
                
            mesas_data = []
            for id_mesa, j1, j2 in res_lista:
                estado_mesa = proxy.leer("VER_MESA", [id_mesa])
                if estado_mesa:
                    mesas_data.append((id_mesa, j1, j2, estado_mesa))
            
            limpiar_pantalla()
            print("=== DASHBOARD DEL CLÚSTER (Tiempo Real) ===")
            print(f"Consultando nodo: {proxy._pyroUri}")
            print(f"Última actualización: {datetime.now().strftime('%H:%M:%S')}\n")
            
            if not mesas_data:
                print("No hay mesas activas en este momento.")
            
            for id_mesa, j1, j2, estado_mesa in mesas_data:
                tablero, j1_real, j2_real, estado, turno_de = estado_mesa
                
                print(f"--- MESA {id_mesa} --- [Estado: {estado}]")
                print(f"Jugadores: {j1} vs {j2}")
                print(formatear_fila(tablero, 0))
                print("---+---+---")
                print(formatear_fila(tablero, 3))
                print("---+---+---")
                print(formatear_fila(tablero, 6))
                print("\n")
                
        except Exception as e:
            limpiar_pantalla()
            print("=== DASHBOARD DEL CLÚSTER ===")
            print(f"[!] Error de conexión: {e}")
            print("Reintentando reconexión en 2 segundos...")
            time.sleep(1) # Sumado al sleep del final, da 2 segs
            
        time.sleep(1) 

if __name__ == "__main__":
    try:
        monitor()
    except KeyboardInterrupt:
        print("\nMonitor cerrado.")