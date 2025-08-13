import argparse
import datetime
import json
import hashlib
import sqlite3
from typing import Dict, Any, Optional

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Input, Button
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.worker import Worker, WorkerState
from textual.reactive import reactive
from textual_plotext import PlotextPlot
from textual.timer import Timer

DB_PATH = "data.db"

class RefreshDialog(ModalScreen[Optional[int]]):
    """A dialog to set the refresh rate."""
    BINDINGS = [
        ("escape", "app.pop_screen", "Cancel"),
    ]

    def __init__(self, initial_value: int) -> None:
        super().__init__()
        self.initial_value = str(initial_value)

    def compose(self) -> ComposeResult:
        with Grid(id="dialog"):
            yield Static("Set refresh rate (in seconds)", classes="dialog-title")
            self.input_field = Input(placeholder="Enter a number...", value=self.initial_value, type="integer", id="refresh-input")
            yield self.input_field
            yield Button("OK", variant="primary", id="ok-button")
            yield Button("Cancel", id="cancel-button")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok-button":
            try:
                new_interval = int(self.input_field.value)
                if new_interval > 0:
                    self.dismiss(new_interval)
                else:
                    self.app.bell()
            except (ValueError, TypeError):
                self.app.bell()
        elif event.button.id == "cancel-button":
            self.dismiss()

class LogGraphApp(App[None]):
    """A Textual app to visualize log data in a graph."""
    data_hash: reactive[str] = reactive("")
    refresh_interval_seconds: reactive[int] = reactive(5)
    current_end_time: reactive[datetime.datetime] = reactive(datetime.datetime.now())
    current_date: reactive[datetime.date] = reactive(datetime.date.today())

    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit", "Quit"),
        ("left", "move_backward", "Move backward"),
        ("right", "move_forward", "Move forward"),
        ("r", "show_refresh_dialog", "Refresh rate"),
        ("up", "change_day_backward", "Jour précédent"),
        ("down", "change_day_forward", "Jour suivant"),
    ]
    
    refresh_timer: Optional[Timer] = None
    read_worker: Optional[Worker[Any]] = None
    parsed_data_from_worker: Optional[Dict[str, Any]] = None

    def __init__(self, time_window_seconds: int, refresh_interval_seconds: int, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.time_window_seconds = time_window_seconds
        self._initial_refresh_interval = refresh_interval_seconds
        self.setup_database()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()
        self.info_static = Static("", classes="header-static")
        yield self.info_static
        yield PlotextPlot(id="data_plot")

    def on_mount(self) -> None:
        self.refresh_interval_seconds = self._initial_refresh_interval
        self.watch_current_date(datetime.date.today(), self.current_date)

    def watch_refresh_interval_seconds(self, old_val: int, new_val: int) -> None:
        self.log(f"Refresh interval changed: {old_val} -> {new_val}")
        if self.refresh_timer is not None:
            self.refresh_timer.stop()
        if new_val > 0:
            self.run_worker_safely()
            self.refresh_timer = self.set_interval(new_val, self.run_worker_safely)

    def watch_current_date(self, old_val: datetime.date, new_val: datetime.date) -> None:
        self.log(f"Viewing date changed: {old_val} -> {new_val}")
        end_of_day = datetime.datetime.combine(new_val, datetime.time.max)
        now = datetime.datetime.now()
        self.current_end_time = min(end_of_day, now)
        self.run_worker_safely()
        self.update_info_static()

    def watch_data_hash(self, old_hash: str, new_hash: str) -> None:
        if new_hash and old_hash != new_hash:
            self.log("Data has changed, refreshing graph.")
            self.call_later(self.update_graph, self.parsed_data_from_worker)

    def run_worker_safely(self) -> None:
        if self.read_worker and not self.read_worker.is_finished:
            self.log("Worker already running, skipping launch.")
            return
        self.log("Launching a new worker.")
        self.read_worker = self.run_worker(self.read_logs_from_db, thread=True, exclusive=True)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Called when the worker state changes."""
        # On s'assure que l'événement vient du bon worker
        if event.worker != self.read_worker:
            return

        # Si le worker a terminé avec succès, on traite le résultat
        if event.state == WorkerState.SUCCESS:
            parsed_data: Optional[Dict[str, Any]] = event.worker.result
            
            if parsed_data:
                self.parsed_data_from_worker = parsed_data
                data_str = json.dumps(parsed_data, sort_keys=True)
                self.data_hash = hashlib.md5(data_str.encode('utf-8')).hexdigest()
            
            self.update_info_static()
            self.read_worker = None # On réinitialise la référence au worker

        # Si le worker a échoué, on peut logger l'erreur
        elif event.state == WorkerState.ERROR:
            self.log(f"Worker error: {event.worker.error}")
            self.read_worker = None

    def update_info_static(self) -> None:
        start_time = self.current_end_time - datetime.timedelta(seconds=self.time_window_seconds)
        self.info_static.update(
            f"Date: {self.current_date.strftime('%Y-%m-%d')} | "
            f"Fenêtre: {start_time.strftime('%H:%M:%S')} - {self.current_end_time.strftime('%H:%M:%S')} | "
            f"Délai: {self.refresh_interval_seconds}s | "
            f"(Nav: ←/→, Jour: ↑/↓, Délai: r, Quitter: q)"
        )

    def update_graph(self, parsed_data: Optional[Dict[str, Any]]) -> None:
        plot = self.query_one(PlotextPlot)
        plot.plt.clf()

        if not parsed_data or (not parsed_data.get("serial_times") and not parsed_data.get("gpio_times")):
            plot.plt.subplots(1, 1)
            ax = plot.plt.subplot(1, 1)
            ax.title("Aucune donnée disponible pour cette période")
            plot.refresh()
            return

        plot.plt.subplots(3, 1)

        serial_times = parsed_data.get("serial_times", [])
        serial_data = parsed_data.get("serial_data", {})
        gpio_times = parsed_data.get("gpio_times", [])
        gpio_data = parsed_data.get("gpio_data", {})

        ax1 = plot.plt.subplot(1, 1)
        ax1.title("Niveau du réservoir")
        ax1.ylabel("Niveau (cm)")
        ax1.xlim(0, self.time_window_seconds)
        if serial_times and "niveau_utile" in serial_data:
            ax1.plot(serial_times, serial_data["niveau_utile"], color="red", label="Niveau utile (cm)")

        ax2 = plot.plt.subplot(2, 1)
        ax2.title("Volume du réservoir")
        ax2.ylabel("Volume (L)")
        ax2.xlim(0, self.time_window_seconds)
        if serial_times and "volume_litres" in serial_data:
            ax2.plot(serial_times, serial_data["volume_litres"], color="orange", label="Volume (L)")

        ax3 = plot.plt.subplot(3, 1)
        ax3.title("Température et Humidité")
        ax3.xlabel("Temps (s)")
        ax3.ylabel("Capteurs GPIO")
        ax3.xlim(0, self.time_window_seconds)
        if gpio_times and "temperature" in gpio_data:
            ax3.plot(gpio_times, gpio_data["temperature"], color="blue", label="Température (°C)")
        if gpio_times and "humidity" in gpio_data:
            ax3.plot(gpio_times, gpio_data["humidity"], color="green", label="Humidité (%)")
        
        plot.refresh()

    def setup_database(self) -> None:
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

    def read_logs_from_db(self) -> Optional[Dict[str, Any]]:
        try:
            with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
                cursor = conn.cursor()
                start_time = self.current_end_time - datetime.timedelta(seconds=self.time_window_seconds)
                query = "SELECT timestamp, type, json FROM logs WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp ASC"
                cursor.execute(query, (start_time.isoformat(), self.current_end_time.isoformat()))
                results = cursor.fetchall()
                serial_times, serial_data = [], {"niveau_utile": [], "volume_litres": []}
                gpio_times, gpio_data = [], {"temperature": [], "humidity": []}
                for timestamp_str, log_type, json_str in results:
                    log_time = datetime.datetime.fromisoformat(timestamp_str)
                    total_seconds = (log_time - start_time).total_seconds()
                    data = json.loads(json_str)
                    if log_type == "Série JSON":
                        serial_times.append(total_seconds)
                        for key in serial_data:
                            serial_data[key].append(data.get(key))
                    elif log_type == "GPIO JSON":
                        gpio_times.append(total_seconds)
                        for key in gpio_data:
                            gpio_data[key].append(data.get(key))
                return {"serial_times": serial_times, "serial_data": serial_data, "gpio_times": gpio_times, "gpio_data": gpio_data}
        except sqlite3.Error as e:
            self.log(f"Erreur de base de données : {e}")
            return None

    def action_show_refresh_dialog(self) -> None:
        def set_refresh_interval(new_interval: Optional[int]) -> None:
            if new_interval is not None:
                self.refresh_interval_seconds = new_interval
                self.update_info_static()
        
        self.push_screen(RefreshDialog(self.refresh_interval_seconds), set_refresh_interval)
            
    def action_change_day_backward(self) -> None:
        self.current_date -= datetime.timedelta(days=1)
        
    def action_change_day_forward(self) -> None:
        new_date = self.current_date + datetime.timedelta(days=1)
        if new_date <= datetime.date.today():
            self.current_date = new_date

    def action_move_backward(self) -> None:
        self.current_end_time -= datetime.timedelta(seconds=self.time_window_seconds / 2)
        self.run_worker_safely()
        
    def action_move_forward(self) -> None:
        new_end_time = self.current_end_time + datetime.timedelta(seconds=self.time_window_seconds / 2)
        self.current_end_time = min(new_end_time, datetime.datetime.now())
        if self.current_end_time.date() != self.current_date:
            self.current_date = self.current_end_time.date()
        self.run_worker_safely()

def main() -> None:
    parser = argparse.ArgumentParser(description="Affiche un graphique de données de log avec une fenêtre de temps glissante et navigable.")
    parser.add_argument("--window", type=int, default=1, help="Fenêtre de temps en heures. Par défaut, 1 heure.")
    parser.add_argument("--refresh", type=int, default=5, help="Délai de rafraîchissement en secondes. Par défaut, 5 secondes.")
    args = parser.parse_args()
    time_window_seconds: int = args.window * 3600
    app = LogGraphApp(time_window_seconds=time_window_seconds, refresh_interval_seconds=args.refresh)
    app.run()

if __name__ == "__main__":
    main()
