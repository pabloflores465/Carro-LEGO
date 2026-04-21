# Prompt para Claude: programa en Python para robot LEGO Mindstorms con QR, YOLO y UI

Quiero que actúes como un desarrollador senior en Python, visión por computadora y robótica educativa, y que me generes un programa completo, estructurado y bien comentado para un robot LEGO Mindstorms.

## Objetivo general
Necesito un sistema en Python para un carro robot LEGO Mindstorms que transporte una bola y la entregue en una ubicación específica según un QR de destino.

El robot tendrá **encima un QR de identificación/destino**, y existirán **6 QR principales permanentes**:
- QR1
- QR2
- QR3
- QR4
- QR5
- QR6

Cada QR principal estará asociado a un **QR de destino** dentro del entorno, por ejemplo:
- QR1 -> QR1.1
- QR2 -> QR2.1
- QR3 -> QR3.1
- QR4 -> QR4.1
- QR5 -> QR5.1
- QR6 -> QR6.1

Pero además quiero que en la interfaz gráfica yo pueda cambiar dinámicamente las asociaciones, por ejemplo:
- QR1 -> QR4
- QR2 -> QR6
- etc.

Es decir:
1. Hay **6 QR permanentes**, que siempre deben existir aunque cierre el programa.
2. El sistema debe permitir **asociar un QR origen con un QR destino** desde la UI.
3. Esas asociaciones deben **guardarse persistentemente** para que no se pierdan al cerrar el programa.

---

## Requerimientos funcionales
### 1. Generación de QR permanentes
El programa debe:
- Generar automáticamente los 6 QR base si no existen.
- Guardarlos en disco en una carpeta, por ejemplo `qrs/`.
- Hacer que sean permanentes y reutilizables.
- No regenerarlos con contenido distinto cada vez.
- Usar un formato estable, por ejemplo:
  - `QR1`
  - `QR2`
  - `QR3`
  - `QR4`
  - `QR5`
  - `QR6`

También debe poder generar los QR de destino si hace falta.

---

### 2. Detección con cámara
Usaré la **cámara del iPhone** conectada en Mac y debe usarse con índice:
- `camera_index = 1`

El sistema debe capturar video en tiempo real y usar visión por computadora para decidir a dónde ir.

---

### 3. Uso de YOLO
Quiero que el programa use **YOLO** para detectar y reconocer el QR objetivo o la señal visual necesaria para navegación.

Aquí quiero que seas cuidadoso y profesional:
- Si YOLO no es la mejor herramienta para leer directamente un QR, entonces combínalo con una librería más adecuada.
- Puedes usar YOLO para detectar el área/objetivo/marker y usar otra librería para decodificar el QR, por ejemplo OpenCV QRCodeDetector o pyzbar.
- Explícame en comentarios por qué tomaste esa decisión.
- No fuerces YOLO para algo para lo que no sea ideal si existe una solución más robusta.

---

### 4. Lógica de navegación
El robot tiene 3 motores:
- **Motor A**: rueda
- **Motor B**: rueda
- **Motor C**: plataforma de liberación

#### Movimiento de A y B
- Si A y B giran en **sentido horario**, el carro avanza.
- Si A y B giran en **sentido antihorario**, el carro retrocede.
- Para girar:
  - puedes mover solo uno de los motores
  - o hacer que uno avance y otro no
  - por ejemplo, usar motor A o B para orientar el carro a izquierda o derecha

#### Motor C
- El motor C controla una base plana inclinable.
- Cuando gira, deja caer la bola.
- En sentido horario gira hacia un lado.
- En sentido antihorario gira hacia el otro.

#### Comportamiento esperado
1. El sistema identifica qué QR lleva el robot encima.
2. Busca en la configuración cuál es su destino asociado.
3. Usa la cámara para encontrar el QR destino en el entorno.
4. Navega hacia él.
5. Cuando llega al QR asociado, se detiene.
6. Activa el motor C para liberar la bola.
7. Opcionalmente puede regresar o quedarse detenido, pero deja esto parametrizable.

---

### 5. Interfaz gráfica
Necesito una UI simple en Python donde pueda:
- Ver los 6 QR permanentes
- Ver su asociación actual
- Cambiar asociaciones manualmente, por ejemplo:
  - QR1 -> QR4
  - QR2 -> QR6
- Guardar la configuración
- Ver el estado del sistema:
  - cámara activa
  - QR detectado en el robot
  - QR destino actual
  - estado del robot: buscando / avanzando / alineando / entregando / detenido
- Tener botones como:
  - Generar QR
  - Iniciar cámara
  - Conectar robot
  - Iniciar misión
  - Detener misión

Puedes usar una UI sencilla, por ejemplo:
- Tkinter, o
- PySide6 / PyQt si lo consideras mejor

Pero prioriza que sea fácil de ejecutar en macOS.

---

## Persistencia
Quiero que todo se guarde en archivos locales:
- Los QR como imágenes PNG
- Las asociaciones en un archivo JSON, por ejemplo:
  - `config/associations.json`

Ejemplo de asociaciones:

```json
{
  "QR1": "QR4",
  "QR2": "QR6",
  "QR3": "QR1",
  "QR4": "QR2",
  "QR5": "QR3",
  "QR6": "QR5"
}
```

Si el archivo no existe, el programa debe crearlo con valores por defecto.

---

## Integración con LEGO Mindstorms
Quiero que el código esté preparado para controlar motores A, B y C.

Si no tienes certeza absoluta del modelo exacto de LEGO Mindstorms o de la librería Python correcta, entonces:
1. no inventes APIs;
2. separa la lógica de movimiento en una capa abstracta;
3. crea una clase como `RobotController`;
4. deja una implementación simulada y otra lista para adaptar al hardware real;
5. marca claramente dónde debo conectar la API real del robot.

Quiero que seas muy explícito con esto.

No asumas una librería específica sin advertirlo. Si necesitas elegir una estructura provisional, usa una interfaz desacoplada como:
- `move_forward()`
- `move_backward()`
- `turn_left()`
- `turn_right()`
- `stop()`
- `release_payload()`

---

## Arquitectura del proyecto
Quiero que el código quede organizado en varios archivos, por ejemplo:

- `main.py`
- `ui.py`
- `qr_manager.py`
- `vision.py`
- `navigation.py`
- `robot_controller.py`
- `config_manager.py`

Si consideras mejor otra estructura, úsala, pero mantenla clara.

---

## Navegación visual
Quiero una lógica práctica, no solo conceptual.

Implementa una estrategia razonable como esta:
- detectar el QR destino en el frame
- calcular su posición relativa respecto al centro de la imagen
- si está a la izquierda, girar a la izquierda
- si está a la derecha, girar a la derecha
- si está centrado, avanzar
- si el QR ocupa suficiente área o parece lo suficientemente cercano, detenerse
- ejecutar liberación con motor C

Haz que esos umbrales sean configurables.

---

## Modo simulación
Como puede que no siempre tenga conectado el robot real, necesito un **modo simulación**:
- que imprima en consola las acciones:
  - avanzar
  - girar izquierda
  - girar derecha
  - detener
  - liberar carga
- para poder probar la lógica sin hardware

---

## Requisitos técnicos
Quiero que el programa:
- esté en Python 3
- use buenas prácticas
- esté bien comentado
- tenga manejo de errores
- tenga instrucciones de instalación
- incluya un `requirements.txt`
- incluya pasos para ejecutar en macOS
- use `camera_index = 1`
- sea claro y mantenible

---

## Sobre YOLO y QR
Quiero una solución realista:
- si usar solo YOLO para leer QR no es lo adecuado, dilo claramente
- puedes usar YOLO para detección visual general y OpenCV/pyzbar para decodificación del QR
- documenta bien esa decisión dentro del código

---

## Lo que debes entregarme
Quiero que me entregues todo esto en una sola respuesta:

1. Explicación breve de la arquitectura
2. Código completo por archivos
3. `requirements.txt`
4. Instrucciones de instalación
5. Instrucciones de ejecución
6. Notas sobre cómo adaptar `RobotController` al LEGO Mindstorms real
7. Ejemplo de archivo JSON de asociaciones
8. Código funcional y coherente, no solo pseudocódigo

---

## Restricciones importantes
- No inventes librerías o métodos de LEGO si no estás seguro.
- Si hay incertidumbre con el hardware, dilo explícitamente.
- Prioriza una solución desacoplada y adaptable.
- El código debe compilar o quedar muy cerca de compilar con cambios mínimos reales.
- No me des solo teoría: necesito implementación.
- Si alguna parte depende del modelo exacto de Mindstorms, sepárala con comentarios `TODO`.

---

## Extra 
Agrega:
- vista previa de cámara en la UI
- opción de probar detección de QR sin mover el robot
- logs de eventos
- parámetro para definir cuánto debe girar el motor C para soltar la bola

Genera ahora la solución completa.
