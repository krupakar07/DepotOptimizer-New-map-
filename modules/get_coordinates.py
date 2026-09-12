import pandas as pd
import pgeocode


# ==========================================
# FILES
# ==========================================

INPUT_FILE = "data/zone_mapping.csv"
OUTPUT_FILE = "data/zip_coordinates.csv"


# ==========================================
# LOAD YOUR ZIP CODES
# ==========================================

mapping = pd.read_csv(
    INPUT_FILE,
    dtype=str
)

mapping.columns = (
    mapping.columns
    .astype(str)
    .str.strip()
)


# ==========================================
# GET UNIQUE ZIP CODES
# ==========================================

zipcodes = (
    mapping["Postal Code"]
    .astype(str)
    .str.strip()
    .drop_duplicates()
    .tolist()
)


print(f"Found {len(zipcodes)} unique ZIP codes.")


# ==========================================
# GEOCODE GERMAN ZIP CODES
# ==========================================

geocoder = pgeocode.Nominatim("de")

coordinates = geocoder.query_postal_code(
    zipcodes
)


# ==========================================
# CREATE OUTPUT
# ==========================================

result = pd.DataFrame({
    "Receiver Zipcode": zipcodes,
    "Latitude": coordinates["latitude"],
    "Longitude": coordinates["longitude"],
    "Accuracy": coordinates["accuracy"]
})


# ==========================================
# ADD ZONE INFORMATION
# ==========================================

zone_mapping = mapping[
    ["Route Fence Name", "Postal Code"]
].copy()

zone_mapping = zone_mapping.rename(
    columns={
        "Route Fence Name": "Zone",
        "Postal Code": "Receiver Zipcode"
    }
)

zone_mapping["Receiver Zipcode"] = (
    zone_mapping["Receiver Zipcode"]
    .astype(str)
    .str.strip()
)


result = result.merge(
    zone_mapping,
    on="Receiver Zipcode",
    how="left"
)


# ==========================================
# REORDER COLUMNS
# ==========================================

result = result[
    [
        "Zone",
        "Receiver Zipcode",
        "Latitude",
        "Longitude",
        "Accuracy"
    ]
]


# ==========================================
# SAVE FILE
# ==========================================

result.to_csv(
    OUTPUT_FILE,
    index=False
)


# ==========================================
# SHOW RESULTS
# ==========================================

print()
print("Coordinate file created successfully!")
print()
print(result.to_string(index=False))
print()
print(f"Saved to: {OUTPUT_FILE}")
