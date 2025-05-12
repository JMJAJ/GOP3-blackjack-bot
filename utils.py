import ctypes
try:
    # Make process DPI aware - This helps with coordinate consistency on scaled displays
    ctypes.windll.shcore.SetProcessDpiAwareness(1) # PROCESS_PER_MONITOR_DPI_AWARE = 1
    print("Process DPI awareness set to Per-Monitor Aware.")
except AttributeError:
    try:
        # Fallback for older Windows versions
        ctypes.windll.user32.SetProcessDPIAware()
        print("Process DPI awareness set using SetProcessDPIAware().")
    except AttributeError:
        print("Warning: Could not set process DPI awareness.")

import os
import sys
import cv2
import time
import win32gui
import win32ui
import win32con
import win32api
from PIL import Image
from ctypes import windll, byref, wintypes

# --- resource_path, safe_imread, find_windows_by_title, get_window_title ---
# ... (Keep these as they were) ...
def resource_path(relative_path):
    try: base_path = sys._MEIPASS
    except Exception: base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def safe_imread(file_path, flag=0):
    img = cv2.imread(resource_path(file_path), flag)
    if img is None: print(f"Warning: Failed to load image {resource_path(file_path)}")
    return img

def find_windows_by_title(title_substring=""):
    hwnds = []
    def callback(hwnd, collector):
        if win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd):
            window_title = win32gui.GetWindowText(hwnd)
            if not title_substring or title_substring.lower() in window_title.lower():
                hwnds.append(hwnd)
        return True
    win32gui.EnumWindows(callback, hwnds)
    return hwnds

def get_window_title(hwnd):
    try: return win32gui.GetWindowText(hwnd)
    except: return "[Error getting title]"

# --- screen_to_client (Still needed for finalizing drag area) ---
def screen_to_client(hwnd, screen_x, screen_y):
    try:
        point = wintypes.POINT(screen_x, screen_y)
        windll.user32.ScreenToClient(hwnd, byref(point))
        return point.x, point.y
    except Exception as e:
        print(f"Error in screen_to_client for hwnd {hwnd}: {e}")
        return None, None

# --- !!! REVISED capture_window_region_to_pil using screen coords !!! ---
# Note: This function now needs the *screen* coordinates of the desired region,
#       in addition to the HWND. We'll adapt the calling code later.
#       Let's revert to passing the *client* rect, as coordinate mapping needs it.
#       The fix is in how we calculate the crop from the full window image.

def capture_window_region_to_pil(hwnd, client_capture_rect):
    """Captures a specific region (client coordinates) of a window using PrintWindow and cropping."""
    hwnd_dc = None
    mfc_dc = None
    save_dc = None
    save_bitmap = None
    try:
        # Target client coordinates and size
        cl_left, cl_top, cl_right, cl_bottom = client_capture_rect
        capture_width = cl_right - cl_left
        capture_height = cl_bottom - cl_top

        if capture_width <= 0 or capture_height <= 0:
            # This check should ideally happen *before* calling capture
            print(f"Invalid client_capture_rect dimensions for hwnd {hwnd}: {capture_width}x{capture_height}")
            return None

        # Get the full window rectangle (screen coordinates) for PrintWindow target size
        window_rect = win32gui.GetWindowRect(hwnd)
        window_width = window_rect[2] - window_rect[0]
        window_height = window_rect[3] - window_rect[1]

        if window_width <= 0 or window_height <= 0:
             print(f"Window {hwnd} has invalid dimensions: {window_width}x{window_height}")
             return None

        # Create compatible DCs and Bitmap for the *full window* size
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        if not hwnd_dc: raise Exception("Failed to get Window DC")
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()

        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_dc, window_width, window_height)
        save_dc.SelectObject(save_bitmap)

        # Use PrintWindow to capture the window content into save_dc
        result = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2) # Try flag 2 first
        if result == 0:
            # print(f"PrintWindow flag 2 failed for hwnd {hwnd}. Trying flag 0.")
            result = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
            if result == 0:
                 raise Exception(f"PrintWindow failed for hwnd {hwnd} with both flag 2 and 0.")

        # Convert the captured full bitmap to a PIL Image
        bmp_info = save_bitmap.GetInfo()
        bmp_str = save_bitmap.GetBitmapBits(True)
        full_im = Image.frombuffer(
            'RGB', (bmp_info['bmWidth'], bmp_info['bmHeight']),
            bmp_str, 'raw', 'BGRX', 0, 1
        )

        # --- Cleanup GDI objects ---
        win32gui.DeleteObject(save_bitmap.GetHandle()); save_bitmap = None
        save_dc.DeleteDC(); save_dc = None
        mfc_dc.DeleteDC(); mfc_dc = None
        win32gui.ReleaseDC(hwnd, hwnd_dc); hwnd_dc = None
        # --- End GDI Cleanup ---

        # --- Fix Crop Calculation ---
        # We need the offset of the client area's origin (0,0 client coords)
        # within the full window image captured by PrintWindow.

        # Get client area top-left position relative to the screen
        try:
            client_origin_screen_x, client_origin_screen_y = win32gui.ClientToScreen(hwnd, (0, 0))
        except Exception as e:
             raise Exception(f"ClientToScreen failed for HWND {hwnd}: {e}") # Window might have closed

        # Get window top-left position relative to the screen (already have from window_rect)
        window_origin_screen_x, window_origin_screen_y = window_rect[0], window_rect[1]

        # Offset of client area top-left within the full window image
        offset_x = client_origin_screen_x - window_origin_screen_x
        offset_y = client_origin_screen_y - window_origin_screen_y

        # Calculate the crop box relative to the full window image's top-left
        # using the *client* coordinates passed in client_capture_rect
        crop_left = cl_left + offset_x
        crop_top = cl_top + offset_y
        crop_right = cl_right + offset_x # Use cl_right (end coord)
        crop_bottom = cl_bottom + offset_y # Use cl_bottom (end coord)

        # Ensure crop coordinates are valid within the full image bounds
        crop_left = max(0, crop_left)
        crop_top = max(0, crop_top)
        crop_right = min(full_im.width, crop_right)
        crop_bottom = min(full_im.height, crop_bottom)

        # Check for validity before cropping
        if crop_right <= crop_left or crop_bottom <= crop_top:
            print(f"Error: Calculated invalid crop box before crop: ({crop_left},{crop_top}) to ({crop_right},{crop_bottom}) for window size {full_im.size}. Offsets: ({offset_x},{offset_y}), ClientRect: {client_capture_rect}")
            # Try capturing the whole client area as a fallback?
            # Get full client rect
            # _, _, client_w, client_h = win32gui.GetClientRect(hwnd)
            # crop_left_fb = offset_x
            # crop_top_fb = offset_y
            # crop_right_fb = min(full_im.width, offset_x + client_w)
            # crop_bottom_fb = min(full_im.height, offset_y + client_h)
            # if crop_right_fb > crop_left_fb and crop_bottom_fb > crop_top_fb:
            #     print("Warning: Falling back to capturing full client area due to invalid crop box.")
            #     return full_im.crop((crop_left_fb, crop_top_fb, crop_right_fb, crop_bottom_fb))
            # else:
            #     print("Error: Fallback crop box also invalid.")
            #     return None # Give up if calculation is fundamentally broken
            return None # Don't fallback for now, just report error

        # Perform the crop
        cropped_im = full_im.crop((crop_left, crop_top, crop_right, crop_bottom))

        # Final check on cropped image size (sometimes PrintWindow captures slightly different dimensions)
        if cropped_im.width != capture_width or cropped_im.height != capture_height:
             # This might be okay if difference is small (1-2 pixels), but log it.
             print(f"Warning: Final cropped image size ({cropped_im.width}x{cropped_im.height}) differs slightly from requested region size ({capture_width}x{capture_height}).")
             # If significantly different, it's an error
             if abs(cropped_im.width - capture_width) > 5 or abs(cropped_im.height - capture_height) > 5:
                 print("Error: Significant size mismatch after cropping.")
                 return None # Treat as error

        return cropped_im

    except Exception as e:
        print(f"Error during capture/crop for hwnd {hwnd}: {e}")
        # Ensure cleanup (redundant with earlier cleanup, but safe)
        if save_bitmap and save_bitmap.GetSafeHandle(): win32gui.DeleteObject(save_bitmap.GetHandle())
        if save_dc and save_dc.GetSafeHdc(): save_dc.DeleteDC()
        if mfc_dc and mfc_dc.GetSafeHdc(): mfc_dc.DeleteDC()
        if hwnd_dc: win32gui.ReleaseDC(hwnd, hwnd_dc)
        return None
    # No finally needed as cleanup happens within try/except

# --- click_in_window_client_coords, map_std_to_custom_coords ---
# ... (Keep these as they were) ...
def click_in_window_client_coords(hwnd, x, y, button='left', duration_seconds=0.05):
    l_param = win32api.MAKELONG(x, y)
    win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, l_param)
    time.sleep(max(0.01, duration_seconds / 5))
    if button == 'left':
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, l_param)
        time.sleep(max(0.025, duration_seconds / 2))
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, l_param)
    elif button == 'right':
        win32api.PostMessage(hwnd, win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON, l_param)
        time.sleep(max(0.025, duration_seconds / 2))
        win32api.PostMessage(hwnd, win32con.WM_RBUTTONUP, 0, l_param)
    time.sleep(max(0.01, duration_seconds / 5))

def map_std_to_custom_coords(std_x, std_y, capture_rect, std_width, std_height):
    custom_left, custom_top, custom_right, custom_bottom = capture_rect
    custom_width = custom_right - custom_left
    custom_height = custom_bottom - custom_top
    if std_width == 0 or std_height == 0: return custom_left, custom_top
    prop_x = std_x / std_width
    prop_y = std_y / std_height
    target_x_in_custom = int(prop_x * custom_width)
    target_y_in_custom = int(prop_y * custom_height)
    final_client_x = custom_left + target_x_in_custom
    final_client_y = custom_top + target_y_in_custom
    return final_client_x, final_client_y