import json
import os
from datetime import datetime

old_file = "seen_articles.json"
# Assume the old file was a simple list of strings: ["url1", "url2"]

if os.path.exists(old_file):
    with open(old_file, 'r') as f:
        old_data = json.load(f)
    
    # Convert old strings to new objects with a generic "migrated" timestamp
    timestamp = datetime.utcnow().isoformat()
    new_data = [{"url": url, "seen_at": timestamp} for url in old_data]
    
    with open(old_file, 'w') as f:
        json.dump(new_data, f, indent=2)
    print("Migration complete! Your file is now in the new chronological format.")
else:
    print("No old file found to migrate.")
