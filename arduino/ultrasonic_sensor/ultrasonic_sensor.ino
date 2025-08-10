// Sensor pin definitions
const int trigPin = 13;
const int echoPin = 12;

// Global variables
long duration;

// --- Structure to group tank data ---
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
const int numOutlierReadings = 5;
int outlierReadings[numOutlierReadings];

// --- Auxiliary Functions ---

// Function to sort an array of integers
void sort(int a[], int size) {
  for (int i = 0; i < size - 1; i++) {
    for (int j = i + 1; j < size; j++) {
      if (a[i] > a[j]) {
        int temp = a[i];
        a[i] = a[j];
        a[j] = temp;
      }
    }
  }
}

// Function to get a stable distance measurement by removing outliers
int getStableDistance() {
  for (int i = 0; i < numOutlierReadings; i++) {
    digitalWrite(trigPin, LOW);
    delayMicroseconds(2);
    digitalWrite(trigPin, HIGH);
    delayMicroseconds(10);
    digitalWrite(trigPin, LOW);
    duration = pulseIn(echoPin, HIGH);

    outlierReadings[i] = duration * 0.0343 / 2;
    delay(5);
  }

  sort(outlierReadings, numOutlierReadings);

  long sum = 0;
  for (int i = 1; i < numOutlierReadings - 1; i++) {
    sum += outlierReadings[i];
  }

  return sum / (numOutlierReadings - 2);
}

// Function to calculate the moving average of a new measurement
int getMovingAverage(int newMeasurement) {
  static const int numReadings = 10;
  static int readings[numReadings];
  static int readingIndex = 0;
  static long total = 0;

  if (total == 0) {
    for (int i = 0; i < numReadings; i++) {
      readings[i] = newMeasurement;
    }
    total = (long)newMeasurement * numReadings;
  }

  total = total - readings[readingIndex];
  readings[readingIndex] = newMeasurement;
  total = total + readings[readingIndex];
  readingIndex = (readingIndex + 1) % numReadings;

  return total / numReadings;
}

// Function to calculate useful level, volume, and percentage
TankData calculateTankData(int filteredDistance) {
  TankData data;

  float waterLevel = tankHeight - filteredDistance;
  float usefulLevel = waterLevel - minWaterDepth;

  if (usefulLevel < 0) {
    usefulLevel = 0;
  }

  data.usefulLevel = usefulLevel;
  data.volumeLiters = (volumePerCmCube * usefulLevel) / 1000.0;
  data.usefulPercentage = (usefulLevel / maxUsefulHeight) * 100;

  return data;
}

// --- Main Loop ---

void setup() {
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);

  Serial.begin(9600);
}

void loop() {
  // 1. Get a stabilized raw measurement (outlier filter)
  int stableMeasurement = getStableDistance();

  // 2. Smooth the measurement with a moving average
  int average = getMovingAverage(stableMeasurement);

  // 3. Perform all tank-related calculations
  TankData tankData = calculateTankData(average);

  // 4. Send the results to the serial port in a structured CSV format
  Serial.print("Niveau utile: ");
  Serial.print(tankData.usefulLevel);
  Serial.print(" cm, ");

  Serial.print("Volume: ");
  Serial.print(tankData.volumeLiters);
  Serial.print(" L, ");

  Serial.print("Pourcentage: ");
  Serial.print(tankData.usefulPercentage);
  Serial.print(" %");

  Serial.println(); // Add a new line at the end

  delay(100);
}
