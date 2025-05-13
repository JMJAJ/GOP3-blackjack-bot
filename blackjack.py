from time import sleep, time # Import time for performance measurement
from numpy import where as np_where
from numpy import sqrt as np_sqrt, array as np_array, mean as np_mean # Added mean
from cv2 import resize, matchTemplate, TM_CCOEFF_NORMED, minMaxLoc, cvtColor, COLOR_RGB2BGR, COLOR_BGR2GRAY
from PyQt5.QtCore import QThread, pyqtSignal
from utils import (
    safe_imread, capture_window_region_to_pil, click_in_window_client_coords,
    map_std_to_custom_coords
)
from constant import (
    NUMBER, COLOR, CHEAT_SHEET, # Removed OP_POS_PERCENT, using dynamic locations now
    WINDOW_WIDTH, WINDOW_HEIGHT, # Standard processing size
    # Removed BUTTON_WIDTH/HEIGHT_PERCENT as we click center of detected buttons
    # Removed specific X coord constants, using clustering now
)
import os
import cv2

# --- Helper Functions ---

def is_close(pt1, pt2, threshold=15): # Slightly increased threshold for NMS
    # Using squared distance avoids sqrt calculation for speed
    return ((pt1[0] - pt2[0]) ** 2 + (pt1[1] - pt2[1]) ** 2) < threshold ** 2

def card_num_from_card_name(card_name):
    if not card_name or len(card_name) < 2: return 0
    rank = card_name[1]
    if rank in ["t", "j", "q", "k"]: return 10
    elif rank == "a": return 11 # Ace is initially 11
    else:
        try: return int(rank)
        except ValueError: return 0

def card_num_str_from_card_name(card_name):
    if not card_name or len(card_name) < 2: return ""
    rank = card_name[1]
    if rank in ["t", "j", "q", "k"]: return "10"
    elif rank == "a": return "A"
    else: return rank

def calculate_hand_value(card_names):
    """Calculates the best value of a hand, handling Aces."""
    total = 0
    num_aces = 0
    for name in card_names:
        val = card_num_from_card_name(name)
        if val == 11:
            num_aces += 1
        total += val
    # Adjust for Aces if total > 21
    while total > 21 and num_aces > 0:
        total -= 10
        num_aces -= 1
    return total

def get_player_key_for_strategy(card_names):
    """Determines the strategy lookup key (e.g., "16", "A,7", "8,8")."""
    num_cards = len(card_names)
    if num_cards == 0: return "" # Handle empty hand

    if num_cards == 1: # If only one card (e.g., after a split, before second card dealt)
        val = card_num_from_card_name(card_names[0])
        if val == 11: return "A" # Treat single Ace as "A"
        return str(val)       # Treat single numeric card as its value

    # Calculate values and string representations
    hand_value = calculate_hand_value(card_names) # This already handles Aces correctly for total
    num_aces_in_hand = sum(1 for name in card_names if card_num_from_card_name(name) == 11)

    # Pair check (initial deal or after split if two cards)
    if num_cards == 2:
        val1_str = card_num_str_from_card_name(card_names[0])
        val2_str = card_num_str_from_card_name(card_names[1])

        # Ensure consistent order for pairs like "2,A" vs "A,2" -> should be "A,2"
        # Or for "2,2", "A,A"
        if val1_str == val2_str:
            return f"{val1_str},{val1_str}"
        # If not a pair, it might be a soft total or hard total for 2 cards
        # The soft/hard logic below will handle it.

    # Soft total check:
    # A hand is "soft" if it contains an Ace that can be counted as 11 without busting.
    # calculate_hand_value returns the highest possible score.
    # If num_aces_in_hand > 0 and hand_value > (sum of non-Aces + num_aces_in_hand * 1),
    # it means at least one Ace is counted as 11.
    if num_aces_in_hand > 0:
        sum_of_non_aces = sum(card_num_from_card_name(name) for name in card_names if card_num_from_card_name(name) != 11)

        # If hand_value is greater than sum_of_non_aces + total number of aces (counted as 1 each)
        # then at least one ace must be counted as 11.
        if hand_value > (sum_of_non_aces + num_aces_in_hand):
            # The "other card value" for "A,X" is hand_value - 11
            other_card_val = hand_value - 11
            return f"A,{other_card_val}"

    # Hard total or two cards that are not a pair and not a soft total (e.g. 5,T -> 15)
    # Also, pairs that are not "A,A" (like "8,8") will be handled by CHEAT_SHEET if an entry exists
    # otherwise they fall through to their sum (e.g. "8,8" -> "16" if no "8,8" entry)
    if num_cards == 2: # Specific handling for two cards that are not pairs and not soft.
        # Check if the pair is explicitly in CHEAT_SHEET (e.g., "2,2", "A,A")
        # This check is now more direct for pairs
        str_c1 = card_num_str_from_card_name(card_names[0])
        str_c2 = card_num_str_from_card_name(card_names[1])

        # For pairs like "2,2", "8,8" (A,A is already covered if soft A,A or hard 2/12)
        if str_c1 == str_c2: # This covers numeric pairs
            return f"{str_c1},{str_c1}"

        # For A,X non-pair like A,7
        if str_c1 == "A": return f"A,{str_c2}"
        if str_c2 == "A": return f"A,{str_c1}"
        # For other two-card totals like 10,7 -> "17" (which is hand_value)
        # or 2,3 -> "5"

    return str(hand_value) # Default to hard total string

class ProgramThread(QThread):
    # Signals: game_id, payload...
    statUpdated = pyqtSignal(int, float, str) # game_id, profit_units, condition ("win", "lose", "draw", "blackjack")
    roundInfoUpdated = pyqtSignal(int, str, list, int, str) # game_id, dealer_card_str, player_hands_list[list[card_str]], active_hand_idx, strategy
    statusUpdated = pyqtSignal(int, str) # game_id, status_message ("Initializing", "Running", "Idle", "Stopped", "Error: ...")
    splitOccurred = pyqtSignal(int) # game_id
    handOutcome = pyqtSignal(int, int, str, float) # game_id, hand_index (0 or 1), result ("win", "lose", "bust", "draw", "blackjack"), profit_units

    def __init__(self, hwnd, capture_rect, bet_amount_str_key, language, game_id, is_web_view=False):
        super().__init__()
        self.hwnd = hwnd
        self.capture_rect = capture_rect
        self.bet_amount_str_key = bet_amount_str_key
        self.language = language
        self.running = True
        self.card_images = {} # Cache card images
        self.templates = {}   # Cache button/state images
        self.image_prefix = "image/" + self.language + "/"
        self.game_id = game_id
        self.log_prefix = f"[Bot {self.game_id} hwnd={self.hwnd}] "
        self.is_web_view = is_web_view  # Flag to indicate if this is a web view

        # For web view clicks, we need a reference to the QWebEngineView
        # This will be set by the caller if is_web_view is True
        self.web_view = None

        # Store the main thread for thread-safe operations
        from PyQt5.QtCore import QThread as QThreadClass
        self.main_thread = QThreadClass.currentThread()

        self.statusUpdated.emit(self.game_id, "Initializing")
        print(f"{self.log_prefix}Pre-loading images...")
        # --- Pre-load all images ONCE ---
        # Cards
        for num in NUMBER:
            for col in COLOR:
                card_name = col + num
                card_image_path = os.path.join("image", "card", card_name + ".png")
                card_image = safe_imread(card_image_path, 0) # Load grayscale
                if card_image is not None: self.card_images[card_name] = card_image
        # UI Templates
        img_path = lambda name: os.path.join(self.image_prefix, name)
        bet_img_path = os.path.join("image", "bet", f"bet{self.bet_amount_str_key}.png")
        template_files = {
            "win": img_path("win.png"), "lose": img_path("lose.png"),
            "bust": img_path("bust.png"), "draw": img_path("draw.png"),
            "double": img_path("double.png"), "stand": img_path("stand.png"),
            "split": img_path("split.png"), "hit": img_path("hit.png"),
            "blackjack": img_path("blackjack.png"), "bet": bet_img_path,
            # Add potential Insurance template if needed/available
            # "insurance": img_path("insurance.png"),
        }
        loaded_count = 0
        for name, path in template_files.items():
            img = safe_imread(path, 0)
            if img is not None:
                self.templates[name] = img
                loaded_count += 1
        print(f"{self.log_prefix}Loaded {len(self.card_images)} cards, {loaded_count} UI templates.")

        essential_keys = ["bet", "hit", "stand"] # Double/Split are optional
        if any(self.templates.get(key) is None for key in essential_keys):
             error_msg = f"CRITICAL: Missing essential UI templates ({[k for k in essential_keys if self.templates.get(k) is None]}). Bot cannot run."
             print(f"{self.log_prefix}{error_msg}")
             self.statusUpdated.emit(self.game_id, f"Error: {error_msg}")
             self.running = False # Prevent running

    def compare(self, target_template, screen_to_search_gray, threshold=0.88):
        """Finds the best match location for a template if above threshold."""
        if target_template is None or screen_to_search_gray is None: return None, 0.0
        if target_template.shape[0] > screen_to_search_gray.shape[0] or \
           target_template.shape[1] > screen_to_search_gray.shape[1]:
           return None, 0.0 # Template bigger than screen

        res = matchTemplate(screen_to_search_gray, target_template, TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = minMaxLoc(res)
        # print(f"Comparing {target_template.shape}, found max_val: {max_val:.3f}") # Debug
        return max_loc if max_val >= threshold else None, max_val

    # --- detect_cards_on_screen (Optimized with better NMS) ---
    def detect_cards_on_screen(self, screen_img_gray_processed):
        _dealer_card = ""
        _player_cards_tuples = [] # (name, x_pos, y_pos) in processed coords
        all_matches = [] # Store potential matches: (score, x, y, w, h, name)

        # Phase 1: Find all potential matches above a base threshold
        base_threshold = 0.92 # Slightly higher threshold for cards
        for card_name, card_image in self.card_images.items():
            if card_image is None: continue
            h, w = card_image.shape[:2]
            if h > screen_img_gray_processed.shape[0] or w > screen_img_gray_processed.shape[1]:
               continue # Skip if template too big

            res = matchTemplate(screen_img_gray_processed, card_image, TM_CCOEFF_NORMED)
            locs = np_where(res >= base_threshold)
            scores = res[locs]
            for y_match, x_match, score in zip(*locs, scores): # Note: np_where returns (y, x)
                 # Store top-left corner (x, y) and dimensions
                 all_matches.append({'score': score, 'x': x_match, 'y': y_match, 'w': w, 'h': h, 'name': card_name})

        # Phase 2: Non-Maximal Suppression (Bounding Box Overlap - simplified)
        all_matches.sort(key=lambda item: item['score'], reverse=True) # Sort by score descending

        final_detected_cards = [] # (y_pos, card_name, (x_match, y_match))
        suppressed_indices = set()

        for i in range(len(all_matches)):
            if i in suppressed_indices: continue
            match_i = all_matches[i]
            final_detected_cards.append( (match_i['y'], match_i['name'], (match_i['x'], match_i['y'])) )

            # Suppress overlapping boxes with lower scores
            for j in range(i + 1, len(all_matches)):
                 if j in suppressed_indices: continue
                 match_j = all_matches[j]
                 # Simple center distance check (faster than full IoU)
                 center_i = (match_i['x'] + match_i['w'] // 2, match_i['y'] + match_i['h'] // 2)
                 center_j = (match_j['x'] + match_j['w'] // 2, match_j['y'] + match_j['h'] // 2)
                 # Check if centers are close (adjust threshold based on card size)
                 if is_close(center_i, center_j, threshold=max(match_i['w'], match_i['h']) * 0.5): # Threshold relative to card size
                     suppressed_indices.add(j)

        # Phase 3: Assign to dealer/player based on Y position
        final_detected_cards.sort() # Sort by Y position
        dealer_y_threshold = WINDOW_HEIGHT * 0.4 # Dealer cards usually above 40% mark
        player_y_threshold = WINDOW_HEIGHT * 0.5 # Player cards usually below 50% mark

        temp_dealer_cards = []
        temp_player_cards = []

        for y_match, card_name, pos in final_detected_cards:
            if y_match < dealer_y_threshold:
                temp_dealer_cards.append((card_name, pos))
            elif y_match > player_y_threshold:
                temp_player_cards.append((card_name, pos)) # Store pos for clustering later

        # Assign dealer card (usually only one visible)
        if temp_dealer_cards:
            _dealer_card = temp_dealer_cards[0][0] # Assume first detected is the one

        # Assign player cards (store with positions)
        _player_cards_tuples = [(name, pos[0], pos[1]) for name, pos in temp_player_cards]

        return _dealer_card, _player_cards_tuples


    # --- Click functions ---
    def perform_click_at_location(self, match_loc_resized, template_img_for_size):
        """ Clicks the center of a detected template location. """
        if match_loc_resized is None or template_img_for_size is None:
             print(f"{self.log_prefix}Warning: Cannot click, invalid location or template size.")
             return False

        template_h, template_w = template_img_for_size.shape[:2]
        std_click_x = match_loc_resized[0] + template_w // 2
        std_click_y = match_loc_resized[1] + template_h // 2
        client_x, client_y = map_std_to_custom_coords(
            std_click_x, std_click_y, self.capture_rect, WINDOW_WIDTH, WINDOW_HEIGHT
        )

        # Handle web view clicks differently
        if self.is_web_view and self.web_view is not None:
            try:
                # For web view, we need to use Qt to simulate a mouse click
                from PyQt5.QtCore import QPoint

                # Get the actual size of the web view
                web_view_width = self.web_view.width()
                web_view_height = self.web_view.height()

                # Calculate the scale factors between the standard processing size and the actual web view size
                width_scale = web_view_width / WINDOW_WIDTH
                height_scale = web_view_height / WINDOW_HEIGHT

                # Scale the coordinates to match the actual web view size
                web_x = int(std_click_x * width_scale)
                web_y = int(std_click_y * height_scale)

                print(f"{self.log_prefix}Web view click at ({web_x}, {web_y}) - Original: ({std_click_x}, {std_click_y})")

                # Create a point for the click
                click_point = QPoint(web_x, web_y)

                # Use PyAutoGUI for direct mouse control
                import pyautogui

                # Get the global screen position
                global_pos = self.web_view.mapToGlobal(click_point)
                screen_x, screen_y = global_pos.x(), global_pos.y()

                # Move mouse and click at the exact screen coordinates
                pyautogui.moveTo(screen_x, screen_y)
                pyautogui.click(screen_x, screen_y)

                print(f"{self.log_prefix}PyAutoGUI click at screen position ({screen_x}, {screen_y})")

                # Add a small delay after the click
                sleep(0.2)

                return True
            except Exception as e:
                print(f"{self.log_prefix}Error during web view click: {e}")
                return False
        else:
            # Standard window click
            # print(f"{self.log_prefix}Clicking at detected location -> client({client_x}, {client_y})")
            click_in_window_client_coords(self.hwnd, client_x, client_y, duration_seconds=0.1) # Faster click
            return True

    # --- Main Run Loop ---
    def run(self):
        self.statusUpdated.emit(self.game_id, "Running")
        last_action_time = time()
        last_state_log_time = 0
        consecutive_capture_fails = 0

        # Round State Variables
        player_hands = [] # List of lists, e.g., [ ['hA', 'sK'], ['d8', 'c8'] ]
        active_hand_idx = 0
        is_doubled = [False] * 2 # Track double status per potential hand
        dealer_card = ""
        round_profit = 0.0
        round_over = True # Start assuming round is over, wait for bet button

        # Timing / Delays
        POST_TERMINAL_DELAY = 1.2 # Reduced further
        RETRY_DELAY = 0.5
        IDLE_DELAY = 0.05
        ACTION_TIMEOUT = 25 # Slightly longer timeout

        print(f"{self.log_prefix}Starting run loop.")

        while self.running:
            loop_start_time = time()

            # 1. Capture Screen
            pil_screen = capture_window_region_to_pil(self.hwnd, self.capture_rect)
            if pil_screen is None:
                consecutive_capture_fails += 1
                if consecutive_capture_fails > 5: # Stop if capture fails repeatedly
                    error_msg = "Capture failed repeatedly. Stopping bot."
                    print(f"{self.log_prefix}{error_msg}")
                    self.statusUpdated.emit(self.game_id, f"Error: {error_msg}")
                    self.running = False
                else:
                     if time() - last_state_log_time > 5: # Log only occasionally
                         print(f"{self.log_prefix}Capture failed. Retrying...")
                         last_state_log_time = time()
                     sleep(RETRY_DELAY)
                continue
            consecutive_capture_fails = 0 # Reset counter on success

            # 2. Preprocess Screen
            # Ensure the captured image has the expected dimensions
            if pil_screen.width != self.capture_rect[2] - self.capture_rect[0] or pil_screen.height != self.capture_rect[3] - self.capture_rect[1]:
                print(f"{self.log_prefix}Note: Captured image size ({pil_screen.width}x{pil_screen.height}) differs from expected size ({self.capture_rect[2] - self.capture_rect[0]}x{self.capture_rect[3] - self.capture_rect[1]}). Using as is.")

            screen_cv_bgr = cvtColor(np_array(pil_screen), COLOR_RGB2BGR)
            current_screen_proc = resize(screen_cv_bgr, (WINDOW_WIDTH, WINDOW_HEIGHT))
            current_screen_proc_gray = cvtColor(current_screen_proc, COLOR_BGR2GRAY)

            action_taken_this_loop = False
            current_time = time()

            # 3. State Detection (Prioritized)

            # 3a. Bet Button (Highest Priority - Start of Round)
            bet_loc, _ = self.compare(self.templates.get("bet"), current_screen_proc_gray, threshold=0.85) # Bet button threshold slightly lower
            if bet_loc:
                if not round_over: # Log previous round results if not already logged by terminal state
                    print(f"{self.log_prefix}Round ended implicitly. Final Profit: {round_profit:+.2f}")
                    self.statUpdated.emit(self.game_id, round_profit, "implicit_end") # Use a distinct state?
                # print(f"{self.log_prefix}State: BET")
                if self.perform_click_at_location(bet_loc, self.templates["bet"]):
                    # Reset round state
                    player_hands = []
                    active_hand_idx = 0
                    is_doubled = [False] * 2
                    dealer_card = ""
                    round_profit = 0.0
                    round_over = False # New round starting
                    action_taken_this_loop = True
                    last_action_time = current_time
                    self.statusUpdated.emit(self.game_id, "Running - New Round")
                    sleep(0.2) # Short delay after clicking bet
                    continue # Restart loop

            # If round is over, don't check for actions/cards
            if round_over:
                 sleep(IDLE_DELAY * 2) # Slightly longer idle sleep when waiting for bet
                 continue

            # 3b. Terminal States (Check *before* action buttons if possible)
            # These indicate the immediate end of player interaction for a hand/round
            terminal_state_found = None
            terminal_loc = None
            highest_score = 0.0
            detected_key = ""
            # Prioritize Blackjack/Bust as they are definitive player outcomes
            terminal_checks = ["blackjack", "bust", "win", "lose", "draw"]
            for key in terminal_checks:
                 template = self.templates.get(key)
                 if template is None: continue
                 match_loc, score = self.compare(template, current_screen_proc_gray, threshold=0.80) # Lower threshold for better terminal state detection
                 if match_loc:
                     # Check Y coordinate to ensure it's likely player's result area
                     if match_loc[1] > WINDOW_HEIGHT / 2:
                         # If we find a better match, update
                         if score > highest_score:
                              highest_score = score
                              terminal_state_found = key
                              terminal_loc = match_loc # Store location if needed for context
                              print(f"{self.log_prefix}Detected terminal state: {key} at {match_loc} with score {score:.3f}") # Debug

            # Also check for blackjack by looking at the cards (in case the terminal state detection misses it)
            if not terminal_state_found and len(player_hands) > 0 and len(player_hands[active_hand_idx]) == 2:
                # Check if player has blackjack (A + 10/J/Q/K)
                current_hand = player_hands[active_hand_idx]
                card_values = [card_num_from_card_name(card) for card in current_hand]
                if (11 in card_values and 10 in card_values) and calculate_hand_value(current_hand) == 21:
                    print(f"{self.log_prefix}Detected blackjack from cards: {current_hand}")
                    terminal_state_found = "blackjack"
                    # No need to set terminal_loc as it's not used for card-detected blackjack

            if terminal_state_found:
                 key = terminal_state_found
                 condition_map = {"win": "win", "blackjack": "win", "lose": "lose", "bust": "lose", "draw": "draw"}
                 condition = condition_map.get(key, "unknown")
                 multiplier = 1.0
                 if is_doubled[active_hand_idx]: multiplier = 2.0
                 pay_rate = {"win": 1.0, "blackjack": 1.5, "lose": -1.0, "bust": -1.0, "draw": 0.0}.get(key, 0.0)
                 profit = pay_rate * multiplier

                 print(f"{self.log_prefix}State: TERMINAL Hand {active_hand_idx} -> {key.upper()} (Profit: {profit:+.2f})")
                 self.handOutcome.emit(self.game_id, active_hand_idx, key, profit) # Signal outcome for specific hand
                 round_profit += profit
                 action_taken_this_loop = True
                 last_action_time = current_time

                 # Move to next hand if split, or end round
                 if len(player_hands) > 1: # If was a split
                     if active_hand_idx == 0:
                         print(f"{self.log_prefix}Switching to second split hand.")
                         active_hand_idx = 1 # Move to next hand
                         # Need to re-evaluate the state for the second hand in the *next* loop cycle
                     else:
                         print(f"{self.log_prefix}Both split hands finished. Round Over.")
                         round_over = True
                         self.statUpdated.emit(self.game_id, round_profit, "split_end") # Aggregate round result
                         sleep(POST_TERMINAL_DELAY)
                 else: # Normal round ended
                     round_over = True
                     self.statUpdated.emit(self.game_id, round_profit, condition) # Single hand result
                     sleep(POST_TERMINAL_DELAY)

                 continue # Restart loop

            # 3c. Action Buttons (Player's Turn - if no terminal state and not round_over)
            # Find all available action buttons *first*
            action_buttons = {} # { 'hit': (loc), 'stand': (loc), ... }

            # First detect stand, double, split with normal threshold
            for btn_name in ["stand", "double", "split"]:
                template = self.templates.get(btn_name)
                if template is not None:
                    loc, score = self.compare(template, current_screen_proc_gray, threshold=0.75) # Lower threshold for better button detection
                    if loc:
                        action_buttons[btn_name] = loc
                        print(f"{self.log_prefix}Detected {btn_name} button with score {score:.3f} at {loc}")

            # If stand is found but hit is not, try to infer hit button position based on stand
            if "stand" in action_buttons and "hit" not in action_buttons:
                stand_x, stand_y = action_buttons["stand"]
                # Hit button is typically to the left of stand button
                # In GOP3, hit is usually about 250-260 pixels to the left of stand
                hit_x = stand_x - 255  # Approximate position
                hit_y = stand_y  # Same Y position

                # Add inferred hit button
                action_buttons["hit"] = (hit_x, hit_y)
                print(f"{self.log_prefix}Inferred HIT button position at ({hit_x}, {hit_y}) based on STAND position")

            # Try to detect hit button normally as well
            template = self.templates.get("hit")
            if template is not None:
                loc, score = self.compare(template, current_screen_proc_gray, threshold=0.70) # Even lower threshold for hit
                if loc:
                    action_buttons["hit"] = loc
                    print(f"{self.log_prefix}Detected hit button with score {score:.3f} at {loc}")

            # If Hit or Stand is visible, it's likely player's turn
            if "hit" in action_buttons or "stand" in action_buttons:
                # print(f"{self.log_prefix}State: ACTION_BUTTONS ({list(action_buttons.keys())})") # Debug
                self.statusUpdated.emit(self.game_id, f"Running - Hand {active_hand_idx} Turn")

                # --- Perform Card Detection ---
                # This is expensive, only do it when necessary
                # detect_start = time()
                detected_dealer_card, all_player_cards_tuples = self.detect_cards_on_screen(current_screen_proc_gray)
                # print(f"{self.log_prefix}Card detection took: {time() - detect_start:.3f}s")
                # --- End card detection ---

                if not detected_dealer_card and not dealer_card: # Only update dealer card if empty and detected
                    print(f"{self.log_prefix}Warning: Dealer card not detected reliably.")
                    # Maybe wait and retry? For now, proceed cautiously.
                    sleep(0.2)
                    continue # Skip this cycle, hope for better detection next time
                elif detected_dealer_card:
                    dealer_card = detected_dealer_card # Update if detected

                if not all_player_cards_tuples:
                    print(f"{self.log_prefix}Warning: Player cards not detected.")
                    sleep(0.2)
                    continue

                # --- Card Association (Handle Splits) ---
                player_card_positions = np_array([(x, y) for _, x, y in all_player_cards_tuples])
                player_card_names = [name for name, _, _ in all_player_cards_tuples]

                if not player_hands: # Initial deal
                    player_hands.append(sorted(player_card_names)) # Assume single hand initially
                elif len(player_hands) == 1 and len(player_card_names) > len(player_hands[0]) and "split" not in action_buttons:
                    # Cards added to the single hand (hit)
                     player_hands[0] = sorted(player_card_names) # Update hand, keep sorted? Maybe not necessary
                elif len(player_hands) == 2:
                    # Split occurred, associate cards to hands 0 and 1 based on X-coord clustering
                    if len(player_card_positions) >= 2: # Need at least one card per split hand theoretically
                         # Find rough center X of all player cards
                         center_x = np_mean(player_card_positions[:, 0])
                         hand0_cards = []
                         hand1_cards = []
                         for name, x, _ in all_player_cards_tuples:  # Use _ for unused y coordinate
                              if x < center_x: hand0_cards.append(name)
                              else: hand1_cards.append(name)
                         player_hands[0] = hand0_cards
                         player_hands[1] = hand1_cards
                    else:
                         print(f"{self.log_prefix}Warning: Not enough cards detected to assign to split hands.")
                         # Keep previous state?
                # Else: State is likely stable between actions

                # Get the current hand being played
                if active_hand_idx >= len(player_hands):
                     print(f"{self.log_prefix}Error: Active hand index {active_hand_idx} out of bounds for player_hands (len {len(player_hands)}). Resetting.")
                     # This might happen if state logic gets confused, reset to be safe
                     active_hand_idx = 0
                     if not player_hands: player_hands.append([]) # Ensure player_hands has at least one empty list

                current_hand_cards = player_hands[active_hand_idx]
                current_total_points = calculate_hand_value(current_hand_cards)

                # Check for auto-stand conditions (e.g., >= 21)
                if current_total_points >= 21:
                    strategy = "stand" # Force stand on 21 or bust
                    print(f"{self.log_prefix} Hand {active_hand_idx}: [{','.join(current_hand_cards)}] ({current_total_points}) >= 21 -> Auto-Stand")
                else:
                    # Determine strategy using cheat sheet
                    player_key = get_player_key_for_strategy(current_hand_cards)
                    dealer_key = card_num_str_from_card_name(dealer_card)
                    if not player_key or not dealer_key:
                         print(f"{self.log_prefix}Warning: Cannot determine strategy keys (P:'{player_key}', D:'{dealer_key}'). Defaulting to Hit.")
                         strategy = "hit"  # Changed default from stand to hit when keys can't be determined
                    else:
                         # Look up the strategy in the cheat sheet
                         lookup_key = (player_key, dealer_key)
                         if lookup_key in CHEAT_SHEET:
                             strategy = CHEAT_SHEET[lookup_key]
                             print(f"{self.log_prefix}Strategy found in cheat sheet: ({player_key}, {dealer_key}) -> {strategy}")
                         else:
                             # If not found in cheat sheet, use a reasonable default based on total
                             strategy = "hit" if current_total_points < 17 else "stand"
                             print(f"{self.log_prefix}No entry in cheat sheet for ({player_key}, {dealer_key}). Using default: {strategy}")

                         # Log the decision for debugging
                         print(f"{self.log_prefix}DECISION: Cards={current_hand_cards}, Total={current_total_points}, Player key={player_key}, Dealer key={dealer_key}, Strategy={strategy}")

                    # Validate strategy against available buttons
                    if strategy == "double" and ("double" not in action_buttons or len(current_hand_cards) != 2):
                        strategy = "hit" # Cannot double if button missing or not initial 2 cards
                    if strategy == "split" and ("split" not in action_buttons or len(current_hand_cards) != 2):
                         # If split suggested but unavailable, fall back to hard/soft total strategy
                         hard_total_key = str(calculate_hand_value(current_hand_cards)) # Use current calculated value
                         strategy = CHEAT_SHEET.get((hard_total_key, dealer_key), "hit" if current_total_points < 17 else "stand")
                         if strategy == "double": strategy = "hit" # Still can't double here
                         if strategy == "split": strategy = "hit" # Avoid infinite loop if fallback is split again


                # Handle missing buttons - try to enable debug screenshots
                if strategy == "hit" and "hit" not in action_buttons:
                    warning_msg = f"Warning: Strategy is HIT, but button not found. (Available: {list(action_buttons.keys())})"
                    print(f"{self.log_prefix}{warning_msg}")
                    self.statusUpdated.emit(self.game_id, "Warning: Hit not found")

                    # Save debug screenshot to help diagnose the issue
                    ts = int(time())
                    debug_filename = f"debug_hit_not_found_{self.game_id}_{ts}.png"
                    print(f"{self.log_prefix}Saving debug screenshot to {debug_filename}")
                    # Save the processed grayscale image used for comparison
                    from cv2 import imwrite
                    imwrite(debug_filename, current_screen_proc_gray)

                    # Try to wait a moment and retry detection
                    print(f"{self.log_prefix}Waiting 0.5s and will retry button detection...")
                    sleep(0.5)

                    # Retry button detection with even lower threshold
                    for btn_name in ["hit", "stand", "double", "split"]:
                        if btn_name in action_buttons:
                            continue  # Already found this button
                        template = self.templates.get(btn_name)
                        if template is not None:
                            loc, score = self.compare(template, current_screen_proc_gray, threshold=0.70)  # Even lower threshold for retry
                            if loc:
                                action_buttons[btn_name] = loc
                                print(f"{self.log_prefix}Retry detected {btn_name} button with score {score:.3f} at {loc}")

                    # If still not found, use stand if available
                    if "hit" not in action_buttons:
                        if "stand" in action_buttons:
                            print(f"{self.log_prefix}Still can't find HIT button. Using STAND instead.")
                            strategy = "stand"
                        else:
                            print(f"{self.log_prefix}Warning: Neither HIT nor STAND buttons found. Assuming hand ended.")
                            # Let terminal state detection handle it next loop
                            continue

                # Check if stand button is missing when needed
                if strategy == "stand" and "stand" not in action_buttons:
                    print(f"{self.log_prefix}Warning: Strategy is STAND, but button not found. Assuming hand ended.")
                    # Let terminal state detection handle it next loop
                    continue

                # Emit current state info
                self.roundInfoUpdated.emit(self.game_id, dealer_card, player_hands, active_hand_idx, strategy)

                # Execute action by clicking the *detected* button
                clicked_ok = False
                if strategy in action_buttons:
                    print(f"{self.log_prefix} Hand {active_hand_idx}: D:[{dealer_card}] P:[{','.join(current_hand_cards)}] ({current_total_points}) -> Strategy: {strategy.upper()}")
                    clicked_ok = self.perform_click_at_location(action_buttons[strategy], self.templates[strategy])
                else:
                    print(f"{self.log_prefix}Error: Chosen strategy '{strategy}' button not found in {list(action_buttons.keys())}. Cannot click.")
                    # Maybe default to stand click if available? Or just wait.
                    sleep(0.5)
                    continue # Avoid action if button missing

                if clicked_ok:
                    action_taken_this_loop = True
                    last_action_time = current_time

                    # Update state AFTER action
                    if strategy == "double":
                        is_doubled[active_hand_idx] = True
                        # Hand ends after double
                        if len(player_hands) > 1 and active_hand_idx == 0:
                            active_hand_idx = 1
                        else:
                            round_over = True # Double ends the turn/round for non-split
                    elif strategy == "split":
                        # Actual splitting logic: create two hands from the first one
                        if len(player_hands) == 1 and len(player_hands[0]) == 2:
                            card1 = player_hands[0][0]
                            card2 = player_hands[0][1]
                            player_hands = [[card1], [card2]] # Initialize split hands (will be updated next cycle)
                            active_hand_idx = 0
                            is_doubled = [False, False] # Reset double status for both new hands
                            self.splitOccurred.emit(self.game_id)
                            print(f"{self.log_prefix}Split performed. Now playing first hand.")
                        else:
                             print(f"{self.log_prefix}Error: Tried to split but conditions not met (Hands: {len(player_hands)}, Cards in hand 0: {len(player_hands[0]) if player_hands else 0})")
                    elif strategy == "stand":
                        # Hand ends, move to next or finish round
                         if len(player_hands) > 1 and active_hand_idx == 0: # Finished first split hand
                             active_hand_idx = 1
                             print(f"{self.log_prefix}Stand on first split hand. Switching to second.")
                         else: # Finished only hand or second split hand
                             round_over = True
                             print(f"{self.log_prefix}Stand on final hand. Round potentially over.")
                             # Let terminal state detector confirm outcome

                    # Short sleep after action to allow game state to update
                    sleep(0.25) # Reduced sleep after action

                continue # Restart loop after action

            # 4. Idle or Unknown State
        if not action_taken_this_loop and not round_over:
            if current_time - last_action_time > ACTION_TIMEOUT:
                # Instead of stopping, try to recover by looking for the bet button
                error_msg = f"Potential stall: No state change for {ACTION_TIMEOUT}s. Attempting recovery..."
                print(f"{self.log_prefix}{error_msg}")
                self.statusUpdated.emit(self.game_id, f"Recovering from stall...")

                # Save debug screenshot
                ts = int(time())
                stall_filename = f"debug_stall_{self.game_id}_{ts}.png"
                print(f"{self.log_prefix}Saving stall screenshot to {stall_filename}")
                from cv2 import imwrite
                imwrite(stall_filename, current_screen_proc_gray)

                # Try to find bet button with lower threshold
                bet_loc, _ = self.compare(self.templates.get("bet"), current_screen_proc_gray, threshold=0.75)
                if bet_loc:
                    print(f"{self.log_prefix}Recovery: Found bet button during stall. Clicking it to restart round.")
                    self.perform_click_at_location(bet_loc, self.templates["bet"])
                    # Reset round state
                    player_hands = []
                    active_hand_idx = 0
                    is_doubled = [False] * 2
                    dealer_card = ""
                    round_profit = 0.0
                    round_over = False
                    last_action_time = current_time
                    action_taken_this_loop = True
                    # Don't use continue here, just let it proceed

                # If we can't find bet button, try clicking in the center of the screen
                else:
                    print(f"{self.log_prefix}Recovery: Clicking center of screen to dismiss any dialogs...")
                    center_x = WINDOW_WIDTH // 2
                    center_y = WINDOW_HEIGHT // 2
                    client_x, client_y = map_std_to_custom_coords(
                        center_x, center_y, self.capture_rect, WINDOW_WIDTH, WINDOW_HEIGHT
                    )
                    click_in_window_client_coords(self.hwnd, client_x, client_y, duration_seconds=0.1)

                # Reset the timer but don't stop the bot
                last_action_time = current_time
                # Only stop if we've had multiple consecutive stalls
                if getattr(self, 'consecutive_stalls', 0) > 3:
                    print(f"{self.log_prefix}Too many consecutive stalls. Stopping bot.")
                    self.statusUpdated.emit(self.game_id, f"Error: Too many stalls")
                    self.running = False
                else:
                    self.consecutive_stalls = getattr(self, 'consecutive_stalls', 0) + 1
                    print(f"{self.log_prefix}Stall count: {self.consecutive_stalls}/3")
            else:
                # Reset stall counter if we're not stalled
                self.consecutive_stalls = 0
                # Reduce logging noise for unknown state
                if current_time - last_state_log_time > 2.0: # Log idle state only every 2s
                    # print(f"{self.log_prefix}State: Waiting/Idle...")
                    self.statusUpdated.emit(self.game_id, "Idle")
                    last_state_log_time = current_time
                sleep(IDLE_DELAY)

            # Optional: Print loop duration for performance monitoring
            # loop_duration = time() - loop_start_time
            # if loop_duration > 0.2: # Log only longer loops
            #      print(f"{self.log_prefix}Loop duration: {loop_duration:.4f}s")

        print(f"{self.log_prefix}Run loop finished.")
        self.statusUpdated.emit(self.game_id, "Stopped")

    def stop(self):
        print(f"{self.log_prefix}Stop signal received.")
        self.statusUpdated.emit(self.game_id, "Stopping...")
        self.running = False