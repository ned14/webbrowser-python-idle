#!/usr/bin/env python3
"""Render the baked page config (/webvm-config.js) from argv.

Single source of truth for the URL/param derivation that the page consumes
when opened without a URL hash. scripts/print-url.sh derives the SAME params
for its explicit-hash URLs; tests/unit/test_scripts.py cross-checks the two
renderings so they cannot drift apart.

Args (in order): CONTROL_HOST CONTROL_PORT HEADSCALE_PREAUTHKEY
STORAGE_BACKEND GATEWAY_TAILNET_IP WEBDAV_PORT WEBDAV_USER WEBDAV_PASS.
"""
import json
import sys

control_host, control_port, auth_key, backend = sys.argv[1:5]
gateway_ip, webdav_port, webdav_user, webdav_pass = sys.argv[5:9]

config = {
    "controlUrl": f"https://{control_host}:{control_port}",
    "authKey": auth_key,
}
if backend == "webdav":
    config["syncUrl"] = f"http://{gateway_ip}:{webdav_port}/webdav/"
    config["syncUser"] = webdav_user
    config["syncPass"] = webdav_pass

print("window.__webvmConfig = ", end="")
json.dump(config, sys.stdout, ensure_ascii=True)
print(";")
