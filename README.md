# Ta-Te-Ti Distribuido — Clúster Híbrido Primario-Backup

Ta-te-ti multijugador sobre un clúster de tres nodos. Un nodo atiende como primario y el resto queda como backup. Si el primario se cae, los que quedan eligen uno nuevo con el algoritmo Bully y la partida sigue donde estaba, sin perder jugadas.

## Parte 1 — Cómo ejecutarlo

### 1.1 Requisitos

- Python 3.8 o superior
- Pyro5 instalado: `pip install Pyro5`

### 1.2 Arranque rápido

Hacen falta seis terminales, todas abiertas en la raíz del proyecto: una para el Name Server, tres para los nodos y una por jugador.

**Terminal 1 — Name Server**

```bash
python -m Pyro5.nameserver -n 127.0.0.1 -p 9090
```

El Name Server sirve solo para comodidad en el primer contacto. Si lo matás, el clúster híbrido sigue funcionando gracias a la topología estática.

**Terminales 2, 3 y 4 — Los tres nodos**

```bash
python Nodo_Servidor.py
```

Te pedirá tu ID (1, 2 o 3), la IP del name server y los IDs de los otros nodos. Las IPs y puertos se asignan automáticamente desde `constants.py`. No hay que elegir quién es el primario: todos arrancan como backup y el primero que no encuentre un líder en la red se autoproclama solo, a los pocos segundos.

**Terminales 5 y 6 — Los jugadores**

```bash
python Nodo_Cliente.py
```

Ingresás la IP (por ejemplo `localhost`) y tu nombre. Entrarás al menú principal con las opciones de jugar u observar.

### 1.3 Cómo jugar

Al entrar al menú, seleccionás "1. Jugar". El sistema te ubicará en una mesa libre o te emparejará si alguien está esperando.

Dentro de la partida:

- Escribís un número del 1 al 9 para colocar tu ficha.
- Si el rival tarda más de 20 segundos, el cliente reclama la victoria por abandono solo: la consola muestra la cuenta regresiva y dispara `RECLAMAR_TIEMPO` automáticamente.
- Presionás `Ctrl+C` para salir de la observación o cerrar el programa abruptamente.

### 1.4 Desconexiones y tiempo de gracia

Si un jugador pierde la conexión durante la partida (se le cae internet, cierra la consola con `Ctrl+C`), el clúster **no** se entera ni destruye la mesa: la partida queda viva y el jugador tiene 20 segundos de gracia para volver. Si vuelve a ejecutar el cliente con **exactamente el mismo nombre**, el servidor lo reconoce por su token (MD5 del nombre) y lo reengancha a la misma mesa, con el tablero como lo dejó.

Vencido ese plazo sin que vuelva, el cliente del rival reclama automáticamente la victoria por abandono, se cierra la sesión y la mesa queda libre para otros jugadores. Y si los dos jugadores se desconectaron, el primario recicla la mesa por inactividad.

El comando `ABANDONAR` queda reservado para las salidas voluntarias: cuando el jugador contesta "n" a la revancha, o cuando cierra la sesión tras ganar por abandono. Cerrar la consola de golpe nunca cuenta como abandono voluntario, justamente para no consumir el tiempo de gracia.

### 1.5 Capacidad y cola de espera

El clúster tiene 3 mesas, de 2 jugadores cada una: 6 jugadores en partida como máximo. La asignación es automática y secuencial (los dos primeros a la mesa 1, los dos siguientes a la mesa 2, y así). Cuando las 3 mesas están ocupadas, el jugador que entra queda en cola: el cliente reintenta cada 3 segundos hasta que alguna mesa se libere, y se sale de la cola con `Ctrl+C`.

### 1.6 Inspeccionar el clúster

Matá el proceso del primario (`Ctrl+C` en su terminal) durante una partida en curso. En las terminales de los backups vas a ver cómo detectan la caída tras 3 latidos fallidos, inician la elección Bully y eligen al nuevo líder. En el cliente, la partida continúa sin errores tras una breve pausa de reconexión.
