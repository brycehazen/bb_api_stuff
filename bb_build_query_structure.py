#!/usr/bin/env python
# bb_build_query_structure.py

import json
import os
import time
from datetime import datetime
import re
from bb_auth import BlackbaudAuth

# API Endpoints
AVAILABLE_FIELDS_ENDPOINT = "/query/querytypes/{query_type_id}/availablefields"
NODES_FIELDS_ENDPOINT = "/query/querytypes/{query_type_id}/nodes/{node_id}/availablefields"

OUTPUT_FILE = "bb_query_structure.json"
LOG_FILE = "bb_query_log.txt"

def load_existing_data():
    """Load existing query structure to avoid redundant API calls."""
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r") as file:
                return json.load(file)
        except json.JSONDecodeError:
            return {}
    return {}

def save_data(data):
    """Save the query structure to a file."""
    with open(OUTPUT_FILE, "w") as file:
        json.dump(data, file, indent=4)

def log_response(endpoint, response):
    """Logs API responses to a file."""
    with open(LOG_FILE, "a") as log:
        log.write(f"\n[{datetime.utcnow().isoformat()}] Endpoint: {endpoint}\n")
        log.write(json.dumps(response, indent=4))
        log.write("\n" + "=" * 80 + "\n")

def get_query_type_ids():
    """Extract query_type_ids from the log file."""
    query_type_ids = {}

    if not os.path.exists(LOG_FILE):
        return query_type_ids

    with open(LOG_FILE, "r") as log:
        content = log.read()

    # Extracting query_type_ids and names
    matches = re.findall(r'\[ID:(\d+), Name: (.+?)\]', content)
    for qt_id, qt_name in matches:
        query_type_ids[qt_id] = qt_name

    return query_type_ids

def get_available_fields(auth, query_type_id):
    """Retrieve available fields and nodes for a given query type."""
    endpoint = AVAILABLE_FIELDS_ENDPOINT.format(query_type_id=query_type_id)
    params = {"product": "RE", "module": "None"}
    response = auth.make_request("GET", endpoint, params=params)

    if response:
        log_response(endpoint, response)
        return response.get("nodes", []), response.get("fields", [])
    
    return [], []

def get_fields_for_node(auth, query_type_id, node_id):
    """Retrieve available fields for a given node within a query type."""
    endpoint = NODES_FIELDS_ENDPOINT.format(query_type_id=query_type_id, node_id=node_id)
    params = {"product": "RE", "module": "None"}
    response = auth.make_request("GET", endpoint, params=params)

    if response:
        log_response(endpoint, response)
        return response.get("nodes", []), response.get("fields", [])
    
    return [], []

def build_query_structure():
    """Retrieve all fields and nodes for each query_type_id and store them in a structured format."""
    auth = BlackbaudAuth()
    existing_data = load_existing_data()
    
    # type IDs from log file
    query_type_ids = get_query_type_ids()
    if not query_type_ids:
        print("No query_type_ids found in log file.")
        return

    query_structure = existing_data 

    #  Iterate through each query type
    for qt_id, qt_name in query_type_ids.items():
        if qt_id in query_structure:
            print(f"Skipping Query Type ID {qt_id} - already processed.")
            continue

        print(f"Processing Query Type: {qt_name} (ID: {qt_id})...")
        nodes, fields = get_available_fields(auth, qt_id)

        query_structure[qt_id] = {
            "name": qt_name,
            "nodes": {},
            "fields": [{
                "id": f["id"],
                "name": f["available_field_name"],
                "selected_name": f.get("selected_field_name", ""),
                "value_type": f["value_type"],
                "allowed_filter_operators": f.get("allowed_filter_operators", [])
            } for f in fields]
        }

        # Step 3: Process each node
        for node in nodes:
            node_id = str(node["id"])
            print(f"  Processing Node: {node['name']} (ID: {node_id})...")

            child_nodes, node_fields = get_fields_for_node(auth, qt_id, node_id)

            query_structure[qt_id]["nodes"][node_id] = {
                "name": node["name"],
                "fields": [{
                    "id": f["id"],
                    "name": f["available_field_name"],
                    "selected_name": f.get("selected_field_name", ""),
                    "value_type": f["value_type"],
                    "allowed_filter_operators": f.get("allowed_filter_operators", [])
                } for f in node_fields],
                "child_nodes": [{
                    "id": cn["id"],
                    "name": cn["name"]
                } for cn in child_nodes]
            }

    # Save results
    save_data(query_structure)
    print(f"\nQuery Structure saved to {OUTPUT_FILE}")

def display_query_structure():
    """Load and display the stored query structure."""
    query_structure = load_existing_data()
    if not query_structure:
        print("No data found. Run `bb_build_query_structure.py` first.")
        return

    print("\nQuery Structure:\n" + "=" * 60)
    for qt_id, data in query_structure.items():
        print(f"\n[Query Type: {data['name']} (ID: {qt_id})]")
        print("-" * 60)

        print("\nFields:")
        for field in data["fields"]:
            print(f"  [{field['id']}] {field['name']} (Type: {field['value_type']})")

        print("\nNodes:")
        for node_id, node_data in data["nodes"].items():
            print(f"  [{node_id}] {node_data['name']}")
            for field in node_data["fields"]:
                print(f"    [{field['id']}] {field['name']} (Type: {field['value_type']})")

            if node_data["child_nodes"]:
                print("    Child Nodes:")
                for cn in node_data["child_nodes"]:
                    print(f"      [{cn['id']}] {cn['name']}")

    print("=" * 60)

if __name__ == "__main__":
    build_query_structure()
    display_query_structure()
