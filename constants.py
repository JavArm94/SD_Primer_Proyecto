NS_PORT = 9090

NOMBRE_PRIMARIO = "cluster.primario"       
NOMBRE_NODO = "cluster.nodo."              

HEARTBEATS_PARA_CAIDA = 3 
SEGUNDOS_ENTRE_HEARTBEATS = 1 

# topologia para red estatica: en caso de caerse el nameserver 
# se hace comunicación directa entre clientes y nodos.
# (seguramente no lo tiran pero por las dudas)
NODOS = {
    1: ("127.0.0.1", 8001),
    2: ("127.0.0.1", 8002),
    3: ("127.0.0.1", 8003),
}