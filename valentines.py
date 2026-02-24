import face_recognition
import cv2
import numpy as np
import random
import time
import os
import threading
import math
import queue
import serial

# --- CONFIGURATION ---
BLUSH_PINK = (193, 182, 255)
CREAM = (220, 235, 250)
DEEP_RED = (60, 60, 220)
CLOUD_GREY = (200, 200, 200)
RAIN_BLUE = (230, 200, 150)
ALBUM_BTN_COLOR = (150, 100, 200)

DISPLAY_W, DISPLAY_H = 800, 480
ALBUM_FOLDER = "valentine_hugs"
HUG_THRESHOLD_RATIO = 1.3

# ARDUINO SETTINGS (Check your specific port!)
ARDUINO_PORT = '/dev/ttyACM0'
BAUD_RATE = 9600

if not os.path.exists(ALBUM_FOLDER):
    os.makedirs(ALBUM_FOLDER)

# --- THREADED AI (HIGH QUALITY) ---
class DetectionThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.frame_queue = queue.Queue(maxsize=1)
        self.result_faces = []
        self.running = True
        self.daemon = True

    def update_frame(self, frame):
        if self.frame_queue.empty():
            self.frame_queue.put(frame)

    def run(self):
        while self.running:
            try:
                # 1. Get the FULL QUALITY frame
                frame = self.frame_queue.get()
               
                # 2. Downscale for Speed vs Accuracy
                # scale = 0.5 is a good balance.
                # If detection is still too bad at distance, change to 0.7 or 1.0 (slower)
                scale = 0.5
                small = cv2.resize(frame, (0,0), fx=scale, fy=scale)
                rgb = small[:, :, ::-1]
               
                # 3. Detect
                # number_of_times_to_upsample=0 is fastest.
                # Change to 1 if faces are very far away.
                locs = face_recognition.face_locations(rgb, number_of_times_to_upsample=0)
               
                # 4. Scale coordinates to 0-1.0 range (normalized)
                h, w, _ = frame.shape
                inv = 1 / scale
                self.result_faces = []
                for (t,r,b,l) in locs:
                    self.result_faces.append((
                        (t*inv)/h, (r*inv)/w, (b*inv)/h, (l*inv)/w
                    ))
            except:
                pass

# --- VISUALS ---
class Visuals:
    def get_beat(self, speed=8, amplitude=5):
        return int(math.sin(time.time() * speed) * amplitude)

    def draw_cloud(self, img, head_x, head_y):
        # Optimized for small res drawing
        cx, cy = int(head_x), int(head_y - 20)
        cv2.circle(img, (cx, cy), 10, CLOUD_GREY, -1)
        cv2.circle(img, (cx-8, cy+2), 8, CLOUD_GREY, -1)
        cv2.circle(img, (cx+8, cy+2), 8, CLOUD_GREY, -1)
        # Rain
        cv2.line(img, (cx-5, cy+10), (cx-5, cy+18), RAIN_BLUE, 1)
        cv2.line(img, (cx+5, cy+10), (cx+5, cy+18), RAIN_BLUE, 1)

    def draw_heart_shape(self, img, x, y, size, color, fill=True):
        x, y, size = int(x), int(y), int(size)
        s = size // 2
        thick = -1 if fill else 1
        cv2.circle(img, (x - s, y), s, color, thick)
        cv2.circle(img, (x + s, y), s, color, thick)
        pts = np.array([[x - size, y + (s//4)], [x + size, y + (s//4)], [x, y + size + 2]])
        if fill: cv2.fillPoly(img, [pts], color)
        else: cv2.polylines(img, [pts], True, color, 1)

# --- MAIN APP ---
class ValentineApp:
    def __init__(self):
        self.state = 'LIVE'
        self.hug_count = 0
        self.flash_alpha = 0
        self.polaroid_timer = 0
        self.countdown_start = 0
        self.countdown_patience = 0
       
        # Tools
        self.vis = Visuals()
        self.detector = DetectionThread()
        self.detector.start()
       
        # Arduino
        self.serial_conn = None
        try:
            self.serial_conn = serial.Serial(ARDUINO_PORT, BAUD_RATE, timeout=0.1)
            time.sleep(2)
            print("Arduino Connected!")
        except:
            print("Arduino Not Found - Simulating")

        # Album
        self.album_files = []
        self.album_index = 0
        self.current_album_image = None
        self.snapshot_frame = None

        # Resolution Settings (Internal Processing Size)
        self.PROC_W = 320
        self.PROC_H = 192

    def trigger_candy(self):
        if self.serial_conn:
            try:
                self.serial_conn.write(b'C')
                print("Candy Signal Sent!")
            except: pass

    def save_photo_background(self, frame, count):
        try:
            # 1. Trigger Candy ASAP
            self.trigger_candy()
           
            # 2. Save Full Quality Photo
            # Scale up the tiny frame to look like a Polaroid
            photo = cv2.resize(frame, (700, 450), interpolation=cv2.INTER_NEAREST)
           
            polaroid = np.full((600, 800, 3), 255, dtype=np.uint8)
            polaroid[40:490, 50:750] = photo
            timestamp = time.strftime("%H:%M:%S")
            cv2.putText(polaroid, f"Hug #{count} - {timestamp}", (200, 550),
                        cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 1.2, (50,50,50), 2)
           
            filename = f"{ALBUM_FOLDER}/hug_{int(time.time())}.jpg"
            cv2.imwrite(filename, polaroid)
           
            # 3. Cleanup Old Photos (Max 3)
            all_files = [os.path.join(ALBUM_FOLDER, f) for f in os.listdir(ALBUM_FOLDER) if f.endswith('.jpg')]
            all_files.sort(key=os.path.getctime)
            while len(all_files) > 3:
                os.remove(all_files.pop(0))
        except: pass

    def start_countdown(self):
        self.state = 'COUNTDOWN'
        self.countdown_start = time.time()
        self.countdown_patience = 0

    def trigger_photo(self, frame):
        self.hug_count += 1
        self.state = 'FLASH'
        self.flash_alpha = 255
        self.snapshot_frame = frame.copy()
        self.polaroid_timer = time.time()
        t = threading.Thread(target=self.save_photo_background, args=(frame.copy(), self.hug_count))
        t.start()

    def get_camera_frame(self, cap):
        ret, frame = cap.read()
        if not ret: return None, None
        frame = cv2.flip(frame, 1)
       
        # 1. High Res for AI (Original Frame)
        high_res = frame
       
        # 2. Low Res for Display (Pixelated look, super fast)
        low_res = cv2.resize(frame, (self.PROC_W, self.PROC_H), interpolation=cv2.INTER_NEAREST)
       
        return high_res, low_res

    def draw_ui_overlay(self, frame):
        # Draw on the TINY frame (Coordinate math adjusts to PROC_W/H)
        bw_top = int(self.PROC_H * 0.12)
        bw_side = 8
       
        # Borders
        cv2.rectangle(frame, (0, 0), (self.PROC_W, bw_top), BLUSH_PINK, -1)
        cv2.rectangle(frame, (0, self.PROC_H-bw_side), (self.PROC_W, self.PROC_H), BLUSH_PINK, -1)
        cv2.rectangle(frame, (0, 0), (bw_side, self.PROC_H), BLUSH_PINK, -1)
        cv2.rectangle(frame, (self.PROC_W-bw_side, 0), (self.PROC_W, self.PROC_H), BLUSH_PINK, -1)

        # Bow (Top Left)
        by = bw_top // 2
        cv2.fillPoly(frame, [np.array([[20,by], [10,by-6], [10,by+6]])], DEEP_RED)
        cv2.fillPoly(frame, [np.array([[20,by], [30,by-6], [30,by+6]])], DEEP_RED)
        cv2.circle(frame, (20, by), 3, DEEP_RED, -1)

        # Stars (Corners - FIXED AND VISIBLE)
        stars = [
            (self.PROC_W-20, 20),           # Top Right
            (20, self.PROC_H-20),           # Bottom Left
            (self.PROC_W-20, self.PROC_H-20)# Bottom Right
        ]
        for (x,y) in stars:
            cv2.line(frame, (x-5, y), (x+5, y), CREAM, 2)
            cv2.line(frame, (x, y-5), (x, y+5), CREAM, 2)
            cv2.circle(frame, (x, y), 2, (255,255,255), -1)

        # Counter
        cv2.putText(frame, f"HUGS: {self.hug_count}", (self.PROC_W//2 - 25, by+4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        # Album Button
        btn_w, btn_h = 30, 20
        cv2.rectangle(frame, (self.PROC_W-btn_w-5, self.PROC_H-btn_h-5), (self.PROC_W-5, self.PROC_H-5), ALBUM_BTN_COLOR, -1)
        cv2.putText(frame, "ALB", (self.PROC_W-btn_w-3, self.PROC_H-10), cv2.FONT_HERSHEY_PLAIN, 0.7, (255,255,255), 1)

    def render(self, high_res, low_res):
        if self.state == 'LIVE' or self.state == 'COUNTDOWN':
            # 1. Update AI
            self.detector.update_frame(high_res)
           
            # 2. Map normalized faces to LOW res display
            faces = []
            for (tn, rn, bn, ln) in self.detector.result_faces:
                faces.append((
                    int(tn * self.PROC_H), int(rn * self.PROC_W),
                    int(bn * self.PROC_H), int(ln * self.PROC_W)
                ))
           
            hugging_now = False
            two_people = False
           
            if len(faces) >= 2:
                two_people = True
                fs = sorted(faces, key=lambda x: x[3])
                f1, f2 = fs[0], fs[1]
               
                c1 = ((f1[3]+f1[1])//2, (f1[0]+f1[2])//2)
                c2 = ((f2[3]+f2[1])//2, (f2[0]+f2[2])//2)
                dist = np.linalg.norm(np.array(c1) - np.array(c2))
                w = f1[1] - f1[3]
               
                if dist < (w * HUG_THRESHOLD_RATIO):
                    hugging_now = True

            # STATE MACHINE
            if self.state == 'LIVE':
                if hugging_now: self.start_countdown()
           
            elif self.state == 'COUNTDOWN':
                # STICKY LOGIC: Patience increased to 40 frames
                if not hugging_now:
                    self.countdown_patience += 1
                    if self.countdown_patience > 40: self.state = 'LIVE'
                else:
                    self.countdown_patience = 0
                   
                    remaining = 3 - int(time.time() - self.countdown_start)
                    if remaining <= 0:
                        self.trigger_photo(low_res) # Pass low res for instant visual snapshot
                    else:
                        text = str(remaining)
                        cx, cy = self.PROC_W//2, self.PROC_H//2
                        cv2.putText(low_res, text, (cx-10, cy+10), cv2.FONT_HERSHEY_SIMPLEX, 2, DEEP_RED, 4)
                        cv2.putText(low_res, text, (cx-10, cy+10), cv2.FONT_HERSHEY_SIMPLEX, 2, CREAM, 1)

            # DRAWING
            if self.state != 'FLASH':
                show_hug = (hugging_now) or (self.state == 'COUNTDOWN' and self.countdown_patience < 40)
               
                if two_people and not show_hug:
                    for (t, r, b, l) in faces[:2]: self.vis.draw_cloud(low_res, (l+r)//2, t)
                    cx, cy = self.PROC_W//2, self.PROC_H//2
                    beat = self.vis.get_beat(amplitude=3)
                    self.vis.draw_heart_shape(low_res, cx, cy, 30+beat, BLUSH_PINK, True)
                    cv2.putText(low_res, "hug!", (cx-15, cy+5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255,255,255), 1)

                if show_hug:
                    for (t, r, b, l) in faces:
                        cy = t - 10
                        beat = self.vis.get_beat(12, 2)
                        self.vis.draw_heart_shape(low_res, (l+r)//2, cy, 8+beat, DEEP_RED, True)

                for (t, r, b, l) in faces:
                    cv2.rectangle(low_res, (l, t), (r, b), BLUSH_PINK, 1)

            self.draw_ui_overlay(low_res)

        elif self.state == 'FLASH':
            out = self.snapshot_frame.copy()
            if self.flash_alpha > 0:
                cv2.rectangle(out, (0,0), (self.PROC_W, self.PROC_H), (255,255,255), -1)
                self.flash_alpha -= 100
            else:
                self.state = 'POLAROID_VIEW'
                self.polaroid_timer = time.time()
            return out

        elif self.state == 'POLAROID_VIEW':
            out = self.snapshot_frame.copy()
            cv2.rectangle(out, (20, 20), (self.PROC_W-20, self.PROC_H-20), CREAM, 4)
           
            beat = self.vis.get_beat(10, 2)
            # Text: COLLECT YOUR CANDY!
            cv2.putText(out, "Make sure to pick up", (30, self.PROC_H - 35),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, DEEP_RED, 2)
            cv2.putText(out, "your candy!", (60, self.PROC_H - 15),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, DEEP_RED, 2)
           
            if time.time() - self.polaroid_timer > 5.0:
                self.state = 'LIVE'
            return out

        elif self.state == 'ALBUM':
            if not self.album_files:
                self.album_files = sorted([f for f in os.listdir(ALBUM_FOLDER) if f.endswith('.jpg')], reverse=True)
                if not self.album_files:
                    self.state = 'LIVE'
                    return low_res

            if self.current_album_image is None:
                fname = self.album_files[self.album_index % len(self.album_files)]
                path = os.path.join(ALBUM_FOLDER, fname)
                if os.path.exists(path):
                    self.current_album_image = cv2.resize(cv2.imread(path), (self.PROC_W, self.PROC_H))
                else:
                    self.album_files.remove(fname)
                    return low_res

            out = self.current_album_image.copy()
           
            # Arrows (Thick and Visible)
            cv2.putText(out, "<", (5, self.PROC_H//2 + 10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, BLUSH_PINK, 3)
            cv2.putText(out, ">", (self.PROC_W - 35, self.PROC_H//2 + 10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, BLUSH_PINK, 3)
           
            # Exit Button
            cv2.rectangle(out, (self.PROC_W-40, 0), (self.PROC_W, 20), DEEP_RED, -1)
            cv2.putText(out, "X", (self.PROC_W-25, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 2)
            return out
       
        return low_res

    def handle_click(self, x, y):
        # Scale inputs from 800x480 to 320x192
        sx = x * (self.PROC_W / DISPLAY_W)
        sy = y * (self.PROC_H / DISPLAY_H)
       
        if self.state == 'LIVE':
            if sx > self.PROC_W-40 and sy > self.PROC_H-40:
                self.state = 'ALBUM'
                self.album_files = []
                self.current_album_image = None
                self.album_index = 0
        elif self.state == 'ALBUM':
            if sx > self.PROC_W-40 and sy < 25: self.state = 'LIVE'
            elif sx > self.PROC_W//2:
                self.album_index += 1
                self.current_album_image = None
            else:
                self.album_index -= 1
                self.current_album_image = None

app = ValentineApp()
def mouse_callback(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN: app.handle_click(x, y)

def main():
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
   
    cv2.namedWindow("Valentine", cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty("Valentine", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.setMouseCallback("Valentine", mouse_callback)
   
    while True:
        high, low = app.get_camera_frame(cap)
        if high is None: break
       
        processed_small = app.render(high, low)
       
        # Stretch to Full Screen (Nearest Neighbor keeps it retro/fast)
        final = cv2.resize(processed_small, (DISPLAY_W, DISPLAY_H), interpolation=cv2.INTER_NEAREST)
       
        cv2.imshow('Valentine', final)
        if cv2.waitKey(1) & 0xFF == ord('q'): break
   
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()

