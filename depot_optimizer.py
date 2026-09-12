import pandas as pd
from ortools.sat.python import cp_model


# ============================================================
# SETTINGS
# ============================================================

PREFERRED_PARCELS = 50
MAX_ZONES_PER_DRIVER = 2


# ============================================================
# ZONE NEIGHBORS
# ============================================================
#
# These are only used INSIDE a company.
#
# ACAR = Zones 1–4
# LEGNO = Zones 5–8
#
# app.py already separates the companies before this
# optimizer is called.
#
# ============================================================

ZONE_NEIGHBORS = {
    1: {2, 3},
    2: {1, 3},
    3: {1, 2, 4},
    4: {3},

    5: {6, 7, 8},
    6: {5, 7},
    7: {5, 6, 8},
    8: {5, 7},
}


# ============================================================
# LOAD ZIP COORDINATES
# ============================================================

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

    required_columns = [
        "Receiver Zipcode",
        "Latitude",
        "Longitude",
        "Company"
    ]

    for column in required_columns:

        if column not in coordinates.columns:

            raise ValueError(
                f"Missing column in zip_coordinates.csv: "
                f"{column}"
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

    coordinates["Company"] = (
        coordinates["Company"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    return coordinates


# ============================================================
# PREPARE ZIP DATA
# ============================================================

def prepare_zip_data(df):

    coordinates = load_zip_coordinates()

    # Count parcels for every zone + ZIP
    zip_data = (
        df.groupby(
            [
                "zone",
                "Receiver Zipcode"
            ])
        .size()
        .reset_index(
            name="Parcels"
        )
    )

    zip_data["Receiver Zipcode"] = (
        zip_data["Receiver Zipcode"]
        .astype(str)
        .str.strip()
    )

    # Attach coordinates
    zip_data = zip_data.merge(
        coordinates[
            [
                "Receiver Zipcode",
                "Latitude",
                "Longitude",
                "Company"
            ]
        ],
        on="Receiver Zipcode",
        how="left"
    )

    return zip_data


# ============================================================
# MAIN OPTIMIZER
# ============================================================

def optimize_depot(
    df,
    available_drivers,
    max_zones_per_driver=MAX_ZONES_PER_DRIVER,
    max_time_seconds=30
):

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if available_drivers < 1:

        return pd.DataFrame(), {
            "status": "No drivers available."
        }


    if df.empty:

        return pd.DataFrame(), {
            "status": "No parcels available."
        }


    # --------------------------------------------------------
    # PREPARE DATA
    # --------------------------------------------------------

    zip_data = prepare_zip_data(df)

    if zip_data.empty:

        return pd.DataFrame(), {
            "status": "No ZIP-code data found."
        }


    number_of_zips = len(zip_data)

    number_of_drivers = min(
        int(available_drivers),
        number_of_zips
    )


    zones = sorted(
        int(zone)
        for zone in
        zip_data["zone"]
        .dropna()
        .unique()
    )


    total_parcels = int(
        zip_data["Parcels"].sum()
    )


    average_parcels = (
        total_parcels
        /
        number_of_drivers
    )


    # ========================================================
    # CREATE MODEL
    # ========================================================

    model = cp_model.CpModel()


    # ========================================================
    # ZIP → DRIVER VARIABLES
    # ========================================================

    assignment = {}

    for z in range(number_of_zips):

        assignment[z] = {}

        for d in range(number_of_drivers):

            assignment[z][d] = (
                model.NewBoolVar(
                    f"zip_{z}_driver_{d}"
                )
            )


    # ========================================================
    # DRIVER → ZONE VARIABLES
    # ========================================================

    zone_used = {}

    for d in range(number_of_drivers):

        zone_used[d] = {}

        for zone in zones:

            zone_used[d][zone] = (
                model.NewBoolVar(
                    f"driver_{d}_zone_{zone}"
                )
            )


    # ========================================================
    # EVERY ZIP MUST HAVE EXACTLY ONE DRIVER
    # ========================================================

    for z in range(number_of_zips):

        model.Add(
            sum(
                assignment[z][d]
                for d in range(number_of_drivers)
            )
            == 1
        )


    # ========================================================
    # CONNECT ZIP ASSIGNMENT TO ZONE ASSIGNMENT
    # ========================================================

    for d in range(number_of_drivers):

        for zone in zones:

            zone_indexes = [
                z
                for z in range(number_of_zips)
                if int(
                    zip_data.iloc[z]["zone"]
                ) == zone
            ]

            if not zone_indexes:

                model.Add(
                    zone_used[d][zone] == 0
                )

                continue


            # If driver gets a ZIP from this zone,
            # the zone is marked as used.

            for z in zone_indexes:

                model.Add(
                    assignment[z][d]
                    <=
                    zone_used[d][zone]
                )


            # If the zone is marked as used,
            # at least one ZIP must be assigned.

            model.Add(
                sum(
                    assignment[z][d]
                    for z in zone_indexes
                )
                >=
                zone_used[d][zone]
            )


    # ========================================================
    # MAXIMUM 2 ZONES PER DRIVER
    # ========================================================

    for d in range(number_of_drivers):

        model.Add(
            sum(
                zone_used[d][zone]
                for zone in zones
            )
            <=
            max_zones_per_driver
        )


    # ========================================================
    # ONLY NEIGHBORING ZONES
    # ========================================================
    #
    # A driver cannot receive two unrelated zones.
    #
    # Example:
    #
    # Zone 3 + Zone 4  → allowed
    # Zone 5 + Zone 6  → allowed
    #
    # Zone 3 + Zone 7  → forbidden
    #
    # ========================================================

    for d in range(number_of_drivers):

        for zone_a in zones:

            for zone_b in zones:

                if zone_a >= zone_b:
                    continue


                neighbors = ZONE_NEIGHBORS.get(
                    zone_a,
                    set()
                )


                if zone_b not in neighbors:

                    model.Add(
                        zone_used[d][zone_a]
                        +
                        zone_used[d][zone_b]
                        <=
                        1
                    )


    # ========================================================
    # EVERY DRIVER SHOULD RECEIVE PARCELS
    # ========================================================

    for d in range(number_of_drivers):

        model.Add(
            sum(
                assignment[z][d]
                for z in range(number_of_zips)
            )
            >= 1
        )


    # ========================================================
    # DRIVER PARCEL LOAD
    # ========================================================

    driver_load = []

    for d in range(number_of_drivers):

        load = model.NewIntVar(
            0,
            total_parcels,
            f"driver_load_{d}"
        )

        model.Add(
            load
            ==
            sum(
                int(
                    zip_data.iloc[z]["Parcels"]
                )
                *
                assignment[z][d]

                for z in range(number_of_zips)
            )
        )

        driver_load.append(
            load
        )


    # ========================================================
    # MAXIMUM DRIVER LOAD
    # ========================================================

    maximum_load = model.NewIntVar(
        0,
        total_parcels,
        "maximum_driver_load"
    )


    # ========================================================
    # MINIMUM DRIVER LOAD
    # ========================================================

    minimum_load = model.NewIntVar(
        0,
        total_parcels,
        "minimum_driver_load"
    )


    for d in range(number_of_drivers):

        model.Add(
            driver_load[d]
            <=
            maximum_load
        )

        model.Add(
            driver_load[d]
            >=
            minimum_load
        )


    # ========================================================
    # LOAD RANGE
    # ========================================================
    #
    # Example:
    #
    # 145 / 96
    #
    # range = 49
    #
    # versus:
    #
    # 121 / 120
    #
    # range = 1
    #
    # The optimizer strongly prefers the second option.
    #
    # ========================================================

    load_range = model.NewIntVar(
        0,
        total_parcels,
        "driver_load_range"
    )

    model.Add(
        load_range
        ==
        maximum_load
        -
        minimum_load
    )


    # ========================================================
    # AVERAGE DEVIATION
    # ========================================================

    average_integer = int(
        round(
            average_parcels
        )
    )

    average_deviation = []

    for d in range(number_of_drivers):

        deviation = model.NewIntVar(
            0,
            total_parcels,
            f"average_deviation_{d}"
        )

        model.AddAbsEquality(
            deviation,
            driver_load[d]
            -
            average_integer
        )

        average_deviation.append(
            deviation
        )


    # ========================================================
    # SECOND-ZONE PENALTY
    # ========================================================
    #
    # We prefer one zone per driver when possible.
    #
    # But we DO allow a second neighboring zone when it
    # is needed to balance the workload.
    #
    # ========================================================

    second_zone_penalty = []

    for d in range(number_of_drivers):

        second_zone_penalty.append(
            sum(
                zone_used[d][zone]
                for zone in zones
            )
        )


    # ========================================================
    # OBJECTIVE
    # ========================================================
    #
    # PRIORITY 1:
    # Balance driver workloads.
    #
    # PRIORITY 2:
    # Keep everyone close to the daily average.
    #
    # PRIORITY 3:
    # Avoid unnecessary second-zone assignments.
    #
    # ========================================================

    model.Minimize(

        load_range * 10000

        +

        sum(
            average_deviation
        ) * 100

        +

        sum(
            second_zone_penalty
        ) * 2
    )


    # ========================================================
    # SOLVE
    # ========================================================

    solver = cp_model.CpSolver()

    solver.parameters.max_time_in_seconds = (
        max_time_seconds
    )

    solver.parameters.num_search_workers = 8

    status = solver.Solve(
        model
    )


    # ========================================================
    # CHECK SOLUTION
    # ========================================================

    if status not in (
        cp_model.OPTIMAL,
        cp_model.FEASIBLE
    ):

        return pd.DataFrame(), {
            "status":
                "No feasible driver plan found."
        }


    # ========================================================
    # BUILD RESULT
    # ========================================================

    results = []


    for d in range(number_of_drivers):

        driver_number = d + 1

        driver_zones = []

        driver_zipcodes = []

        parcels = 0


        for z in range(number_of_zips):

            assigned = solver.Value(
                assignment[z][d]
            )


            if assigned != 1:
                continue


            row = zip_data.iloc[z]


            zone = int(
                row["zone"]
            )

            zipcode = str(
                row["Receiver Zipcode"]
            )

            parcel_count = int(
                row["Parcels"]
            )


            parcels += parcel_count

            driver_zipcodes.append(
                zipcode
            )


            if zone not in driver_zones:

                driver_zones.append(
                    zone
                )


        results.append({

            "Driver":
                driver_number,

            "Zones":
                ", ".join(
                    str(zone)
                    for zone in sorted(
                        driver_zones
                    )
                ),

            "ZIP Codes":
                ", ".join(
                    driver_zipcodes
                ),

            "Parcels":
                parcels,

            "Difference From Average":
                round(
                    parcels
                    -
                    average_parcels,
                    1
                ),

            "Difference From 50":
                parcels
                -
                PREFERRED_PARCELS
        })


    # ========================================================
    # RESULT DATAFRAME
    # ========================================================

    result_df = pd.DataFrame(
        results
    )


    # ========================================================
    # INFORMATION FOR APP
    # ========================================================

    information = {

        "status":
            "Optimization completed.",

        "total_parcels":
            total_parcels,

        "drivers":
            number_of_drivers,

        "average_parcels":
            round(
                average_parcels,
                1
            ),

        "preferred_parcels":
            PREFERRED_PARCELS
    }


    return result_df, information