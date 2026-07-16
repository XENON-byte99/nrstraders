import os
import zipfile

LOCAL_ROOT = os.path.dirname(os.path.abspath(__file__))

def main():
    zip_filename = os.path.join(LOCAL_ROOT, "deploy.zip")
    print(f"Creating {zip_filename}...")
    
    skip_dirs = {".venv", "venv", "__pycache__", ".git", "static_root", "media", ".github"}

    # Never overwrite server-owned config/secrets. The production server keeps
    # its own .env (with DEBUG=False, real SECRET_KEY, ALLOWED_HOSTS); pushing
    # the local .env would flip production into debug mode.
    skip_files = {
        "deploy.zip", "deploy_logs.txt", "deploy_nrs_out.txt",
        ".env", ".env.local", "db_upload.sqlite3", "db_deploy.sqlite3",
    }

    count = 0
    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(LOCAL_ROOT):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f in skip_files:
                    continue
                local_path = os.path.join(root, f)
                rel_path = os.path.relpath(local_path, LOCAL_ROOT)
                
                if f == "db.sqlite3":
                    zipf.write(local_path, "db_deploy.sqlite3")
                    print("Added db.sqlite3 as db_deploy.sqlite3")
                else:
                    zipf.write(local_path, rel_path)
                count += 1
                    
    print(f"Zip creation complete! Added {count} files.")

if __name__ == "__main__":
    main()
