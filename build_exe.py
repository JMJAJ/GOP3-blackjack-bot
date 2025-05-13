"""
Build Executable Script

This script automates the process of building executable files for the GOP3 Blackjack Bot.
It builds both the main application and the web interface, and ensures all necessary
DLLs are included in the distribution.

Usage:
    python build_exe.py

The script will:
1. Install required packages
2. Build the main application executable
3. Build the web interface executable
4. Download and fix missing DLLs
5. Create a combined distribution folder with all files
"""

import os
import sys
import subprocess
import shutil
import platform

def install_requirements():
    """Install required packages"""
    print("Installing required packages...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"])
    subprocess.run([sys.executable, "-m", "pip", "install", "PyQtWebEngine"])
    print("Required packages installed.\n")

def build_main_app():
    """Build the main application executable"""
    print("Building main application...")
    subprocess.run(["pyinstaller", "main.spec"])
    print("Main application built.\n")

def build_web_interface():
    """Build the web interface executable"""
    print("Building web interface...")
    subprocess.run(["pyinstaller", "web_launcher.spec"])
    print("Web interface built.\n")

def fix_missing_dlls():
    """Download and fix missing DLLs"""
    print("Downloading and fixing missing DLLs...")
    subprocess.run([sys.executable, "download_missing_dlls.py"])
    print("DLLs fixed.\n")

def copy_dlls_to_dist():
    """Copy DLLs to executable directories"""
    print("Copying DLLs to executable directories...")
    
    # List of DLLs to copy
    dll_list = [
        "api-ms-win-core-path-l1-1-0.dll",
        "api-ms-win-core-file-l1-2-0.dll",
        "api-ms-win-core-file-l2-1-0.dll",
        "api-ms-win-core-localization-l1-2-0.dll",
        "api-ms-win-core-synch-l1-2-0.dll",
        "api-ms-win-core-processthreads-l1-1-1.dll",
        "api-ms-win-core-datetime-l1-1-1.dll",
        "api-ms-win-core-string-l1-1-0.dll",
    ]
    
    # Ensure directories exist
    os.makedirs("dist/GOP3_Blackjack_Bot", exist_ok=True)
    os.makedirs("dist/GOP3_Web_Interface", exist_ok=True)
    
    # Copy DLLs
    for dll in dll_list:
        if os.path.exists(dll):
            print(f"Copying {dll} to dist folders...")
            shutil.copy2(dll, os.path.join("dist", "GOP3_Blackjack_Bot", dll))
            shutil.copy2(dll, os.path.join("dist", "GOP3_Web_Interface", dll))
    
    print("DLLs copied.\n")

def create_combined_dist():
    """Create a combined distribution folder with all files"""
    print("Creating combined distribution folder...")
    
    # Create combined dist directory
    combined_dir = "dist/GOP3_Combined"
    os.makedirs(combined_dir, exist_ok=True)
    
    # Copy main app
    main_app_dir = "dist/GOP3_Blackjack_Bot"
    if os.path.exists(main_app_dir):
        for item in os.listdir(main_app_dir):
            src = os.path.join(main_app_dir, item)
            dst = os.path.join(combined_dir, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
    
    # Copy web interface executable
    web_exe = "dist/GOP3_Web_Interface/GOP3_Web_Interface.exe"
    if os.path.exists(web_exe):
        shutil.copy2(web_exe, combined_dir)
    
    # Copy README files
    for readme in ["README.md", "README_BUILDING.md", "README_WEB_INTERFACE.md", "README_ENHANCED_WEB.md"]:
        if os.path.exists(readme):
            shutil.copy2(readme, combined_dir)
    
    print("Combined distribution created.\n")

def main():
    """Main function"""
    # Check if running on Windows
    if platform.system() != "Windows":
        print("This script is only for Windows systems.")
        return
    
    print("GOP3 Blackjack Bot Build Script")
    print("==============================\n")
    
    # Execute build steps
    install_requirements()
    build_main_app()
    build_web_interface()
    fix_missing_dlls()
    copy_dlls_to_dist()
    create_combined_dist()
    
    print("Build completed!")
    print("\nThe executables are located in:")
    print("- dist/GOP3_Blackjack_Bot/GOP3_Blackjack_Bot.exe (Full application)")
    print("- dist/GOP3_Web_Interface/GOP3_Web_Interface.exe (Web interface only)")
    print("- dist/GOP3_Combined/ (Combined distribution with both executables)")
    print("\nTo distribute the application, copy the entire contents of the respective")
    print("directory to the target machine.")

if __name__ == "__main__":
    main()
