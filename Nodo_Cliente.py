import sys, time, Pyro5.api, Pyro5.errors, lamport, hashlib, secrets
from constants import NS_PORT, NOMBRE_PRIMARIO, NODOS

CANTIDAD_DE_REINTENTOS = 5
SEGUNDOS_ENTRE_REINTENTOS = 1
SEGUNDOS_ENTRE_INTENTOS_DE_MESA = 3   # cada cuanto reintenta quien esta en cola
contador_de_pedidos = 0

# identificador distinto en cada ejecucion del cliente.
#
# sin esto el contador arrancaba de cero al reabrir el cliente y el primer
# pedido volvia a llamarse "nombre-1" que el servidor ya tenia cacheado de la
# sesion anterior
#
# en vez de procesar la reconexion devolvia la respuesta vieja
# guardada y por eso el jugador que volvia con el mismo nombre no entraba a su
# mesa 
# 
# el token del jugador sigue saliendo del nombre, asi que la reconexion
# se sigue reconociendo igual
id_de_sesion = secrets.token_hex(3)

# incrementa el contador de solicitudes - orden causal
def siguiente_request_id(nombre_cliente):
    global contador_de_pedidos
    contador_de_pedidos += 1
    return f"{nombre_cliente}-{id_de_sesion}-{contador_de_pedidos}"

# se encarga de buscar al nodo primario
# 1- intenta hacerlo a traves del nameserver (caso ideal)
# 2- si el nameserver esta caido opta por comuncación directa
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

# variables para controlar carga
# solo aplica a la funcion de observacion
# luego de X lecturas continuas las solicitudes del observador 
# se redirigen al otro backup
LECTURAS_POR_BACKUP = 5
contador_lecturas_observador = 0

def listar_backups_vivos():
    ##evalua el estado de la red viendo que backups se encuentran activos
    vivos = []
    for id_n, (ip, puerto) in NODOS.items():
        try:
            proxy = Pyro5.api.Proxy(f"PYRO:nodo.{id_n}@{ip}:{puerto}")
            proxy._pyroTimeout = 1.0
            if not proxy.es_primario():
                vivos.append(proxy)
        except Exception:
            continue  # nodo caido se lo omite
    return vivos

def enviar_lectura(ns_host, nombre_cliente, operacion, argumentos):
    #determina en caso de tratarse de acciones de observador 
    # como VER_MESA/LISTAR_MESAS a quien enviar la solicitud entre backups
    # de no haber backups es enviada al primario
    global contador_lecturas_observador
    backups_vivos = listar_backups_vivos()

    if backups_vivos:
        indice = (contador_lecturas_observador // LECTURAS_POR_BACKUP) % len(backups_vivos)
        contador_lecturas_observador += 1
        try: #intento enviar al backup correspondiente en base a la cantidad y limite de lecturas
            return backups_vivos[indice].leer(operacion, argumentos)
        except Exception:
            pass  # si justo se cayo el backup elegido dirigo al primario

    return enviar_solicitud(ns_host, nombre_cliente, operacion, argumentos)

def enviar_solicitud(ns_host, nombre_cliente, operacion, argumentos):
    # envia la solicitud y evalua reintenos + logica del reloj
    request_id = siguiente_request_id(nombre_cliente)
    intento = 1
    while intento <= CANTIDAD_DE_REINTENTOS:
        try:
            primario = buscar_primario(ns_host)
            ts = lamport.tick()
            return primario.pedido(operacion, argumentos, request_id, ts)
        except Exception:
            time.sleep(SEGUNDOS_ENTRE_REINTENTOS)
            intento += 1

    # aca si es el mismo caso que antes hacia el print de "no hay respuesta
    # del cluster": recien cuando se agotaron los 5 intentos. en el primer
    # timeout no avisamos nada porque puede ser solo la eleccion del
    # primario resolviendose sola en un par de segundos
    print("[!] No se pudo contactar al servidor tras varios intentos (puede haber una elección de primario en curso).")
    return None

LINEAS_GANADORAS = [(0, 1, 2), (3, 4, 5), (6, 7, 8),
                     (0, 3, 6), (1, 4, 7), (2, 5, 8),
                     (0, 4, 8), (2, 4, 6)]


def imprimir_tablero(t, j1=None, j2=None):
    # para mostrar, una celda vacía se ve como su número de posición (1 a 9)
    celdas = [t[i] if t[i] != '-' else str(i + 1) for i in range(9)]
    print("\n")
    if j1 or j2:
        # j1 entra primero a la mesa y por eso siempre juega con X (ver JUGAR_NUEVO en motor_juego.py)
        nombre_x = j1 if j1 else "?"
        nombre_o = j2 if j2 else "(esperando rival)"
        print(f"  {nombre_x} (X) vs {nombre_o} (O)")
    print(f"  {celdas[0]} | {celdas[1]} | {celdas[2]} ")
    print(" ---+---+---")
    print(f"  {celdas[3]} | {celdas[4]} | {celdas[5]} ")
    print(" ---+---+---")
    print(f"  {celdas[6]} | {celdas[7]} | {celdas[8]} \n")


def evaluar_resultado(tablero, j1, j2, nombre_cliente):
    # mira el tablero final (misma logica de _hay_ganador en motor_juego.py)
    # y arma el mensaje segun si el que lo esta mirando gano, perdio o empato
    for a, b, c in LINEAS_GANADORAS:
        if tablero[a] != '-' and tablero[a] == tablero[b] == tablero[c]:
            ganador = j1 if tablero[a] == 'X' else j2
            if ganador == nombre_cliente:
                return "¡Ganaste!"
            return f"Perdiste, ganó {ganador}."
    if '-' not in tablero:
        return "Empate."
    return None

if __name__ == "__main__":
    nombre_cliente = sys.argv[1] if len(sys.argv) > 1 else input("Tu nombre: ").strip()    
    # si pasamos la IP como segundo parametro la usa por si cambiamos el ns el dia de la presentacion
    ns_host = sys.argv[2] if len(sys.argv) > 2 else NODOS[sorted(NODOS)[0]][0]
    # esto es fijo a partir del nombre, generamos un token a traves del nombre
    # y si el cliente volviera a entrar con el mismo nombre
    # el servidor lo reconoce y lo reengancha en
    # su mesa en vez de tratarlo como un jugador nuevo
    # y que hayan dos lugares ocupados por el mismo jugador
    
    # mesa en la que estoy sentado ahora mismo (None si estoy en el menu)
    # sirve para avisarle al servidor si me voy con Ctrl+C, asi la mesa se
    # libera en el acto en vez de esperar a que el primario la reciclea
    # a proposito no se avisa nada al servidor si el cliente se cierra de golpe
    # Cerrar la consola es una perdida de conexion, no un abandono voluntario: la
    # mesa tiene que quedar viva para que el jugador tenga sus 20 segundos de
    # gracia y pueda reengancharse con el mismo nombre por otro lado abandonar se manda solo
    # cuando el usuario decide irse (contesta "n" a la revancha).
    mi_token = hashlib.md5(nombre_cliente.encode()).hexdigest()[:8]



    ## control de flujo de interfaz par ael usuario
    while True:
        print("\n MENÚ PRINCIPAL ")
        print("1. Jugar")
        print("2. Observar")
        print("3. Salir")
        opcion = input("Elige una opción: ").strip()
        #flujo jugador
        if opcion == "1":
            res = enviar_solicitud(ns_host, nombre_cliente, "JUGAR_NUEVO", [nombre_cliente, mi_token])

            # las 3 mesas estan ocupadas (6 jugadores) en vez de rebotar al
            # menu ponemos al cliente en una cola donde reintenta cada tantos 
            # segundos hasta que alguna mesa se libere y se sale con Ctrl+C.
            if res == "ERROR_SERVIDOR_LLENO":
                print("\nLas 3 mesas están ocupadas. Quedás en cola, esperando que se libere una.")
                print("(Ctrl+C para volver al menú)")
                try:
                    while res == "ERROR_SERVIDOR_LLENO":
                        time.sleep(SEGUNDOS_ENTRE_INTENTOS_DE_MESA)
                        print("Sigo esperando una mesa libre...", end="\r")
                        res = enviar_solicitud(ns_host, nombre_cliente, "JUGAR_NUEVO", [nombre_cliente, mi_token])
                except KeyboardInterrupt:
                    print("\nSaliste de la cola.")
                    continue

            if not res or res.startswith("ERROR"):
                print("Error:", res)
                continue   
                     
            estado_str, id_mesa_str = res.split("|") 
            id_mesa = int(id_mesa_str)
            if estado_str == "RECONECTADO": # por si volvio
                print(f"\n[+] Te reconectaste a tu mesa {id_mesa}.")
            else:
                print(f"\n[+] Ingresaste a la mesa {id_mesa}. Estado inicial: {estado_str}")
            
            ultimo_tablero = ""
            segundos_esperando = 0
            ya_vote_revancha = False
            resultado_ya_mostrado = False
            reconectando = False
            while True: ## una vez establecida la conexion con la mesa
                time.sleep(2)
                estado_mesa = enviar_solicitud(ns_host, nombre_cliente, "VER_MESA", [id_mesa])

                if not estado_mesa:
                    # si enviar_solicitud agoto los reintentos ya avisó por consola,
                    # aca solo guardamos que veniamos fallando para avisar cuando vuelva
                    reconectando = True
                    continue

                if reconectando:
                    print("[+] Conexión restablecida con el servidor.")
                    reconectando = False

                tablero, j1, j2, estado, turno_de = estado_mesa
                
                if tablero != ultimo_tablero:
                    imprimir_tablero(tablero, j1, j2)
                    ultimo_tablero = tablero

                if estado == "LIBRE":
                    print("\n[!] El rival abandonó (o la mesa se reinició). Volviendo al menú...")
                    break

                if estado == "ESPERANDO":
                    print("Esperando que se una un oponente...", end="\r")
                    continue

                if estado == "JUGANDO":
                    ya_vote_revancha = False   # arrancó una partida nueva
                    resultado_ya_mostrado = False
                    if turno_de == mi_token:
                        segundos_esperando = 0  # reseteamos el contador
                        
                        ficha = "X" if nombre_cliente == j1 else "O"
                        print(f"¡Es tu turno ({ficha})!")
                        pos = input("Ingresa posición (1-9): ").strip()
                        
                        if pos.isdigit() and 1 <= int(pos) <= 9:
                            posicion_fixeada = int(pos) - 1
                            res_jugada = enviar_solicitud(ns_host, nombre_cliente, "JUGAR", [mi_token, id_mesa, posicion_fixeada, ficha])
                            # si res_jugada da None ya se agotaron los reintentos y
                            # enviar_solicitud avisó por consola, asi que reintentamos
                            # solo en la proxima vuelta sin duplicar el mensaje aca
                            if res_jugada is not None and "ERROR" in res_jugada:
                                print("Error:", res_jugada)
                        else:
                            print("Posición inválida.")
                    else:
                        # actualiza instantáneamente
                        tiempo_restante = max(0, 40 - segundos_esperando)
                        print(f"Esperando jugada del rival... (Victoria automática en {tiempo_restante}s)   ", end="\r")
                        
                        segundos_esperando += 2
                        if segundos_esperando >= 40:
                            res_reclamo = enviar_solicitud(ns_host, nombre_cliente, "RECLAMAR_TIEMPO", [mi_token, id_mesa])
                            if res_reclamo and "VICTORIA" in res_reclamo:
                                # vencido el tiempo de gracia gana el que sigue
                                # conectado y se cierra la sesion no tenia sentido
                                # ofrecerle revancha a un rival que ya no esta asi
                                # que se libera la mesa y se vuelve al menu
                                print("\n[!] ¡Ganaste! El rival no volvió dentro de los 40 segundos de gracia.")
                                enviar_solicitud(ns_host, nombre_cliente, "ABANDONAR", [mi_token, id_mesa])
                                break
                            else:
                                segundos_esperando -= 2
                
                # ESPERANDO_REVANCHA tambien entra aca por si el rival ya voto y yo
                # todavia no entonces me toca decidir  
                # antes este estado solo imprimia
                # "esperando al rival" para los dos y la mesa quedaba trabada
                # hasta que el servidor la reciclaba
                elif estado in ("FINALIZADA", "ESPERANDO_REVANCHA"):
                    if not resultado_ya_mostrado:
                        mensaje_resultado = evaluar_resultado(tablero, j1, j2, nombre_cliente)
                        print(f"\n[!] La partida ha FINALIZADO. {mensaje_resultado}")
                        resultado_ya_mostrado = True

                    if ya_vote_revancha:
                        print("Esperando que el rival acepte la revancha...", end="\r")
                        continue

                    resp = input("¿Querés pedir revancha? (s/n): ").strip().lower()

                    if resp == 's':
                        ya_vote_revancha = True
                        res_rev = enviar_solicitud(ns_host, nombre_cliente, "VOTAR_REVANCHA", [mi_token, id_mesa])
                        if res_rev and "SERIE_TERMINADA" in res_rev:
                            print("Límite de 3 partidas alcanzado. La mesa se liberó.")
                            break
                        else:
                            print("Esperando la decisión del rival...")
                    else:
                        enviar_solicitud(ns_host, nombre_cliente, "ABANDONAR", [mi_token, id_mesa])
                        print("Abandonaste la mesa. Volviendo al menú...")
                        break
        #flujo de observador
        elif opcion == "2":
            mesas_activas = enviar_lectura(ns_host, nombre_cliente, "LISTAR_MESAS", [])
            if not mesas_activas:
                print("No hay partidas activas en este momento.")
                continue
                
            print("\nMesas en juego:")
            for id_mesas, j1, j2 in mesas_activas:
                print(f"Mesa {id_mesas}: {j1} vs {j2}")
            
            id_mesa_obs = input("ID de la mesa a observar: ").strip()
            if not id_mesa_obs.isdigit(): continue
            
            print(f"\nObservando Mesa {id_mesa_obs}. Presiona Ctrl+C para salir.")
            ultimo_tablero = ""
            resultado_mostrado_obs = False
            try:
                while True:
                    estado_mesa = enviar_lectura(ns_host, nombre_cliente, "VER_MESA", [int(id_mesa_obs)])
                    if estado_mesa:
                        tablero, j1, j2, estado, turno_de = estado_mesa
                        if tablero != ultimo_tablero:
                            imprimir_tablero(tablero, j1, j2)
                            print(f"Estado: {estado} | Jugadores: {j1} vs {j2}")
                            ultimo_tablero = tablero
                            resultado_mostrado_obs = False

                        if estado in ("FINALIZADA", "ESPERANDO_REVANCHA") and not resultado_mostrado_obs:
                            # el observador no tiene ficha propia, asi que en vez de
                            # GANASTE/PERDISTE mostramos directo quien de los dos gano
                            for a, b, c in LINEAS_GANADORAS:
                                if tablero[a] != '-' and tablero[a] == tablero[b] == tablero[c]:
                                    ganador = j1 if tablero[a] == 'X' else j2
                                    print(f"[!] Ganó {ganador}.")
                                    break
                            else:
                                if '-' not in tablero:
                                    print("[!] Empate.")
                            resultado_mostrado_obs = True

                        #si los jugadores se fueron, el observador también sale
                        if estado == "LIBRE":
                            print("\n[!] La mesa se ha liberado. Volviendo al menú...")
                            break
                    time.sleep(2)
            except KeyboardInterrupt:
                print("\nDejaste de observar.")

        elif opcion == "3":
            sys.exit(0)
