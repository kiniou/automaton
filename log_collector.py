import asyncio
import serial_asyncio
import adafruit_dht
import board
import datetime
import logging
import sys
import os
import json

# Configuration des journaux
LOG_DIR = "./logs"
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(f"{LOG_DIR}/{datetime.date.today()}.log"),
                        logging.StreamHandler(sys.stdout)
                    ])

# Configuration du capteur GPIO (DHT)
DHT_PIN = board.D4
dht_device = adafruit_dht.DHT11(DHT_PIN, use_pulseio=False)

# Variable globale pour suivre la dernière température envoyée
last_sent_temp = None

# --- Fonctions asynchrones ---

async def read_serial_and_log(writer):
    """
    Lit les données JSON du port série, les journalise et renvoie les données traitées à la fonction principale.
    """
    reader, _ = await serial_asyncio.open_serial_connection(url='/dev/ttyACM0', baudrate=9600)
    while True:
        line_bytes = await reader.readuntil(b'\n')
        line = line_bytes.decode('utf-8', errors='ignore').strip()

        if line:
            try:
                json.loads(line)
                logging.info(f"Série JSON: {line}")
            except json.JSONDecodeError:
                logging.error(f"Erreur de décodage JSON sur la ligne : {line}")

        await asyncio.sleep(0.1)

async def read_gpio_sensor_and_send_serial(writer):
    """
    Lit la température et l'humidité du capteur GPIO, journalise les données et renvoie la température brute sur le port série si elle a changé.
    """
    global last_sent_temp

    while True:
        try:
            temperature = dht_device.temperature
            humidity = dht_device.humidity

            if temperature is not None and humidity is not None:
                # Envoi de la température sur le port série uniquement si elle a changé
                if last_sent_temp is None or temperature != last_sent_temp:
                    temperature_str = str(temperature) + '\n'
                    writer.write(temperature_str.encode('utf-8'))
                    await writer.drain()
                    last_sent_temp = temperature

                # Journalisation du couple température/humidité (toujours effectuée)
                gpio_data = {
                    "temperature": temperature,
                    "humidity": humidity
                }
                logging.info(f"GPIO JSON: {json.dumps(gpio_data)}")
            else:
                logging.warning("Échec de la lecture du capteur DHT.")
        except RuntimeError as error:
            logging.error(f"Erreur de lecture du capteur DHT : {error.args[0]}")
        except Exception as e:
            logging.error(f"Erreur générale de lecture du GPIO : {e}")
            break

        # Délai de 3 secondes pour la prochaine lecture
        await asyncio.sleep(3)

async def main():
    """ Fonction principale qui gère la communication série et l'exécution des tâches. """
    try:
        reader, writer = await serial_asyncio.open_serial_connection(url='/dev/ttyACM0', baudrate=9600)
        await asyncio.gather(read_serial_and_log(writer), read_gpio_sensor_and_send_serial(writer))
    except FileNotFoundError:
        logging.error("Le port série /dev/ttyACM0 n'a pas été trouvé.")
    except Exception as e:
        logging.error(f"Erreur lors de la configuration de la connexion série : {e}")
    finally:
        if 'dht_device' in locals():
            dht_device.exit()
        print("Arrêt du programme.")

if __name__ == "__main__":
    asyncio.run(main())
