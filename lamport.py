import threading 
 

reloj_logico = 0 
lock = threading.Lock() 

#logica del reloj de lamport para poder determinar
#el orden causal de los procesos


def tick():
    global reloj_logico
    with lock:
        reloj_logico += 1 
        return reloj_logico

def actualizar(recibido):
    global reloj_logico
    with lock:      
        reloj_logico = max(reloj_logico, recibido) + 1 
        return reloj_logico