import nxt.locator, nxt.motor, time

brick = nxt.locator.find()
motor_b = nxt.motor.Motor(brick, nxt.motor.Port.B)
motor_c = nxt.motor.Motor(brick, nxt.motor.Port.C)

print("Adelante...")
motor_b.turn(30, 360)
motor_c.turn(30, 360)

time.sleep(1)

print("Atrás...")
motor_b.turn(-30, 360)
motor_c.turn(-30, 360)

print("Listo")
