import asyncio
import json
import sqlite3
import datetime
import board
import adafruit_dht
import serial
import serial_asyncio

# --- Configuration ---

# Chemin de la base de données SQLite
DB_PATH = "data.db"
# Port série (adaptez si nécessaire, /dev/ttyACM0 est courant pour les Arduinos)
SERIAL_PORT = '/dev/ttyACM0'
# Vitesse de communication
BAUDRATE = 9600
# Intervalle de lecture du capteur GPIO en secondes
GPIO_INTERVAL_SECONDS = 3

# --- Initialisation du capteur ---

# CORRECTION : Utilisation du pin D18 pour le capteur DHT11.
try:
    dht_sensor = adafruit_dht.DHT11(board.D18)
except NotImplementedError:
    print("La plateforme ne supporte pas 'pulseio', essayez d'exécuter en tant que root ou vérifiez la configuration.")
    dht_sensor = None
except Exception as e:
    print(f"Impossible d'initialiser le capteur DHT11 : {e}")
    dht_sensor = None


def setup_database():
    """Crée la base de données et la table logs si elles n'existent pas."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                json TEXT
            )
        """)
        conn.commit()

async def read_dht11(db_conn, serial_writer):
    """
    Tâche asynchrone pour lire le capteur DHT11, stocker les données
    et envoyer la température sur le port série.
    """
    if not dht_sensor:
        print("Capteur DHT non initialisé. La tâche de lecture GPIO ne peut pas démarrer.")
        return

    while True:
        try:
            await asyncio.sleep(GPIO_INTERVAL_SECONDS)
            
            current_time = datetime.datetime.now()
            temperature = dht_sensor.temperature
            humidity = dht_sensor.humidity

            if humidity is not None and temperature is not None:
                gpio_data = {"temperature": round(temperature, 2), "humidity": round(humidity, 2)}
                gpio_json_str = json.dumps(gpio_data)
                timestamp = current_time.isoformat()
                
                cursor = db_conn.cursor()
                cursor.execute("INSERT INTO logs (timestamp, type, json) VALUES (?, ?, ?)",
                               (timestamp, "GPIO JSON", gpio_json_str))
                db_conn.commit()
                print(f"Log GPIO inséré : {gpio_json_str}")

                if serial_writer:
                    temp_to_send = f"{temperature:.1f}\n".encode('utf-8')
                    serial_writer.write(temp_to_send)
                    await serial_writer.drain()
                    print(f"Température envoyée via Série : {temp_to_send.strip().decode()}")

        except RuntimeError as error:
            print(f"Erreur de lecture du DHT11: {error.args[0]}")
        except Exception as e:
            print(f"Erreur inattendue dans la tâche DHT11 : {e}")


async def read_serial(db_conn, serial_reader):
    """
    Tâche asynchrone pour lire les données du port série dès qu'elles sont disponibles.
    """
    while True:
        try:
            line_bytes = await serial_reader.readline()
            if not line_bytes:
                continue

            line = line_bytes.decode('utf-8', errors='ignore').strip()
            if line:
                current_time = datetime.datetime.now()
                try:
                    json.loads(line)
                    timestamp = current_time.isoformat()
                    cursor = db_conn.cursor()
                    cursor.execute("INSERT INTO logs (timestamp, type, json) VALUES (?, ?, ?)",
                                   (timestamp, "Série JSON", line))
                    db_conn.commit()
                    print(f"Log Série JSON inséré : {line}")
                except json.JSONDecodeError:
                    print(f"Ligne non-JSON reçue : {line}")

        except Exception as e:
            print(f"Erreur dans la tâche de lecture série : {e}")
            await asyncio.sleep(1)

async def main():
    """Fonction principale qui configure et lance les tâches asynchrones."""
    setup_database()
    
    db_conn = sqlite3.connect(DB_PATH)

    try:
        print(f"Tentative de connexion au port série {SERIAL_PORT}...")
        
        reader, writer = await serial_asyncio.open_serial_connection(
            url=SERIAL_PORT, 
            baudrate=BAUDRATE
        )
        
        print(f"Connecté ! Écoute sur {SERIAL_PORT} et lecture du capteur GPIO.")

        await asyncio.gather(
            read_dht11(db_conn, writer),
            read_serial(db_conn, reader)
        )

    except serial.serialutil.SerialException as e:
        print(f"ERREUR : Impossible de se connecter au port série {SERIAL_PORT}. {e}")
        print("Vérifiez que le périphérique est bien connecté et que le nom du port est correct.")
    except Exception as e:
        print(f"Une erreur est survenue dans la boucle principale : {e}")
    finally:
        db_conn.close()
        print("Connexion à la base de données fermée.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nArrêt du collecteur.")
