from warnings import simplefilter
import pyautogui
from PyQt5.QtWidgets import QApplication
from gui import MainApp

# Omit pyautogui warning
simplefilter("ignore")
pyautogui.FAILSAFE = False

if __name__ == "__main__":
    app = QApplication([])
    window = MainApp()
    window.show()
    app.exec_()
