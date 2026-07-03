import paramiko
c=paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('194.238.19.210', username='root', password='qscfkz0B&VY?rf7D')
_, out, _ = c.exec_command('python3 -c "import sqlite3; print(sqlite3.connect(\'/var/www/nrs-software/db.sqlite3\').execute(\'SELECT COUNT(*) FROM documents_transaction;\').fetchone()[0])"')
print("Remote DB count:", out.read().decode())
