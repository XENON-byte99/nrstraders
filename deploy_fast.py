import paramiko

VPS_HOST    = "194.238.19.210"
VPS_PORT    = 22
VPS_USER    = "root"
VPS_PASS    = "qscfkz0B&VY?rf7D"
REMOTE_ROOT = "/var/www/nrs"

def run_ssh(client, cmd):
    print(f"Running: {cmd}")
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out: print(out)
    if err: print(err)

def main():
    print("Connecting...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER, password=VPS_PASS)
    sftp = client.open_sftp()
    
    print("Uploading deploy.zip...")
    sftp.put("deploy.zip", "/tmp/deploy.zip")
    
    print("Extracting files...")
    run_ssh(client, f"mkdir -p {REMOTE_ROOT} && unzip -o /tmp/deploy.zip -d {REMOTE_ROOT}")
    
    print("Setting permissions...")
    run_ssh(client, f"chmod 666 {REMOTE_ROOT}/db.sqlite3")
    run_ssh(client, f"chown -R www-data:www-data {REMOTE_ROOT}")
    
    print("Running Django commands...")
    # Setup venv if it doesn't exist
    run_ssh(client, f"cd {REMOTE_ROOT} && python3 -m venv .venv")
    run_ssh(client, f"cd {REMOTE_ROOT} && {REMOTE_ROOT}/.venv/bin/pip install -r requirements.txt")
    run_ssh(client, f"cd {REMOTE_ROOT} && {REMOTE_ROOT}/.venv/bin/python manage.py collectstatic --no-input")
    run_ssh(client, f"cd {REMOTE_ROOT} && {REMOTE_ROOT}/.venv/bin/python manage.py migrate")
    
    # We should make sure the service is correct
    print("Restarting service...")
    run_ssh(client, f"systemctl restart nrs || systemctl daemon-reload && systemctl restart nrs")
    
    client.close()
    print("Deployment finished successfully!")

if __name__ == "__main__":
    main()
