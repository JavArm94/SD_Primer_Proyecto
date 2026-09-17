import time, threading, hashlib, secrets, random
import Pyro5.api
import lamport
from constants import NS_PORT, NOMBRE_PRIMARIO, NODOS

NS_HOST = "127.0.0.1" # Cambialo si tu NS está en otra IP

# --- VARIABLES GLOBALES PARA LA INTERFAZ DE LOS BOTS ---
cola_espera = []
lock_cola = threading.Lock()

def buscar_primario(ns_host):
    try:
        ns = Pyro5.api.locate_ns(host=ns_host, port=NS_PORT)
        uri = ns.lookup(NOMBRE_PRIMARIO)
        return Pyro5.api.Proxy(uri) 
    except Exception:
        pass 
        
    for id_nodo, (ip, puerto) in NODOS.items():
        uri_directa = f"PYRO:nodo.{id_nodo}@{ip}:{puerto}"
        try:
            nodo = Pyro5.api.Proxy(uri_directa)
            nodo._pyroTimeout = 1.0 
            if nodo.es_primario():
                return nodo
        except Exception:
            continue 
    raise Exception("No se pudo contactar a ningún primario")

def enviar_solicitud_bot(nombre_cliente, mi_token, id_sesion, contador, operacion, argumentos):
    request_id = f"{nombre_cliente}-{id_sesion}-{contador[0]}"
    contador[0] += 1
    intento = 1
    while intento <= 3:
        try:
            primario = buscar_primario(NS_HOST)
            ts = lamport.tick()
            return primario.pedido(operacion, argumentos, request_id, ts)
        except Exception:
            time.sleep(1) 
            intento += 1 
    return None

def imprimir_cola():
    """Hilo en background que dibuja la cola en la consola cada 3 segundos"""
    while True:
        time.sleep(3)
        with lock_cola:
            if cola_espera:
                # Dibuja algo como: EN COLA ===== [Bot-7] == [Bot-8] == [Bot-9]
                grafico_cola = " == ".join([f"[{b}]" for b in cola_espera])
                print(f"\n⏳ EN COLA ===== {grafico_cola} =====\n")

def comportamiento_bot(id_bot):
    nombre = f"Bot-{id_bot}"
    mi_token = hashlib.md5(nombre.encode()).hexdigest()[:8]
    id_sesion = secrets.token_hex(3)
    contador = [1]
    
    def req(op, args):
        return enviar_solicitud_bot(nombre, mi_token, id_sesion, contador, op, args)

    while True:
        res = req("JUGAR_NUEVO", [nombre, mi_token])
        
        if not res or res == "ERROR_SERVIDOR_LLENO":
            with lock_cola:
                if nombre not in cola_espera:
                    cola_espera.append(nombre)
            time.sleep(3) # Espera antes de volver a pedir mesa
            continue
            
        # Si logró entrar, se saca de la cola visual
        with lock_cola:
            if nombre in cola_espera:
                cola_espera.remove(nombre)
                
        estado, id_mesa_str = res.split("|")
        id_mesa = int(id_mesa_str)
        print(f"[{nombre}] 🟢 Entré a la Mesa {id_mesa} ({estado}).")
        
        while True:
            time.sleep(2)
            estado_mesa = req("VER_MESA", [id_mesa])
            if not estado_mesa: continue
                
            tablero, j1, j2, estado_m, turno_de = estado_mesa
            
            if estado_m == "LIBRE":
                print(f"[{nombre}] ⚪ La mesa {id_mesa} se liberó. Saliendo...")
                break
                
            if estado_m == "JUGANDO":
                if turno_de == mi_token:
                    ficha = "X" if nombre == j1 else "O"
                    
                    # Pausa de 8 segundos para que la demo no vaya a la velocidad de la luz
                    # y te de tiempo a tirar nodos y ver el Dashboard
                    time.sleep(8)
                    
                    # MAGIA: El bot busca TODAS las posiciones vacías y elige una al azar.
                    # Esto garantiza que las 3 mesas van a jugar partidas totalmente distintas.
                    posiciones_vacias = [i for i, c in enumerate(tablero) if c == '-']
                    if posiciones_vacias:
                        pos_elegida = random.choice(posiciones_vacias)
                        req("JUGAR", [mi_token, id_mesa, pos_elegida, ficha])
                        print(f"[{nombre}] 🕹️ Jugué {ficha} en la posición {pos_elegida+1} (Mesa {id_mesa}).")
                        
            elif estado_m == "FINALIZADA":
                print(f"[{nombre}] 🏁 Partida terminada. Abandonando mesa para el próximo...")
                req("ABANDONAR", [mi_token, id_mesa])
                time.sleep(3) 
                break

if __name__ == "__main__":
    print("Iniciando Prueba de Carga (10 Bots Simulatáneos)...")
    print("========================================================\n")
    
    # Lanzamos el dibujante de la cola
    threading.Thread(target=imprimir_cola, daemon=True).start()
    
    hilos_bots = []
    for i in range(1, 11):
        t = threading.Thread(target=comportamiento_bot, args=(i,), daemon=True)
        hilos_bots.append(t)
        t.start()
        time.sleep(0.3) 
        
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nPrueba de carga finalizada.")