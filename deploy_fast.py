import paramiko

VPS_HOST    = "194.238.19.210"
VPS_PORT    = 22
VPS_USER    = "root"
VPS_PASS    = "qscfkz0B&VY?rf7D"

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
    sftp.close()
    
    dirs = ["/var/www/nrs-software", "/var/www/nrs"]
    
    for folder in dirs:
        print(f"\n--- Deploying to {folder} ---")
        run_ssh(client, f"mkdir -p {folder} && unzip -o /tmp/deploy.zip -d {folder}")
        run_ssh(client, f"if [ -f {folder}/db_deploy.sqlite3 ]; then mv -f {folder}/db_deploy.sqlite3 {folder}/db.sqlite3; fi")
        run_ssh(client, f"chmod 666 {folder}/db.sqlite3")
        run_ssh(client, f"chown -R www-data:www-data {folder}")
        
        cmd_env = f"cd {folder} && if [ -d venv ]; then ENV_PATH=venv; elif [ -d .venv ]; then ENV_PATH=.venv; else python3 -m venv venv && ENV_PATH=venv; fi && $ENV_PATH/bin/pip install -r requirements.txt && $ENV_PATH/bin/python manage.py collectstatic --no-input && $ENV_PATH/bin/python manage.py migrate"
        run_ssh(client, cmd_env)
        
    print("\n--- Restarting services ---")
    run_ssh(client, "systemctl restart nrs-software || true")
    run_ssh(client, "systemctl restart nrs || true")
    
    client.close()
    print("Deployment finished successfully!")

if __name__ == "__main__":
    main()
