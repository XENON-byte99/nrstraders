import urllib.request
import json

repo = "XENON-byte99/nrstraders"
run_id = 27072115484

jobs_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
with urllib.request.urlopen(jobs_url) as response:
    raw = response.read().decode()

jobs_data = json.loads(raw)
for job in jobs_data.get("jobs", []):
    print(f"Job: {job['name']} | Conclusion: {job['conclusion']}")
    for step in job.get("steps", []):
        marker = "FAIL" if step.get("conclusion") == "failure" else "OK" if step.get("conclusion") == "success" else "SKIP"
        print(f"  [{marker}] Step {step['number']}: {step['name']}")
