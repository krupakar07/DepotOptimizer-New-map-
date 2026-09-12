import pandas as pd
import math


def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate approximate distance between two coordinates in km.
    """

    earth_radius = 6371.0

    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)

    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return earth_radius * c


def load_zip_coordinates():

    coordinates = pd.read_csv(
        "data/zip_coordinates.csv",
        dtype=str
    )

    coordinates.columns = (
        coordinates.columns
        .astype(str)
        .str.strip()
    )

    coordinates["Receiver Zipcode"] = (
        coordinates["Receiver Zipcode"]
        .astype(str)
        .str.strip()
    )

    coordinates["Latitude"] = pd.to_numeric(
        coordinates["Latitude"],
        errors="coerce"
    )

    coordinates["Longitude"] = pd.to_numeric(
        coordinates["Longitude"],
        errors="coerce"
    )

    return coordinates


def allocate_zipcodes_to_drivers(
    zone_data,
    number_of_drivers
):
    """
    Geographic ZIP-code allocation.

    Rules:
    - A ZIP code cannot be split.
    - Every ZIP must be assigned.
    - Try to balance parcel quantities.
    - Prefer geographically close ZIP codes.
    """

    if zone_data.empty:

        return pd.DataFrame(
            columns=[
                "Driver",
                "ZIP Codes",
                "Parcels"
            ]
        )


    # --------------------------------------
    # Parcel count per ZIP
    # --------------------------------------

    zip_counts = (
        zone_data
        .groupby("Receiver Zipcode")
        .size()
        .reset_index(name="Parcels")
    )

    zip_counts["Receiver Zipcode"] = (
        zip_counts["Receiver Zipcode"]
        .astype(str)
        .str.strip()
    )


    # --------------------------------------
    # Load coordinates
    # --------------------------------------

    coordinates = load_zip_coordinates()


    zip_counts = zip_counts.merge(
        coordinates[
            [
                "Receiver Zipcode",
                "Latitude",
                "Longitude"
            ]
        ],
        on="Receiver Zipcode",
        how="left"
    )


    # --------------------------------------
    # Remove ZIPs without coordinates
    # from geographic calculations
    # --------------------------------------

    zip_counts["Latitude"] = pd.to_numeric(
        zip_counts["Latitude"],
        errors="coerce"
    )

    zip_counts["Longitude"] = pd.to_numeric(
        zip_counts["Longitude"],
        errors="coerce"
    )


    # --------------------------------------
    # Sort largest ZIPs first
    # --------------------------------------

    zip_counts = zip_counts.sort_values(
        "Parcels",
        ascending=False
    ).reset_index(drop=True)


    # --------------------------------------
    # Create drivers
    # --------------------------------------

    drivers = []

    for driver_number in range(
        1,
        number_of_drivers + 1
    ):

        drivers.append({
            "Driver": driver_number,
            "ZIP Codes": [],
            "Parcels": 0,
            "Coordinates": []
        })


    # --------------------------------------
    # Give the first ZIP to each driver
    #
    # This creates geographic "seeds".
    # --------------------------------------

    seed_count = min(
        number_of_drivers,
        len(zip_counts)
    )


    for index in range(seed_count):

        row = zip_counts.iloc[index]

        driver = drivers[index]

        zipcode = str(
            row["Receiver Zipcode"]
        )

        parcels = int(
            row["Parcels"]
        )

        driver["ZIP Codes"].append(
            zipcode
        )

        driver["Parcels"] += parcels

        if (
            pd.notna(row["Latitude"])
            and pd.notna(row["Longitude"])
        ):

            driver["Coordinates"].append(
                (
                    float(row["Latitude"]),
                    float(row["Longitude"])
                )
            )


    # --------------------------------------
    # Assign remaining ZIPs
    # --------------------------------------

    for index in range(
        seed_count,
        len(zip_counts)
    ):

        row = zip_counts.iloc[index]

        zipcode = str(
            row["Receiver Zipcode"]
        )

        parcels = int(
            row["Parcels"]
        )

        latitude = row["Latitude"]
        longitude = row["Longitude"]


        best_driver = None
        best_score = float("inf")


        for driver in drivers:

            # --------------------------------
            # Parcel balancing component
            # --------------------------------

            current_load = driver["Parcels"]

            load_score = current_load


            # --------------------------------
            # Geographic component
            # --------------------------------

            geographic_score = 0


            if (
                pd.notna(latitude)
                and pd.notna(longitude)
                and driver["Coordinates"]
            ):

                distances = []

                for (
                    driver_lat,
                    driver_lon
                ) in driver["Coordinates"]:

                    distance = haversine_distance(
                        float(latitude),
                        float(longitude),
                        driver_lat,
                        driver_lon
                    )

                    distances.append(
                        distance
                    )


                # Use nearest point belonging
                # to this driver's territory.
                geographic_score = min(
                    distances
                )


            # --------------------------------
            # Combined score
            # --------------------------------
            #
            # Parcel balance is important.
            # Geography is also important.
            #
            # The multiplier can be adjusted later.
            # --------------------------------

            score = (
                load_score
                + geographic_score * 2
            )


            if score < best_score:

                best_score = score
                best_driver = driver


        # --------------------------------
        # Assign ZIP
        # --------------------------------

        best_driver["ZIP Codes"].append(
            zipcode
        )

        best_driver["Parcels"] += parcels


        if (
            pd.notna(latitude)
            and pd.notna(longitude)
        ):

            best_driver["Coordinates"].append(
                (
                    float(latitude),
                    float(longitude)
                )
            )


    # --------------------------------------
    # Create final dataframe
    # --------------------------------------

    result = []

    for driver in drivers:

        result.append({
            "Driver": driver["Driver"],
            "ZIP Codes": ", ".join(
                driver["ZIP Codes"]
            ),
            "Parcels": driver["Parcels"]
        })


    return pd.DataFrame(result)