#include <ArduinoJson.h>

// Sensor pin definitions
const int trigPin = 13;
const int echoPin = 12;

// Global variables
float duration;
float soundSpeedCmPerUs = 0.0343; // Default speed of sound at 20°C
float externalTemperature = 20.0; // Default external temperature

// --- Structure to group tank data (simplified) ---
struct TankData {
  float usefulLevel;
  float volumeLiters;
  float usefulPercentage;
};

// --- Tank parameters ---
const float tankHeight = 90.0;
const float tankRadius = 40.0;
const float minWaterDepth = 10.0;
const float maxUsefulHeight = tankHeight - minWaterDepth;
const float volumePerCmCube = 3.14159 * tankRadius * tankRadius;

// --- Outlier filter parameters ---
const int numOutlierReadings = 15;
const int numToTrim = 2;
float outlierReadings[numOutlierReadings];

// --- Moving average variables ---
const int numReadings = 10;
float readings[numReadings];
int readingIndex = 0;

// --- Timing variables for precise one-second loop ---
unsigned long previousMillis = 0;
const long interval = 1000;

// --- Auxiliary Functions ---
void sort(float a[], int size) {
  for (int i = 0; i < size - 1; i++) {
    for (int j = i + 1; j < size; j++) {
      if (a[i] > a[j]) {
        float temp = a[i];
        a[i] = a[j];
        a[j] = temp;
      }
    }
  }
}

void calculateSoundSpeed(float temp) {
  soundSpeedCmPerUs = (331.3 + 0.606 * temp) / 10000.0;
}

float getStableDistance(float& minOutlier, float& maxOutlier, float& minFiltered, float& maxFiltered) {
  for (int i = 0; i < numOutlierReadings; i++) {
    digitalWrite(trigPin, LOW);
    delayMicroseconds(2);
    digitalWrite(trigPin, HIGH);
    delayMicroseconds(10);
    digitalWrite(trigPin, LOW);
    duration = pulseIn(echoPin, HIGH);
    outlierReadings[i] = (int)(duration * soundSpeedCmPerUs / 2.0 * 10.0) / 10.0;
    delay(60);
  }
  sort(outlierReadings, numOutlierReadings);
  minOutlier = outlierReadings[0];
  maxOutlier = outlierReadings[numOutlierReadings - 1];
  minFiltered = outlierReadings[numToTrim];
  maxFiltered = outlierReadings[numOutlierReadings - numToTrim - 1];
  float sum = 0.0;
  for (int i = numToTrim; i < numOutlierReadings - numToTrim; i++) {
    sum += outlierReadings[i];
  }
  return sum / (float)(numOutlierReadings - 2 * numToTrim);
}

float getMovingAverage(float newMeasurement) {
  readings[readingIndex] = newMeasurement;
  readingIndex = (readingIndex + 1) % numReadings;

  float sum = 0.0;
  for (int i = 0; i < numReadings; i++) {
    sum += readings[i];
  }
  return sum / numReadings;
}

TankData calculateTankData(float movingAverage) {
  TankData data;
  float waterLevel = tankHeight - movingAverage;
  float usefulLevel = waterLevel - minWaterDepth;
  if (usefulLevel < 0) {
    usefulLevel = 0.0;
  }
  data.usefulLevel = usefulLevel;
  data.volumeLiters = (volumePerCmCube * usefulLevel) / 1000.0;
  data.usefulPercentage = (usefulLevel / maxUsefulHeight) * 100.0;
  return data;
}

// --- Main Loop ---
void setup() {
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
  Serial.begin(9600);
  for (int i = 0; i < numReadings; i++) {
    readings[i] = 0.0;
  }
}

void loop() {
  unsigned long currentMillis = millis();

  // Check for incoming temperature data from Raspberry Pi
  if (Serial.available() > 0) {
    String tempString = Serial.readStringUntil('\n');
    externalTemperature = tempString.toFloat();
    // Le calcul de la vitesse du son se fait directement avec la température externe
    calculateSoundSpeed(externalTemperature);
  }

  if (currentMillis - previousMillis >= interval) {
    previousMillis = currentMillis;
    float minOutlier, maxOutlier, minFiltered, maxFiltered;
    float outlierFilteredDistance = getStableDistance(minOutlier, maxOutlier, minFiltered, maxFiltered);
    float average = getMovingAverage(outlierFilteredDistance);
    TankData tankData = calculateTankData(average);
    StaticJsonDocument<200> doc;

    doc["brut_filtre"] = outlierFilteredDistance;
    doc["outliers_min"] = minOutlier;
    doc["outliers_max"] = maxOutlier;
    doc["filtrees_min"] = minFiltered;
    doc["filtrees_max"] = maxFiltered;
    doc["average"] = average;
    doc["temperature"] = externalTemperature; // Affichage de la température externe
    doc["niveau_utile"] = tankData.usefulLevel;
    doc["volume_litres"] = tankData.volumeLiters;
    doc["pourcentage"] = tankData.usefulPercentage;

    serializeJson(doc, Serial, 1);
    Serial.println();
  }
}
