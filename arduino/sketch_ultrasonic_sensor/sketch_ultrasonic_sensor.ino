const int TrigPin = 13;
const int EchoPin = 12;
float cm;
void setup()
{
Serial.begin(9600);
pinMode(TrigPin, OUTPUT);
pinMode(EchoPin, INPUT);
}
void loop()
{
digitalWrite(TrigPin, LOW);
delayMicroseconds(2);
digitalWrite(TrigPin, HIGH);
delayMicroseconds(10);
digitalWrite(TrigPin, LOW);
cm = pulseIn(EchoPin, HIGH) / 58.0; //The echo time is converted into cmcm = (int(cm * 100.0)) / 100.0; //Keep two decimal places
Serial.print("distance=");
Serial.print(cm);
Serial.println();
delay(1000);
}