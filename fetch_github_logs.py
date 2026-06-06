import urllib.request
import json
import zipfile
import io

repo = "XENON-byte99/nrstraders"
runs_url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=1"

try:
    with urllib.request.urlopen(runs_url) as response:
        data = json.loads(response.read().decode())
    
    if not data.get("workflow_runs"):
        print("No workflow runs found.")
        exit()
        
    run = data["workflow_runs"][0]
    print(f"Run ID: {run['id']}, Status: {run['status']}, Conclusion: {run['conclusion']}")
    
    jobs_url = run["jobs_url"]
    with urllib.request.urlopen(jobs_url) as response:
        jobs_data = json.loads(response.read().decode())
        
    for job in jobs_data.get("jobs", []):
        if job["conclusion"] == "failure":
            print(f"Job failed: {job['name']}")
            for step in job["steps"]:
                if step["conclusion"] == "failure":
                    print(f"  Step failed: {step['name']}")
                    
            # Try to get logs if possible (requires auth for zips usually, but we can print the step)
            logs_url = f"https://api.github.com/repos/{repo}/actions/jobs/{job['id']}/logs"
            print(f"You can view logs at: {run['html_url']}")
            
except Exception as e:
    print(f"Error fetching logs: {e}")
