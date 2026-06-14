import os
import urllib.request
import json

os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

req = urllib.request.Request("http://127.0.0.1:8001/save_for_later", 
    data=json.dumps({"text": "测试文本", "source": "web", "generate_podcast": False}).encode(),
    headers={"Content-Type": "application/json"})

try:
    with urllib.request.urlopen(req) as resp:
        print("Status:", resp.status)
        print("Response:", resp.read().decode())
except Exception as e:
    print("Error:", e)
    if hasattr(e, 'read'):
        print(e.read().decode())
