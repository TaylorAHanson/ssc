#!/usr/bin/env python3
"""
Entrypoint for running ATLAS on Databricks Apps.
Handles path setup and starts the uvicorn server.
"""
import os
import sys
import subprocess

def main():
    # Get absolute path of python executable
    python_exe = os.path.abspath(sys.executable)

    # Get the directory where this script lives
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    print(f"Starting ATLAS...")
    print(f"Script directory: {script_dir}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Directory contents: {os.listdir(script_dir)}")
    
    # === DEBUG: Dump database-related environment variables ===
    print("\n=== DATABASE ENV VARS DEBUG ===", flush=True)
    db_vars = ["DATABASE_URL", "DATABASE_HOST", "DATABASE_PORT", "DATABASE_NAME", 
               "DATABASE_USER", "DATABASE_PASSWORD", "PGUSER", "PGPASSWORD", "PGHOST"]
    for var in db_vars:
        val = os.environ.get(var)
        if val:
            # Mask passwords
            if "PASSWORD" in var or "URL" in var:
                print(f"  {var} = [SET, length={len(val)}]", flush=True)
            else:
                print(f"  {var} = {val}", flush=True)
        else:
            print(f"  {var} = [NOT SET]", flush=True)
    print("=== END DATABASE ENV VARS ===\n", flush=True)
    
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
        subprocess.run([python_exe, "-m", "pip", "install", "-r", req_file, "-q"], check=True)
        
    # Import and run uvicorn
    print("Starting uvicorn server...")
    import uvicorn
    from app.main import app
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
