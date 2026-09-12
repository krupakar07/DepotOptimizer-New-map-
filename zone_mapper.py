import pandas as pd


def load_zone_mapping():

    mapping = pd.read_csv(
        "data/zip_coordinates.csv",
        dtype={
            "Zone": str,
            "Receiver Zipcode": str,
            "Company": str
        }
    )

    mapping.columns = (
        mapping.columns
        .astype(str)
        .str.strip()
    )

    mapping["Receiver Zipcode"] = (
        mapping["Receiver Zipcode"]
        .astype(str)
        .str.strip()
    )

    mapping["Zone"] = (
        mapping["Zone"]
        .astype(str)
        .str.strip()
    )

    mapping["Company"] = (
        mapping["Company"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    return mapping


def assign_zones(df):

    mapping = load_zone_mapping()

    # Make sure parcel ZIP is treated as text
    df["Receiver Zipcode"] = (
        df["Receiver Zipcode"]
        .astype(str)
        .str.strip()
    )

    # Remove old zone/company columns if they already exist
    if "zone" in df.columns:
        df = df.drop(columns=["zone"])

    if "Company" in df.columns:
        df = df.drop(columns=["Company"])

    # Merge zone + company into today's parcel data
    df = df.merge(
        mapping[
            [
                "Receiver Zipcode",
                "Zone",
                "Company"
            ]
        ],
        on="Receiver Zipcode",
        how="left"
    )

    # Rename Zone to the name used by the optimizer
    df = df.rename(
        columns={
            "Zone": "zone"
        }
    )

    return df