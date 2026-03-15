import os
import sys
import platform
import subprocess
import urllib.request
import venv
import shutil
import re

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

def create_directories():
    print_step("Creating required directories...")
    for d in ["clips", "logs", "temp"]:
        os.makedirs(d, exist_ok=True)
        print(f"✅ Created '{d}/'")

def create_launchers(os_name, is_arch=False):
    print_step("Creating Launchers and Update Scripts...")
    py_exec = sys.executable

    if os_name == "Windows":
        with open("start.bat", "w") as f:
            f.write("@echo off\n")
            f.write("call venv\\Scripts\\activate\n")
            f.write("python -m streamlit run app.py\n")  # <-- FIXED LINE
            f.write("pause\n")
        with open("update.bat", "w", encoding="utf-8") as f:
            f.write("@echo off\necho 🔄 Updating AI Video Editor...\n")
            f.write("git fetch\ngit reset --hard @{u}\n") 
            f.write("echo ⚙️ Re-checking dependencies...\n")
            f.write(f'"{py_exec}" install.py\npause\n')
        print("✅ Created 'start.bat' and 'update.bat'")
    else:
        with open("start.sh", "w") as f:
            f.write("#!/bin/bash\n")
            if not is_arch: 
                f.write("source venv/bin/activate\n")
            f.write("python3 -m streamlit run app.py\n") # <-- FIXED LINE
            
        with open("update.sh", "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write('echo "🔄 Updating AI Video Editor..."\n')
            f.write("git fetch\ngit reset --hard @{u}\n") 
            f.write('echo "⚙️ Re-checking dependencies..."\n')
            f.write(f'"{py_exec}" install.py\n')
            f.write('echo "✅ Update complete!"\n')
        os.chmod("start.sh", 0o755)
        os.chmod("update.sh", 0o755)
        print("✅ Created 'start.sh' and 'update.sh'")

def setup_env_file(ts_path):
    print_step("Configuring .env file paths...")
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            content = f.read()
        
        # Replace the TORRSERVER_PATH line
        if "TORRSERVER_PATH=" in content:
            content = re.sub(r"TORRSERVER_PATH=.*", f"TORRSERVER_PATH={ts_path}", content)
        else:
            content += f"\nTORRSERVER_PATH={ts_path}\n"
            
        with open(".env", "w", encoding="utf-8") as f:
            f.write(content)
        print("✅ Updated TorrServer path in existing .env file.")
    else:
        print("⚠️ .env file missing! Did you forget to pull it from Git?")

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

    if not shutil.which("ffmpeg"):
        if os_name == "Windows": run_cmd("winget install -e --id Gyan.FFmpeg", shell=True)
        elif os_name == "Darwin": run_cmd(["brew", "install", "ffmpeg"])
        elif shutil.which("apt"):
            run_cmd(["sudo", "apt", "update"])
            run_cmd(["sudo", "apt", "install", "-y", "ffmpeg"])

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
        print(f"Downloading local TorrServer...")
        try:
            urllib.request.urlretrieve(TORRSERVER_BASE_URL + ts_filename, out_name)
            if os_name != "Windows": os.chmod(out_name, 0o755)
        except Exception as e: print(f"❌ Failed to download TorrServer: {e}")

    venv_dir = "venv"
    if not os.path.exists(venv_dir): venv.create(venv_dir, with_pip=True)
    
    pip_cmd = os.path.join(venv_dir, "Scripts", "pip") if os_name == "Windows" else os.path.join(venv_dir, "bin", "pip")
    py_cmd = os.path.join(venv_dir, "Scripts", "python") if os_name == "Windows" else os.path.join(venv_dir, "bin", "python")

    print("Installing python packages via pip...")
    run_cmd([pip_cmd, "install", "--upgrade", "pip"])
    run_cmd([pip_cmd, "install", "-r", "requirements.txt"])
    run_cmd([py_cmd, "-c", "import nltk; nltk.download('wordnet', quiet=True); nltk.download('omw-1.4', quiet=True); nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True)"])

    setup_env_file(f"./{out_name}" if os_name != "Windows" else out_name)
    create_launchers(os_name, is_arch=False)

def main():
    print("\n🎬 Welcome to the AI Video Editor Universal Installer 🎬\n")
    create_directories()
    
    if is_arch_linux(): install_arch()
    else: install_standard()
        
    print_step("INSTALLATION COMPLETE!")
    print("Use the 'start' script to run, and the 'update' script to get the latest version.")

if __name__ == "__main__":
    main()