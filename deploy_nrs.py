import os
import sys
import paramiko
import time

VPS_HOST    = "194.238.19.210"
VPS_PORT    = 22
VPS_USER    = "root"
VPS_PASS    = "qscfkz0B&VY?rf7D"
LOCAL_ROOT  = os.path.dirname(os.path.abspath(__file__))
REMOTE_ROOT = "/var/www/nrs"

def ensure_remote_dirs(sftp, paths):
    created = set()
    for rp in paths:
        d = rp.rsplit("/", 1)[0]
        if d == REMOTE_ROOT or d in created:
            continue
        parts = d.split("/")
        for i in range(2, len(parts) + 1):
            partial = "/".join(parts[:i])
            if partial not in created:
                try:
                    sftp.stat(partial)
                except FileNotFoundError:
                    sftp.mkdir(partial)
                created.add(partial)

def run_ssh(client, cmd):
    print(f"Running: {cmd}")
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out: print(out)
    if err: print(err)

def main():
    print("Collecting files...")
    files_to_upload = []
    skip_dirs = {".venv", "venv", "__pycache__", ".git", "static_root", "media", ".github"}
    for root, dirs, files in os.walk(LOCAL_ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f in {"deploy_nrs.py", "deploy.bat", "START_WEBSITE.bat", "db_upload.sqlite3", "fetch_github_logs.py"}:
                continue
            local_path = os.path.join(root, f)
            rel = os.path.relpath(local_path, LOCAL_ROOT)
            remote_path = f"{REMOTE_ROOT}/{rel.replace(chr(92), '/')}"
            files_to_upload.append((local_path, remote_path))

    print(f"Uploading {len(files_to_upload)} files...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER, password=VPS_PASS)
    sftp = client.open_sftp()
    
    ensure_remote_dirs(sftp, [rp for _, rp in files_to_upload])
    for lp, rp in files_to_upload:
        sftp.put(lp, rp)
        print(f"Uploaded: {rp}")
        
    print("Upload complete. Running server commands...")
    
    # Set permissions on the database so www-data can read/write it
    run_ssh(client, f"chmod 666 {REMOTE_ROOT}/db.sqlite3")
    
    run_ssh(client, f"cd {REMOTE_ROOT} && {REMOTE_ROOT}/.venv/bin/pip install -r requirements.txt")
    run_ssh(client, f"cd {REMOTE_ROOT} && {REMOTE_ROOT}/.venv/bin/python manage.py collectstatic --no-input")
    run_ssh(client, f"cd {REMOTE_ROOT} && {REMOTE_ROOT}/.venv/bin/python manage.py migrate")
    run_ssh(client, f"systemctl restart nrs")
    
    client.close()
    print("Deployment finished successfully!")

if __name__ == "__main__":
    main()
