import json


def load_driver_rules():
    with open("data/driver_rules.json", "r") as file:
        return json.load(file)


def calculate_drivers(parcel_count):

    rules = load_driver_rules()["rules"]

    for rule in rules:

        if rule["min"] <= parcel_count <= rule["max"]:
            return rule["drivers"]

    return 5

print(calculate_drivers(30))
print(calculate_drivers(70))
print(calculate_drivers(90))
print(calculate_drivers(120))
print(calculate_drivers(150))
print(calculate_drivers(200))
