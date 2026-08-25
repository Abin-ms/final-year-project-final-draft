#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include <ESP32Servo.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ================= PINS =================
#define IN1 14
#define IN2 27
#define IN3 26
#define IN4 25

#define HUMAN_TRIG 5
#define HUMAN_ECHO 18

// NEW SENSORS FOR DRY AND WET BIN FILL LEVELS
#define DRY_TRIG 22
#define DRY_ECHO 23

#define WET_TRIG 19
#define WET_ECHO 21

#define SOIL_DO 32
#define SERVO_PIN 33

#define LED_PIN 23

// ================= WIFI & SERVER =================
const char* WIFI_SSID     = "12345";
const char* WIFI_PASSWORD = "12345678";

// ================= CONFIGURABLE PARAMS =================
String flaskIP = "10.33.17.112"; // IP of Laptop running Flask
int flaskPort = 5000;
int BIN_ID = 1;

// These will be updated automatically from the Flask Backend!
int stepperDelayUs = 1500;
unsigned long lidOpenTime = 10000; // 10 sec
unsigned long cooldownTime = 5000;  // 5 sec
int humanThreshold = 50;
int binDepthCm = 17;

const int OPEN_ANGLE = 180;
const int CLOSED_ANGLE = 100;
const int MIN_DISTANCE_CM = 3;

// ================= GLOBAL =================
Servo lidServo;

bool lidOpen = false;
unsigned long lidOpenTimeStart = 0;

bool inCooldown = false;
unsigned long cooldownStart = 0;

bool staffOverride = false;
unsigned long overrideTimer = 0;
const unsigned long OVERRIDE_TIMEOUT = 10000;

float dryFill = 0;
float wetFill = 0;

int stepIndex = 0;
bool directionLocked = false;
int motorDirection = -1; // -1 = DRY, 1 = WET

int steps[4][4] = {
  {1,0,0,0},
  {0,1,0,0},
  {0,0,1,0},
  {0,0,0,1}
};

void stepMotor(int dir);
void doFullRotation();
void stopMotor();

// ================= BLE GLOBAL =================
BLEServer* pServer = NULL;
BLECharacteristic* pTxCharacteristic = NULL;
bool deviceConnected = false;
bool oldDeviceConnected = false;

#define SERVICE_UUID           "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID_TX "beb5483e-36e1-4688-b7f5-ea07361b26a8"
#define CHARACTERISTIC_UUID_RX "8c3b7082-f5ce-4927-99e2-51a7b1b36a71"

class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
      Serial.println("[BLE] Device Connected!");
    };
    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
      Serial.println("[BLE] Device Disconnected!");
    }
};

class MyCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      std::string rxValue = pCharacteristic->getValue();
      if (rxValue.length() > 0) {
        String payload = String(rxValue.c_str());
        Serial.println("[BLE] RX: " + payload);
        
        if (payload.indexOf("\"cmd\":\"open_lid\"") >= 0 || payload.indexOf("open_lid") >= 0) {
          staffOverride = true;
          overrideTimer = millis();
          lidServo.write(180); // OPEN_ANGLE
          lidOpen = true;
          Serial.println("[BLE] Manual Open Lid");
        }
        else if (payload.indexOf("\"cmd\":\"close_lid\"") >= 0 || payload.indexOf("close_lid") >= 0) {
          staffOverride = false;
          lidServo.write(100); // CLOSED_ANGLE
          lidOpen = false;
          
          doFullRotation();
          
          digitalWrite(14, LOW); digitalWrite(27, LOW); digitalWrite(26, LOW); digitalWrite(25, LOW);
          Serial.println("[BLE] Manual Close Lid");
        }
        else if (payload.indexOf("\"cmd\":\"reset_stepper\"") >= 0 || payload.indexOf("reset_stepper") >= 0) {
          Serial.println("[BLE] Reset Stepper");
          digitalWrite(14, LOW); digitalWrite(27, LOW); digitalWrite(26, LOW); digitalWrite(25, LOW);
        }
        else if (payload.indexOf("\"cmd\":\"config\"") >= 0) {
          // Parse config JSON over BLE
          auto parseConfig = [](String json, String key, int currentVal) -> int {
            int idx = json.indexOf("\"" + key + "\":");
            if (idx == -1) return currentVal;
            int start = idx + key.length() + 3; 
            int end1 = json.indexOf(",", start);
            int end2 = json.indexOf("}", start);
            int end = (end1 != -1 && (end1 < end2 || end2 == -1)) ? end1 : end2;
            if (end == -1) return currentVal;
            return json.substring(start, end).toInt();
          };
          
          stepperDelayUs = parseConfig(payload, "stepper_speed", stepperDelayUs);
          lidOpenTime    = parseConfig(payload, "open_time", lidOpenTime);
          cooldownTime   = parseConfig(payload, "cooldown_time", cooldownTime);
          humanThreshold = parseConfig(payload, "human_threshold", humanThreshold);
          binDepthCm     = parseConfig(payload, "bin_depth", binDepthCm);
          Serial.println("[BLE] Hardware Configuration Updated via Bluetooth");
        }
      }
    }
};

// Removed duplicate variable definitions from here

unsigned long lastServerUpdate = 0;

// ================= FUNCTIONS =================

float getDistance(int trig, int echo) {
  digitalWrite(trig, LOW); delayMicroseconds(2);
  digitalWrite(trig, HIGH); delayMicroseconds(10);
  digitalWrite(trig, LOW);

  long duration = pulseIn(echo, HIGH, 30000);
  if (duration == 0) return 999;
  return duration * 0.034 / 2;
}

float distanceToPercent(float dist) {
  if (dist >= binDepthCm || dist == 999.0) return 0.0;
  float clamped = constrain(dist, (float)MIN_DISTANCE_CM, (float)binDepthCm);
  return ((binDepthCm - clamped) / (float)(binDepthCm - MIN_DISTANCE_CM)) * 100.0f;
}

void doFullRotation() {
  Serial.println("🔄 Rotating motor for 30 seconds post-close");
  
  // NOTE: If speed is "too slow" and it's a 28BYJ-48 stepper, it might be missing steps!
  // A delay of 1500us is generally the sweet spot for max speed without skipping/stalling.
  int speedDelay = 1500; 
  
  unsigned long startT = millis();
  int stepCounter = 0;
  
  // Run for exactly 30 seconds (30000 ms)
  while (millis() - startT < 5000) {
    stepMotor(motorDirection);
    delayMicroseconds(speedDelay);
    
    stepCounter++;
    // Yield every 50 steps to prevent ESP32 Watchdog Crash
    if (stepCounter % 50 == 0) yield(); 
  }
  stopMotor();
}

void stepMotor(int dir) {
  stepIndex = (stepIndex + dir + 4) % 4;
  digitalWrite(IN1, steps[stepIndex][0]);
  digitalWrite(IN2, steps[stepIndex][1]);
  digitalWrite(IN3, steps[stepIndex][2]);
  digitalWrite(IN4, steps[stepIndex][3]);
}

void stopMotor() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

void postBinLevels(float humanDist) {
  HTTPClient http;
  String url = "http://" + flaskIP + ":" + String(flaskPort) + "/api/bins/update";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  String body = "{\"bin_id\":" + String(BIN_ID)
              + ",\"dry_level\":" + String((int)dryFill)
              + ",\"wet_level\":" + String((int)wetFill)
              + ",\"human_dist\":" + String((int)humanDist) + "}";

  int code = http.POST(body);
  if (code > 0) {
    Serial.printf("[POST] dry=%.0f%% wet=%.0f%% human=%.0fcm\n", dryFill, wetFill, humanDist);
  }
  http.end();
}

void pollCommands() {
  HTTPClient http;
  String url = "http://" + flaskIP + ":" + String(flaskPort) + "/api/hardware/poll/" + String(BIN_ID);
  http.begin(url);
  int code = http.GET();

  if (code == 200) {
    String payload = http.getString();
    
    if (payload.indexOf("open_lid") >= 0) {
      staffOverride = true;
      overrideTimer = millis();
      lidServo.write(OPEN_ANGLE);
      lidOpen = true;
      Serial.println("[WEB] Manual Open Lid Command Received");
    }
    if (payload.indexOf("close_lid") >= 0) {
      staffOverride = false;
      lidServo.write(CLOSED_ANGLE);
      lidOpen = false;
      
      doFullRotation();
      
      Serial.println("[WEB] Manual Close Lid Command Received");
    }

    // --- Dynamically Update Configuration Variables from Flask ---
    auto parseConfig = [](String json, String key, int currentVal) -> int {
        int idx = json.indexOf("\"" + key + "\":");
        if (idx == -1) return currentVal;
        int start = idx + key.length() + 3; // skip past "key": (with space)
        int end1 = json.indexOf(",", start);
        int end2 = json.indexOf("}", start);
        int end = (end1 != -1 && (end1 < end2 || end2 == -1)) ? end1 : end2;
        if (end == -1) return currentVal;
        return json.substring(start, end).toInt();
    };

    stepperDelayUs = parseConfig(payload, "stepper_speed", stepperDelayUs);
    lidOpenTime    = parseConfig(payload, "open_time", lidOpenTime);
    cooldownTime   = parseConfig(payload, "cooldown_time", cooldownTime);
    humanThreshold = parseConfig(payload, "human_threshold", humanThreshold);
    binDepthCm     = parseConfig(payload, "bin_depth", binDepthCm);

  }
  http.end();
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);

  pinMode(IN1, OUTPUT); pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT); pinMode(IN4, OUTPUT);

  pinMode(HUMAN_TRIG, OUTPUT); pinMode(HUMAN_ECHO, INPUT);
  pinMode(DRY_TRIG, OUTPUT);   pinMode(DRY_ECHO, INPUT);
  pinMode(WET_TRIG, OUTPUT);   pinMode(WET_ECHO, INPUT);

  pinMode(SOIL_DO, INPUT);
  pinMode(LED_PIN, OUTPUT);

  lidServo.attach(SERVO_PIN);
  lidServo.write(CLOSED_ANGLE);

  Serial.printf("\n[WIFI] Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  
  // Non-blocking WiFi check (10 seconds timeout)
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WIFI] Connected!");
    Serial.print("[WIFI] IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[WIFI] Failed to connect. Running autonomously offline.");
  }

  // ================= BLE SETUP =================
  BLEDevice::init("SmartBin_01");
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  BLEService *pService = pServer->createService(SERVICE_UUID);
  pTxCharacteristic = pService->createCharacteristic(
                      CHARACTERISTIC_UUID_TX,
                      BLECharacteristic::PROPERTY_NOTIFY
                    );
  pTxCharacteristic->addDescriptor(new BLE2902());

  BLECharacteristic *pRxCharacteristic = pService->createCharacteristic(
                       CHARACTERISTIC_UUID_RX,
                       BLECharacteristic::PROPERTY_WRITE
                     );
  pRxCharacteristic->setCallbacks(new MyCallbacks());

  pService->start();
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->setMinPreferred(0x06);  
  pAdvertising->setMinPreferred(0x12);
  BLEDevice::startAdvertising();
  Serial.println("[BLE] Bluetooth Ready. Pair as 'SmartBin_01'");

  Serial.println("🚀 SMART BIN SYSTEM READY");
}

// ================= LOOP =================
void loop() {
  unsigned long now = millis();

  // ================= 1. HUMAN DETECTION =================
  float humanDist = getDistance(HUMAN_TRIG, HUMAN_ECHO);
  bool humanNear = (humanDist > 5 && humanDist < humanThreshold);

  // ================= 2. COOLDOWN =================
  if (inCooldown && (now - cooldownStart >= cooldownTime)) {
    inCooldown = false;
    Serial.println("Cooldown finished");
  }

  // ================= 3. AUTO OPEN LID =================
// ================= 3. AUTO OPEN LID =================
bool binFull = (dryFill >= 90.0 || wetFill >= 90.0);

if (humanNear && !lidOpen && !inCooldown && !staffOverride && !binFull) {
  Serial.println("👤 Human detected → OPEN");
  lidServo.write(OPEN_ANGLE);     
  lidOpen = true;
  lidOpenTimeStart = now;

  directionLocked = false;
  motorDirection = -1;
}

  // ================= 4. STEPPER CONTROL =================
  if (lidOpen && !staffOverride) {
    int soilState = digitalRead(SOIL_DO);
    
    if (!directionLocked) {
      if (soilState == LOW) {
        Serial.println("💧 WET detected → LOCK REVERSE");
        motorDirection = 1;
        directionLocked = true;
      } else {
        Serial.println("🌵 DRY → FORWARD");
        motorDirection = -1;
      }
    }
    
    stepMotor(motorDirection);
    delayMicroseconds(stepperDelayUs);
  }

  // ================= 5. AUTO CLOSE LID =================
  if (lidOpen && humanNear && !staffOverride) {
    lidOpenTimeStart = now; // Keep timer reset as long as human is near
  }

  if (lidOpen && !staffOverride && (now - lidOpenTimeStart >= lidOpenTime)) {
    Serial.println("⛔ Closing lid");
    lidServo.write(CLOSED_ANGLE);
    lidOpen = false;
    
    doFullRotation();
    
    inCooldown = true;
    cooldownStart = now;
  }

  // ================= 6. STAFF OVERRIDE TIMEOUT =================
  if (staffOverride && (now - overrideTimer >= OVERRIDE_TIMEOUT)) {
    Serial.println("[OVERRIDE] Timeout — closing lid");
    staffOverride = false;
    lidServo.write(CLOSED_ANGLE);
    lidOpen = false;
    
    doFullRotation();
  }

  // ================= 7. SERVER SYNC & SENSOR READS =================
  if (now - lastServerUpdate >= 2000) {
    lastServerUpdate = now;
    
    // Only check sensors & HTTP POST when NOT sorting to keep motors perfectly smooth
    if (!lidOpen) {
       float dryDist = getDistance(DRY_TRIG, DRY_ECHO);
       float wetDist = getDistance(WET_TRIG, WET_ECHO);
       dryFill = distanceToPercent(dryDist);
       wetFill = distanceToPercent(wetDist);

       if (deviceConnected) {
         // 🔵 BLUETOOTH MODE (Preferred when nearby)
         String json = "{\"dry_level\":" + String((int)dryFill) + ",\"wet_level\":" + String((int)wetFill) + ",\"human_dist\":" + String((int)humanDist) + "}";
         pTxCharacteristic->setValue(json.c_str());
         pTxCharacteristic->notify();
       } else {
         // 🌐 WI-FI MODE (Fallback when disconnected)
         if (WiFi.status() == WL_CONNECTED && flaskIP.length() > 5) {
           postBinLevels(humanDist);
           pollCommands();
         }
       }
    }
  }

  // ================= 8. BLE RECONNECT LOGIC =================
  if (!deviceConnected && oldDeviceConnected) {
      delay(500); 
      pServer->startAdvertising(); 
      Serial.println("[BLE] Restarting Advertising");
      oldDeviceConnected = deviceConnected;
  }
  if (deviceConnected && !oldDeviceConnected) {
      oldDeviceConnected = deviceConnected;
  }

  // This delay is critical! It prevents the stepper motor from being driven too fast
  // and stalling out, matching exactly how your tested logic operated.
  delay(10);
}
