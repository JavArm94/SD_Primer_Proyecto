import threading, time, Pyro5.api, Pyro5.errors, lamport, random
import persistencia
from constants import NS_PORT, NOMBRE_PRIMARIO, NODOS, HEARTBEATS_PARA_CAIDA, SEGUNDOS_ENTRE_HEARTBEATS
from motor_juego import MotorTateti

# distinción para no replicar,cachear o loguear esas operaciones.
OPERACIONES_DE_LECTURA = ["LISTAR_MESAS", "VER_MESA"]

SEGUNDOS_ENTRE_LIMPIEZAS = 10   # cada cuanto el primario busca mesas colgadas


def normalizar_mesas(mesas):
    #esto es porque nos estaba dando drama str e int
    return {int(id_m): mesa for id_m, mesa in mesas.items()}


@Pyro5.api.expose
class NodoDelCluster:
    def __init__(self, mi_id, ids_de_otros_nodos, ns_host):
        self.mi_id = mi_id
        self.otros_nodos = ids_de_otros_nodos   
        self.ns_host = ns_host
        self.rol = "BACKUP"                  
        self.id_primario = None 
        self.lock = threading.Lock()
        self.solicitudes_aplicadas = {}
        self.motor = MotorTateti()
        self.sincronizado = False
        self.contador_limpiezas = 0   # para armar un request_id unico por limpieza
        # puntaje de confiabilidad: arranca en ID+10 (asi el ID pesa al
        # principio - ¿quizas ID*2 para que tenga mas peso el ID?) 
        # y baja 1 punto cada vez que a ese nodo se lo detecta
        # caído lo usamos para desempatar 
        
        # criterio para la elección: un nodo con ID alto
        # que se cae seguido termina perdiendo contra uno más confiable
        # aunque tenga ID más bajo
        self.puntajes = {n: n + 10 for n in [mi_id] + list(ids_de_otros_nodos)}
        # guarda para no arrancar dos elecciones en paralelo en el mismo nodo:
        # ahora eleccion() puede disparar una, y el latido puede disparar otra
        # casi al mismo tiempo.
        self.eleccion_en_curso = False
        self.lock_eleccion = threading.Lock()
        self.ultimo_evento = "Inicialización"

    @Pyro5.api.expose
    def es_primario(self):
        return self.rol == "PRIMARIO"

    @Pyro5.api.expose
    def dump_estado(self):
        return {
            "mesas": self.motor.mesas,
            "solicitudes": self.solicitudes_aplicadas,
            "puntajes": self.puntajes,
            "ts": lamport.tick()
        }
    
    # guardamos en un json auxiliar para sobrevivr ante la caida total del sistema
    def guardar_respaldo_local(self):
        persistencia.guardar_estado(self.mi_id, self.motor.mesas, self.solicitudes_aplicadas, self.puntajes)
    
    
    def fusionar_puntajes(self, puntajes_remotos):
        # se toma siempre el más bajo conocido para cada nodo: si alguien ya
        # vio caer a un nodo, ese punto de vista "gana" al de quien todavía
        # no se enteró
        for id_n, puntaje_remoto in puntajes_remotos.items():
            id_n = int(id_n)
            actual = self.puntajes.get(id_n, id_n + 10)
            self.puntajes[id_n] = min(actual, puntaje_remoto)

    # solicitamos al primario el estado del sistema
    # para sincronizar en caso de incorporarnos a la red
    # + replicas
    def sincronizar_con_primario(self, uri_primario):
        try:
            estado = Pyro5.api.Proxy(uri_primario).dump_estado()
            with self.lock:
                self.motor.mesas = normalizar_mesas(estado["mesas"])
                self.solicitudes_aplicadas = estado["solicitudes"]
                self.fusionar_puntajes(estado.get("puntajes", {}))
            lamport.actualizar(estado["ts"])
            self.guardar_respaldo_local()
            print(f"[{self.mi_id}] Estado sincronizado con el primario.")
        except Exception as e:
            print(f"[{self.mi_id}] Error al sincronizar: {e}")

    def recuperar_estado_del_cluster(self):
        # para cuando se asume como primario
        # comprobamos el estado mas nuevo del sistema en el cluster
        # como usamos bully por ID/puntaje puede ser que el primario
        # que es seleccionado no tenga la ultima jugada
        # ej: el primario responde a una jugada y pierde la eleccion
        # si nadie contesta levanto del json
    
        alguien_respondio = False
        for otro_id in self.otros_nodos:
            try:
                ip, puerto = NODOS[otro_id]
                proxy = Pyro5.api.Proxy(f"PYRO:nodo.{otro_id}@{ip}:{puerto}")
                proxy._pyroTimeout = 1.5
                estado = proxy.dump_estado()
            except Exception:
                continue
            alguien_respondio = True
            with self.lock:
                for id_m, mesa_remota in normalizar_mesas(estado["mesas"]).items():
                    mesa_local = self.motor.mesas.get(id_m)
                    # compruebo si la mesa que fetchie es mayor a mi version y actualizo
                    if mesa_local is None or mesa_remota.get('version', 0) > mesa_local.get('version', 0):
                        self.motor.mesas[id_m] = mesa_remota
                self.solicitudes_aplicadas.update(estado["solicitudes"])
                self.fusionar_puntajes(estado.get("puntajes", {}))
            lamport.actualizar(estado["ts"])

        if not alguien_respondio:
            estado_local = persistencia.cargar_estado(self.mi_id)
            if estado_local:
                with self.lock:
                    self.motor.mesas = normalizar_mesas(estado_local["mesas"])
                    self.solicitudes_aplicadas = estado_local["solicitudes"]
                    self.fusionar_puntajes(estado_local["puntajes"])
                print(f"[{self.mi_id}] Nadie del clúster contestó: restauro mi respaldo local "
                      f"({persistencia.archivo_de(self.mi_id)}).")

        self.guardar_respaldo_local()
        print(f"[{self.mi_id}] Estado recuperado del clúster antes de asumir como primario.")

    def pedido(self, operacion, argumentos, request_id, reloj_cliente):
        lamport.actualizar(reloj_cliente)
        if self.rol != "PRIMARIO":
            raise Exception(f"Nodo {self.mi_id} no es el primario")
        self.ultimo_evento = f"Pedido cliente ({operacion})"
        with self.lock:
            if request_id in self.solicitudes_aplicadas:
                return self.solicitudes_aplicadas[request_id]

            ts = lamport.tick()

            # las lecturas no cambian nada: se responden y listo
            # no se cachean ni se replican
            # antes se guardaban en solicitudes_aplicadas y como
            # cada cliente pide VER_MESA cada 2 segundos el diccionario (y por lo
            # tanto el JSON del primario) crecia para siempre.
            # 
            # esa era la razon de que el archivo del primario
            # fuera mucho mas grande que el de los backups.
            if operacion in OPERACIONES_DE_LECTURA:
                return self.motor.procesar(operacion, argumentos)

            # a todas las escrituras se les agrega la hora del primario para que
            # los backups apliquen exactamente el mismo valor al replicar.
            argumentos = list(argumentos)
            argumentos.append(time.time())

            resultado = self.motor.procesar(operacion, argumentos)
            self.solicitudes_aplicadas[request_id] = resultado

        # la replicacion sale del lock a proposito: son llamadas de red que pueden
        # tardar hasta 1.5s por backup caido
        # si se hicieran con el lock tomado todos los demas clientes (incluidos los que solo miran el tablero)
        # quedarian esperando ese tiempo
        # el estado ya se modifico arriba en la zona critica
        # asi que aca solo se esta avisando a los backups.
        try:
            self.replicar_en_secundarios(operacion, argumentos, ts, request_id, resultado)
            print(f"[{self.mi_id}] {operacion} aplicada y replicada (req {request_id}, Lamport {ts}).")
        except Exception as e:
            # si no se junta la mayoria de ACKs, el primario sigue andando igual
            # si despues vuelve se reincorporan y continuamos sin notificar al cliente
            print(f"[{self.mi_id}] Sin respaldo por ahora ({e}). Sigo funcionando en solitario.")

        self.guardar_respaldo_local()
        return resultado

    def replicar_en_secundarios(self, operacion, argumentos, ts, request_id, resultado):
        # si es de lectura, no replicamos
        if operacion in ["LISTAR_MESAS", "VER_MESA", "RECUPERAR_PARTIDA"]:
            return True 

        # umbral de ACKs de la replicacion sincronica primario-backup: el
        # primario ya cuenta como una confirmacion, y pide que la mayoria del
        # cluster (la mitad mas uno) confirme antes de darle el OK al cliente.
        #
        # es un parametro de la replicacion primario-backup: el primario sigue siendo el unico que
        # decide el orden y aplica antes de reenviar; los backups solo copian.
        acks_recibidos = 1
        acks_para_confirmar = len(NODOS) // 2 + 1

        # iteramos sobre los backups
        for otro_id in self.otros_nodos:
            try:
                ip, puerto = NODOS[otro_id]
                proxy = Pyro5.api.Proxy(f"PYRO:nodo.{otro_id}@{ip}:{puerto}")
                proxy._pyroTimeout = 1.5  # si el nodo está caído, cortamos rápido
                proxy.replicar(operacion, argumentos, ts, request_id, resultado)
                
                # si no salto una excepcion la replica fue exitosa
                acks_recibidos += 1
            except Exception as e:
                print(f"[{self.mi_id}] Backup {otro_id} inalcanzable: {e}")

        # verificamos si juntamos la mayoria de ACKs
        print(f"[{self.mi_id}] Confirmaciones de {operacion}: {acks_recibidos}/{acks_para_confirmar} (ACKs recibidos/necesarios).")
        if acks_recibidos < acks_para_confirmar:
            print(f"[{self.mi_id}] ¡ATENCIÓN! No se alcanzó la mayoría de ACKs ({acks_recibidos}/{acks_para_confirmar}).")
            raise Exception("No se alcanzó la mayoría de confirmaciones en el clúster.")
            
        return True

    @Pyro5.api.expose
    def replicar(self, operacion, argumentos, ts, request_id, resultado):
        lamport.actualizar(ts)
        self.ultimo_evento = f"Réplica ({operacion})"
        with self.lock:
            if request_id in self.solicitudes_aplicadas:
                print(f"[{self.mi_id}] Replica repetida de {request_id}, ya la tenia. La ignoro.")
                return True
            self.motor.procesar(operacion, argumentos)
            self.solicitudes_aplicadas[request_id] = resultado
        self.guardar_respaldo_local()
        print(f"[{self.mi_id}] Replique {operacion} del primario (req {request_id}, Lamport {ts}).")
        return True

    def ping(self):
        lamport.tick()
        return "OK"

    def intentar_descubrir_primario(self):
        # para evitar llamar a votacion con un primario existente al entrar a la red
        for otro_id in self.otros_nodos:
            try:
                ip, puerto = NODOS[otro_id]
                proxy = Pyro5.api.Proxy(f"PYRO:nodo.{otro_id}@{ip}:{puerto}")
                proxy._pyroTimeout = 1.0
                if proxy.es_primario():
                    self.id_primario = otro_id
                    print(f"[{self.mi_id}] Descubrí que el primario es el nodo {otro_id}.")
                    self.sincronizar_con_primario(f"PYRO:nodo.{otro_id}@{ip}:{puerto}")
                    self.sincronizado = True
                    return True
            except Exception:
                continue
        return False

    @Pyro5.api.expose
    def leer(self, operacion, argumentos):
        # devuelve lectura desde cualquier nodo (primario o backup) puede responder con su
        # propio estado replicado sin pasar por el rol de primario ni por el umbral de ACKs
        if operacion not in ("VER_MESA", "LISTAR_MESAS"):
            return "ERROR_OPERACION_NO_PERMITIDA_EN_LECTURA"
        lamport.tick()
        self.ultimo_evento = f"Lectura cliente ({operacion})"
        with self.lock:
            return self.motor.procesar(operacion, argumentos)

    def fijarse_si_el_primario_esta_vivo(self):
        fallos = 0
        while True:
            time.sleep(SEGUNDOS_ENTRE_HEARTBEATS)
            if self.rol == "PRIMARIO": continue
            
            if self.id_primario is None:
                if self.intentar_descubrir_primario():
                    fallos = 0
                    continue
                fallos += 1
                if fallos >= HEARTBEATS_PARA_CAIDA:
                    print(f"[{self.mi_id}] No hay primario conocido. Inicio elección inicial.")
                    fallos = 0
                    self.sincronizado = False
                    self.disparar_algoritmo_bully()
                continue
                
            try:
                ip, puerto = NODOS[self.id_primario]
                proxy = Pyro5.api.Proxy(f"PYRO:nodo.{self.id_primario}@{ip}:{puerto}")
                proxy._pyroTimeout = 1.0
                proxy.ping()
                
                if fallos > 0 or not getattr(self, "sincronizado", False):
                    uri_primario = f"PYRO:nodo.{self.id_primario}@{ip}:{puerto}"
                    self.sincronizar_con_primario(uri_primario)
                    self.sincronizado = True

                fallos = 0
            except Exception:
                fallos += 1
                if fallos >= HEARTBEATS_PARA_CAIDA:
                    print(f"[{self.mi_id}] ¡El primario {self.id_primario} se cayo. Inicio eleccion.")
                    self.puntajes[self.id_primario] = self.puntajes.get(self.id_primario, self.id_primario + 10) - 1
                    print(f"[{self.mi_id}] Puntaje del nodo {self.id_primario} baja a "
                          f"{self.puntajes[self.id_primario]} por la caída.")
                    fallos = 0
                    self.id_primario = None 
                    self.sincronizado = False
                    self.disparar_algoritmo_bully()
                    
    def reciclar_mesas_inactivas(self):
        # cada tanto se liberan mesas por el primario y
        # eso es replicado como una solicitud para evitar
        # que cada nodo libere mesas en tiempos distintos
        # y nos genere inconsistencias
        while True:
            time.sleep(SEGUNDOS_ENTRE_LIMPIEZAS)
            if self.rol != "PRIMARIO":
                continue
            try:
                # primero se mira si hay algo para limpiar
                with self.lock:
                    hay_vencidas = bool(self.motor.procesar("MESAS_VENCIDAS", [time.time()]))
                if not hay_vencidas:
                    continue

                self.contador_limpiezas += 1
                request_id = f"limpieza-{self.mi_id}-{self.contador_limpiezas}"
                liberadas = self.pedido("LIMPIAR_INACTIVAS", [], request_id, lamport.tick())
                print(f"[{self.mi_id}] Mesas liberadas por inactividad: {liberadas}")
            except Exception as e:
                print(f"[{self.mi_id}] Error al reciclar mesas: {e}")

    def mostrar_estado(self):
        """Hilo informativo: cada tanto muestra en que anda el nodo."""
        while True:
            time.sleep(SEGUNDOS_ENTRE_LIMPIEZAS)
            with self.lock:
                ocupadas = [id_m for id_m, m in self.motor.mesas.items() if m['estado'] != 'LIBRE']
                cacheadas = len(self.solicitudes_aplicadas)
            print(f"[{self.mi_id}] {self.rol} | primario={self.id_primario} | "
                  f"mesas ocupadas={ocupadas} | escrituras en cache={cacheadas} | "
                  f"Lamport={lamport.tick()} | último evento={self.ultimo_evento}")

    def registrarme_como_primario(self):
        try:
            ns = Pyro5.api.locate_ns(host=self.ns_host, port=NS_PORT)
            try: ns.remove(NOMBRE_PRIMARIO)
            except: pass
            ns.register(NOMBRE_PRIMARIO, self.mi_uri)
        except Exception:
            print(f"[{self.mi_id}] Soy Primario, pero el ns esta caido. Buscado por IP.")
        
    @Pyro5.api.expose
    def eleccion(self, id_emisor):
        lamport.tick()
        print(f"[{self.mi_id}] El nodo {id_emisor} me consulta por la elección, le respondo OK.")
        if self.rol != "PRIMARIO":
            threading.Thread(target=self.disparar_algoritmo_bully, daemon=True).start()
        return "OK"

    @Pyro5.api.expose
    def coordinador_elegido(self, nuevo_primario_id):
        print(f"[{self.mi_id}] El nodo {nuevo_primario_id} ganó la elección.")
        with self.lock: 
            self.rol = "BACKUP"
            self.id_primario = nuevo_primario_id 
        return "OK"

    def rango(self, id_nodo):
        #devuelve el valor del puntaje de reputacion
        #y la id por si tenemos que desempatar
        return (self.puntajes.get(id_nodo, id_nodo + 10), id_nodo)

    def disparar_algoritmo_bully(self):
        with self.lock_eleccion:
            if self.eleccion_en_curso:
                print(f"[{self.mi_id}] Ya tengo una elección en curso, no arranco otra.")
                return
            self.eleccion_en_curso = True

        try:
            time.sleep(random.uniform(0.5, 2.0))

            mi_rango = self.rango(self.mi_id)
            hay_nodo_mayor = False
            for otro_id in self.otros_nodos:
                if self.rango(otro_id) > mi_rango:
                    try:
                        ip, puerto = NODOS[otro_id]
                        proxy = Pyro5.api.Proxy(f"PYRO:nodo.{otro_id}@{ip}:{puerto}")
                        proxy._pyroTimeout = 2.0
                        if proxy.eleccion(self.mi_id) == "OK":
                            hay_nodo_mayor = True
                    except:
                        pass

            if not hay_nodo_mayor:
                print(f"[{self.mi_id}] ¡Me autoproclamo PRIMARIO! (puntaje {mi_rango[0]})")
                self.recuperar_estado_del_cluster()
                with self.lock: 
                    self.rol = "PRIMARIO"
                    self.id_primario = self.mi_id 
                self.registrarme_como_primario()
                for otro_id in self.otros_nodos:
                    try:
                        ip, puerto = NODOS[otro_id]
                        Pyro5.api.Proxy(f"PYRO:nodo.{otro_id}@{ip}:{puerto}").coordinador_elegido(self.mi_id)
                    except: pass
            else:
                print(f"[{self.mi_id}] Un nodo más confiable respondió, cedo la elección.")
        finally:
            with self.lock_eleccion:
                self.eleccion_en_curso = False

def main():
    mi_id = int(input("Tu id (ej 1, 2, 3): "))
    ip = NODOS[mi_id][0]
    puerto = NODOS[mi_id][1]
    ns_host = input("IP del name server: ")
    texto_otros = input("Ids de los otros nodos separados por coma (ej 2,3): ")
    otros_nodos = [int(x) for x in texto_otros.split(",") if x.strip() != ""]
    
    nodo = NodoDelCluster(mi_id, otros_nodos, ns_host)
    daemon = Pyro5.api.Daemon(host=ip, port=puerto)
    
    uri = daemon.register(nodo, objectId=f"nodo.{mi_id}")
    nodo.mi_uri = uri

    try:
        ns = Pyro5.api.locate_ns(host=ns_host, port=NS_PORT)
        ns.register(f"cluster.nodo.{mi_id}", uri)
    except:
        pass

    
    print(f"[{mi_id}] Arranqué como BACKUP. Buscando red...")

    # establecemos un hilo para que siempre se corra la comprobacion de vida del primario
    threading.Thread(target=nodo.fijarse_si_el_primario_esta_vivo, daemon=True).start()
    # el primario recicla las mesas que quedaron colgadas sin jugadores
    threading.Thread(target=nodo.reciclar_mesas_inactivas, daemon=True).start()
    # latido informativo para ver por consola que el nodo esta trabajando
    threading.Thread(target=nodo.mostrar_estado, daemon=True).start()
    print(f"[{mi_id}] Escuchando peticiones en {uri}...")
    # escucho los request permanentemente en el hilo principal
    daemon.requestLoop()

if __name__ == "__main__":
    main()