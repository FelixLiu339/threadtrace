// ThreadTrace LED app (Arduino App Lab, MCU side).
// Scrolls the latest field event ("+1 A000074", "12.4KG -35L") on the 8x13 LED matrix.
// NOTE: if the LED/Bridge API names differ in your App Lab version, copy the setup from the
// official "Air quality on LED matrix" example and keep only the show() function below.
#include <Arduino_RouterBridge.h>
#include "ArduinoGraphics.h"
#include "Arduino_LED_Matrix.h"

ArduinoLEDMatrix matrix;
String current = "THREADTRACE";

void show(String text) {
  current = text;
}

void setup() {
  matrix.begin();
  Bridge.begin();
  Bridge.provide("show", show);
}

void loop() {
  matrix.beginDraw();
  matrix.stroke(0xFFFFFFFF);
  matrix.textScrollSpeed(55);
  matrix.textFont(Font_5x7);
  matrix.beginText(0, 1, 0xFFFFFF);
  matrix.println("  " + current + "  ");
  matrix.endText(SCROLL_LEFT);
  matrix.endDraw();
}
