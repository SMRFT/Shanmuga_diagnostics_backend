import urllib.request, json
with urllib.request.urlopen('http://127.0.0.1:1071/_b_a_c_k_e_n_d/LIS/sample-collector/') as res:
    data = json.loads(res.read().decode())
    print(json.dumps(data[:3] if isinstance(data, list) else data, indent=2))
