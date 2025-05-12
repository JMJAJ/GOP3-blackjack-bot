import sys
import time
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QComboBox, QMessageBox, QGraphicsView, QGraphicsScene, QDialog, QDialogButtonBox,
    QHeaderView, QAbstractItemView, QTextEdit, QGraphicsPixmapItem, QSizePolicy, QMenu
)
from PyQt5.QtGui import QIntValidator, QCursor, QPixmap, QImage # Import QCursor, QPixmap, QImage
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer # Import QObject, QTimer
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from pynput import mouse # For global mouse listener
import numpy as np # For image conversion
import cv2 # For image conversion

from constant import SUPPORTED_LANGUAGE, LANGUAGE_MAP, CHEAT_SHEET, BET_AMOUNT
from blackjack import ProgramThread
from utils import (
    find_windows_by_title, get_window_title, screen_to_client,
    capture_window_region_to_pil, # Keep for preview
    map_std_to_custom_coords # Keep for mapping calculation example if needed
)
import win32gui # For SetForegroundWindow

# --- Global Listener Setup (same as before) ---
class MouseSignalEmitter(QObject):
     mouse_clicked = pyqtSignal(int, int, mouse.Button)

mouse_emitter = MouseSignalEmitter()
current_listener = None

def on_click(x, y, button, pressed):
    global current_listener
    if pressed:
        # print(f'GUI: Global click detected at ({x}, {y}) with {button}') # Less noisy
        mouse_emitter.mouse_clicked.emit(x, y, button)
        if current_listener:
             # Use QTimer to stop the listener safely from the listener thread
             QTimer.singleShot(0, current_listener.stop)
             current_listener = None
             # print("GUI: Mouse listener stop scheduled.") # Less noisy
        return False # Stop listener
    return True

def start_mouse_listener():
     global current_listener
     if current_listener:
         # print("GUI: Listener already running.") # Less noisy
         return
     try:
        listener = mouse.Listener(on_click=on_click)
        listener.start()
        current_listener = listener
        # print("GUI: Mouse listener started.") # Less noisy
     except Exception as e:
         print(f"Error starting mouse listener: {e}")
         QMessageBox.critical(None, "Listener Error", f"Could not start mouse listener:\n{e}")


# --- Simple Dialog for Instructions (same as before) ---
class InstructionDialog(QDialog):
     def __init__(self, message, parent=None):
         super().__init__(parent)
         self.setWindowTitle("Action Required")
         layout = QVBoxLayout(self)
         layout.addWidget(QLabel(message))
         buttons = QDialogButtonBox(QDialogButtonBox.Ok)
         buttons.accepted.connect(self.accept)
         layout.addWidget(buttons)

# --- Main App ---
class App(QWidget):
    # define_area_coords_signal = pyqtSignal(int, int, int, int) # Not needed, handled internally

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Blackjack Bot - Dynamic Multi-Instance v2")
        self.setGeometry(50, 50, 1600, 950) # Adjusted size

        # --- Data Structures ---
        self.configured_bots = {} # {hwnd: {"title": title, "rect": (l,t,r,b)|None, "table_row": row_idx, "game_id": id|None, "status": "Idle", "preview_img": QPixmap|None}}
        self.active_threads = {}  # {game_id: ProgramThread}
        self.bot_stats = {} # {game_id: {stats}} - Now tracks more per-bot info
        self.next_game_id = 1
        self.defining_area_for_hwnd = None # Track which HWND we are defining area for
        self.defining_area_stage = 0 # 0: idle, 1: waiting for top-left, 2: waiting for bottom-right
        self.temp_coords = {} # Store first click coords

        # --- GUI Layout ---
        main_layout = QHBoxLayout()
        left_panel = QVBoxLayout()
        middle_panel = QVBoxLayout()
        right_panel = QVBoxLayout()

        # --- Left Panel: Window Selection & Config ---
        # Window Selection
        sel_group = QVBoxLayout()
        sel_group.addWidget(QLabel("<b>1. Available Windows</b>"))
        self.window_filter_input = QLineEdit("Governor of Poker 3")
        self.refresh_windows_button = QPushButton("Refresh List")
        self.refresh_windows_button.clicked.connect(self.refresh_window_list)
        self.available_windows_list = QListWidget()
        self.available_windows_list.setFixedHeight(150)
        self.add_window_button = QPushButton(">> Add Selected Window >>")
        self.add_window_button.clicked.connect(self.add_selected_window)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        filter_layout.addWidget(self.window_filter_input)
        filter_layout.addWidget(self.refresh_windows_button)
        sel_group.addLayout(filter_layout)
        sel_group.addWidget(self.available_windows_list)
        sel_group.addWidget(self.add_window_button, alignment=Qt.AlignCenter)
        left_panel.addLayout(sel_group)

        # General Config
        config_group_box = QGridLayout()
        config_group_box.addWidget(QLabel("<b>2. General Configuration</b>"), 0, 0, 1, 2)
        # Language
        self.language_label = QLabel("GOP3 Language:")
        self.language_input = QComboBox()
        for language in SUPPORTED_LANGUAGE: self.language_input.addItem(language)
        config_group_box.addWidget(self.language_label, 1, 0)
        config_group_box.addWidget(self.language_input, 1, 1)
        # Bet Amount
        self.bet_amount_label = QLabel("Bet Amount:")
        self.bet_amount_input = QComboBox()
        self.bet_amount_input.addItems(list(BET_AMOUNT.keys()))
        config_group_box.addWidget(self.bet_amount_label, 2, 0)
        config_group_box.addWidget(self.bet_amount_input, 2, 1)
        # Stop Conditions (apply per bot)
        self.num_games_label = QLabel("Max Hands/Bot:")
        self.num_games_input = QLineEdit("1000"); self.num_games_input.setValidator(QIntValidator())
        config_group_box.addWidget(self.num_games_label, 3, 0)
        config_group_box.addWidget(self.num_games_input, 3, 1)
        self.stop_win_label = QLabel("Stop Profit (units):")
        self.stop_win_input = QLineEdit("100"); self.stop_win_input.setValidator(QIntValidator())
        config_group_box.addWidget(self.stop_win_label, 4, 0)
        config_group_box.addWidget(self.stop_win_input, 4, 1)
        self.stop_lose_label = QLabel("Stop Loss (units):")
        self.stop_lose_input = QLineEdit("100"); self.stop_lose_input.setValidator(QIntValidator())
        config_group_box.addWidget(self.stop_lose_label, 5, 0)
        config_group_box.addWidget(self.stop_lose_input, 5, 1)
        left_panel.addLayout(config_group_box)
        left_panel.addStretch() # Pushes config to top

        # --- Middle Panel: Configured Bots Table & Controls ---
        conf_group = QVBoxLayout()
        conf_group.addWidget(QLabel("<b>3. Configured Bot Instances</b>"))
        self.bots_table = QTableWidget()
        self.bots_table.setColumnCount(8) # HWND, Title, Status, Area, Preview, Hands, Net Win, Last Action
        self.bots_table.setHorizontalHeaderLabels(["HWND", "Title", "Status", "Cap. Area", "Preview", "Hands", "Net Win", "Last Info"])
        self.bots_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.bots_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.bots_table.verticalHeader().setVisible(False)
        self.bots_table.setEditTriggers(QAbstractItemView.NoEditTriggers) # Read-only
        self.bots_table.itemSelectionChanged.connect(self.update_bot_button_states)
        # Adjust column widths
        self.bots_table.setColumnWidth(0, 60)  # HWND
        self.bots_table.setColumnWidth(1, 150) # Title
        self.bots_table.setColumnWidth(2, 80) # Status
        self.bots_table.setColumnWidth(3, 120) # Area
        self.bots_table.setColumnWidth(4, 100) # Preview
        self.bots_table.setColumnWidth(5, 50)  # Hands
        self.bots_table.setColumnWidth(6, 60)  # Net Win
        self.bots_table.horizontalHeader().setStretchLastSection(True) # Last Action fills space
        conf_group.addWidget(self.bots_table)

        bot_control_layout = QHBoxLayout()
        self.define_area_button = QPushButton("Define Capture Area (for selected)")
        self.define_area_button.clicked.connect(self.initiate_define_area)
        self.define_area_button.setEnabled(False)
        self.remove_bot_button = QPushButton("Remove Selected Bot")
        self.remove_bot_button.clicked.connect(self.remove_selected_bot)
        self.remove_bot_button.setEnabled(False)
        bot_control_layout.addWidget(self.define_area_button)
        bot_control_layout.addWidget(self.remove_bot_button)
        conf_group.addLayout(bot_control_layout)

        # Start/Stop All
        start_stop_layout = QHBoxLayout()
        self.start_button = QPushButton("Start All Ready Bots")
        self.start_button.clicked.connect(self.start_all_bots)
        self.stop_button = QPushButton("Stop All Running Bots")
        self.stop_button.clicked.connect(self.stop_all_bots)
        start_stop_layout.addWidget(self.start_button)
        start_stop_layout.addWidget(self.stop_button)
        conf_group.addLayout(start_stop_layout)
        middle_panel.addLayout(conf_group)

        # --- Right Panel: Stats, Graph, Logs ---
        right_panel.addWidget(QLabel("<b>Aggregated Stats & Performance</b>"))
        # Aggregated Stats
        agg_stats_layout = QGridLayout()
        self.agg_total_hand_label = QLabel("Total Hands: 0")
        self.agg_total_win_label = QLabel("Wins: 0")
        self.agg_total_lose_label = QLabel("Losses: 0")
        self.agg_total_draw_label = QLabel("Draws: 0")
        self.agg_running_bots_label = QLabel("Running Bots: 0")
        self.agg_net_win_label = QLabel("Total Net Win (Units): 0.0")
        agg_stats_layout.addWidget(self.agg_total_hand_label, 0, 0)
        agg_stats_layout.addWidget(self.agg_running_bots_label, 0, 1)
        agg_stats_layout.addWidget(self.agg_total_win_label, 1, 0)
        agg_stats_layout.addWidget(self.agg_total_lose_label, 1, 1)
        agg_stats_layout.addWidget(self.agg_total_draw_label, 2, 0)
        agg_stats_layout.addWidget(self.agg_net_win_label, 2, 1)
        right_panel.addLayout(agg_stats_layout)

        # Graph
        self.graph_view = QGraphicsView()
        self.graph_scene = QGraphicsScene()
        self.graph_view.setScene(self.graph_scene)
        # self.graph_view.setMinimumHeight(250)
        # self.graph_view.setMaximumHeight(300) # Limit graph height
        self.graph_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding) # Allow vertical expansion
        right_panel.addWidget(self.graph_view, 1) # Give graph stretch factor

        # Log Area
        right_panel.addWidget(QLabel("<b>Event Log</b>"))
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFixedHeight(150) # Limit log height initially
        right_panel.addWidget(self.log_output)


        # --- Assemble Main Layout ---
        main_layout.addLayout(left_panel, 1) # Proportion 1
        main_layout.addLayout(middle_panel, 3) # Proportion 3 (Table needs space)
        main_layout.addLayout(right_panel, 2) # Proportion 2
        self.setLayout(main_layout)

        # --- Initialize ---
        self.agg_graph_hands = [0]
        self.agg_graph_net_units = [0]
        self.refresh_window_list()
        self.update_bot_button_states()
        self.update_start_stop_buttons()
        self.update_graph() # Initial empty graph
        self.setup_context_menu()

        # Connect the mouse signal
        mouse_emitter.mouse_clicked.connect(self.handle_global_click)

    def log_message(self, message):
        """Appends a message to the log area."""
        self.log_output.append(message)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum()) # Auto-scroll

    # --- Window/Bot List Management ---
    def refresh_window_list(self):
        self.available_windows_list.clear()
        filter_text = self.window_filter_input.text()
        hwnds = find_windows_by_title(filter_text)
        configured_hwnds = self.configured_bots.keys()

        for hwnd in hwnds:
            if hwnd in configured_hwnds: continue
            title = get_window_title(hwnd)
            item = QListWidgetItem(f"{title} (HWND: {hwnd})")
            item.setData(Qt.UserRole, hwnd)
            self.available_windows_list.addItem(item)

    def add_selected_window(self):
        selected_items = self.available_windows_list.selectedItems()
        if not selected_items: return

        item = selected_items[0]
        hwnd = item.data(Qt.UserRole)
        title = get_window_title(hwnd)

        if hwnd in self.configured_bots:
            QMessageBox.information(self, "Already Added", "This window is already configured.")
            return

        row_position = self.bots_table.rowCount()
        self.bots_table.insertRow(row_position)

        self.configured_bots[hwnd] = {
            "title": title,
            "rect": None,
            "table_row": row_position,
            "game_id": None,
            "status": "Idle",
            "preview_img": None
        }

        # Populate table row
        self.bots_table.setItem(row_position, 0, QTableWidgetItem(str(hwnd)))
        self.bots_table.setItem(row_position, 1, QTableWidgetItem(title))
        self.bots_table.setItem(row_position, 2, QTableWidgetItem("Idle")) # Initial status
        self.bots_table.setItem(row_position, 3, QTableWidgetItem("[Not Set]")) # Area
        # Add placeholder for preview
        preview_label = QLabel("No Preview")
        preview_label.setAlignment(Qt.AlignCenter)
        self.bots_table.setCellWidget(row_position, 4, preview_label)
        self.bots_table.setRowHeight(row_position, 80) # Make row taller for preview
        # Stats placeholders
        self.bots_table.setItem(row_position, 5, QTableWidgetItem("0")) # Hands
        self.bots_table.setItem(row_position, 6, QTableWidgetItem("0.0")) # Net Win
        self.bots_table.setItem(row_position, 7, QTableWidgetItem("N/A")) # Last Action

        self.available_windows_list.takeItem(self.available_windows_list.row(item))
        self.update_bot_button_states()
        self.log_message(f"Added window: {title} (HWND: {hwnd})")

    def remove_selected_bot(self):
        selected_rows = self.bots_table.selectionModel().selectedRows()
        if not selected_rows: return

        row_index = selected_rows[0].row()
        hwnd_item = self.bots_table.item(row_index, 0)
        if not hwnd_item: return
        hwnd = int(hwnd_item.text())

        if hwnd in self.configured_bots:
             bot_info = self.configured_bots[hwnd]
             game_id = bot_info.get("game_id")

             # Stop the thread if it's running
             if game_id and game_id in self.active_threads:
                 self.log_message(f"Stopping bot on HWND {hwnd} before removal...")
                 self.active_threads[game_id].stop()
                 # Cleanup handled by thread finish signal, but remove from active_threads now
                 if game_id in self.active_threads:
                      del self.active_threads[game_id]

             # Remove from data structure and table
             del self.configured_bots[hwnd]
             self.bots_table.removeRow(row_index)
             self.log_message(f"Removed bot config for HWND {hwnd}")

             # Adjust row indices for bots below the removed one
             for h, info in self.configured_bots.items():
                  if info["table_row"] > row_index:
                       info["table_row"] -= 1

             # Update Aggregated Stats if the bot had contributed
             if game_id and game_id in self.bot_stats:
                  del self.bot_stats[game_id]
                  self.update_aggregate_gui_stats() # Recalculate totals

             self.refresh_window_list() # Refresh available list
             self.update_bot_button_states()
             self.update_running_bots_count()
             self.update_start_stop_buttons() # Update button states

    def update_bot_button_states(self):
        selected_rows = self.bots_table.selectionModel().selectedRows()
        has_selection = bool(selected_rows)
        self.define_area_button.setEnabled(has_selection)
        self.remove_bot_button.setEnabled(has_selection)

    # --- Area Definition ---
    def initiate_define_area(self):
        if self.defining_area_stage != 0:
            QMessageBox.warning(self, "Busy", "Already in the process of defining an area.")
            return

        selected_rows = self.bots_table.selectionModel().selectedRows()
        if not selected_rows: return
        row_index = selected_rows[0].row()
        hwnd_item = self.bots_table.item(row_index, 0)
        if not hwnd_item: return
        hwnd = int(hwnd_item.text())

        self.defining_area_for_hwnd = hwnd

        try:
             win32gui.SetForegroundWindow(hwnd)
             QMessageBox.information(self, "Define Area", f"Target window (HWND {hwnd}) brought to front.\nClick the TOP-LEFT corner of the Blackjack game area.")
             self.update_bot_status(hwnd, "Defining Area...")
             self.defining_area_stage = 1
             start_mouse_listener()
        except Exception as e:
             QMessageBox.critical(self, "Error", f"Could not bring window to front or start listener:\n{e}")
             self.reset_define_area_state()

    def handle_global_click(self, x, y, button):
         if self.defining_area_stage == 0: return
         if button != mouse.Button.left:
             # Restart listener needed if wrong button stops it
             QTimer.singleShot(10, start_mouse_listener) # Restart listener shortly
             return

         hwnd = self.defining_area_for_hwnd
         if not hwnd: return

         if self.defining_area_stage == 1: # Waiting for top-left
             self.temp_coords['tl_x'] = x
             self.temp_coords['tl_y'] = y
             # print(f"GUI: Top-left captured at screen ({x}, {y}).") # Less noisy
             QMessageBox.information(self, "Define Area", "Top-left corner captured.\nNow click the BOTTOM-RIGHT corner.")
             self.defining_area_stage = 2
             start_mouse_listener() # Restart listener for the second click

         elif self.defining_area_stage == 2: # Waiting for bottom-right
             self.temp_coords['br_x'] = x
             self.temp_coords['br_y'] = y
             # print(f"GUI: Bottom-right captured at screen ({x}, {y}).") # Less noisy
             self.finalize_define_area()
             # Listener stopped itself

    def finalize_define_area(self):
         hwnd = self.defining_area_for_hwnd
         if hwnd is None or hwnd not in self.configured_bots: return
         bot_info = self.configured_bots[hwnd]

         tl_x_scr = self.temp_coords.get('tl_x')
         tl_y_scr = self.temp_coords.get('tl_y')
         br_x_scr = self.temp_coords.get('br_x')
         br_y_scr = self.temp_coords.get('br_y')

         if None in [tl_x_scr, tl_y_scr, br_x_scr, br_y_scr]:
             QMessageBox.warning(self, "Error", "Failed to capture both coordinates.")
             self.reset_define_area_state()
             return

         cl_tl_x, cl_tl_y = screen_to_client(hwnd, tl_x_scr, tl_y_scr)
         cl_br_x, cl_br_y = screen_to_client(hwnd, br_x_scr, br_y_scr)

         if None in [cl_tl_x, cl_tl_y, cl_br_x, cl_br_y]:
              QMessageBox.warning(self, "Error", "Failed to convert screen coordinates to client coordinates. Is the window still valid?")
              self.reset_define_area_state()
              return

         left = min(cl_tl_x, cl_br_x)
         top = min(cl_tl_y, cl_br_y)
         right = max(cl_tl_x, cl_br_x)
         bottom = max(cl_tl_y, cl_br_y)

         if right <= left or bottom <= top:
              QMessageBox.warning(self, "Invalid Area", f"Defined area has zero or negative size ({left},{top})-({right},{bottom}). Please try again.")
              self.reset_define_area_state()
              return

         capture_rect = (left, top, right, bottom)
         bot_info["rect"] = capture_rect
         self.log_message(f"Area defined for HWND {hwnd}: {capture_rect}")

         # Update table
         row = bot_info["table_row"]
         self.bots_table.setItem(row, 3, QTableWidgetItem(str(capture_rect)))
         self.update_bot_status(hwnd, "Ready") # Update status to Ready

         # Attempt to capture and display preview
         self.update_capture_preview(hwnd)

         self.reset_define_area_state()
         self.update_start_stop_buttons()

    def reset_define_area_state(self):
         hwnd = self.defining_area_for_hwnd
         if hwnd and hwnd in self.configured_bots and self.configured_bots[hwnd]['status'] == "Defining Area...":
              # Reset status only if it was left in defining state
              self.update_bot_status(hwnd, "Idle" if not self.configured_bots[hwnd]['rect'] else "Ready")

         self.defining_area_for_hwnd = None
         self.defining_area_stage = 0
         self.temp_coords = {}
         global current_listener
         if current_listener:
             QTimer.singleShot(0, current_listener.stop)
             current_listener = None

    def update_capture_preview(self, hwnd):
        """Captures the defined region and updates the preview cell."""
        if hwnd not in self.configured_bots: return
        bot_info = self.configured_bots[hwnd]
        rect = bot_info.get("rect")
        row = bot_info.get("table_row")
        if rect is None or row is None: return

        pil_img = capture_window_region_to_pil(hwnd, rect)
        if pil_img:
            try:
                # Convert PIL to QPixmap for display
                img_np = np.array(pil_img.convert("RGB")) # Ensure RGB
                h, w, ch = img_np.shape
                bytes_per_line = ch * w
                qt_img = QImage(img_np.data, w, h, bytes_per_line, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qt_img)

                # Scale pixmap to fit the cell (e.g., 90x70)
                scaled_pixmap = pixmap.scaled(90, 70, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                bot_info["preview_img"] = scaled_pixmap # Store it

                preview_label = QLabel()
                preview_label.setPixmap(scaled_pixmap)
                preview_label.setAlignment(Qt.AlignCenter)
                self.bots_table.setCellWidget(row, 4, preview_label) # Update widget in table
            except Exception as e:
                print(f"Error creating preview for HWND {hwnd}: {e}")
                # Fallback text if conversion fails
                preview_label = QLabel("Preview Err")
                preview_label.setAlignment(Qt.AlignCenter)
                self.bots_table.setCellWidget(row, 4, preview_label)
        else:
            # Show error in preview cell if capture fails
            preview_label = QLabel("Capture Fail")
            preview_label.setAlignment(Qt.AlignCenter)
            self.bots_table.setCellWidget(row, 4, preview_label)


    # --- Bot Start/Stop Logic ---
    def start_all_bots(self):
        self.log_message("Attempting to start bots...") # Add entry log
        if self.active_threads:
            self.log_message("Start aborted: Bots are already running.")
            QMessageBox.information(self, "Bots Running", "Bots are already running. Stop them first.")
            return

        # Read config settings
        language_code = LANGUAGE_MAP[self.language_input.currentText()]
        bet_key = self.bet_amount_input.currentText()
        max_hands = int(self.num_games_input.text()) if self.num_games_input.text() else 1000000
        stop_profit = float(self.stop_win_input.text()) if self.stop_win_input.text() else float('inf')
        stop_loss = float(self.stop_lose_input.text()) if self.stop_lose_input.text() else float('inf')

        bots_started = 0
        # Use list() to create a copy for safe iteration if needed, though not strictly necessary here
        for hwnd, bot_info in self.configured_bots.items():
            capture_rect = bot_info.get("rect") # Use .get for safety
            current_status = bot_info.get("status", "Unknown") # Use .get for safety
            title = bot_info.get("title", f"HWND {hwnd}")

            self.log_message(f"Checking HWND {hwnd} ('{title}'). Status: '{current_status}', Rect defined: {bool(capture_rect)}") # Log check

            # --- Refined Status and Readiness Check ---
            allowed_start_statuses = ["Ready", "Stopped", "Idle"] # Define which statuses are OK to start from
            can_start = True
            skip_reason = ""

            if current_status not in allowed_start_statuses:
                 can_start = False
                 skip_reason = f"Status '{current_status}' not in {allowed_start_statuses}"
            elif not capture_rect:
                 can_start = False
                 skip_reason = "Capture area not defined"
            elif not win32gui.IsWindow(hwnd):
                 can_start = False
                 skip_reason = "Window handle invalid or window closed"

            if not can_start:
                 self.log_message(f"Skipping HWND {hwnd}: {skip_reason}.")
                 # Optionally update status to Error if window not found?
                 if "Window handle invalid" in skip_reason:
                      self.update_bot_status(hwnd, "Error: No Window")
                 continue
            # --- End Refined Check ---


            # --- Proceed with starting the bot ---
            game_id = self.next_game_id
            self.next_game_id += 1
            bot_info["game_id"] = game_id # Store game_id back

            # Initialize/reset stats for this bot
            self.bot_stats[game_id] = {
                 "total_hand": 0, "total_win": 0, "total_lose": 0, "total_draw": 0,
                 "net_win_units": 0.0, "max_hands": max_hands,
                 "stop_profit": stop_profit, "stop_loss": stop_loss,
                 "dealer_card": "N/A", "player_hands": [], "active_hand_idx": 0, "strategy": "N/A",
                 "last_condition": "N/A",
                 "hwnd": hwnd,
                 "graph_hands": [0],
                 "graph_units": [0]
            }

            self.log_message(f"Starting Bot {game_id} for HWND {hwnd} ('{title}')...")
            self.update_bot_status(hwnd, "Starting...")

            try:
                thread = ProgramThread(hwnd, capture_rect, bet_key, language_code, game_id)
                # Connect signals... (ensure these are correct)
                thread.statUpdated.connect(self.handle_stat_update)
                thread.roundInfoUpdated.connect(self.handle_round_info_update)
                thread.statusUpdated.connect(self.handle_bot_status_update)
                thread.splitOccurred.connect(self.handle_split_occurred)
                thread.handOutcome.connect(self.handle_hand_outcome)
                thread.finished.connect(lambda gid=game_id: self.handle_bot_finished(gid))

                self.active_threads[game_id] = thread
                thread.start()
                # Short delay to allow thread init messages/errors to potentially appear
                QApplication.processEvents()
                time.sleep(0.1)
                # Check if thread actually started running, otherwise init likely failed
                if not thread.isRunning() and game_id in self.active_threads:
                     self.log_message(f"Error: Bot {game_id} (HWND {hwnd}) thread failed to stay running after start. Check console for errors.")
                     self.update_bot_status(hwnd, "Error: Thread Init?")
                     # Clean up failed thread attempt
                     del self.active_threads[game_id]
                     bot_info["game_id"] = None
                     if game_id in self.bot_stats: del self.bot_stats[game_id]
                     continue # Don't count as started

                bots_started += 1
                self.log_message(f"Bot {game_id} (HWND {hwnd}) thread started successfully.")

            except Exception as e:
                 self.log_message(f"CRITICAL ERROR during thread creation/start for HWND {hwnd}: {e}")
                 self.update_bot_status(hwnd, f"Error: Init Failed")
                 # Clean up failed attempt state
                 bot_info["game_id"] = None
                 if game_id in self.bot_stats: del self.bot_stats[game_id]
                 # Ensure it's not in active_threads if exception happened before adding
                 if game_id in self.active_threads: del self.active_threads[game_id]
                 continue # Skip to next bot

        # --- Post-Loop ---
        if bots_started > 0:
             self.log_message(f"Finished attempt. Started {bots_started} bot(s) in total.")
        else:
             self.log_message("Finished attempt. No bots were started.")
             QMessageBox.warning(self, "No Bots Started", "Could not start any bots. Check logs and ensure bots are 'Ready' with a valid window and capture area.")

        self.update_running_bots_count()
        self.update_start_stop_buttons()

    def stop_all_bots(self):
         if not self.active_threads: return
         count = len(self.active_threads)
         self.log_message(f"Stopping {count} running bot(s)...")
         for game_id, thread in list(self.active_threads.items()):
             if thread.isRunning():
                 self.handle_bot_status_update(game_id, "Stopping...") # Update GUI immediately
                 thread.stop()
         # Actual cleanup and status updates happen in handle_bot_finished
         self.update_start_stop_buttons() # Reflect immediate intent

    def handle_bot_finished(self, game_id):
         self.log_message(f"Bot {game_id} thread finished.")
         if game_id in self.active_threads:
             del self.active_threads[game_id]

         # Update status in table (find HWND from game_id)
         hwnd = None
         final_status = "Stopped"
         if game_id in self.bot_stats:
             hwnd = self.bot_stats[game_id].get("hwnd")
             # Check if stopped due to error status reported earlier
             if hwnd and hwnd in self.configured_bots:
                 if "Error:" in self.configured_bots[hwnd].get("status", ""):
                      final_status = self.configured_bots[hwnd]["status"] # Keep error status
         if hwnd:
              self.update_bot_status(hwnd, final_status)
         else: # Bot finished but we lost track? Log it.
              self.log_message(f"Warning: Bot {game_id} finished, but couldn't find corresponding HWND.")

         # Reset game_id in configured_bots
         if hwnd and hwnd in self.configured_bots:
             self.configured_bots[hwnd]["game_id"] = None

         self.update_running_bots_count()
         self.update_start_stop_buttons()

    # --- Stat/Info Update Handlers (Slots) ---
    def update_bot_status(self, hwnd, status_text):
        """Updates the status column for a specific HWND."""
        if hwnd in self.configured_bots:
            bot_info = self.configured_bots[hwnd]
            row = bot_info.get("table_row")
            if row is not None and row < self.bots_table.rowCount():
                bot_info["status"] = status_text # Update internal state
                self.bots_table.setItem(row, 2, QTableWidgetItem(status_text))
                # Maybe change row color based on status?
                # item = self.bots_table.item(row, 2)
                # if "Error" in status_text: item.setBackground(Qt.red)
                # elif "Running" in status_text: item.setBackground(Qt.green)
                # else: item.setBackground(Qt.white)
            else:
                 self.log_message(f"Error updating status: Invalid row {row} for HWND {hwnd}")
        # else: # Ignore status updates for removed bots
        #      self.log_message(f"Ignoring status update '{status_text}' for unknown HWND {hwnd}")

    def handle_bot_status_update(self, game_id, status_message):
        """Slot to receive status updates from ProgramThread."""
        hwnd = self.bot_stats.get(game_id, {}).get("hwnd")
        if hwnd:
             self.update_bot_status(hwnd, status_message)
             if "Error:" in status_message:
                  self.log_message(f"[Bot {game_id} / HWND {hwnd}] {status_message}")
        else:
             self.log_message(f"[Bot {game_id}] Status: {status_message} (HWND not found)")

    # Handles end-of-round/implicit end updates
    def handle_stat_update(self, game_id, profit_units, condition):
        if game_id not in self.bot_stats: return
        stats = self.bot_stats[game_id]
        hwnd = stats.get("hwnd")

        # This signal might be redundant if handOutcome covers all cases now.
        # Keep it for now as a general round summary signal.
        # Update stats based on the *condition* provided by this signal if needed,
        # but handOutcome might be more precise for win/loss counts.
        stats["last_condition"] = condition
        # profit_units here might be the aggregate round profit. Update net_win if needed.
        # stats["net_win_units"] += profit_units # Careful: HandOutcome might already do this.

        self.log_message(f"[Bot {game_id}] Round End: {condition}, Profit: {profit_units:+.2f}")
        self.update_bot_table_stats(hwnd, game_id)
        self.update_aggregate_gui_stats() # Update totals and graph
        self.check_stop_conditions(game_id) # Check stop conditions at round end

    # Handles per-hand outcomes, especially useful for splits
    def handle_hand_outcome(self, game_id, hand_index, result, profit_units):
        if game_id not in self.bot_stats: return
        stats = self.bot_stats[game_id]
        hwnd = stats.get("hwnd")

        stats["total_hand"] += 1 # Count each resolved hand (or first hand if no split)
        stats["net_win_units"] += profit_units

        if result in ["win", "blackjack"]: stats["total_win"] += 1
        elif result in ["lose", "bust"]: stats["total_lose"] += 1
        elif result == "draw": stats["total_draw"] += 1

        stats["last_condition"] = f"Hand {hand_index}: {result}"

        self.log_message(f"[Bot {game_id}] Hand {hand_index} Result: {result}, Units: {profit_units:+.2f}")
        self.update_bot_table_stats(hwnd, game_id)
        # Don't update aggregate graph here, wait for handle_stat_update or implicit end
        # self.check_stop_conditions(game_id) # Check stop conditions per hand resolved

    def handle_split_occurred(self, game_id):
        self.log_message(f"[Bot {game_id}] Split occurred.")
        # Potentially update status or log, main handling is in thread

    def handle_round_info_update(self, game_id, dealer_card_str, player_hands_list, active_hand_idx, strategy):
        if game_id not in self.bot_stats: return
        stats = self.bot_stats[game_id]
        hwnd = stats.get("hwnd")

        stats["dealer_card"] = dealer_card_str
        stats["player_hands"] = player_hands_list
        stats["active_hand_idx"] = active_hand_idx
        stats["strategy"] = strategy

        # Update "Last Action" column in table
        if hwnd and hwnd in self.configured_bots:
             row = self.configured_bots[hwnd].get("table_row")
             if row is not None:
                  dealer_str = f"D: {dealer_card_str}"
                  hands_str = " P: " + " | ".join([f"H{i}[{','.join(h)}]" for i, h in enumerate(player_hands_list)])
                  active_str = f" Active: H{active_hand_idx}"
                  strat_str = f" -> {strategy.upper()}"
                  info_text = dealer_str + hands_str + active_str + strat_str
                  self.bots_table.setItem(row, 7, QTableWidgetItem(info_text))

    def update_bot_table_stats(self, hwnd, game_id):
        """Updates the Hands and Net Win columns for a bot in the table."""
        if hwnd and hwnd in self.configured_bots and game_id in self.bot_stats:
            bot_info = self.configured_bots[hwnd]
            stats = self.bot_stats[game_id]
            row = bot_info.get("table_row")
            if row is not None:
                self.bots_table.setItem(row, 5, QTableWidgetItem(str(stats["total_hand"])))
                self.bots_table.setItem(row, 6, QTableWidgetItem(f"{stats['net_win_units']:+.1f}"))

    def check_stop_conditions(self, game_id):
        """Checks and stops a bot if stop conditions are met."""
        if game_id not in self.bot_stats or game_id not in self.active_threads: return
        stats = self.bot_stats[game_id]

        stop = False
        reason = ""
        if stats["total_hand"] >= stats["max_hands"]:
             stop = True; reason = f"Reached max hands ({stats['max_hands']})"
        elif stats["net_win_units"] >= stats["stop_profit"]:
             stop = True; reason = f"Reached profit stop ({stats['net_win_units']:+.1f} >= {stats['stop_profit']})"
        # Use <= for loss condition
        elif stats["net_win_units"] <= -abs(stats["stop_loss"]): # Ensure stop_loss is positive in comparison
             stop = True; reason = f"Reached loss stop ({stats['net_win_units']:+.1f} <= {-abs(stats['stop_loss'])})"

        if stop:
             self.log_message(f"[Bot {game_id}] {reason}. Stopping.")
             self.handle_bot_status_update(game_id, f"Stopped: {reason}") # Update status before stopping thread
             self.active_threads[game_id].stop()


    def update_aggregate_gui_stats(self):
        agg_total_hand = sum(s["total_hand"] for s in self.bot_stats.values())
        agg_total_win = sum(s["total_win"] for s in self.bot_stats.values())
        agg_total_lose = sum(s["total_lose"] for s in self.bot_stats.values())
        agg_total_draw = sum(s["total_draw"] for s in self.bot_stats.values())
        agg_net_win_units = sum(s["net_win_units"] for s in self.bot_stats.values())

        self.agg_total_hand_label.setText(f"Total Hands: {agg_total_hand}")
        self.agg_total_win_label.setText(f"Wins: {agg_total_win}")
        self.agg_total_lose_label.setText(f"Losses: {agg_total_lose}")
        self.agg_total_draw_label.setText(f"Draws: {agg_total_draw}")
        self.agg_net_win_label.setText(f"Total Net Win (Units): {agg_net_win_units:+.1f}")

        # Update aggregate graph data only if hands increased
        if not self.agg_graph_hands or agg_total_hand > self.agg_graph_hands[-1]:
             self.agg_graph_hands.append(agg_total_hand)
             self.agg_graph_net_units.append(agg_net_win_units)
             self.update_graph(self.agg_graph_hands, self.agg_graph_net_units)

    def update_running_bots_count(self):
        count = len(self.active_threads)
        self.agg_running_bots_label.setText(f"Running Bots: {count}")

    def update_start_stop_buttons(self):
         is_running = bool(self.active_threads)
         # has_ready_bots = any(binfo.get("status") == "Ready" for binfo in self.configured_bots.values())

         self.start_button.setEnabled(not is_running) #  and has_ready_bots
         self.stop_button.setEnabled(is_running)

         # Style changes (Optional but helpful)
         if is_running:
             self.start_button.setStyleSheet("background-color: #E0E0E0; color: #888888;") # Greyed out
             self.stop_button.setStyleSheet("background-color: #F44336; color: white; font-weight: bold;") # Red
         else:
             start_bg = "#4CAF50" # if has_ready_bots else "#E0E0E0"
             start_fg = "white" # if has_ready_bots else "#888888"
             self.start_button.setStyleSheet(f"background-color: {start_bg}; color: {start_fg}; font-weight: bold;")
             self.stop_button.setStyleSheet("background-color: #E0E0E0; color: #888888;") # Greyed out

    def update_graph(self, x_data=None, y_data=None):
         if x_data is None: x_data = [0]
         if y_data is None: y_data = [0]
         # Clear previous graph items from the scene
         for item in self.graph_scene.items():
             self.graph_scene.removeItem(item)

         # Create figure and canvas
         figure = plt.figure(figsize=(5, 3), dpi=90) # Adjust size/dpi as needed
         canvas = FigureCanvas(figure)
         ax = figure.add_subplot(111)

         if len(x_data) > 1: # Plot only if there's actual data
             ax.plot(x_data, y_data, marker='.', linestyle='-', markersize=4) # Add markers
             # Add horizontal line at y=0
             ax.axhline(0, color='grey', linestyle='--', linewidth=0.8)
             # Annotate last point
             ax.text(x_data[-1], y_data[-1], f' {y_data[-1]:.1f}', va='bottom', ha='left', fontsize=8)


         ax.set_xlabel("Total Hands Played", fontsize=9)
         ax.set_ylabel("Agg. Net Win (Units)", fontsize=9)
         ax.set_title("Aggregated Performance Over Time", fontsize=10)
         ax.grid(True, linestyle=':', linewidth=0.5)
         ax.tick_params(axis='both', which='major', labelsize=8)
         figure.tight_layout(pad=0.2) # Reduce padding

         # Add canvas to scene (important!)
         self.graph_scene.addWidget(canvas)
         self.graph_view.setSceneRect(self.graph_scene.itemsBoundingRect()) # Fit view

        # --- Right-Click Context Menu ---

    def setup_context_menu(self):
        """Sets up the custom context menu for the table."""
        self.bots_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bots_table.customContextMenuRequested.connect(self.show_context_menu)

    def show_context_menu(self, pos):
        """Shows the right-click menu for the selected table row."""
        selected_rows = self.bots_table.selectionModel().selectedRows()
        if not selected_rows:
            return # Don't show menu if no row selected

        row_index = selected_rows[0].row()
        hwnd_item = self.bots_table.item(row_index, 0)
        if not hwnd_item: return
        hwnd = int(hwnd_item.text())

        if hwnd not in self.configured_bots: return # Should not happen
        bot_info = self.configured_bots[hwnd]
        game_id = bot_info.get("game_id")
        is_running = game_id is not None and game_id in self.active_threads

        menu = QMenu(self)

        # --- Define Actions ---
        start_action = menu.addAction("Start This Bot")
        stop_action = menu.addAction("Stop This Bot")
        define_area_action = menu.addAction("Define Capture Area")
        remove_action = menu.addAction("Remove This Bot")

        # --- Enable/Disable Actions based on State ---
        can_start_individually = (not is_running) and bot_info.get("rect") and win32gui.IsWindow(hwnd) and (bot_info.get("status") in ["Ready", "Stopped", "Idle"])
        start_action.setEnabled(can_start_individually)
        stop_action.setEnabled(is_running)
        define_area_action.setEnabled(not is_running) # Can only define area when stopped
        remove_action.setEnabled(True) # Always allow removal

        # --- Connect Actions ---
        start_action.triggered.connect(lambda: self.start_single_bot(hwnd))
        stop_action.triggered.connect(lambda: self.stop_single_bot(hwnd))
        # Define Area and Remove can reuse existing methods if selection is handled implicitly
        # or pass the hwnd explicitly
        define_area_action.triggered.connect(lambda: self.initiate_define_area_for_hwnd(hwnd))
        remove_action.triggered.connect(lambda: self.remove_bot_by_hwnd(hwnd))

        # --- Show Menu ---
        menu.exec_(QCursor.pos()) # Show menu at cursor position

    def initiate_define_area_for_hwnd(self, hwnd):
        """Selects the row and initiates define area for the given HWND."""
        if hwnd in self.configured_bots:
             row = self.configured_bots[hwnd].get("table_row")
             if row is not None:
                  self.bots_table.selectRow(row) # Ensure the correct row is selected
                  self.initiate_define_area() # Call the existing method

    def remove_bot_by_hwnd(self, hwnd):
        """Selects the row and removes the bot for the given HWND."""
        if hwnd in self.configured_bots:
             row = self.configured_bots[hwnd].get("table_row")
             if row is not None:
                  self.bots_table.selectRow(row) # Ensure the correct row is selected
                  self.remove_selected_bot() # Call the existing method

    def start_single_bot(self, hwnd):
        """Starts only the specified bot."""
        if hwnd not in self.configured_bots:
            self.log_message(f"Error: Cannot start HWND {hwnd}, not configured.")
            return

        bot_info = self.configured_bots[hwnd]
        game_id = bot_info.get("game_id")
        is_running = game_id is not None and game_id in self.active_threads
        is_ready = bot_info.get("rect") and win32gui.IsWindow(hwnd) and (bot_info.get("status") in ["Ready", "Stopped", "Idle"])

        if is_running:
            self.log_message(f"Bot for HWND {hwnd} is already running (Game ID: {game_id}).")
            return
        if not is_ready:
            self.log_message(f"Bot for HWND {hwnd} is not ready to start (Status: {bot_info.get('status')}, Rect: {bool(bot_info.get('rect'))}, Window: {win32gui.IsWindow(hwnd)}).")
            QMessageBox.warning(self, "Cannot Start", f"Bot for HWND {hwnd} is not ready. Ensure window exists, area is defined, and status is Ready/Stopped/Idle.")
            return

        # Read necessary configs
        language_code = LANGUAGE_MAP[self.language_input.currentText()]
        bet_key = self.bet_amount_input.currentText()
        max_hands = int(self.num_games_input.text()) if self.num_games_input.text() else 1000000
        stop_profit = float(self.stop_win_input.text()) if self.stop_win_input.text() else float('inf')
        stop_loss = float(self.stop_lose_input.text()) if self.stop_lose_input.text() else float('inf')
        capture_rect = bot_info["rect"]
        title = bot_info["title"]

        # Assign Game ID
        game_id = self.next_game_id
        self.next_game_id += 1
        bot_info["game_id"] = game_id

        # Initialize stats
        self.bot_stats[game_id] = {
             "total_hand": 0, "total_win": 0, "total_lose": 0, "total_draw": 0,
             "net_win_units": 0.0, "max_hands": max_hands,
             "stop_profit": stop_profit, "stop_loss": stop_loss,
             "dealer_card": "N/A", "player_hands": [], "active_hand_idx": 0, "strategy": "N/A",
             "last_condition": "N/A", "hwnd": hwnd,
             "graph_hands": [0], "graph_units": [0]
        }

        self.log_message(f"Starting SINGLE Bot {game_id} for HWND {hwnd} ('{title}')...")
        self.update_bot_status(hwnd, "Starting...")

        try:
            thread = ProgramThread(hwnd, capture_rect, bet_key, language_code, game_id)
            # Connect signals (same as in start_all_bots)
            thread.statUpdated.connect(self.handle_stat_update)
            thread.roundInfoUpdated.connect(self.handle_round_info_update)
            thread.statusUpdated.connect(self.handle_bot_status_update)
            thread.splitOccurred.connect(self.handle_split_occurred)
            thread.handOutcome.connect(self.handle_hand_outcome)
            thread.finished.connect(lambda gid=game_id: self.handle_bot_finished(gid))

            self.active_threads[game_id] = thread
            thread.start()
            QApplication.processEvents(); time.sleep(0.1) # Allow init msgs

            if not thread.isRunning() and game_id in self.active_threads:
                 self.log_message(f"Error: Bot {game_id} (HWND {hwnd}) thread failed to stay running after start.")
                 self.update_bot_status(hwnd, "Error: Thread Init?")
                 del self.active_threads[game_id]
                 bot_info["game_id"] = None
                 if game_id in self.bot_stats: del self.bot_stats[game_id]
            else:
                 self.log_message(f"Bot {game_id} (HWND {hwnd}) thread started successfully.")

        except Exception as e:
             self.log_message(f"CRITICAL ERROR during single thread creation/start for HWND {hwnd}: {e}")
             self.update_bot_status(hwnd, f"Error: Init Failed")
             bot_info["game_id"] = None
             if game_id in self.bot_stats: del self.bot_stats[game_id]
             if game_id in self.active_threads: del self.active_threads[game_id]

        self.update_running_bots_count()
        self.update_start_stop_buttons()


    def stop_single_bot(self, hwnd):
        """Stops the running bot associated with the specified HWND."""
        if hwnd not in self.configured_bots: return
        bot_info = self.configured_bots[hwnd]
        game_id = bot_info.get("game_id")

        if game_id is not None and game_id in self.active_threads:
            self.log_message(f"Stopping Bot {game_id} (HWND {hwnd})...")
            self.handle_bot_status_update(game_id, "Stopping...") # Update GUI status
            self.active_threads[game_id].stop()
            # Cleanup happens in handle_bot_finished
            self.update_start_stop_buttons() # Reflect change
        else:
            self.log_message(f"Bot for HWND {hwnd} is not currently running.")

    # --- Overrides ---
    def closeEvent(self, event):
         global current_listener
         self.log_message("Close event called. Cleaning up...")
         if current_listener:
             self.log_message("Stopping mouse listener...")
             current_listener.stop()
             current_listener = None
         if self.active_threads:
             self.log_message("Stopping active bots...")
             self.stop_all_bots()
             # Simple wait loop (use with caution, might hang GUI if threads don't stop)
             wait_start = time.time()
             while self.active_threads and (time.time() - wait_start < 5.0): # Max 5 sec wait
                  QApplication.processEvents() # Keep GUI responsive
                  time.sleep(0.1)
             if self.active_threads:
                  self.log_message("Warning: Some bot threads may not have stopped cleanly.")
         event.accept()


if __name__ == '__main__':
    # Optional: Increase logging level for debugging specific modules
    # import logging
    # logging.basicConfig(level=logging.DEBUG)

    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec_())