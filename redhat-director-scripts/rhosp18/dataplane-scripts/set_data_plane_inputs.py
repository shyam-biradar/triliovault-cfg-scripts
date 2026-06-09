#!/usr/bin/python3
import yaml

YAML_FILE = "../ctlplane-scripts/tvo-operator-inputs.yaml"
CM_FILE = "cm-trilio-datamover.yaml"

# Load YAML file
with open(YAML_FILE, encoding='utf-8') as f:
    data = yaml.safe_load(f)

# Extract data
rabbit_host = data["spec"]["rabbitmq"]["common"].get("host", "")
rabbit_ssl = data["spec"]["rabbitmq"]["common"].get("ssl", True)
rabbit_quorum_queue = data["spec"]["rabbitmq"]["cluster"].get("rabbit_quorum_queue", False)
database_host = data["spec"]["database"]["common"].get("host", "")
database_port = data["spec"]["database"]["common"].get("port", "3306")
keystone_auth_url = data["spec"]["keystone"]["common"].get("auth_url", "")
keystone_ssl_verify = data["spec"]["keystone"]["common"].get("ssl_verify", True)
dms_image = data["spec"]["images"].get("triliovault_dms", "")
wlm_image = data["spec"]["images"].get("triliovault_wlm", "")

# Read and update lines
updated_lines = []
with open(CM_FILE, "r", encoding='utf-8') as f:
    for line in f:
        if "rabbit_host:" in line:
            updated_lines.append('    rabbit_host: "{}"\n'.format(rabbit_host))
        elif "rabbit_ssl:" in line:
            updated_lines.append('    rabbit_ssl: {}\n'.format(str(rabbit_ssl).lower()))
        elif "rabbit_quorum_queue:" in line:
            updated_lines.append('    rabbit_quorum_queue: {}\n'.format(str(rabbit_quorum_queue).lower()))
        elif "database_host:" in line:
            updated_lines.append('    database_host: "{}"\n'.format(database_host))
        elif "database_port:" in line:
            updated_lines.append('    database_port: "{}"\n'.format(database_port))
        elif "keystone_auth_url:" in line:
            updated_lines.append('    keystone_auth_url: "{}"\n'.format(keystone_auth_url))
        elif "keystone_ssl_verify:" in line:
            updated_lines.append('    keystone_ssl_verify: {}\n'.format(str(keystone_ssl_verify).lower()))
        elif "triliovault_dms_image:" in line:
            updated_lines.append('    triliovault_dms_image: "{}"\n'.format(dms_image))
        elif "triliovault_wlm_image:" in line:
            updated_lines.append('    triliovault_wlm_image: "{}"\n'.format(wlm_image))
        else:
            updated_lines.append(line)

# Write updated content back
with open(CM_FILE, "w", encoding='utf-8') as f:
    f.writelines(updated_lines)

print("Updated rabbit/database/keystone config, image URLs, and backup targets in cm-trilio-datamover.yaml")
