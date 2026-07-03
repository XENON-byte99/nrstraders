import paramiko
import os

host = "194.238.19.210"
user = "root"
pwd = "qscfkz0B&VY?rf7D"
zip_path = "deploy.zip"

print("Connecting to VPS...")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, password=pwd)

print("Uploading deploy.zip...")
sftp = ssh.open_sftp()
sftp.put(zip_path, "/tmp/deploy.zip")
sftp.close()

commands = [
    "unzip -o /tmp/deploy.zip -d /var/www/nrs-software",
    "mv /var/www/nrs-software/db_deploy.sqlite3 /var/www/nrs-software/db.sqlite3",
    "chown -R www-data:www-data /var/www/nrs-software",
    "chmod 666 /var/www/nrs-software/db.sqlite3",
    "cd /var/www/nrs-software && venv/bin/pip install -r requirements.txt",
    "cd /var/www/nrs-software && venv/bin/python manage.py collectstatic --no-input",
    "cd /var/www/nrs-software && venv/bin/python manage.py migrate",
    "systemctl restart nrs-software"
]

for cmd in commands:
    print(f"Executing: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    
    # Wait for the command to finish
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    
    if out:
        print(f"Output: {out}")
    if err:
        print(f"Error: {err}")
    if exit_status != 0:
        print(f"Command failed with exit status {exit_status}")
        break

ssh.close()
print("Deployment Complete!")
