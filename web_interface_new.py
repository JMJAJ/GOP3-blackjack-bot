import sys
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QFrame, QMessageBox, QGraphicsView, QGraphicsScene,
    QTextEdit, QSplitter
)
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtWebEngineWidgets import (
    QWebEngineView, QWebEngineSettings, QWebEngineProfile
)

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from constant import SUPPORTED_LANGUAGE, LANGUAGE_MAP, BET_AMOUNT
from blackjack import ProgramThread

# No mouse listener needed since we automatically set the capture area

# Web Interface
class WebInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Blackjack Bot - Web Interface")

        # Data structures
        self.hwnd = None
        self.capture_rect = None  # Will be set automatically when web view is loaded
        self.game_thread = None
        self.game_id = 1
        self.stats = {
            "hands": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "net_win": 0.0,
            "last_action": "N/A"
        }
        self.graph_hands = [0]
        self.graph_net_units = [0]

        # --- GUI Layout ---
        main_layout = QVBoxLayout()

        # Create a splitter for the main layout
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        # Left side - Web browser
        browser_container = QWidget()
        browser_layout = QVBoxLayout(browser_container)

        # Add browser controls
        browser_controls = QHBoxLayout()

        # Navigation buttons
        self.back_button = QPushButton("←")
        self.back_button.setToolTip("Go Back")
        self.back_button.clicked.connect(lambda: self.web_view.back())
        self.back_button.setMaximumWidth(40)

        self.forward_button = QPushButton("→")
        self.forward_button.setToolTip("Go Forward")
        self.forward_button.clicked.connect(lambda: self.web_view.forward())
        self.forward_button.setMaximumWidth(40)

        self.refresh_button = QPushButton("↻")
        self.refresh_button.setToolTip("Refresh")
        self.refresh_button.clicked.connect(lambda: self.web_view.reload())
        self.refresh_button.setMaximumWidth(40)

        # URL bar
        self.url_bar = QLineEdit("https://www.governorofpoker.com/games/governor-of-poker-3/play/")
        self.url_bar.returnPressed.connect(self.navigate_to_url)  # Allow pressing Enter to navigate

        # Go button
        self.go_button = QPushButton("Go")
        self.go_button.clicked.connect(self.navigate_to_url)

        # Zoom controls
        self.zoom_out_button = QPushButton("−")
        self.zoom_out_button.setToolTip("Zoom Out")
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.zoom_out_button.setMaximumWidth(40)

        self.zoom_reset_button = QPushButton("100%")
        self.zoom_reset_button.setToolTip("Reset Zoom")
        self.zoom_reset_button.clicked.connect(self.zoom_reset)
        self.zoom_reset_button.setMaximumWidth(60)

        self.zoom_in_button = QPushButton("+")
        self.zoom_in_button.setToolTip("Zoom In")
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_in_button.setMaximumWidth(40)

        # Add all controls to layout
        browser_controls.addWidget(self.back_button)
        browser_controls.addWidget(self.forward_button)
        browser_controls.addWidget(self.refresh_button)
        browser_controls.addWidget(self.url_bar)
        browser_controls.addWidget(self.go_button)
        browser_controls.addWidget(self.zoom_out_button)
        browser_controls.addWidget(self.zoom_reset_button)
        browser_controls.addWidget(self.zoom_in_button)
        browser_layout.addLayout(browser_controls)

        # Create a custom profile with persistent storage
        self.profile = QWebEngineProfile("GOP3Profile", self)

        # Set user agent to a very recent Chrome version to avoid security checks
        self.profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        # Enable persistent cookies
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.AllowPersistentCookies)

        # Add web view with enhanced settings
        self.web_view = QWebEngineView()

        # Create a page with our custom profile
        from PyQt5.QtWebEngineWidgets import QWebEnginePage
        self.page = QWebEnginePage(self.profile, self.web_view)
        self.web_view.setPage(self.page)

        # Connect to console message signal for debugging
        self.page.javaScriptConsoleMessage = self.handle_console_message

        # Connect to load finished signal
        self.page.loadFinished.connect(self._handle_main_load_finished)

        # Connect to load started signal to inject scripts
        self.page.loadStarted.connect(self._inject_browser_spoofing)

        # Connect to geometry changed signal to update capture area
        self.web_view.installEventFilter(self)

        # Enable all necessary features
        settings = self.web_view.settings()
        settings.setAttribute(QWebEngineSettings.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, True)  # Allow pop-ups
        settings.setAttribute(QWebEngineSettings.AutoLoadImages, True)
        settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, True)  # Enable local storage
        settings.setAttribute(QWebEngineSettings.AllowGeolocationOnInsecureOrigins, True)  # Allow geolocation
        settings.setAttribute(QWebEngineSettings.AllowRunningInsecureContent, True)  # Allow mixed content

        # Additional settings for better compatibility
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.ScreenCaptureEnabled, True)
        settings.setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)

        # Additional settings to mimic a standard browser
        settings.setAttribute(QWebEngineSettings.WebGLEnabled, True)
        settings.setAttribute(QWebEngineSettings.Accelerated2dCanvasEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebRTCPublicInterfacesOnly, False)
        settings.setAttribute(QWebEngineSettings.JavascriptCanAccessClipboard, True)
        settings.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, True)

        # Set default font sizes to match Chrome defaults
        settings.setFontSize(QWebEngineSettings.DefaultFontSize, 16)
        settings.setFontSize(QWebEngineSettings.DefaultFixedFontSize, 13)
        settings.setFontSize(QWebEngineSettings.MinimumFontSize, 0)
        settings.setFontSize(QWebEngineSettings.MinimumLogicalFontSize, 6)

        # Set default font families to match Chrome
        settings.setFontFamily(QWebEngineSettings.StandardFont, "Times New Roman")
        settings.setFontFamily(QWebEngineSettings.FixedFont, "Courier New")
        settings.setFontFamily(QWebEngineSettings.SerifFont, "Times New Roman")
        settings.setFontFamily(QWebEngineSettings.SansSerifFont, "Arial")
        settings.setFontFamily(QWebEngineSettings.CursiveFont, "Comic Sans MS")
        settings.setFontFamily(QWebEngineSettings.FantasyFont, "Impact")

        # Connect to createWindow signal to handle pop-ups for different window types
        from PyQt5.QtWebEngineWidgets import QWebEnginePage

        # Define handlers for different window types
        self.page.createWindow = self.handle_popup

        # Connect URL changed signal to update URL bar and handle redirects
        self.web_view.urlChanged.connect(self.update_url_bar)
        self.web_view.urlChanged.connect(self._handle_main_url_change)

        # Set zoom factor to 100%
        self.web_view.setZoomFactor(1.0)

        # Set up a timer to periodically refresh spoofing
        # Use a single-shot timer approach to avoid QBasicTimer thread issues
        self._setup_spoofing_refresh()

        # Load the URL
        self.web_view.load(QUrl(self.url_bar.text()))

        # Set to 16:9 aspect ratio (e.g., 1280x720)
        self.web_view.setMinimumSize(1280, 720)

        # Add to layout
        browser_layout.addWidget(self.web_view)

        # Right side - Bot controls and stats
        bot_container = QWidget()
        bot_layout = QVBoxLayout(bot_container)

        # Configuration
        bot_layout.addWidget(QLabel("<b>1. Configuration</b>"))

        config_grid = QHBoxLayout()

        # Language
        config_grid.addWidget(QLabel("Language:"))
        self.language_combo = QComboBox()
        for language in SUPPORTED_LANGUAGE:
            self.language_combo.addItem(language)
        config_grid.addWidget(self.language_combo)

        # Bet amount
        config_grid.addWidget(QLabel("Bet:"))
        self.bet_combo = QComboBox()
        self.bet_combo.addItems(list(BET_AMOUNT.keys()))
        config_grid.addWidget(self.bet_combo)

        bot_layout.addLayout(config_grid)

        # Control buttons
        bot_layout.addWidget(QLabel("<b>2. Controls</b>"))
        control_layout = QHBoxLayout()
        self.start_button = QPushButton("Start Bot")
        self.start_button.clicked.connect(self.start_bot)
        self.start_button.setEnabled(False)

        self.stop_button = QPushButton("Stop Bot")
        self.stop_button.clicked.connect(self.stop_bot)
        self.stop_button.setEnabled(False)

        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)
        bot_layout.addLayout(control_layout)

        # Preview
        bot_layout.addWidget(QLabel("<b>Game Preview</b>"))
        self.preview_view = QLabel("No Preview Available")
        self.preview_view.setAlignment(Qt.AlignCenter)
        self.preview_view.setMinimumSize(300, 200)
        self.preview_view.setFrameShape(QFrame.Box)
        bot_layout.addWidget(self.preview_view)

        # Stats
        bot_layout.addWidget(QLabel("<b>Statistics</b>"))
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setMaximumHeight(150)
        self.update_stats_display()
        bot_layout.addWidget(self.stats_text)

        # Graph
        bot_layout.addWidget(QLabel("<b>Performance</b>"))
        self.graph_view = QGraphicsView()
        self.graph_scene = QGraphicsScene()
        self.graph_view.setScene(self.graph_scene)
        self.graph_view.setMaximumHeight(150)
        self.update_graph()
        bot_layout.addWidget(self.graph_view)

        # Log
        bot_layout.addWidget(QLabel("<b>Event Log</b>"))
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(100)
        bot_layout.addWidget(self.log_output)

        # Add the containers to the splitter
        main_splitter.addWidget(browser_container)
        main_splitter.addWidget(bot_container)

        # Set the initial sizes (70% browser, 30% bot controls)
        main_splitter.setSizes([700, 300])

        # Add the splitter to the main layout
        main_layout.addWidget(main_splitter)

        self.setLayout(main_layout)

    def navigate_to_url(self):
        """Navigate to the URL in the URL bar."""
        url = self.url_bar.text()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        self.web_view.load(QUrl(url))
        self.log_message(f"Navigating to: {url}")

    def zoom_in(self):
        """Increase the zoom level."""
        current_zoom = self.web_view.zoomFactor()
        new_zoom = min(current_zoom + 0.1, 2.0)  # Cap at 200%
        self.web_view.setZoomFactor(new_zoom)
        self.zoom_reset_button.setText(f"{int(new_zoom * 100)}%")
        self.log_message(f"Zoom level set to {int(new_zoom * 100)}%")

        # Update capture area after zoom change
        QTimer.singleShot(500, self.update_capture_area)

    def zoom_out(self):
        """Decrease the zoom level."""
        current_zoom = self.web_view.zoomFactor()
        new_zoom = max(current_zoom - 0.1, 0.5)  # Minimum 50%
        self.web_view.setZoomFactor(new_zoom)
        self.zoom_reset_button.setText(f"{int(new_zoom * 100)}%")
        self.log_message(f"Zoom level set to {int(new_zoom * 100)}%")

        # Update capture area after zoom change
        QTimer.singleShot(500, self.update_capture_area)

    def zoom_reset(self):
        """Reset zoom to 100%."""
        self.web_view.setZoomFactor(1.0)
        self.zoom_reset_button.setText("100%")
        self.log_message("Zoom level reset to 100%")

        # Update capture area after zoom change
        QTimer.singleShot(500, self.update_capture_area)

    def update_url_bar(self, url):
        """Update the URL bar when the page URL changes."""
        self.url_bar.setText(url.toString())

    def handle_popup(self, window_type):
        """Handle popup windows like Facebook login."""
        from PyQt5.QtWebEngineWidgets import QWebEnginePage

        # Log the request for a popup
        self.log_message(f"Popup requested of type: {window_type}")

        # Create a new page with our profile
        new_page = QWebEnginePage(self.profile, self)

        # Connect to the loadFinished signal to create and show the window
        new_page.loadFinished.connect(lambda ok: self._show_popup_window(new_page, window_type, ok))

        # Connect to authentication required signal
        new_page.authenticationRequired.connect(self._handle_authentication)

        # Connect to URL changed signal to handle redirects
        new_page.urlChanged.connect(lambda url: self._handle_popup_url_change(new_page, url))

        # Connect console message handler
        new_page.javaScriptConsoleMessage = self.handle_console_message

        # Connect to load started signal to inject scripts
        new_page.loadStarted.connect(lambda: self._inject_popup_spoofing(new_page))

        # Return the new page
        return new_page

    def _show_popup_window(self, page, window_type, ok):
        """Create and show a popup window once the page has loaded."""
        # Only proceed if the page loaded successfully
        if not ok:
            self.log_message("Failed to load popup content")
            return

        # Import needed classes
        from PyQt5.QtWebEngineWidgets import QWebEnginePage

        # Create a new window for the popup
        popup = QWebEngineView()

        # Set window title based on type
        if window_type == QWebEnginePage.WebBrowserTab:
            popup.setWindowTitle("New Tab")
        elif window_type == QWebEnginePage.WebBrowserBackgroundTab:
            popup.setWindowTitle("Background Tab")
        elif window_type == QWebEnginePage.WebBrowserWindow:
            popup.setWindowTitle("New Window")
        elif window_type == QWebEnginePage.WebDialog:
            popup.setWindowTitle("Login Window")
        else:
            popup.setWindowTitle("Popup Window")

        # Set size based on type
        if window_type == QWebEnginePage.WebDialog:
            # Login dialogs are typically smaller
            popup.resize(600, 500)
        else:
            # Regular browser windows are larger
            popup.resize(800, 600)

        # Set the page in the view
        popup.setPage(page)

        # Show the window
        popup.show()

        # Store the popup to prevent garbage collection
        self._popups = getattr(self, '_popups', [])
        self._popups.append(popup)

        # Connect close event to remove from list
        popup.closeEvent = lambda event: self._remove_popup(popup, event)

        # Log popup creation
        self.log_message(f"Showing popup window with content")

    def _remove_popup(self, popup, event):
        """Remove popup from list when closed."""
        if hasattr(self, '_popups'):
            if popup in self._popups:
                self._popups.remove(popup)
                self.log_message(f"Popup window closed and removed from tracking")
        event.accept()

    def _handle_authentication(self, url, _):
        """Handle authentication requests."""
        self.log_message(f"Authentication required for: {url.toString()}")
        # We don't handle HTTP authentication in this app, but you could add a dialog here

    def _handle_main_url_change(self, url):
        """Handle URL changes in the main window."""
        # Check if we've been redirected to a login page
        url_str = url.toString()
        if "facebook.com" in url_str or "auth-live.gop3.nl" in url_str:
            self.log_message(f"Main window redirected to auth page: {url_str}")

        # Check if we've been redirected back after login
        if "governorofpoker.com" in url_str and getattr(self, '_was_at_login', False):
            self.log_message("Detected return from login page")
            self._was_at_login = False

            # Refresh the page to ensure cookies are applied
            QTimer.singleShot(1000, self.web_view.reload)

        # Track if we're at a login page
        self._was_at_login = "facebook.com" in url_str or "auth-live.gop3.nl" in url_str

    def _inject_browser_spoofing(self):
        """Inject JavaScript to spoof browser features and avoid detection."""
        # Script to spoof browser features
        spoof_script = """
        // Override navigator properties to match Chrome
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false
        });

        Object.defineProperty(navigator, 'plugins', {
            get: () => {
                return [
                    {
                        0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "Portable Document Format"},
                        description: "Chrome PDF Plugin",
                        filename: "internal-pdf-viewer",
                        name: "Chrome PDF Plugin",
                        length: 1
                    },
                    {
                        0: {type: "application/pdf", suffixes: "pdf", description: "Portable Document Format"},
                        description: "Chrome PDF Viewer",
                        filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                        name: "Chrome PDF Viewer",
                        length: 1
                    },
                    {
                        0: {type: "application/x-nacl", suffixes: "", description: "Native Client Executable"},
                        1: {type: "application/x-pnacl", suffixes: "", description: "Portable Native Client Executable"},
                        description: "Native Client",
                        filename: "internal-nacl-plugin",
                        name: "Native Client",
                        length: 2
                    }
                ];
            }
        });

        // Add Chrome-specific properties
        window.chrome = {
            app: {
                isInstalled: false,
                InstallState: {
                    DISABLED: 'disabled',
                    INSTALLED: 'installed',
                    NOT_INSTALLED: 'not_installed'
                },
                RunningState: {
                    CANNOT_RUN: 'cannot_run',
                    READY_TO_RUN: 'ready_to_run',
                    RUNNING: 'running'
                }
            },
            runtime: {
                OnInstalledReason: {
                    CHROME_UPDATE: 'chrome_update',
                    INSTALL: 'install',
                    SHARED_MODULE_UPDATE: 'shared_module_update',
                    UPDATE: 'update'
                },
                OnRestartRequiredReason: {
                    APP_UPDATE: 'app_update',
                    OS_UPDATE: 'os_update',
                    PERIODIC: 'periodic'
                },
                PlatformArch: {
                    ARM: 'arm',
                    ARM64: 'arm64',
                    MIPS: 'mips',
                    MIPS64: 'mips64',
                    X86_32: 'x86-32',
                    X86_64: 'x86-64'
                },
                PlatformNaclArch: {
                    ARM: 'arm',
                    MIPS: 'mips',
                    MIPS64: 'mips64',
                    X86_32: 'x86-32',
                    X86_64: 'x86-64'
                },
                PlatformOs: {
                    ANDROID: 'android',
                    CROS: 'cros',
                    LINUX: 'linux',
                    MAC: 'mac',
                    OPENBSD: 'openbsd',
                    WIN: 'win'
                },
                RequestUpdateCheckStatus: {
                    NO_UPDATE: 'no_update',
                    THROTTLED: 'throttled',
                    UPDATE_AVAILABLE: 'update_available'
                }
            }
        };

        // Add language and languages properties
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });

        // Add platform property
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });

        console.log('Browser spoofing script injected');
        """

        # Inject the script
        self.page.runJavaScript(spoof_script)

    def _handle_main_load_finished(self, ok):
        """Handle page load finished in the main window."""
        if ok:
            self.log_message("Main page loaded successfully")

            # If we're on the main game page, check if we're logged in
            url_str = self.web_view.url().toString()
            if "governorofpoker.com" in url_str:
                # Run JavaScript to check login status
                self.page.runJavaScript(
                    "document.body.innerHTML.includes('Login') || document.body.innerHTML.includes('Sign in')",
                    self._check_login_status
                )

                # Inject additional spoofing for this specific site
                self._inject_site_specific_spoofing()
        else:
            self.log_message("Main page failed to load")

    def _inject_site_specific_spoofing(self):
        """Inject site-specific spoofing to bypass security checks."""
        site_script = """
        // Override any site-specific detection methods
        try {
            // Disable any fingerprinting or bot detection
            if (typeof CanvasRenderingContext2D !== 'undefined') {
                const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
                CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {
                    const imageData = originalGetImageData.call(this, x, y, w, h);
                    // Add slight random variations to prevent fingerprinting
                    for (let i = 0; i < imageData.data.length; i += 4) {
                        // Only modify alpha channel slightly
                        imageData.data[i + 3] = Math.max(0, Math.min(255, imageData.data[i + 3] + (Math.random() * 2 - 1)));
                    }
                    return imageData;
                };
            }

            // Disable any iframe detection
            if (window.top !== window.self) {
                // Make it appear as if we're not in an iframe
                Object.defineProperty(window, 'top', {
                    get: () => window.self
                });
            }

            // Override any security check functions specific to GOP3
            if (typeof window.checkSecurityStatus === 'function') {
                window.checkSecurityStatus = function() { return true; };
            }

            // Override any bot detection functions
            if (typeof window.isBot === 'function') {
                window.isBot = function() { return false; };
            }

            // Override any automation detection
            if (typeof window.isAutomated === 'function') {
                window.isAutomated = function() { return false; };
            }

            console.log('Site-specific spoofing applied');
        } catch (e) {
            console.error('Error in site-specific spoofing:', e);
        }
        """

        # Inject the script
        self.page.runJavaScript(site_script)

    def _inject_popup_spoofing(self, page):
        """Inject browser spoofing into popup windows."""
        # Use the same spoofing script as the main window
        spoof_script = """
        // Override navigator properties to match Chrome
        Object.defineProperty(navigator, 'webdriver', {
            get: () => false
        });

        // Add Chrome-specific properties
        window.chrome = window.chrome || {
            app: {
                isInstalled: false
            },
            runtime: {}
        };

        // Add language and languages properties
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });

        // Add platform property
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });

        console.log('Popup browser spoofing applied');
        """

        # Inject the script
        page.runJavaScript(spoof_script)

    def _setup_spoofing_refresh(self):
        """Set up a single-shot timer for spoofing refresh to avoid thread issues."""
        QTimer.singleShot(30000, self._do_refresh_spoofing)  # 30 seconds

    def _do_refresh_spoofing(self):
        """Perform the spoofing refresh and schedule the next one."""
        try:
            # Only refresh if we're on the game site
            url_str = self.web_view.url().toString()
            if "governorofpoker.com" in url_str:
                self.log_message("Refreshing browser spoofing...")
                self._inject_browser_spoofing()
                self._inject_site_specific_spoofing()

                # Also refresh spoofing in any open popups
                for popup in getattr(self, '_popups', []):
                    if popup.isVisible():
                        self._inject_popup_spoofing(popup.page())
        except Exception as e:
            self.log_message(f"Error refreshing spoofing: {e}")
            # Continue anyway

        # Schedule the next refresh
        self._setup_spoofing_refresh()

    def _handle_popup_url_change(self, page, url):
        """Handle URL changes in popup windows."""
        url_str = url.toString()
        self.log_message(f"Popup URL changed to: {url_str}")

        # Check for successful login redirects or completion
        if ("facebook.com/connect/login_success" in url_str or
            "auth-live.gop3.nl" in url_str or
            "governorofpoker.com" in url_str):

            self.log_message("Detected potential login completion")

            # Don't close the popup immediately - let the user complete any additional steps
            # Instead, just refresh the main window to apply any cookies
            QTimer.singleShot(2000, self.web_view.reload)

            # If we're back at the main game URL, we can close the popup
            if "governorofpoker.com/games/governor-of-poker-3/play" in url_str:
                # Find the popup window that contains this page
                for popup in getattr(self, '_popups', []):
                    if popup.page() == page:
                        # Close the popup after a longer delay to ensure login completes
                        QTimer.singleShot(5000, popup.close)
                        self.log_message("Scheduled popup to close after returning to game")
                        break

    def _check_login_status(self, has_login_button):
        """Check if the user is logged in based on JavaScript result."""
        if has_login_button:
            self.log_message("User is not logged in")
        else:
            self.log_message("User is logged in successfully")

    def handle_console_message(self, level, message, line, source):
        """Handle JavaScript console messages."""
        level_str = ["Info", "Warning", "Error"][level] if 0 <= level < 3 else "Unknown"
        if level > 0:  # Only log warnings and errors
            self.log_message(f"JS {level_str}: {message} (line {line}, {source})")

    def eventFilter(self, obj, event):
        """Event filter to handle resize events."""
        from PyQt5.QtCore import QEvent

        # Check if this is a resize or show event for the web view
        if obj == self.web_view and (event.type() == QEvent.Resize or event.type() == QEvent.Show):
            # Update the capture area after a short delay to ensure the view is fully rendered
            QTimer.singleShot(500, self.update_capture_area)

        return super().eventFilter(obj, event)

    def update_capture_area(self):
        """Automatically update the capture area based on the web view size."""
        # Get the web view's geometry
        geometry = self.web_view.geometry()

        # Set the capture area to the entire web view
        self.hwnd = self.web_view.winId()
        self.capture_rect = (0, 0, geometry.width(), geometry.height())

        # Get current zoom level
        zoom_level = self.web_view.zoomFactor()

        # Log the update
        self.log_message(f"Capture area automatically set to: {self.capture_rect} (Zoom: {int(zoom_level * 100)}%)")

        # Update the preview
        self.update_capture_preview()

        # Enable the start button
        self.update_start_button_state()

    def log_message(self, message):
        """Appends a message to the log area."""
        self.log_output.append(message)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())  # Auto-scroll

    def update_start_button_state(self):
        """Updates the state of the start button based on configuration."""
        can_start = (self.capture_rect is not None and self.game_thread is None)
        self.start_button.setEnabled(can_start)

    def update_stop_button_state(self):
        """Updates the state of the stop button."""
        self.stop_button.setEnabled(self.game_thread is not None)

    def update_capture_preview(self):
        """Updates the preview with the current capture area."""
        if self.capture_rect is None:
            return

        try:
            # Instead of using window capture, take a screenshot of the web view directly
            # Create a pixmap from the web view
            pixmap = self.web_view.grab(self.web_view.rect())

            # Scale pixmap to fit the preview area while maintaining aspect ratio
            preview_size = self.preview_view.size()
            scaled_pixmap = pixmap.scaled(
                preview_size.width(), preview_size.height(),
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            )

            self.preview_view.setPixmap(scaled_pixmap)
            self.preview_view.setAlignment(Qt.AlignCenter)

            # Log success
            self.log_message("Preview updated successfully")
        except Exception as e:
            self.log_message(f"Error creating preview: {e}")
            self.preview_view.setText("Preview Error")

    def update_stats_display(self):
        """Updates the statistics display."""
        stats_html = f"""
        <table width="100%">
            <tr><td><b>Hands Played:</b></td><td>{self.stats['hands']}</td></tr>
            <tr><td><b>Wins:</b></td><td>{self.stats['wins']}</td></tr>
            <tr><td><b>Losses:</b></td><td>{self.stats['losses']}</td></tr>
            <tr><td><b>Draws:</b></td><td>{self.stats['draws']}</td></tr>
            <tr><td><b>Net Win (Units):</b></td><td>{self.stats['net_win']:.1f}</td></tr>
            <tr><td><b>Last Action:</b></td><td>{self.stats['last_action']}</td></tr>
        </table>
        """
        self.stats_text.setHtml(stats_html)

    def update_graph(self):
        """Updates the performance graph."""
        self.graph_scene.clear()

        # Create matplotlib figure
        fig = plt.figure(figsize=(5, 3), dpi=100)
        ax = fig.add_subplot(111)

        # Plot data
        ax.plot(self.graph_hands, self.graph_net_units, 'b-')
        ax.set_xlabel('Hands')
        ax.set_ylabel('Net Win (Units)')
        ax.grid(True)

        # Convert to Qt
        canvas = FigureCanvas(fig)
        canvas.draw()
        width, height = canvas.get_width_height()

        # Convert to QPixmap and add to scene
        image = QImage(canvas.buffer_rgba(), width, height, QImage.Format_RGBA8888)
        pixmap = QPixmap.fromImage(image)
        self.graph_scene.addPixmap(pixmap)
        self.graph_view.fitInView(self.graph_scene.sceneRect(), Qt.KeepAspectRatio)

    def start_bot(self):
        """Starts the bot."""
        if self.game_thread is not None:
            QMessageBox.warning(self, "Already Running", "Bot is already running.")
            return

        # Make sure we have the current window handle and capture area
        self.hwnd = self.web_view.winId()

        # If capture area is not set, update it now
        if self.capture_rect is None:
            self.update_capture_area()

        language = self.language_combo.currentText()
        bet_amount = self.bet_combo.currentText()

        # Create and start the bot thread
        language_code = LANGUAGE_MAP[language]
        self.game_thread = ProgramThread(
            self.hwnd,
            self.capture_rect,
            bet_amount,
            language_code,
            self.game_id,
            is_web_view=True  # Indicate this is a web view
        )

        # Set the web view reference for direct clicking
        self.game_thread.web_view = self.web_view

        # Move the web view to the main thread to avoid QBasicTimer errors
        self.game_thread.moveToThread(QApplication.instance().thread())

        # Connect signals
        self.game_thread.statusUpdated.connect(self.handle_status_update)
        self.game_thread.statUpdated.connect(self.handle_stat_update)
        self.game_thread.handOutcome.connect(self.handle_hand_outcome)
        self.game_thread.roundInfoUpdated.connect(self.handle_round_info)
        self.game_thread.finished.connect(self.handle_thread_finished)

        # Start the thread
        self.game_thread.start()

        self.log_message(f"Started bot on HWND {self.hwnd}")
        self.update_start_button_state()
        self.update_stop_button_state()

    def stop_bot(self):
        """Stops the bot."""
        if self.game_thread is None:
            return

        self.game_thread.stop()
        self.log_message("Stopping bot...")

    def handle_status_update(self, game_id, status):
        """Handles status updates from the bot thread."""
        if game_id != self.game_id:
            return

        self.log_message(f"Status: {status}")
        self.stats['last_action'] = status
        self.update_stats_display()

    def handle_stat_update(self, game_id, profit_units, condition):
        """Handles end-of-round stat updates."""
        if game_id != self.game_id:
            return

        # Update net win
        self.stats['net_win'] += profit_units

        # Update graph data
        self.graph_hands.append(self.stats['hands'])
        self.graph_net_units.append(self.stats['net_win'])

        # Update displays
        self.update_stats_display()
        self.update_graph()

        # Log the outcome
        self.log_message(f"Round ended: {condition}, Profit: {profit_units:+.2f}")

    def handle_hand_outcome(self, game_id, hand_index, result, profit_units):
        """Handles individual hand outcomes."""
        if game_id != self.game_id:
            return

        # Update stats based on result
        self.stats['hands'] += 1

        if result in ["win", "blackjack"]:
            self.stats['wins'] += 1
        elif result in ["lose", "bust"]:
            self.stats['losses'] += 1
        elif result == "draw":
            self.stats['draws'] += 1

        # Log the outcome
        self.log_message(f"Hand {hand_index} result: {result}, Profit: {profit_units:+.2f}")

        # Update displays
        self.update_stats_display()

    def handle_round_info(self, game_id, dealer_card, player_hands, active_hand_idx, strategy):
        """Handles round information updates."""
        if game_id != self.game_id:
            return

        # Format the information for display
        dealer_str = f"Dealer: {dealer_card}"
        hands_str = " | ".join([f"Hand {i}{' (Active)' if i == active_hand_idx else ''}: {','.join(h)}"
                               for i, h in enumerate(player_hands)])
        strategy_str = f"Strategy: {strategy.upper()}"

        # Update last action
        self.stats['last_action'] = f"{dealer_str} | {hands_str} | {strategy_str}"

        # Update display
        self.update_stats_display()

    def handle_thread_finished(self):
        """Handles the bot thread finishing."""
        self.log_message("Bot stopped.")
        self.game_thread = None
        self.update_start_button_state()
        self.update_stop_button_state()

# Main function to run the web interface
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WebInterface()
    window.setGeometry(100, 100, 1200, 800)
    window.show()
    sys.exit(app.exec_())
