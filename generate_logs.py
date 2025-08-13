import json
import datetime
import random
import sqlite3

# Utiliser le même chemin de base de données
DB_PATH = "data.db"

def setup_database():
    """Crée la base de données et la table logs si elles n'existent pas."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            timestamp TEXT NOT NULL,
            type TEXT NOT NULL,
            json TEXT
        )
    """)
    conn.commit()
    conn.close()

def generate_and_insert_logs(num_days=3, log_interval_seconds=60):
    setup_database()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Supprimer toutes les données existantes avant de générer les nouvelles
    cursor.execute("DELETE FROM logs")
    conn.commit()

    end_time = datetime.datetime.now()
    start_time = end_time - datetime.timedelta(days=num_days)

    current_time = start_time
    print(f"Génération de {num_days} jours de données...")

    with conn:
        while current_time < end_time:
            timestamp_str = current_time.isoformat()

            # Données série
            niveau_utile = random.uniform(10, 50)
            volume_litres = niveau_utile * 10
            serial_data = {
                "brut_filtre": random.uniform(100, 200),
                "niveau_utile": round(niveau_utile, 2),
                "volume_litres": round(volume_litres, 2),
                "pourcentage": round((niveau_utile - 10) / 40 * 100, 2)
            }
            cursor.execute("INSERT INTO logs (timestamp, type, json) VALUES (?, ?, ?)",
                           (timestamp_str, "Série JSON", json.dumps(serial_data)))

            # Données GPIO
            gpio_data = {
                "temperature": random.uniform(20, 30),
                "humidity": random.uniform(50, 70)
            }
            cursor.execute("INSERT INTO logs (timestamp, type, json) VALUES (?, ?, ?)",
                           (timestamp_str, "GPIO JSON", json.dumps(gpio_data)))

            current_time += datetime.timedelta(seconds=log_interval_seconds)

    print(f"Génération de {cursor.rowcount} logs terminée.")
    conn.close()

if __name__ == "__main__":
    generate_and_insert_logs()

