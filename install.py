import os
import sys
import platform
import subprocess
import urllib.request
import venv
import shutil

TORRSERVER_VERSION = "MatriX.135"
TORRSERVER_BASE_URL = f"https://github.com/YouRoK/TorrServer/releases/download/{TORRSERVER_VERSION}/"

def print_step(msg):
    print(f"\n{'-'*60}\n🚀 {msg}\n{'-'*60}")

def run_cmd(cmd, shell=False):
    try:
        subprocess.run(cmd, shell=shell, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Command failed: {e}")
        return False

def is_arch_linux():
    return os.path.exists("/etc/arch-release")

def create_launchers(os_name, is_arch=False):
    print_step("Creating Launchers and Update Scripts...")
    
    # Get the python executable that is running this script
    py_exec = sys.executable

    if os_name == "Windows":
        # 1. Windows START Script
        with open("start.bat", "w") as f:
            f.write("@echo off\n")
            f.write("call venv\\Scripts\\activate\n")
            f.write("streamlit run app.py\n")
            f.write("pause\n")
            
        # 2. Windows UPDATE Script
        with open("update.bat", "w", encoding="utf-8") as f:
            f.write("@echo off\n")
            f.write("echo 🔄 Updating AI Video Editor...\n")
            f.write("git fetch\n")
            f.write("git reset --hard @{u}\n") # Force sync with GitHub branch
            f.write("echo ⚙️ Re-checking dependencies...\n")
            f.write(f'"{py_exec}" install.py\n')
            f.write("pause\n")
            
        print("✅ Created 'start.bat' and 'update.bat'")
        
    else:
        # 1. Mac/Linux START Script
        with open("start.sh", "w") as f:
            f.write("#!/bin/bash\n")
            if not is_arch:
                f.write("source venv/bin/activate\n")
            f.write("streamlit run app.py\n")
            
        # 2. Mac/Linux UPDATE Script
        with open("update.sh", "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write('echo "🔄 Updating AI Video Editor..."\n')
            f.write("git fetch\n")
            f.write("git reset --hard @{u}\n") # Force sync with GitHub branch
            f.write('echo "⚙️ Re-checking dependencies..."\n')
            f.write(f'"{py_exec}" install.py\n')
            f.write('echo "✅ Update complete!"\n')
            
        os.chmod("start.sh", 0o755)
        os.chmod("update.sh", 0o755)
        print("✅ Created 'start.sh' and 'update.sh'")


# ==========================================
# ARCH LINUX NATIVE INSTALLATION
# ==========================================
def install_arch():
    print_step("Arch Linux Detected! Installing 'The Arch Way' via yay...")
    
    if not shutil.which("yay"):
        print("❌ 'yay' is not installed. Please install it first.")
        sys.exit(1)

    arch_packages = [
        "ffmpeg", "yt-dlp", "torrserver-bin",
        "python-streamlit", "python-dotenv", "python-pandas", 
        "python-nltk", "python-requests", "python-beautifulsoup4", 
        "python-pymorphy3", "python-bencode.py"
    ]
    
    print(f"Installing packages: {' '.join(arch_packages)}")
    run_cmd(["yay", "-S", "--needed", "--noconfirm"] + arch_packages)

    print("\nDownloading NLTK language models globally...")
    run_cmd([sys.executable, "-c", "import nltk; nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True); nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True)"])

    setup_env_file("torrserver")
    create_launchers("Linux", is_arch=True)

# ==========================================
# WINDOWS / MACOS / UBUNTU INSTALLATION
# ==========================================
def install_standard():
    os_name = platform.system()
    print_step(f"{os_name} Detected! Setting up Virtual Environment...")

    # 1. Install System FFmpeg
    if not shutil.which("ffmpeg"):
        if os_name == "Windows":
            print("Attempting to install FFmpeg via winget...")
            run_cmd("winget install -e --id Gyan.FFmpeg", shell=True)
        elif os_name == "Darwin":
            print("Attempting to install FFmpeg via Homebrew...")
            run_cmd(["brew", "install", "ffmpeg"])
        elif shutil.which("apt"):
            run_cmd(["sudo", "apt", "update"])
            run_cmd(["sudo", "apt", "install", "-y", "ffmpeg"])

    # 2. Download Local TorrServer
    arch = platform.machine().lower()
    if os_name == "Windows":
        ts_filename = "TorrServer-windows-amd64.exe"
        out_name = "torrserver.exe"
    elif os_name == "Darwin":
        ts_filename = "TorrServer-darwin-arm64" if "arm" in arch else "TorrServer-darwin-amd64"
        out_name = "torrserver"
    else:
        ts_filename = "TorrServer-linux-arm64" if "arm" in arch or "aarch" in arch else "TorrServer-linux-amd64"
        out_name = "torrserver"

    if not os.path.exists(out_name):
        url = TORRSERVER_BASE_URL + ts_filename
        print(f"Downloading local TorrServer from {url}...")
        try:
            urllib.request.urlretrieve(url, out_name)
            if os_name != "Windows": os.chmod(out_name, 0o755)
            print("✅ TorrServer downloaded.")
        except Exception as e:
            print(f"❌ Failed to download TorrServer: {e}")

    # 3. Setup VENV & PIP
    venv_dir = "venv"
    if not os.path.exists(venv_dir):
        venv.create(venv_dir, with_pip=True)
    
    pip_cmd = os.path.join(venv_dir, "Scripts", "pip") if os_name == "Windows" else os.path.join(venv_dir, "bin", "pip")
    py_cmd = os.path.join(venv_dir, "Scripts", "python") if os_name == "Windows" else os.path.join(venv_dir, "bin", "python")

    print("Installing python packages via pip...")
    run_cmd([pip_cmd, "install", "--upgrade", "pip"])
    run_cmd([pip_cmd, "install", "-r", "requirements.txt"])

    print("Downloading NLTK language models into venv...")
    run_cmd([py_cmd, "-c", "import nltk; nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True); nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True)"])

    ts_path = f"./{out_name}" if os_name != "Windows" else out_name
    setup_env_file(ts_path)
    create_launchers(os_name, is_arch=False)

# ==========================================
# SHARED UTILITIES
# ==========================================
def setup_env_file(ts_path):
    print_step("Configuring .env file...")
    if not os.path.exists(".env") and os.path.exists(".env.example"):
        shutil.copy(".env.example", ".env")
        with open(".env", "r") as f: data = f.read()
        data = data.replace("TORRSERVER_PATH=", f"TORRSERVER_PATH={ts_path}")
        with open(".env", "w") as f: f.write(data)
        print("✅ Created '.env'. PLEASE ADD YOUR API KEYS INSIDE THIS FILE!")
    else:
        print("✅ '.env' file already exists.")

def main():
    print("\n🎬 Welcome to the AI Video Editor Universal Installer 🎬\n")
    
    # Check Git
    if not shutil.which("git"):
        print("⚠️ Warning: Git is not installed. Auto-updating will not work.")
        
    if is_arch_linux():
        install_arch()
    else:
        install_standard()
        
    print_step("INSTALLATION COMPLETE!")
    print("1. Open the '.env' file and add your OpenRouter API key and RuTracker login.")
    print("2. Ensure 'movies_master.sqlite' is placed in this folder.")
    print("3. Use the 'start' script to run the app, and the 'update' script to get the latest version.")

if __name__ == "__main__":
    main()