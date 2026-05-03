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
# colors in BGR because opencv is like that
BLUSH_PINK = (193, 182, 255)
CREAM = (220, 235, 250)
DEEP_RED = (60, 60, 220)
CLOUD_GREY = (200, 200, 200)
RAIN_BLUE = (230, 200, 150)
ALBUM_BTN_COLOR = (150, 100, 200)

# display is 800x480 but we render at 320x192 internally for speed
# then stretch it up — gives a retro pixel look for free
DISPLAY_W, DISPLAY_H = 800, 480

# folder where polaroid photos get saved (max 3 at a time)
ALBUM_FOLDER = "valentine_hugs"

# if the distance between two faces is less than 1.3x one face width, that's a hug
HUG_THRESHOLD_RATIO = 1.3

# arduino port — change this if yours is different! check with `ls /dev/tty*`
ARDUINO_PORT = '/dev/ttyACM0'
BAUD_RATE = 9600

# make the album folder if it doesn't exist yet
if not os.path.exists(ALBUM_FOLDER):
    os.makedirs(ALBUM_FOLDER)


# --- THREADED AI (HIGH QUALITY) ---
class DetectionThread(threading.Thread):
    # runs face detection on a separate thread so the display never freezes
    # the main loop just drops frames into the queue and reads results whenever they're ready
    def __init__(self):
        super().__init__()
        self.frame_queue = queue.Queue(maxsize=1)  # maxsize=1 means we always process the freshest frame
        self.result_faces = []  # normalized face coords (0.0 to 1.0), updated every time detection finishes
        self.running = True
        self.daemon = True  # dies when the main program exits

    def update_frame(self, frame):
        # only add if the queue is empty — we don't want a backlog of stale frames
        if self.frame_queue.empty():
            self.frame_queue.put(frame)

    def run(self):
        while self.running:
            try:
                # grab the full quality frame
                frame = self.frame_queue.get()

                # downscale to 50% for faster detection
                # tweak this if faces aren't being picked up from far away — try 0.7 or 1.0
                scale = 0.5
                small = cv2.resize(frame, (0, 0), fx=scale, fy=scale)
                rgb = small[:, :, ::-1]  # face_recognition wants RGB, opencv gives BGR

                # number_of_times_to_upsample=0 is fastest
                # bump to 1 if faces are tiny/far away and detection is bad
                locs = face_recognition.face_locations(rgb, number_of_times_to_upsample=0)

                # scale coordinates back up and normalize to 0.0-1.0 range
                # normalizing means we can map them to any display size later
                h, w, _ = frame.shape
                inv = 1 / scale
                self.result_faces = []
                for (t, r, b, l) in locs:
                    self.result_faces.append((
                        (t * inv) / h, (r * inv) / w, (b * inv) / h, (l * inv) / w
                    ))
            except:
                pass


# --- VISUALS ---
class Visuals:
    # sine wave trick to make things pulse/bounce — speed controls frequency, amplitude controls size
    def get_beat(self, speed=8, amplitude=5):
        return int(math.sin(time.time() * speed) * amplitude)

    def draw_cloud(self, img, head_x, head_y):
        # sad little cloud drawn above a face when two people are close but not quite hugging yet
        cx, cy = int(head_x), int(head_y - 20)
        cv2.circle(img, (cx, cy), 10, CLOUD_GREY, -1)
        cv2.circle(img, (cx - 8, cy + 2), 8, CLOUD_GREY, -1)
        cv2.circle(img, (cx + 8, cy + 2), 8, CLOUD_GREY, -1)
        # tiny rain lines below the cloud
        cv2.line(img, (cx - 5, cy + 10), (cx - 5, cy + 18), RAIN_BLUE, 1)
        cv2.line(img, (cx + 5, cy + 10), (cx + 5, cy + 18), RAIN_BLUE, 1)

    def draw_heart_shape(self, img, x, y, size, color, fill=True):
        # two circles + a triangle = a heart. simple but it works great at small sizes
        x, y, size = int(x), int(y), int(size)
        s = size // 2
        thick = -1 if fill else 1  # -1 = filled, 1 = outline only
        cv2.circle(img, (x - s, y), s, color, thick)
        cv2.circle(img, (x + s, y), s, color, thick)
        pts = np.array([[x - size, y + (s // 4)], [x + size, y + (s // 4)], [x, y + size + 2]])
        if fill:
            cv2.fillPoly(img, [pts], color)
        else:
            cv2.polylines(img, [pts], True, color, 1)


# --- MAIN APP ---
class ValentineApp:
    def __init__(self):
        # state machine — controls what gets drawn and what logic runs each frame
        # LIVE → COUNTDOWN → FLASH → POLAROID_VIEW → ALBUM
        self.state = 'LIVE'
        self.hug_count = 0
        self.flash_alpha = 0       # controls the white flash brightness after a photo
        self.polaroid_timer = 0    # tracks when the polaroid view started
        self.countdown_start = 0   # when the countdown timer began
        self.countdown_patience = 0  # frames of non-hug we'll tolerate before cancelling the countdown

        self.vis = Visuals()
        self.detector = DetectionThread()
        self.detector.start()

        # try to connect to arduino — if it's not there, just simulate (no candy but everything else works)
        self.serial_conn = None
        try:
            self.serial_conn = serial.Serial(ARDUINO_PORT, BAUD_RATE, timeout=0.1)
            time.sleep(2)  # arduino needs ~2 seconds to reset after serial connect
            print("Arduino Connected!")
        except:
            print("Arduino Not Found - Simulating")

        # album state
        self.album_files = []
        self.album_index = 0
        self.current_album_image = None
        self.snapshot_frame = None

        # internal processing resolution — we render everything tiny then scale up
        # this is what gives the retro pixelated look and keeps things fast on the jetson
        self.PROC_W = 320
        self.PROC_H = 192

    def trigger_candy(self):
        # sends a single 'C' byte to the arduino — arduino code listens for this and triggers the dispenser
        if self.serial_conn:
            try:
                self.serial_conn.write(b'C')
                print("Candy Signal Sent!")
            except:
                pass

    def save_photo_background(self, frame, count):
        # runs on its own thread so the display doesn't freeze during file I/O
        try:
            # candy first! dispatch the signal before anything else so there's no delay
            self.trigger_candy()

            # scale the tiny 320x192 frame up to a bigger polaroid size
            photo = cv2.resize(frame, (700, 450), interpolation=cv2.INTER_NEAREST)

            # white polaroid border + timestamp caption at the bottom
            polaroid = np.full((600, 800, 3), 255, dtype=np.uint8)
            polaroid[40:490, 50:750] = photo
            timestamp = time.strftime("%H:%M:%S")
            cv2.putText(polaroid, f"Hug #{count} - {timestamp}", (200, 550),
                        cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, 1.2, (50, 50, 50), 2)

            filename = f"{ALBUM_FOLDER}/hug_{int(time.time())}.jpg"
            cv2.imwrite(filename, polaroid)

            # keep max 3 photos — delete oldest when we go over
            all_files = [os.path.join(ALBUM_FOLDER, f) for f in os.listdir(ALBUM_FOLDER) if f.endswith('.jpg')]
            all_files.sort(key=os.path.getctime)
            while len(all_files) > 3:
                os.remove(all_files.pop(0))
        except:
            pass

    def start_countdown(self):
        # switch to countdown state and reset the timer + patience counter
        self.state = 'COUNTDOWN'
        self.countdown_start = time.time()
        self.countdown_patience = 0

    def trigger_photo(self, frame):
        # capture the moment! flash the screen white, save the photo in the background
        self.hug_count += 1
        self.state = 'FLASH'
        self.flash_alpha = 255  # start fully white
        self.snapshot_frame = frame.copy()
        self.polaroid_timer = time.time()
        # background thread so saving doesn't block the display
        t = threading.Thread(target=self.save_photo_background, args=(frame.copy(), self.hug_count))
        t.start()

    def get_camera_frame(self, cap):
        ret, frame = cap.read()
        if not ret:
            return None, None
        frame = cv2.flip(frame, 1)  # mirror flip so it feels like a selfie cam

        # two versions of the same frame:
        # high_res → goes to the AI detection thread for accuracy
        # low_res → tiny 320x192 canvas we draw all the UI on
        high_res = frame
        low_res = cv2.resize(frame, (self.PROC_W, self.PROC_H), interpolation=cv2.INTER_NEAREST)

        return high_res, low_res

    def draw_ui_overlay(self, frame):
        # all coordinates are in 320x192 space
        bw_top = int(self.PROC_H * 0.12)  # top border height
        bw_side = 8

        # pink borders on all 4 sides
        cv2.rectangle(frame, (0, 0), (self.PROC_W, bw_top), BLUSH_PINK, -1)
        cv2.rectangle(frame, (0, self.PROC_H - bw_side), (self.PROC_W, self.PROC_H), BLUSH_PINK, -1)
        cv2.rectangle(frame, (0, 0), (bw_side, self.PROC_H), BLUSH_PINK, -1)
        cv2.rectangle(frame, (self.PROC_W - bw_side, 0), (self.PROC_W, self.PROC_H), BLUSH_PINK, -1)

        # little bow in the top left using two triangles + a circle
        by = bw_top // 2
        cv2.fillPoly(frame, [np.array([[20, by], [10, by - 6], [10, by + 6]])], DEEP_RED)
        cv2.fillPoly(frame, [np.array([[20, by], [30, by - 6], [30, by + 6]])], DEEP_RED)
        cv2.circle(frame, (20, by), 3, DEEP_RED, -1)

        # star decorations in 3 corners — cross + dot
        stars = [
            (self.PROC_W - 20, 20),             # top right
            (20, self.PROC_H - 20),             # bottom left
            (self.PROC_W - 20, self.PROC_H - 20)  # bottom right
        ]
        for (x, y) in stars:
            cv2.line(frame, (x - 5, y), (x + 5, y), CREAM, 2)
            cv2.line(frame, (x, y - 5), (x, y + 5), CREAM, 2)
            cv2.circle(frame, (x, y), 2, (255, 255, 255), -1)

        # hug counter centered in the top border
        cv2.putText(frame, f"HUGS: {self.hug_count}", (self.PROC_W // 2 - 25, by + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        # tiny "ALB" button in bottom right corner to open the photo album
        btn_w, btn_h = 30, 20
        cv2.rectangle(frame, (self.PROC_W - btn_w - 5, self.PROC_H - btn_h - 5),
                      (self.PROC_W - 5, self.PROC_H - 5), ALBUM_BTN_COLOR, -1)
        cv2.putText(frame, "ALB", (self.PROC_W - btn_w - 3, self.PROC_H - 10),
                    cv2.FONT_HERSHEY_PLAIN, 0.7, (255, 255, 255), 1)

    def render(self, high_res, low_res):
        if self.state == 'LIVE' or self.state == 'COUNTDOWN':
            # feed the fresh frame to the detection thread
            self.detector.update_frame(high_res)

            # map normalized face coords (0.0-1.0) back to 320x192 pixel space
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
                # sort faces left to right to get consistent f1, f2
                fs = sorted(faces, key=lambda x: x[3])
                f1, f2 = fs[0], fs[1]

                # find center of each face
                c1 = ((f1[3] + f1[1]) // 2, (f1[0] + f1[2]) // 2)
                c2 = ((f2[3] + f2[1]) // 2, (f2[0] + f2[2]) // 2)

                # euclidean distance between face centers
                dist = np.linalg.norm(np.array(c1) - np.array(c2))

                # use face width as a reference unit — if they're closer than 1.3 face widths apart, it's a hug
                w = f1[1] - f1[3]
                if dist < (w * HUG_THRESHOLD_RATIO):
                    hugging_now = True

            # state machine logic
            if self.state == 'LIVE':
                if hugging_now:
                    self.start_countdown()

            elif self.state == 'COUNTDOWN':
                if not hugging_now:
                    # give 40 frames of grace before cancelling — stops flickering if detection hiccups
                    self.countdown_patience += 1
                    if self.countdown_patience > 40:
                        self.state = 'LIVE'
                else:
                    self.countdown_patience = 0

                    remaining = 3 - int(time.time() - self.countdown_start)
                    if remaining <= 0:
                        # countdown finished! take the photo
                        self.trigger_photo(low_res)
                    else:
                        # draw the big countdown number in the center
                        text = str(remaining)
                        cx, cy = self.PROC_W // 2, self.PROC_H // 2
                        # draw twice: thick red base + thin cream on top for that retro outlined text look
                        cv2.putText(low_res, text, (cx - 10, cy + 10), cv2.FONT_HERSHEY_SIMPLEX, 2, DEEP_RED, 4)
                        cv2.putText(low_res, text, (cx - 10, cy + 10), cv2.FONT_HERSHEY_SIMPLEX, 2, CREAM, 1)

            # drawing logic — separate from state machine
            if self.state != 'FLASH':
                # show_hug = true if they're actively hugging OR if countdown is in progress
                show_hug = (hugging_now) or (self.state == 'COUNTDOWN' and self.countdown_patience < 40)

                if two_people and not show_hug:
                    # two people detected but not close enough — show sad clouds and a "hug!" prompt
                    for (t, r, b, l) in faces[:2]:
                        self.vis.draw_cloud(low_res, (l + r) // 2, t)
                    cx, cy = self.PROC_W // 2, self.PROC_H // 2
                    beat = self.vis.get_beat(amplitude=3)
                    self.vis.draw_heart_shape(low_res, cx, cy, 30 + beat, BLUSH_PINK, True)
                    cv2.putText(low_res, "hug!", (cx - 15, cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

                if show_hug:
                    # hugging! draw little bouncing hearts above each face
                    for (t, r, b, l) in faces:
                        cy = t - 10
                        beat = self.vis.get_beat(12, 2)
                        self.vis.draw_heart_shape(low_res, (l + r) // 2, cy, 8 + beat, DEEP_RED, True)

                # always draw the face bounding boxes in pink
                for (t, r, b, l) in faces:
                    cv2.rectangle(low_res, (l, t), (r, b), BLUSH_PINK, 1)

            self.draw_ui_overlay(low_res)

        elif self.state == 'FLASH':
            # white screen flash effect — fades out by 100 alpha per frame
            out = self.snapshot_frame.copy()
            if self.flash_alpha > 0:
                cv2.rectangle(out, (0, 0), (self.PROC_W, self.PROC_H), (255, 255, 255), -1)
                self.flash_alpha -= 100
            else:
                # flash done, move to polaroid view
                self.state = 'POLAROID_VIEW'
                self.polaroid_timer = time.time()
            return out

        elif self.state == 'POLAROID_VIEW':
            # show the frozen snapshot with a "pick up your candy!" message for 5 seconds
            out = self.snapshot_frame.copy()
            cv2.rectangle(out, (20, 20), (self.PROC_W - 20, self.PROC_H - 20), CREAM, 4)

            beat = self.vis.get_beat(10, 2)
            cv2.putText(out, "Make sure to pick up", (30, self.PROC_H - 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, DEEP_RED, 2)
            cv2.putText(out, "your candy!", (60, self.PROC_H - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, DEEP_RED, 2)

            if time.time() - self.polaroid_timer > 5.0:
                self.state = 'LIVE'
            return out

        elif self.state == 'ALBUM':
            # browse saved polaroid photos — left half of screen = prev, right half = next
            if not self.album_files:
                self.album_files = sorted(
                    [f for f in os.listdir(ALBUM_FOLDER) if f.endswith('.jpg')], reverse=True
                )
                if not self.album_files:
                    # no photos yet, go back to live
                    self.state = 'LIVE'
                    return low_res

            if self.current_album_image is None:
                fname = self.album_files[self.album_index % len(self.album_files)]
                path = os.path.join(ALBUM_FOLDER, fname)
                if os.path.exists(path):
                    self.current_album_image = cv2.resize(cv2.imread(path), (self.PROC_W, self.PROC_H))
                else:
                    # file got deleted, refresh the list
                    self.album_files.remove(fname)
                    return low_res

            out = self.current_album_image.copy()

            # navigation arrows on left and right sides
            cv2.putText(out, "<", (5, self.PROC_H // 2 + 10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, BLUSH_PINK, 3)
            cv2.putText(out, ">", (self.PROC_W - 35, self.PROC_H // 2 + 10), cv2.FONT_HERSHEY_SIMPLEX, 1.5, BLUSH_PINK, 3)

            # X button in top right to exit album
            cv2.rectangle(out, (self.PROC_W - 40, 0), (self.PROC_W, 20), DEEP_RED, -1)
            cv2.putText(out, "X", (self.PROC_W - 25, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            return out

        return low_res

    def handle_click(self, x, y):
        # touch/click coords come in at 800x480 (display res) — scale down to 320x192 (processing res)
        sx = x * (self.PROC_W / DISPLAY_W)
        sy = y * (self.PROC_H / DISPLAY_H)

        if self.state == 'LIVE':
            # bottom right corner opens the album
            if sx > self.PROC_W - 40 and sy > self.PROC_H - 40:
                self.state = 'ALBUM'
                self.album_files = []
                self.current_album_image = None
                self.album_index = 0
        elif self.state == 'ALBUM':
            if sx > self.PROC_W - 40 and sy < 25:
                # X button — back to live
                self.state = 'LIVE'
            elif sx > self.PROC_W // 2:
                # right half — next photo
                self.album_index += 1
                self.current_album_image = None
            else:
                # left half — previous photo
                self.album_index -= 1
                self.current_album_image = None


app = ValentineApp()


def mouse_callback(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        app.handle_click(x, y)


def main():
    # V4L2 backend is more stable on jetson than the default
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    cv2.namedWindow("Valentine", cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty("Valentine", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    cv2.setMouseCallback("Valentine", mouse_callback)

    while True:
        high, low = app.get_camera_frame(cap)
        if high is None:
            break

        processed_small = app.render(high, low)

        # stretch the 320x192 render up to 800x480 for the display
        # INTER_NEAREST keeps the pixel art look — no blurring
        final = cv2.resize(processed_small, (DISPLAY_W, DISPLAY_H), interpolation=cv2.INTER_NEAREST)

        cv2.imshow('Valentine', final)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
