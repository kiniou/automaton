import argparse
import datetime
import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Static,
    TabbedContent,
    TabPane,
)
from textual.worker import Worker, WorkerState
from textual_plotext import PlotextPlot

if TYPE_CHECKING:
    from textual.events import Key

DB_PATH = "data.db"

# Configuration centralisée des graphiques
PLOT_CONFIG = [
    {
        "id": "reservoir",
        "title": "Réservoir",
        "plots": [
            {
                "data_key": "niveau_utile",
                "label": "Niveau",
                "unit": "cm",
                "color": "red",
                "yside": "left",
                "ylim": [0, 50],
            },
            {
                "data_key": "volume_litres",
                "label": "Volume",
                "unit": "L",
                "color": "orange",
                "yside": "right",
                "ylim": [0, 250],
            },
        ],
    },
    {
        "id": "capteurs",
        "title": "Capteurs",
        "plots": [
            {
                "data_key": "temperature",
                "label": "Température",
                "unit": "°C",
                "color": "blue",
                "yside": "left",
                "ylim": [0, 60],
            },
            {
                "data_key": "humidity",
                "label": "Humidité",
                "unit": "%",
                "color": "green",
                "yside": "right",
                "ylim": [0, 100],
            },
        ],
    },
]


class Indicator(Static):
    """Widget pour afficher une seule valeur mise en forme."""

    def __init__(self, label: str, unit: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.label = label
        self.unit = unit
        self.value: Optional[float] = None

    def update_value(self, value: Optional[float]) -> None:
        """Met à jour la valeur affichée par l'indicateur."""
        self.value = value
        if value is None:
            display_value = "-.--"
        else:
            display_value = f"{value:.1f}"

        self.update(
            f"[b]{self.label}[/b]\n[bold green]{display_value}[/] [dim]{self.unit}[/dim]"
        )


class RefreshDialog(ModalScreen[Optional[int]]):
    """Boîte de dialogue pour changer l'intervalle de rafraîchissement."""

    CSS = """
    RefreshDialog {
        align: center middle;
    }
    #dialog-container {
        width: 40%;
        height: 30%;
        padding: 2;
        background: $surface;
        border: thick $primary;
    }
    #dialog-container > * { margin-bottom: 1; }
    #dialog-buttons {
        layout: horizontal;
        width: 100%;
        align-horizontal: center;
    }
    #dialog-buttons > Button { margin: 0 1; }
    """

    def __init__(self, initial_value: int) -> None:
        super().__init__()
        self.initial_value = str(initial_value)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-container"):
            yield Static("Set refresh rate (in seconds):")
            self.input_field = Input(
                placeholder="Seconds...", value=self.initial_value, type="integer"
            )
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
    """Application Textual pour visualiser des données de log."""

    CSS = """
    #plots_container { height: 1fr; }
    .overview_plot { height: 1fr; }
    #indicators_container {
        layout: grid;
        grid-size: 2 2;
        grid-gutter: 1;
        padding: 1;
    }
    Indicator {
        content-align: center middle;
        text-align: center;
        border: round $primary;
        height: 1fr;
    }
    """
    data_hash: reactive[str] = reactive("")
    refresh_interval_seconds: reactive[int] = reactive(5)
    current_end_time: reactive[datetime.datetime] = reactive(datetime.datetime.now())
    current_date: reactive[datetime.date] = reactive(datetime.date.today())
    follow_mode: reactive[bool] = reactive(True)
    show_grid: reactive[bool] = reactive(False)

    BINDINGS = [
        ("d", "toggle_dark", "Toggle dark mode"),
        ("q", "quit", "Quit"),
        ("f", "toggle_follow_mode", "Follow"),
        ("g", "toggle_grid", "Toggle Grid"),
        ("j", "move_backward", "Move Time ←"),
        ("k", "move_forward", "Move Time →"),
        ("h", "change_day_backward", "Prev Day ↑"),
        ("l", "change_day_forward", "Next Day ↓"),
        ("r", "show_refresh_dialog", "Refresh"),
    ]

    refresh_timer: Optional[Timer] = None
    read_worker: Optional[Worker[Any]] = None
    parsed_data_from_worker: Optional[Dict[str, Any]] = None

    def __init__(
        self,
        time_window_seconds: int,
        refresh_interval_seconds: int,
        *args: Any,
        **kwargs: Any,
    ) -> None:
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
            with TabPane("Indicateurs", id="tab_indicators"):
                with Vertical(id="indicators_container"):
                    yield Indicator("Niveau", "cm", id="indicator_niveau")
                    yield Indicator("Volume", "L", id="indicator_volume")
                    yield Indicator("Température", "°C", id="indicator_temperature")
                    yield Indicator("Humidité", "%", id="indicator_humidity")

            for config in PLOT_CONFIG:
                with TabPane(config["title"], id=f"tab_{config['id']}"):
                    yield PlotextPlot(id=f"plot_tab_{config['id']}")

    def on_mount(self) -> None:
        self.refresh_interval_seconds = self._initial_refresh_interval
        self.action_snap_to_now()

    def _prepare_aggregated_data(
        self, raw_data: Dict[str, Any], plot_width: int
    ) -> Dict[str, Any]:
        serial_times = raw_data.get("serial_times", [])
        serial_data = raw_data.get("serial_data", {})
        gpio_times = raw_data.get("gpio_times", [])
        gpio_data = raw_data.get("gpio_data", {})

        aggregated_results = {}

        # Aggregate all keys found in serial data
        for key, values in serial_data.items():
            agg_times, agg_values = self.aggregate_data_median(
                serial_times, values, self.time_window_seconds, plot_width
            )
            aggregated_results[key] = {"times": agg_times, "values": agg_values}

        # Aggregate all keys found in GPIO data
        for key, values in gpio_data.items():
            agg_times, agg_values = self.aggregate_data_median(
                gpio_times, values, self.time_window_seconds, plot_width
            )
            aggregated_results[key] = {"times": agg_times, "values": agg_values}

        return aggregated_results

    def update_displays(self, parsed_data: Optional[Dict[str, Any]]) -> None:
        """Met à jour tous les affichages (graphiques et indicateurs)."""
        self.update_indicator_pane(parsed_data)
        self.update_graph_panes(parsed_data)

    def update_indicator_pane(self, parsed_data: Optional[Dict[str, Any]]) -> None:
        """Met à jour les indicateurs avec les dernières valeurs."""
        last_values = {}
        if parsed_data:
            all_data_keys = ["niveau_utile", "volume_litres", "temperature", "humidity"]
            for key in all_data_keys:
                source_data = (
                    parsed_data["serial_data"]
                    if "niveau" in key or "volume" in key
                    else parsed_data["gpio_data"]
                )
                last_value = next(
                    (v for v in reversed(source_data.get(key, [])) if v is not None),
                    None,
                )
                last_values[key.split("_")[0]] = last_value

        self.query_one("#indicator_niveau", Indicator).update_value(
            last_values.get("niveau")
        )
        self.query_one("#indicator_volume", Indicator).update_value(
            last_values.get("volume")
        )
        self.query_one("#indicator_temperature", Indicator).update_value(
            last_values.get("temperature")
        )
        self.query_one("#indicator_humidity", Indicator).update_value(
            last_values.get("humidity")
        )

    def update_graph_panes(self, parsed_data: Optional[Dict[str, Any]]) -> None:
        """Met à jour les onglets des graphiques."""
        start_time = self.current_end_time - datetime.timedelta(
            seconds=self.time_window_seconds
        )

        for config in PLOT_CONFIG:
            plot_widget = self.query_one(f"#plot_tab_{config['id']}", PlotextPlot)
            plot_widget.plt.clf()
            plot_widget.plt.grid(self.show_grid)

            if not parsed_data or (
                not parsed_data.get("serial_times")
                and not parsed_data.get("gpio_times")
            ):
                plot_widget.plt.title("Aucune donnée disponible")
                plot_widget.refresh()
                continue

            plot_width = plot_widget.plt.plot_size()[0]
            aggregated_data = self._prepare_aggregated_data(parsed_data, plot_width)

            plot_widget.plt.title(config["title"])

            tick_positions, tick_labels = self._generate_time_ticks(
                start_time, self.time_window_seconds
            )
            plot_widget.plt.xticks(tick_positions, tick_labels)

            for plot_info in config["plots"]:
                data = aggregated_data.get(plot_info["data_key"])
                if data and data["times"]:
                    plot_widget.plt.ylim(
                        *plot_info.get("ylim", [0, None]), yside=plot_info["yside"]
                    )
                    plot_widget.plt.ylabel(plot_info["label"], yside=plot_info["yside"])
                    plot_widget.plt.plot(
                        data["times"],
                        data["values"],
                        color=plot_info["color"],
                        label=plot_info["label"],
                        yside=plot_info["yside"],
                    )

            plot_widget.refresh()

    def aggregate_data_median(
        self,
        times: List[float],
        values: List[float],
        time_window_seconds: int,
        plot_width: int,
    ) -> Tuple[List[float], List[float]]:
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
        aggregated_times: List[float] = []
        aggregated_values: List[float] = []
        for bin_index, bin_values in sorted(bins.items()):
            if not bin_values:
                continue
            sorted_bin = sorted(bin_values)
            n = len(sorted_bin)
            mid = n // 2
            if n % 2 == 0:
                median = (sorted_bin[mid - 1] + sorted_bin[mid]) / 2
            else:
                median = sorted_bin[mid]
            aggregated_times.append(bin_index * seconds_per_cell)
            aggregated_values.append(median)
        return aggregated_times, aggregated_values

    def _generate_time_ticks(
        self, start_time: datetime.datetime, window_seconds: int, num_ticks: int = 5
    ) -> Tuple[List[float], List[str]]:
        if window_seconds <= 0:
            return [], []
        ticks_pos, ticks_labels = [], []
        interval = window_seconds / (num_ticks - 1)
        for i in range(num_ticks):
            seconds_offset = i * interval
            ticks_pos.append(seconds_offset)
            tick_time = start_time + datetime.timedelta(seconds=seconds_offset)
            ticks_labels.append(tick_time.strftime("%H:%M"))
        return ticks_pos, ticks_labels

    def watch_data_hash(self, old_hash: str, new_hash: str) -> None:
        if new_hash and old_hash != new_hash:
            self.log("Data has changed, refreshing displays.")
            self.call_later(self.update_displays, self.parsed_data_from_worker)

    def watch_show_grid(self) -> None:
        self.update_displays(self.parsed_data_from_worker)

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

    def run_worker_safely(self) -> None:
        if self.read_worker and not self.read_worker.is_finished:
            self.log("Worker already running, skipping launch.")
            return
        self.log("Launching a new worker.")
        self.read_worker = self.run_worker(
            self.read_logs_from_db, thread=True, exclusive=True
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker != self.read_worker:
            return
        if event.state == WorkerState.SUCCESS:
            parsed_data: Optional[Dict[str, Any]] = event.worker.result
            if parsed_data:
                self.parsed_data_from_worker = parsed_data
                data_str = json.dumps(parsed_data, sort_keys=True)
                self.data_hash = hashlib.md5(data_str.encode("utf-8")).hexdigest()
            self.update_info_static()
            self.read_worker = None
        elif event.state == WorkerState.ERROR:
            self.log(f"Worker error: {event.worker.error}")
            self.read_worker = None

    def update_info_static(self) -> None:
        start_time = self.current_end_time - datetime.timedelta(
            seconds=self.time_window_seconds
        )
        mode_indicator = "(  Live  )" if self.follow_mode else "( Paused )"
        self.info_static.update(
            f"{mode_indicator} Date: {self.current_date.strftime('%Y-%m-%d')} | "
            f"Fenêtre: {start_time.strftime('%H:%M:%S')} - {self.current_end_time.strftime('%H:%M:%S')} | "
            f"Délai: {self.refresh_interval_seconds}s"
        )

    def setup_database(self) -> None:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS logs (timestamp TEXT NOT NULL, type TEXT NOT NULL, json TEXT)"
            )
            conn.commit()

    def read_logs_from_db(self) -> Optional[Dict[str, Any]]:
        try:
            with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
                cursor = conn.cursor()
                start_time = self.current_end_time - datetime.timedelta(
                    seconds=self.time_window_seconds
                )
                query = "SELECT timestamp, type, json FROM logs WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp ASC"
                cursor.execute(
                    query, (start_time.isoformat(), self.current_end_time.isoformat())
                )
                results = cursor.fetchall()

                serial_rows, gpio_rows = [], []
                serial_keys, gpio_keys = set(), set()

                for timestamp_str, log_type, json_str in results:
                    log_time = datetime.datetime.fromisoformat(timestamp_str)
                    total_seconds = (log_time - start_time).total_seconds()
                    data = json.loads(json_str)

                    if log_type == "Série JSON":
                        serial_rows.append((total_seconds, data))
                        serial_keys.update(
                            k for k, v in data.items() if isinstance(v, (int, float))
                        )
                    elif log_type == "GPIO JSON":
                        gpio_rows.append((total_seconds, data))
                        gpio_keys.update(
                            k for k, v in data.items() if isinstance(v, (int, float))
                        )

                serial_times = [row[0] for row in serial_rows]
                gpio_times = [row[0] for row in gpio_rows]

                serial_data = {
                    key: [row[1].get(key) for row in serial_rows] for key in serial_keys
                }
                gpio_data = {
                    key: [row[1].get(key) for row in gpio_rows] for key in gpio_keys
                }

                return {
                    "serial_times": serial_times,
                    "serial_data": serial_data,
                    "gpio_times": gpio_times,
                    "gpio_data": gpio_data,
                }
        except sqlite3.Error as e:
            self.log(f"Erreur de base de données : {e}")
            return None

    def action_show_refresh_dialog(self) -> None:
        def set_refresh_interval(new_interval: Optional[int]) -> None:
            if new_interval is not None:
                self.refresh_interval_seconds = new_interval
                self.update_info_static()

        self.push_screen(
            RefreshDialog(self.refresh_interval_seconds), set_refresh_interval
        )

    def action_toggle_follow_mode(self) -> None:
        self.follow_mode = not self.follow_mode

    def action_toggle_grid(self) -> None:
        self.show_grid = not self.show_grid

    def action_snap_to_now(self) -> None:
        self.current_end_time = datetime.datetime.now()
        self.current_date = self.current_end_time.date()
        self.follow_mode = True
        self.run_worker_safely()

    def action_change_day_backward(self) -> None:
        self.follow_mode = False
        self.current_date -= datetime.timedelta(days=1)
        self.current_end_time = datetime.datetime.combine(
            self.current_date, datetime.time.max
        )
        self.run_worker_safely()

    def action_change_day_forward(self) -> None:
        new_date = self.current_date + datetime.timedelta(days=1)
        if new_date <= datetime.date.today():
            self.current_date = new_date
            if self.current_date == datetime.date.today():
                self.action_snap_to_now()
            else:
                self.follow_mode = False
                self.current_end_time = datetime.datetime.combine(
                    self.current_date, datetime.time.max
                )
                self.run_worker_safely()

    def action_move_backward(self) -> None:
        self.follow_mode = False
        self.current_end_time -= datetime.timedelta(
            seconds=self.time_window_seconds / 2
        )
        self.current_date = self.current_end_time.date()
        self.run_worker_safely()

    def action_move_forward(self) -> None:
        new_end_time = self.current_end_time + datetime.timedelta(
            seconds=self.time_window_seconds / 2
        )
        if new_end_time >= datetime.datetime.now():
            self.action_snap_to_now()
        else:
            self.follow_mode = False
            self.current_end_time = new_end_time
            self.current_date = self.current_end_time.date()
            self.run_worker_safely()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Affiche un graphique de données de log avec une fenêtre de temps glissante et navigable."
    )
    parser.add_argument(
        "--window",
        type=int,
        default=1,
        help="Fenêtre de temps en heures. Par défaut, 1 heure.",
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=5,
        help="Délai de rafraîchissement en secondes. Par défaut, 5 secondes.",
    )
    args = parser.parse_args()
    time_window_seconds: int = args.window * 3600
    app = LogGraphApp(
        time_window_seconds=time_window_seconds, refresh_interval_seconds=args.refresh
    )
    app.run()


if __name__ == "__main__":
    main()
