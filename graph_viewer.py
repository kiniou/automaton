import argparse
import datetime
import json
import hashlib
import sqlite3
from typing import Dict, Any, Optional, List, Tuple, TYPE_CHECKING
import humanize

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Input, Button, TabbedContent, TabPane
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.worker import Worker, WorkerState
from textual.reactive import reactive
from textual_plotext import PlotextPlot, plot
from textual.timer import Timer

if TYPE_CHECKING:
    from textual.events import Key

DB_PATH = "data.db"

# Configuration centralisée des graphiques
PLOT_CONFIG = [
    {
        "id": "reservoir",
        "title": "Réservoir",
        "plots": [
            { "data_key": "niveau", "label": "Niveau utile (cm)", "color": "red", "yside": "left", },
            { "data_key": "volume", "label": "Volume (L)", "color": "orange", "yside": "right", },
        ],
    },
    {
        "id": "capteurs",
        "title": "Capteurs",
        "plots": [
            { "data_key": "temperature", "label": "Température (°C)", "color": "blue", "yside": "left", },
            { "data_key": "humidity", "label": "Humidité (%)", "color": "green", "yside": "left", },
        ],
    },
]

class RefreshDialog(ModalScreen[Optional[int]]):
    """A dialog to set the refresh rate, with auto-sizing and keyboard controls."""
    CSS = """
    RefreshDialog { align: center middle; }
    #dialog-container { width: auto; height: auto; padding: 2; background: $surface; border: thick $primary; }
    #dialog-container > * { margin-bottom: 1; }
    #dialog-buttons { width: 100%; align-horizontal: right; }
    """
    def __init__(self, initial_value: int) -> None:
        super().__init__()
        self.initial_value = str(initial_value)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-container"):
            yield Static("Set refresh rate (in seconds):")
            self.input_field = Input(placeholder="Seconds...", value=self.initial_value, type="integer")
            yield self.input_field
            with Horizontal(id="dialog-buttons"):
                yield Button("OK", variant="primary", id="ok-button")
                yield Button("Cancel", id="cancel-button")

    def _validate_and_dismiss(self) -> None:
        try:
            new_interval = int(self.input_field.value)
            if new_interval > 0:
                self.dismiss(new_interval)
            else:
                self.app.bell()
        except (ValueError, TypeError):
            self.app.bell()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok-button":
            self._validate_and_dismiss()
        elif event.button.id == "cancel-button":
            self.dismiss()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._validate_and_dismiss()

    def on_key(self, event: "Key") -> None:
        if event.key == "escape":
            self.dismiss()


class LogGraphApp(App[None]):
    """A Textual app to visualize log data in a graph."""
    CSS = """
    #plots_container {
        height: 1fr;
    }
    .overview_plot {
        height: 1fr;
    }
    """
    data_hash: reactive[str] = reactive("")
    refresh_interval_seconds: reactive[int] = reactive(5)
    current_end_time: reactive[datetime.datetime] = reactive(datetime.datetime.now())
    current_date: reactive[datetime.date] = reactive(datetime.date.today())
    follow_mode: reactive[bool] = reactive(True)

    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit", "Quit"),
        ("f", "toggle_follow_mode", "Follow"),
        ("h", "change_day_backward", "Prev Day"),
        ("j", "move_backward", "Backward"),
        ("k", "move_forward", "Forward"),
        ("l", "change_day_forward", "Next Day"),
        ("r", "show_refresh_dialog", "Set Delay"),
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

        with TabbedContent(id="plots_container"):
            # Onglet "Vue d'ensemble"
            with TabPane("Vue d'ensemble", id="tab_overview"):
                with Vertical():
                    for config in PLOT_CONFIG:
                        yield PlotextPlot(id=f"plot_overview_{config['id']}", classes="overview_plot")
            
            # Onglets individuels
            for config in PLOT_CONFIG:
                with TabPane(config["title"], id=f"tab_{config['id']}"):
                    yield PlotextPlot(id=f"plot_tab_{config['id']}")

    def on_mount(self) -> None:
        self.refresh_interval_seconds = self._initial_refresh_interval
        self.action_snap_to_now()

    def _prepare_averaged_data(self, raw_data: Dict[str, Any], plot_width: int) -> Dict[str, Any]:
        serial_times = raw_data.get("serial_times", [])
        serial_data = raw_data.get("serial_data", {})
        gpio_times = raw_data.get("gpio_times", [])
        gpio_data = raw_data.get("gpio_data", {})

        datasets_to_process = {
            "niveau": (serial_times, serial_data.get("niveau_utile", [])),
            "volume": (serial_times, serial_data.get("volume_litres", [])),
            "temperature": (gpio_times, gpio_data.get("temperature", [])),
            "humidity": (gpio_times, gpio_data.get("humidity", [])),
        }

        averaged_results = {}
        for name, (times, values) in datasets_to_process.items():
            avg_times, avg_values = self.average_data(times, values, self.time_window_seconds, plot_width)
            averaged_results[name] = {"times": avg_times, "values": avg_values}

        return averaged_results

    def update_graph(self, parsed_data: Optional[Dict[str, Any]]) -> None:
        """Met à jour tous les graphiques (vue d'ensemble et onglets)."""
        time_delta_str = humanize.naturaldelta(datetime.timedelta(seconds=self.time_window_seconds))

        for config in PLOT_CONFIG:
            # Récupère les deux widgets pour ce graphique
            plot_widgets = [
                self.query_one(f"#plot_overview_{config['id']}", PlotextPlot),
                self.query_one(f"#plot_tab_{config['id']}", PlotextPlot),
            ]

            for plot_widget in plot_widgets:
                plot_widget.plt.clf()

                if not parsed_data or (not parsed_data.get("serial_times") and not parsed_data.get("gpio_times")):
                    plot_widget.plt.title("Aucune donnée disponible")
                    plot_widget.refresh()
                    continue
                plot_size = plot_widget.plt.plot_size()
                plot_width, _ = plot_size
                averaged_data = self._prepare_averaged_data(parsed_data, plot_width)

                plot_widget.plt.title(config["title"])
                plot_widget.plt.xlabel(f"Temps ({time_delta_str})")


                for plot_info in config["plots"]:
                    data = averaged_data[plot_info["data_key"]]
                    if data["times"]:
                        plot_widget.plt.ylabel(plot_info["label"], yside=plot_info["yside"])
                        plot_widget.plt.ylim(lower=0, yside=plot_info["yside"])
                        plot_widget.plt.plot(
                            data["times"], data["values"], color=plot_info["color"],
                            label=plot_info["label"], yside=plot_info["yside"]
                        )
                plot_widget.refresh()

    def average_data(self, times: List[float], values: List[float], time_window_seconds: int, plot_width: int) -> Tuple[List[float], List[float]]:
        if not times or not values or plot_width == 0:
            return [], []

        seconds_per_cell = time_window_seconds / plot_width
        bins: Dict[int, List[float]] = {}

        for time_point, value_point in zip(times, values):
            if value_point is None:
                continue

            bin_index = int(time_point / seconds_per_cell)
            if bin_index not in bins:
                bins[bin_index] = []
            bins[bin_index].append(value_point)

        averaged_times: List[float] = []
        averaged_values: List[float] = []

        for bin_index, bin_values in sorted(bins.items()):
            if bin_values:
                averaged_times.append(bin_index * seconds_per_cell)
                averaged_values.append(sum(bin_values) / len(bin_values))

        return averaged_times, averaged_values

    def watch_refresh_interval_seconds(self, old_val: int, new_val: int) -> None:
        self.log(f"Refresh interval changed: {old_val} -> {new_val}")
        if self.refresh_timer is not None:
            self.refresh_timer.stop()

        if new_val > 0:
            self.refresh_timer = self.set_interval(new_val, self._live_update)

    def watch_follow_mode(self, new_val: bool) -> None:
        self.log(f"Follow mode set to {new_val}")
        if new_val:
            self.action_snap_to_now()

        self.update_info_static()

    def _live_update(self) -> None:
        if self.follow_mode:
            self.current_end_time = datetime.datetime.now()
            self.run_worker_safely()

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
        if event.worker != self.read_worker:
            return

        if event.state == WorkerState.SUCCESS:
            parsed_data: Optional[Dict[str, Any]] = event.worker.result
            if parsed_data:
                self.parsed_data_from_worker = parsed_data
                data_str = json.dumps(parsed_data, sort_keys=True)
                self.data_hash = hashlib.md5(data_str.encode('utf-8')).hexdigest()

            self.update_info_static()
            self.read_worker = None
        elif event.state == WorkerState.ERROR:
            self.log(f"Worker error: {event.worker.error}")
            self.read_worker = None

    def update_info_static(self) -> None:
        start_time = self.current_end_time - datetime.timedelta(seconds=self.time_window_seconds)
        mode_indicator = "Live" if self.follow_mode else "Paused"
        self.info_static.update(
            f"| {mode_indicator} | Date: {self.current_date.strftime('%Y-%m-%d')} | "
            f"Fenêtre: {start_time.strftime('%H:%M:%S')} - {self.current_end_time.strftime('%H:%M:%S')} | "
            f"Délai: {self.refresh_interval_seconds}s"
        )

    def setup_database(self) -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS logs (timestamp TEXT NOT NULL, type TEXT NOT NULL, json TEXT)")
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

    def action_toggle_follow_mode(self) -> None:
        self.follow_mode = not self.follow_mode

    def action_snap_to_now(self) -> None:
        self.current_end_time = datetime.datetime.now()
        self.current_date = self.current_end_time.date()
        self.follow_mode = True
        self.run_worker_safely()

    # def action_next_tab(self) -> None:
    #     """Passe à l'onglet suivant de manière générique."""
    #     tabs = self.query_one(TabbedContent)
    #     panes = self.query(TabPane)
    #     active_index = panes.nodes.index(tabs.active_pane)
    #     next_index = (active_index + 1) % len(panes)
    #     tabs.active = panes.nodes[next_index].id
    #
    # def action_previous_tab(self) -> None:
    #     """Passe à l'onglet précédent de manière générique."""
    #     tabs = self.query_one(TabbedContent)
    #     panes = self.query(TabPane)
    #     active_index = panes.nodes.index(tabs.active_pane)
    #     previous_index = (active_index - 1 + len(panes)) % len(panes)
    #     tabs.active = panes.nodes[previous_index].id

    def action_change_day_backward(self) -> None:
        self.follow_mode = False
        self.current_date -= datetime.timedelta(days=1)
        self.current_end_time = datetime.datetime.combine(self.current_date, datetime.time.max)
        self.run_worker_safely()

    def action_change_day_forward(self) -> None:
        new_date = self.current_date + datetime.timedelta(days=1)
        if new_date <= datetime.date.today():
            self.current_date = new_date
            if self.current_date == datetime.date.today():
                self.action_snap_to_now()
            else:
                self.follow_mode = False
                self.current_end_time = datetime.datetime.combine(self.current_date, datetime.time.max)
                self.run_worker_safely()

    def action_move_backward(self) -> None:
        self.follow_mode = False
        self.current_end_time -= datetime.timedelta(seconds=self.time_window_seconds / 2)
        self.current_date = self.current_end_time.date()
        self.run_worker_safely()

    def action_move_forward(self) -> None:
        new_end_time = self.current_end_time + datetime.timedelta(seconds=self.time_window_seconds / 2)
        if new_end_time >= datetime.datetime.now():
            self.action_snap_to_now()
        else:
            self.follow_mode = False
            self.current_end_time = new_end_time
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
