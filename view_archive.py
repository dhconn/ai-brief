import json

def view_archive():
    with open('seen_articles.json', 'r') as f:
        data = json.load(f)
    
    print(f"{'DATE':<20} | {'URL'}")
    print("-" * 60)
    # Sorts by timestamp so the newest is always at the top
    for item in sorted(data, key=lambda x: x['seen_at'], reverse=True):
        print(f"{item['seen_at'][:16]:<20} | {item['url']}")

if __name__ == "__main__":
    view_archive()
