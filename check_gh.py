import urllib.request
import json

url = 'https://api.github.com/repos/XENON-byte99/nrstraders/actions/runs/28680621025/jobs'
req = urllib.request.Request(url)
r = urllib.request.urlopen(req)
data = json.loads(r.read())
job = data['jobs'][0]
print(f"Job Status: {job['status']}, Conclusion: {job['conclusion']}")
for s in job['steps']:
    print(f"Step: {s['name']} - {s['conclusion']}")
