import Jetson.GPIO as GPIO

# Setup GPIO
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)
GPIO.setup(18, GPIO.OUT, initial=GPIO.HIGH)

def trigger_candy(self):
    print("Sending physical signal to Arduino...")
    GPIO.output(18, GPIO.LOW) # Send power
    time.sleep(1)                     # Hold for half a second
    GPIO.output(18, GPIO.HIGH)  # Turn off
    
