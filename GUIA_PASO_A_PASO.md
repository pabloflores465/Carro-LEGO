# Guía paso a paso — Clasificador de paquetes LEGO NXT + QR

## Cómo funciona el sistema

Hay dos tipos de QR en el sistema:

| QR | Dónde va | Para qué sirve |
|----|----------|----------------|
| **QR del paquete** | Pegado encima del paquete | Le dice al robot a qué destino llevarlo |
| **QR del suelo** | En el suelo en cada agujero | El robot lo usa para saber cuándo parar |

Ambos QR tienen el **mismo contenido** (ej. `QR1`). El paquete dice a dónde va, y el QR del suelo es la señal de parada.

### Flujo completo

```
1. Paquete entra a la estación con "QR1" pegado encima
2. Cámara fija lee el QR del paquete → destino: agujero 1
3. Robot arranca despacio hacia adelante
4. Cámara lee QRs del suelo mientras avanza
5. Ve "QR1" en el suelo → sabe que está cerca
6. Sigue avanzando → "QR1" desaparece (robot lo tapa) → PARA
7. Motor C inclina la plataforma → objeto cae al agujero
8. Robot regresa exactamente la misma distancia (tacómetro)
9. Listo para el siguiente paquete
```

**No necesitas medir distancias ni calibrar posiciones.** El QR del suelo es la referencia.

---

## Requisitos antes de empezar

### Hardware necesario
- 1 robot LEGO MINDSTORMS **NXT** (el brick naranja/gris)
- 1 cable USB mini-B (el que viene con el NXT)
- 2 motores grandes (Motor A y Motor B → llantas)
- 1 motor mediano (Motor C → plataforma giratoria)
- 1 cámara USB
- 1 computadora con Python 3.10+

### Hardware NO necesario
- ~~MicroSD~~ — el NXT se controla directo desde la PC por USB
- ~~Instalar nada en el brick~~ — no se toca el NXT internamente

---

## Paso 1 — Instalar dependencias

```bash
# 1. Instalar libzbar (lee QR más confiablemente)
#    macOS:
brew install zbar
#    Ubuntu/Debian:
sudo apt install libzbar0
#    Windows: no se necesita paso extra

# 2. Instalar paquetes de Python
pip install -r requirements.txt
```

Verifica que el NXT se detecta con el cable USB conectado:
```python
import nxt.locator
brick = nxt.locator.find()
print("NXT encontrado:", brick.get_device_info())
```
Si imprime el nombre del brick, todo está bien. Si da error, revisa la sección de solución de problemas al final.

---

## Paso 2 — Conectar los motores al NXT

| Motor | Puerto NXT | Función |
|-------|-----------|---------|
| Motor A | OUT A | Plataforma giratoria (suelta el paquete) |
| Motor B | OUT B | Rueda izquierda |
| Motor C | OUT C | Rueda derecha |

Los puertos están marcados en el brick NXT. Conecta los cables de los motores a esos puertos.

---

## Paso 3 — Ajustar parámetros del movimiento desde el dashboard

Los parámetros del robot se ajustan **desde el navegador**, sin tocar el código.

### 3.1 Arrancar el dashboard

```bash
# Terminal 1: broker MQTT
mosquitto -v

# Terminal 2: orquestador
python orchestrator/server.py

# Terminal 3: dashboard
python orchestrator/dashboard/app.py
```

Abre **http://localhost:8000** en el navegador. Verás el panel de calibración al final de la página.

### 3.2 Parámetros disponibles

| Parámetro | Rango | Qué hace |
|-----------|-------|----------|
| **Velocidad avance** | 5–100 | Qué tan rápido va el robot hacia el QR. Empieza en 30. |
| **Velocidad retorno** | 5–100 | Velocidad al regresar a home. |
| **Potencia Motor C** | 5–100 | Fuerza del motor que inclina la plataforma. |
| **Grados inclinación** | 30–360 | Cuánto gira Motor C para soltar el objeto. |
| **Frames encima** | 1–30 | Frames sin ver el QR para confirmar que el robot está encima. |

Cada parámetro tiene un **slider** y un **campo numérico** sincronizados. Presiona **Guardar calibración** y el robot usará los nuevos valores en el siguiente ciclo, sin reiniciar nada.

### 3.3 Cómo encontrar el valor correcto de "Grados inclinación"

Antes de hacer el ciclo completo, prueba solo el Motor C con este script:

```python
# Ejecutar desde la terminal para probar Motor C
import nxt.locator, nxt.motor, time
brick = nxt.locator.find()
motor_a = nxt.motor.Motor(brick, nxt.motor.Port.A)
motor_a.turn(-10, 45)   # inclina (usa los grados que pusiste en la UI)
time.sleep(1)
motor_a.turn(10, 45)  # restablece
```

- Si el objeto **no cae**: sube los grados en el dashboard (prueba 150, 180)
- Si la plataforma **choca algo** al restablecerse: baja los grados

### 3.4 Cómo ajustar "Frames encima"

- Robot **para antes de llegar**: sube el número (prueba 10, 15)
- Robot **se pasa del punto**: baja el número (prueba 3, 4) o reduce la velocidad de avance

---

## Paso 4 — Preparar los QR

Necesitas **dos conjuntos** de QR:

### QR para los paquetes (uno por paquete)
Contenido: `QR1`, `QR2`, `QR3`

### QR para el suelo en cada destino (uno por agujero)
Mismo contenido: `QR1` en el agujero 1, `QR2` en el agujero 2, etc.

### Generar los QR en Python
```bash
pip install qrcode pillow
```
```python
import qrcode
for n in [1, 2, 3]:
    img = qrcode.make(f"QR{n}")
    img.save(f"qr{n}.png")
    print(f"Guardado: qr{n}.png")
```

### Tamaño y colocación

| | QR del paquete | QR del suelo |
|-|---------------|-------------|
| Tamaño mínimo | 3×3 cm | 5×5 cm |
| Colocación | Centrado encima del paquete, plano | En el suelo frente al agujero, sin reflejos |

### Configurar los agujeros válidos

Abre `config/destinations.yaml` y edita según cuántos agujeros tiene tu robot:
```yaml
valid_holes: [1, 2, 3]
```

---

## Paso 5 — Posicionar la cámara

La cámara cumple **dos roles** durante el ciclo:

| Momento | Qué lee |
|---------|---------|
| Robot en home (parado) | QR del paquete en el área de entrada |
| Robot avanzando | QR del suelo en cada destino |

La cámara debe tener visión tanto del área de carga como del suelo frente al robot mientras avanza. Iluminación uniforme, sin sombras.

**Verifica que la cámara lee bien antes de la práctica:**
```python
import cv2
from station.vision.classifier import QRClassifier

clf = QRClassifier()
cam = cv2.VideoCapture(0)

while True:
    _, frame = cam.read()
    result = clf.predict(frame)
    label = f"QR: {result.class_name}" if result else "Sin QR"
    cv2.putText(frame, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)
    cv2.imshow("Camara", frame)
    if cv2.waitKey(1) == ord('q'):
        break
```

Pon cada QR frente a la cámara y confirma que aparece el texto en pantalla. Presiona `Q` para cerrar.

---

## Paso 6 — Prueba en modo simulación (sin NXT)

Antes de conectar el NXT real, verifica que todo el flujo de software funciona:

```bash
python station/main.py --simulate
```

Salida esperada:
```
[INFO] Estación 1 iniciada.
[INFO] Estado: IDLE → DETECTING
[INFO] [SIM] Escaneando QR del paquete...
[INFO] QR del paquete: 'QR1' → destino agujero 1
[INFO] Estado: CLASSIFYING → WAITING_AUTH
[INFO] Estado: WAITING_AUTH → EXECUTING
[INFO] [SIM] Robot buscando QR 'QR1' en el suelo...
[INFO] Estado: EXECUTING → RETURNING
[INFO] Ciclo completo en 2.01s — paquete en agujero 1
```

---

## Paso 7 — Ciclo completo con NXT real

```bash
# Terminal 4 (con NXT conectado por USB)
python station/main.py --station-id 1 --broker localhost
```

### Secuencia de operación
1. Pon el robot en la **posición home** (inicio del riel)
2. Coloca un paquete con QR en el área de entrada
3. La cámara lee el QR del paquete automáticamente
4. El robot arranca solo, avanza despacio leyendo el suelo
5. Al encontrar y pasar el QR del suelo, para
6. Motor C suelta el paquete
7. Robot regresa a home
8. El dashboard actualiza el estado en tiempo real

---

## Resumen: 4 terminales para arrancar todo

| Terminal | Comando | Qué hace |
|----------|---------|----------|
| 1 | `mosquitto -v` | Broker de mensajes |
| 2 | `python orchestrator/server.py` | Autoriza ciclos, guarda log CSV |
| 3 | `python orchestrator/dashboard/app.py` | Dashboard + calibración en http://localhost:8000 |
| 4 | `python station/main.py --station-id 1` | La estación (con NXT y cámara) |

---

## Solución de problemas

### El NXT no se detecta
```
nxt.locator.BrickNotFoundError
```
- Verifica que el cable USB esté bien conectado y el NXT encendido
- **Windows**: instala el driver desde LEGO Mindstorms NXT software, o usa [Zadig](https://zadig.akeo.ie/) para instalar WinUSB
- **macOS / Linux**: driver incluido — desconecta y reconecta el cable

### El QR del paquete no se lee
- Iluminación directa al QR, sin sombras ni reflejos
- Tamaño mínimo impreso: 3×3 cm
- Usa el script de verificación del Paso 5

### El robot para antes de llegar al agujero
- Sube **Frames encima** en el dashboard (prueba 10 o 15)
- Asegúrate de que el QR del suelo esté bien iluminado (sin sombra del robot)

### El robot se pasa del agujero
- Baja **Velocidad avance** en el dashboard (prueba 20 o 25)
- Baja **Frames encima** (prueba 3 o 4)

### El paquete no cae
- Sube **Grados inclinación** en el dashboard (prueba 150 o 180)
- Verifica que el Motor C esté en el puerto OUT C del NXT

### El robot no regresa derecho a home
- Si se tuerce levemente, sube o baja la velocidad de un motor desde el código (`nxt/nxt_controller.py`, función `_motors_return`)
- Revisa que las llantas estén bien alineadas mecánicamente
