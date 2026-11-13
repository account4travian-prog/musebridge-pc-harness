// MuseBridge serial BLE peripheral. No credentials, Wi-Fi or pairing crypto here.
#include <Arduino.h>
#include <NimBLEDevice.h>
#include <mbedtls/base64.h>
#include <ArduinoJson.h>

static const char *SERVICE = "7fdd3d1c-38ea-46cf-8b46-314ecf5f240c";
static const char *RX_UUID = "4d593029-28a2-4a6e-a1f0-3c2d5e8f9b01";
static const char *TX_UUID = "d75dc4ca-7b2b-4e9c-8f0a-1d2e3f4a5b6c";
static NimBLEServer *server;
static NimBLECharacteristic *tx;
static uint32_t epoch, generation = 0;
static uint16_t connectionHandle = BLE_HS_CONN_HANDLE_NONE;
static bool connected = false, configured = false;
static uint32_t lastHost = 0;
static SemaphoreHandle_t outputLock;

static void emit(JsonDocument &doc) {
  doc["v"] = 1; doc["epoch"] = epoch;
  xSemaphoreTake(outputLock, portMAX_DELAY);
  serializeJson(doc, Serial); Serial.println();
  xSemaphoreGive(outputLock);
}
static void event(const char *name) {
  JsonDocument doc; doc["event"] = name; doc["gen"] = generation; emit(doc);
}
class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *, ble_gap_conn_desc *desc) override {
    connectionHandle = desc->conn_handle;
    connected = true; ++generation; event("connect");
  }
  void onDisconnect(NimBLEServer *) override {
    connected = false; connectionHandle = BLE_HS_CONN_HANDLE_NONE; event("disconnect");
    if (configured) NimBLEDevice::startAdvertising();
  }
  void onMTUChange(uint16_t mtu, ble_gap_conn_desc *) override {
    JsonDocument doc; doc["event"] = "mtu"; doc["gen"] = generation;
    doc["mtu"] = mtu; emit(doc);
  }
};
class RxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic *characteristic) override {
    std::string value = characteristic->getValue();
    if (value.size() > 512) return;
    unsigned char encoded[685]; size_t length = 0;
    if (mbedtls_base64_encode(encoded, sizeof(encoded), &length,
        reinterpret_cast<const unsigned char *>(value.data()), value.size()) != 0) return;
    JsonDocument doc; doc["event"] = "rx"; doc["gen"] = generation;
    doc["data"] = std::string(reinterpret_cast<char *>(encoded), length); emit(doc);
  }
};
static void process(const String &line) {
  JsonDocument doc;
  if (deserializeJson(doc, line) || doc["v"].as<int>() != 1) return;
  String op = doc["op"].as<String>();
  if (op == "hello") { event("hello"); return; }
  if (doc["epoch"].as<uint32_t>() != epoch) return;
  lastHost = millis();
  if (op == "configure" && !connected) {
    String name = doc["name"].as<String>();
    if (name.length() != 16 || !name.startsWith("MuseGadget")) return;
    for (int i = 10; i < 16; ++i) if (!isxdigit(name[i])) return;
    NimBLEDevice::setDeviceName(name.c_str());
    auto adv = NimBLEDevice::getAdvertising(); adv->stop();
    adv->setName(name.c_str()); adv->addServiceUUID(SERVICE);
    adv->setManufacturerData(std::string("\xff\xff\x00", 3));
    adv->setScanResponse(true); adv->start();
    configured = true; event("ready"); return;
  }
  if (op == "keepalive") return;
  if (doc["gen"].as<uint32_t>() != generation) return;
  if (op == "disconnect") {
    if (connected) server->disconnect(connectionHandle);
  } else if (op == "tx") {
    unsigned char bytes[512]; size_t length = 0;
    String data = doc["data"].as<String>();
    bool ok = connected && data.length() <= 684 &&
      mbedtls_base64_decode(bytes, sizeof(bytes), &length,
        reinterpret_cast<const unsigned char *>(data.c_str()), data.length()) == 0;
    if (ok) {
      auto packet = ble_hs_mbuf_from_flat(bytes, length);
      ok = packet && ble_gatts_notify_custom(connectionHandle, tx->getHandle(), packet) == 0;
    }
    JsonDocument ack; ack["event"] = "ack"; ack["gen"] = generation;
    ack["id"] = doc["id"]; ack["ok"] = ok; emit(ack);
  }
}
void setup() {
  Serial.begin(115200);
  outputLock = xSemaphoreCreateMutex(); epoch = esp_random();
  NimBLEDevice::init("MuseBridgeSetup"); NimBLEDevice::setMTU(256);
  server = NimBLEDevice::createServer(); server->setCallbacks(new ServerCallbacks());
  auto svc = server->createService(SERVICE);
  svc->createCharacteristic(RX_UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR)
      ->setCallbacks(new RxCallbacks());
  tx = svc->createCharacteristic(TX_UUID, NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ);
  svc->start(); event("hello");
}
void loop() {
  static String line;
  static bool overflow = false;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      if (!overflow) process(line);
      line = ""; overflow = false;
    } else if (line.length() < 4096 && !overflow) line += c;
    else { overflow = true; line = ""; }
  }
  if (configured && millis() - lastHost > 15000) {
    configured = false; NimBLEDevice::getAdvertising()->stop();
    if (connected) server->disconnect(connectionHandle);
  }
  delay(2);
}
