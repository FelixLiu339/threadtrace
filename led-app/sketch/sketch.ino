// ThreadTrace LED app (Arduino App Lab, MCU side) for the UNO Q 8x13 LED matrix.
// Pixel animations that mirror the big screen:
//   idle  : a slow "thread" wave (gateway alive)
//   scan  : a check mark, then one border segment per wash cycle + the cycle count
//   weigh : a falling water drop, a water level for litres saved, then the weight (e.g. 12.4)
// Python calls show(kind, a, b) over the Bridge:
//   kind 1 = scan  (a = cycles)
//   kind 2 = weigh (a = kg x 10, b = litres saved)
#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>

Arduino_LED_Matrix matrix;

const int W = 13, H = 8;
const int MAXV = 7;          // 3 grayscale bits -> 0..7
uint8_t fb[W * H];

int mode = 0, va = 0, vb = 0;
unsigned long t0 = 0;

void clr() { memset(fb, 0, sizeof(fb)); }
void px(int x, int y, int v) {
  if (x < 0 || x >= W || y < 0 || y >= H) return;
  if (v > fb[y * W + x]) fb[y * W + x] = v;
}

// 3x5 digits, bit 4 = left column
const uint8_t DIG[10][5] = {
  {7,5,5,5,7},{2,6,2,2,7},{7,1,7,4,7},{7,1,7,1,7},{5,5,7,1,1},
  {7,4,7,1,7},{7,4,7,5,7},{7,1,1,2,2},{7,5,7,5,7},{7,5,7,1,7}};
void digit(int d, int x, int y) {
  for (int r = 0; r < 5; r++)
    for (int c = 0; c < 3; c++)
      if (DIG[d][r] & (4 >> c)) px(x + c, y + r, MAXV);
}

// border cell i (clockwise from top-left), 38 cells in total
void borderXY(int i, int &x, int &y) {
  if (i < W) { x = i; y = 0; return; }
  i -= W;
  if (i < H - 1) { x = W - 1; y = i + 1; return; }
  i -= H - 1;
  if (i < W - 1) { x = W - 2 - i; y = H - 1; return; }
  i -= W - 1;
  x = 0; y = H - 2 - i;
}
const int BORDER = 2 * (W + H) - 4;

String show(int kind, int a, int b) {
  mode = kind; va = a; vb = b; t0 = millis();
  return String("ok");
}

void idleWave(unsigned long t) {
  for (int x = 0; x < W; x++) {
    float yf = 3.5 + 2.4 * sin(x * 0.55 + t / 320.0);
    int y = (int)(yf + 0.5);
    px(x, y, MAXV);
    px(x, y - 1, 1);
    px(x, y + 1, 1);
  }
}

void scanAnim(unsigned long t) {
  if (t < 900) {                       // check mark draws in
    const int pts[][2] = {{2,4},{3,5},{4,6},{5,5},{6,4},{7,3},{8,2},{9,1},{10,0}};
    int n = min(9, (int)(t / 80) + 1);
    for (int i = 0; i < n; i++) px(pts[i][0], pts[i][1], MAXV);
    return;
  }
  int cycles = constrain(va, 1, 12);
  unsigned long u = t - 900;
  int lit = min(cycles, (int)(u / 350) + 1);        // one segment every 350 ms
  int seg = BORDER / cycles;
  for (int s = 0; s < lit; s++)
    for (int k = 0; k < seg - 1; k++) { int x, y; borderXY(s * seg + k, x, y); px(x, y, MAXV); }
  if (lit >= cycles) {                               // count in the middle
    if (cycles >= 10) { digit(cycles / 10, 3, 2); digit(cycles % 10, 7, 2); }
    else digit(cycles, 5, 2);
  } else {
    if (lit >= 10) { digit(lit / 10, 3, 2); digit(lit % 10, 7, 2); }
    else digit(lit, 5, 2);
  }
}

void weighAnim(unsigned long t) {
  if (t < 1000) {                      // drop falls
    int y = (int)(t / 125);
    px(6, y - 1, 3); px(6, y, MAXV); px(5, y + 1, MAXV); px(6, y + 1, MAXV); px(7, y + 1, MAXV); px(6, y + 2, 4);
    return;
  }
  if (t < 3000) {                      // water level for litres saved (full at 60 L)
    int full = map(constrain(vb, 0, 60), 0, 60, 1, H);
    int lvl = min(full, (int)((t - 1000) / 180) + 1);
    for (int r = 0; r < lvl; r++)
      for (int x = 0; x < W; x++) px(x, H - 1 - r, (x + r + (int)(t / 120)) % 4 == 0 ? MAXV : 4);
    return;
  }
  // weight digits: 12.4  (tens, ones, dot, tenths) or 9.8
  int tenths = va % 10, whole = va / 10;
  if (whole >= 10) {
    digit((whole / 10) % 10, 0, 1); digit(whole % 10, 4, 1); px(8, 5, MAXV); digit(tenths, 10, 1);
  } else {
    digit(whole, 2, 1); px(6, 5, MAXV); digit(tenths, 8, 1);
  }
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);
  Bridge.begin();
  Bridge.provide("show", show);
}

void loop() {
  unsigned long now = millis(), t = now - t0;
  clr();
  if (mode == 1 && t < 7000) scanAnim(t);
  else if (mode == 2 && t < 7000) weighAnim(t);
  else { mode = 0; idleWave(now); }
  matrix.draw(fb);
  delay(30);
}
