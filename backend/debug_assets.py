import os
import json
from dotenv import load_dotenv
from databricks.sdk import WorkspaceClient

load_dotenv()
w = WorkspaceClient()

print("--- DASHBOARDS ---")
dashboards = w.lakeview.list()
for dash in dashboards:
    print(f"Dash: {dash.display_name} ({dash.dashboard_id})")
    try:
        pub = w.api_client.do('GET', f'/api/2.0/lakeview/dashboards/{dash.dashboard_id}/published')
        print(f"  Published embed: {pub.get('embed_credentials')}")
    except Exception as e:
        print(f"  Published err: {e}")
    try:
        perms = w.api_client.do('GET', f'/api/2.0/permissions/dashboards/{dash.dashboard_id}')
        acs = perms.get('access_control_list', [])
        groups = [ac.get('group_name') for ac in acs if 'group_name' in ac]
        print(f"  Groups: {groups}")
    except Exception as e:
        print(f"  Perms err: {e}")

print("\n--- JOBS ---")
jobs = w.jobs.list()
for job in jobs:
    print(f"Job: {job.settings.name if hasattr(job, 'settings') else 'unknown'} ({job.job_id})")
    tags = {}
    if hasattr(job, 'settings') and hasattr(job.settings, 'tags'):
        for t in job.settings.tags or []:
            tags[getattr(t, 'key', '')] = getattr(t, 'value', '')
    print(f"  Tags: {tags}")
    
