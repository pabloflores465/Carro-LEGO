import nxt.locator, nxt.motor, time                                                           
                                                                                               
brick = nxt.locator.find()                                                                    
motor_b = nxt.motor.Motor(brick, nxt.motor.Port.B)                                            
motor_c = nxt.motor.Motor(brick, nxt.motor.Port.C)                                          

print("Adelante...")                                
motor_b.turn(5, 20)                                                                         
motor_c.turn(5, 20)    

print("Atrás...")
motor_b.turn(-5, 20)
motor_c.turn(-5, 20)