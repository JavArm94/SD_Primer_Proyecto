SEGUNDOS_LIMITE_SIN_JUGAR = 40.0   # tiempo sin jugar antes de poder reclamar victoria por abandono

# Definimos cuanto puede quedar una mesa sin ninguna accion antes de que el servidor la libere solo.
# Se separan porque no interpretamos de la misma forma una partida activa (donde el reclamo por tiempo ya
# corta a los 20) que alguien esperando rival
SEGUNDOS_MESA_INACTIVA = 60.0      # JUGANDO / FINALIZADA / ESPERANDO_REVANCHA
SEGUNDOS_MESA_ESPERANDO = 180.0    # ESPERANDO (todavia no llego el rival)

#logica del juego, mesas, control de turnos, revanchas, control de versiones

class MotorTateti:
    def __init__(self):
        self.mesas = {
            1: self._mesa_vacia(),
            2: self._mesa_vacia(),
            3: self._mesa_vacia()
        }

    def _mesa_vacia(self):
        return {
            'estado': 'LIBRE',
            'j1_token': None, 'j1_nombre': None,
            'j2_token': None, 'j2_nombre': None,
            'tablero': '---------',
            'turno_de': None,
            'inicio_turno': 0.0,
            'ultima_actividad': 0.0,   # para que el servidor pueda reutilizar mesas colgadas
            'juegos_disputados': 0,
            'votos_revancha': 0,
            'version': 0
        }

    def _vaciar_mesa(self, id_mesa, hora_actual):
        # la version no tiene que volver a cero al borrarse 
        version_actual = self.mesas[id_mesa]['version']
        self.mesas[id_mesa] = self._mesa_vacia()
        self.mesas[id_mesa]['version'] = version_actual + 1
        self.mesas[id_mesa]['ultima_actividad'] = hora_actual

    def _mesas_vencidas(self, hora_actual):
        # mesas que han estado mucho tiempo sin actividad
        vencidas = []
        for id_m, mesa in self.mesas.items():
            if mesa['estado'] == 'LIBRE':
                continue
            limite = SEGUNDOS_MESA_ESPERANDO if mesa['estado'] == 'ESPERANDO' else SEGUNDOS_MESA_INACTIVA
            if (hora_actual - mesa['ultima_actividad']) > limite:
                vencidas.append(id_m)
        return vencidas

    def _hay_ganador(self, tablero):
        lineas = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
        for a, b, c in lineas:
            if tablero[a] != '-' and tablero[a] == tablero[b] == tablero[c]:
                return True
        return False

    def procesar(self, operacion, argumentos):
        if operacion == "JUGAR_NUEVO":
            nombre, token, hora_actual = argumentos[0], argumentos[1], float(argumentos[2])
            # si identificamos con el token que ya tiene una mesa en curso
            # (por ejemplo, se le corto la conexion y volvio a entrar con el mismo
            # nombre) lo reengancha en vez de sentarlo en una mesa nueva
            for id_m, mesa in self.mesas.items():
                if token in (mesa['j1_token'], mesa['j2_token']) and mesa['estado'] != 'LIBRE':
                    mesa['ultima_actividad'] = hora_actual
                    return f"RECONECTADO|{id_m}"

            for id_m, mesa in self.mesas.items():
                if mesa['estado'] == 'ESPERANDO':
                    mesa['j2_token'] = token
                    mesa['j2_nombre'] = nombre
                    mesa['estado'] = 'JUGANDO'
                    mesa['juegos_disputados'] = 1
                    mesa['inicio_turno'] = hora_actual
                    mesa['ultima_actividad'] = hora_actual
                    mesa['version'] += 1
                    return f"PARTIDA_INICIADA|{id_m}"
            for id_m, mesa in self.mesas.items():
                if mesa['estado'] == 'LIBRE':
                    mesa['j1_token'] = token
                    mesa['j1_nombre'] = nombre
                    mesa['turno_de'] = token
                    mesa['estado'] = 'ESPERANDO'
                    mesa['inicio_turno'] = hora_actual
                    mesa['ultima_actividad'] = hora_actual
                    mesa['version'] += 1
                    return f"ESPERANDO_OPONENTE|{id_m}"
            return "ERROR_SERVIDOR_LLENO"

        elif operacion == "LISTAR_MESAS":
            mesas_activas = []
            for id_m, mesa in self.mesas.items():
                if mesa['estado'] == 'JUGANDO':
                    mesas_activas.append((id_m, mesa['j1_nombre'], mesa['j2_nombre']))
            return mesas_activas

        elif operacion == "VER_MESA":
            id_mesa = int(argumentos[0])
            mesa = self.mesas.get(id_mesa)
            if mesa:
                return (mesa['tablero'], mesa['j1_nombre'], mesa['j2_nombre'], mesa['estado'], mesa['turno_de'])
            return None

        elif operacion == "JUGAR":
            token, id_mesa, pos, ficha, hora_actual = argumentos[0], int(argumentos[1]), int(argumentos[2]), argumentos[3], float(argumentos[4])
            mesa = self.mesas.get(id_mesa)

            if not mesa or mesa['estado'] != 'JUGANDO': return "ERROR_PARTIDA_INACTIVA"
            if token != mesa['turno_de']: return "ERROR_NO_ES_TU_TURNO"
            if not (0 <= pos <= 8): return "ERROR_POSICION_INVALIDA"

            tablero_lista = list(mesa['tablero'])
            if tablero_lista[pos] != '-': return "ERROR_CASILLA_OCUPADA"

            tablero_lista[pos] = ficha
            nuevo_tablero = "".join(tablero_lista)
            mesa['tablero'] = nuevo_tablero
            mesa['version'] += 1
            mesa['ultima_actividad'] = hora_actual

            if self._hay_ganador(nuevo_tablero):
                mesa['estado'] = 'FINALIZADA'
                return f"GANASTE|{nuevo_tablero}"
            elif '-' not in nuevo_tablero:
                mesa['estado'] = 'FINALIZADA'
                return f"EMPATE|{nuevo_tablero}"

            mesa['turno_de'] = mesa['j2_token'] if token == mesa['j1_token'] else mesa['j1_token']
            mesa['inicio_turno'] = hora_actual
            return f"OK|{nuevo_tablero}"

        elif operacion == "RECLAMAR_TIEMPO":
            token, id_mesa, hora_actual = argumentos[0], int(argumentos[1]), float(argumentos[2])
            mesa = self.mesas.get(id_mesa)
            if mesa and mesa['estado'] == 'JUGANDO':
                if token != mesa['turno_de'] and (hora_actual - mesa['inicio_turno']) > SEGUNDOS_LIMITE_SIN_JUGAR:
                    mesa['estado'] = 'FINALIZADA'
                    mesa['version'] += 1
                    mesa['ultima_actividad'] = hora_actual
                    return "VICTORIA_POR_ABANDONO"
            return "CONTINUA_EN_PARTIDA"

        elif operacion == "VOTAR_REVANCHA":
            token, id_mesa, hora_actual = argumentos[0], int(argumentos[1]), float(argumentos[2])
            mesa = self.mesas.get(id_mesa)
            if not mesa or mesa['estado'] == 'LIBRE':
                return "ERROR_PARTIDA_INACTIVA"
            if token not in (mesa['j1_token'], mesa['j2_token']):
                return "ERROR_NO_SOS_JUGADOR"

            if mesa['juegos_disputados'] >= 3:
                self._vaciar_mesa(id_mesa, hora_actual)
                return "SERIE_TERMINADA"

            mesa['votos_revancha'] += 1
            mesa['version'] += 1
            mesa['ultima_actividad'] = hora_actual
            if mesa['votos_revancha'] >= 2:
                mesa['estado'] = 'JUGANDO'
                mesa['tablero'] = '---------'
                mesa['juegos_disputados'] += 1
                mesa['votos_revancha'] = 0
                # sin esto la revancha arranca con el inicio_turno de la partida
                # anterior y el reclamo por tiempo salta al instante.
                mesa['turno_de'] = mesa['j1_token']
                mesa['inicio_turno'] = hora_actual
                return "REVANCHA_INICIADA"
            else:
                mesa['estado'] = 'ESPERANDO_REVANCHA'
                return "ESPERANDO_VOTO_RIVAL"

        elif operacion == "ABANDONAR":
            token, id_mesa, hora_actual = argumentos[0], int(argumentos[1]), float(argumentos[2])
            mesa = self.mesas.get(id_mesa)
            if mesa and token in (mesa['j1_token'], mesa['j2_token']):
                self._vaciar_mesa(id_mesa, hora_actual)
            return "MESA_LIBERADA"

        elif operacion == "MESAS_VENCIDAS":
            # dice que mesas estan para reciclar, sin tocar nada
            # el problema de las mesas colgadas persistia bastante
            return self._mesas_vencidas(float(argumentos[0]))

        elif operacion == "LIMPIAR_INACTIVAS":
            #la dispara el primario cada tanto y libera las mesas que quedaron
            # colgadas porque los jugadores cerraron el cliente sin avisar
            hora_actual = float(argumentos[0])
            liberadas = self._mesas_vencidas(hora_actual)
            for id_m in liberadas:
                self._vaciar_mesa(id_m, hora_actual)
            return liberadas
