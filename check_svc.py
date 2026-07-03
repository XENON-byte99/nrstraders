import paramiko, sys
sys.stdout.reconfigure(encoding='utf-8')
c=paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('194.238.19.210', username='root', password='qscfkz0B&VY?rf7D')
_, out, _ = c.exec_command('systemctl status nrs-software --no-pager')
print(out.read().decode(errors='replace'))
