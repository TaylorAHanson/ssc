#!/usr/bin/env python3
"""
Entrypoint for running EDAS Hub on Databricks Apps.
Handles path setup and starts the uvicorn server.
"""
import os
import sys
import subprocess

def main():
    # Get the directory where this script lives
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    print(f"Starting EDAS Hub...")
    print(f"Script directory: {script_dir}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Directory contents: {os.listdir(script_dir)}")
    
    # Change to script directory
    os.chdir(script_dir)
    print(f"Changed to: {os.getcwd()}")
    
    # Add script directory to Python path
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    
    print(f"Python path: {sys.path[:3]}...")
    
    # Install requirements if needed
    req_file = os.path.join(script_dir, "requirements.txt")
    if os.path.exists(req_file):
        print("Installing requirements...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file, "-q"], check=True)
    
    # Import and run uvicorn
    print("Starting uvicorn server...")
    import uvicorn
    from app.main import app
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
