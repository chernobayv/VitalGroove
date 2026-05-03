# 💝 VitalGroove

an affective computing system that detects hugs in real time and rewards them with candy. built for makeUofT 2026 valentine's day hackathon.

i owned the full computer vision pipeline — face detection, hug logic, state machine, and UI overlay. the arduino/mechatronic side was a team effort.

> ⚠️ some files were lost in the transfer (including the arduino code). i'm actively recovering and organizing this repo... check back soon!

---

### 📺 demo

<a href="https://www.youtube.com/watch?v=wWBHdTOpPEo" target="_blank">
  <img src="https://img.youtube.com/vi/wWBHdTOpPEo/maxresdefault.jpg" alt="Watch the demo!" width="600" />
</a>

[▶️ click here to watch the full demo](https://www.youtube.com/watch?v=wWBHdTOpPEo)

---

### 🧠 how it works

VitalGroove runs on an NVIDIA Jetson Nano and uses a camera feed to detect when two people are hugging. when a hug is detected, it:

1. starts a 3-second countdown
2. takes a polaroid-style photo
3. triggers an arduino-controlled candy dispenser via serial
4. saves the photo to an on-device album you can browse on screen

the whole thing renders on an 800x480 display in a retro pixel art aesthetic — pink borders, animated hearts, clouds over sad faces, the works.

---

### ⚙️ tech stack

| layer | tech |
|---|---|
| hardware | NVIDIA Jetson Nano |
| computer vision | `face_recognition`, `OpenCV`, `MediaPipe` |
| display | OpenCV fullscreen window, 320x192 internal render scaled to 800x480 |
| serial comms | `pyserial` → Arduino |
| language | Python |

---

### 🏗️ architecture

- **DetectionThread** — runs face detection on a background thread so the main loop never blocks. takes full-res frames, downscales for speed, normalizes coordinates back to full res
- **Visuals** — draws hearts, clouds, rain, bows, stars on the tiny 320x192 canvas
- **ValentineApp** — state machine with 5 states: `LIVE → COUNTDOWN → FLASH → POLAROID_VIEW → ALBUM`
- **Serial bridge** — sends a single byte `'C'` to the Arduino when a hug photo is captured, triggering the candy dispenser

---

### 🚀 running it

```bash
# install dependencies
pip install face_recognition opencv-python numpy pyserial

# check your arduino port first! default is /dev/ttyACM0
# then run
python vitalgroove.py
```

> make sure your arduino is connected and the port in `CONFIGURATION` matches before running

---

### 📁 repo status

- [x] python CV + UI code
- [x] demo video
- [ ] arduino candy dispenser code *(recovering)*
- [ ] wiring schematic
- **bonus cv stuff...** [x] TouchDesigner exploration files >> https://drive.google.com/file/d/1s0s6xp3R5_rzWg_XYZUYyFebSEgedzzN/view?pli=1

---

### 👾 made with love at makeUofT 2026

victoria chernobay — CV pipeline, state machine, UI, display rendering  
stack — arduino integration, mechatronics, hardware

---

[![Hack Club](https://img.shields.io/badge/hack%20club-%23ec3750?style=flat&logo=data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjQiIGhlaWdodD0iMjQiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMTIgMkw0IDZWMTJDNCAyMCAxMiAyMiAxMiAyMkMyMCAyMiAyMCAyMCAxNiAxMkgxMlYxMEgxNlY2TDEyIDJaIiBmaWxsPSJ3aGl0ZSIvPjwvc3ZnPg==)](https://hackclub.com)
