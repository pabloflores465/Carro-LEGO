import nxt.locator, nxt.motor, time
brick = nxt.locator.find()
motor_a = nxt.motor.Motor(brick, nxt.motor.Port.A)
motor_a.turn(-20, 45)   # inclina (usa los grados que pusiste en la UI)
time.sleep(1)
motor_a.turn(20, 45)  # restablece (potencia negativa = sentido contrario)