import pandas as pd
from ortools.sat.python import cp_model


# ============================================================
# SETTINGS
# ============================================================

ALL_ZONES = set(range(1, 10))

TARGET_PARCELS = 50


# ============================================================
# PREPARE ZIP DATA
# ============================================================

def prepare_data(df):

    required_columns = [
        "zone",
        "Receiver Zipcode",
    ]

    for column in required_columns:

        if column not in df.columns:

            raise ValueError(
                f"Missing required column: {column}"
            )

    data = df.copy()

    data["zone"] = pd.to_numeric(
        data["zone"],
        errors="coerce"
    )

    data["Receiver Zipcode"] = (
        data["Receiver Zipcode"]
        .astype(str)
        .str.strip()
    )

    data = data.dropna(
        subset=["zone"]
    )

    data["zone"] = data["zone"].astype(int)

    invalid_zones = sorted(
        set(data["zone"].unique())
        -
        ALL_ZONES
    )

    if invalid_zones:

        raise ValueError(
            "The parcel file contains unsupported "
            f"zone(s): {invalid_zones}. "
            "Allowed zones are 1–9."
        )

    zip_data = (
        data.groupby(
            [
                "zone",
                "Receiver Zipcode",
            ]
        )
        .size()
        .reset_index(
            name="Parcels"
        )
    )

    return zip_data


# ============================================================
# OPTIMIZE MANUALLY ASSIGNED ZONES
# ============================================================

def optimize_mannheim(
    df,
    driver_assignments,
):

    """
    driver_assignments example:

    {
        "ACAR 1": {
            "Team": "ACAR",
            "Zones": [1, 3]
        },

        "ACAR 2": {
            "Team": "ACAR",
            "Zones": [3]
        },

        "LEGNO 1": {
            "Team": "LEGNO",
            "Zones": [5, 6]
        }
    }

    The optimizer NEVER changes the manually assigned zones.

    It only distributes ZIP codes inside those zones.
    """

    # ========================================================
    # VALIDATE DRIVER ASSIGNMENTS
    # ========================================================

    if not driver_assignments:

        raise ValueError(
            "No drivers were configured."
        )

    # ========================================================
    # PREPARE DATA
    # ========================================================

    zip_data = prepare_data(
        df
    )

    # ========================================================
    # NORMALIZE ASSIGNMENTS
    # ========================================================

    normalized_assignments = {}

    for driver_name, settings in driver_assignments.items():

        team = str(
            settings["Team"]
        ).strip().upper()

        zones = set(
            int(zone)
            for zone in settings["Zones"]
        )

        # Remove anything outside 1–9
        zones = zones.intersection(
            ALL_ZONES
        )

        if not zones:

            raise ValueError(
                f"{driver_name} has no zones assigned."
            )

        normalized_assignments[
            driver_name
        ] = {
            "Team": team,
            "Zones": zones
        }

    # ========================================================
    # CHECK THAT EVERY PARCEL ZONE HAS A DRIVER
    # ========================================================

    parcel_zones = set(
        zip_data["zone"].unique()
    )

    assigned_zones = set()

    for settings in normalized_assignments.values():

        assigned_zones.update(
            settings["Zones"]
        )

    missing_zones = sorted(
        parcel_zones
        -
        assigned_zones
    )

    if missing_zones:

        raise ValueError(
            "These parcel zones have no driver assigned: "
            f"{missing_zones}"
        )

    # ========================================================
    # DRIVER LIST
    # ========================================================

    drivers = list(
        normalized_assignments.keys()
    )

    driver_count = len(
        drivers
    )

    # ========================================================
    # ELIGIBLE DRIVERS FOR EACH ZIP
    # ========================================================

    eligible_drivers = {}

    for zip_index, row in zip_data.iterrows():

        zone = int(
            row["zone"]
        )

        eligible = [

            driver

            for driver in drivers

            if zone
            in
            normalized_assignments[
                driver
            ]["Zones"]

        ]

        if not eligible:

            raise ValueError(
                f"Zone {zone}, ZIP "
                f"{row['Receiver Zipcode']} "
                "has no eligible driver."
            )

        eligible_drivers[
            zip_index
        ] = eligible

    # ========================================================
    # CREATE CP MODEL
    # ========================================================

    model = cp_model.CpModel()

    # ========================================================
    # ZIP -> DRIVER ASSIGNMENT
    # ========================================================

    assignment = {}

    for zip_index, eligible in eligible_drivers.items():

        assignment[zip_index] = {}

        for driver in eligible:

            safe_driver_name = (
                driver
                .replace(" ", "_")
                .replace("-", "_")
            )

            assignment[
                zip_index
            ][driver] = (
                model.NewBoolVar(
                    f"zip_{zip_index}_"
                    f"{safe_driver_name}"
                )
            )

    # ========================================================
    # EVERY ZIP MUST GO TO EXACTLY ONE DRIVER
    # ========================================================

    for zip_index, eligible in eligible_drivers.items():

        model.Add(
            sum(
                assignment[
                    zip_index
                ][driver]

                for driver in eligible
            )
            == 1
        )

    # ========================================================
    # DRIVER LOADS
    # ========================================================

    total_parcels = int(
        zip_data["Parcels"].sum()
    )

    loads = {}

    for driver in drivers:

        loads[driver] = model.NewIntVar(
            0,
            total_parcels,
            (
                "load_"
                +
                driver.replace(
                    " ",
                    "_"
                )
            )
        )

        load_terms = []

        for zip_index, eligible in eligible_drivers.items():

            if driver not in eligible:
                continue

            parcel_count = int(
                zip_data.loc[
                    zip_index,
                    "Parcels"
                ]
            )

            load_terms.append(
                parcel_count
                *
                assignment[
                    zip_index
                ][driver]
            )

        if load_terms:

            model.Add(
                loads[driver]
                ==
                sum(load_terms)
            )

        else:

            model.Add(
                loads[driver]
                == 0
            )

    # ========================================================
    # EVERY DRIVER MUST GET SOMETHING
    # ========================================================

    for driver in drivers:

        model.Add(
            loads[driver]
            >= 1
        )

    # ========================================================
    # MINIMUM / MAXIMUM LOAD
    # ========================================================

    minimum_load = model.NewIntVar(
        0,
        total_parcels,
        "minimum_load"
    )

    maximum_load = model.NewIntVar(
        0,
        total_parcels,
        "maximum_load"
    )

    for driver in drivers:

        model.Add(
            loads[driver]
            >= minimum_load
        )

        model.Add(
            loads[driver]
            <= maximum_load
        )

    load_range = model.NewIntVar(
        0,
        total_parcels,
        "load_range"
    )

    model.Add(
        load_range
        ==
        maximum_load
        -
        minimum_load
    )

    # ========================================================
    # AVERAGE LOAD
    # ========================================================

    average = (
        total_parcels
        /
        driver_count
    )

    average_integer = int(
        round(average)
    )

    deviations = []

    for driver in drivers:

        safe_driver_name = (
            driver
            .replace(" ", "_")
            .replace("-", "_")
        )

        deviation = model.NewIntVar(
            0,
            total_parcels,
            f"deviation_{safe_driver_name}"
        )

        model.AddAbsEquality(
            deviation,
            loads[driver]
            -
            average_integer
        )

        deviations.append(
            deviation
        )

    # ========================================================
    # OBJECTIVE
    # ========================================================
    #
    # Priority 1:
    # Keep highest and lowest driver loads close.
    #
    # Priority 2:
    # Keep each driver close to average.
    #
    # The manually assigned zones are HARD constraints.
    #
    # The optimizer cannot move a ZIP to a driver who
    # wasn't manually assigned that ZIP's zone.
    # ========================================================

    model.Minimize(

        load_range * 10000

        +

        sum(
            deviations
        ) * 100
    )

    # ========================================================
    # SOLVE
    # ========================================================

    solver = cp_model.CpSolver()

    solver.parameters.max_time_in_seconds = 30

    solver.parameters.num_search_workers = 8

    status = solver.Solve(
        model
    )

    if status not in (
        cp_model.OPTIMAL,
        cp_model.FEASIBLE,
    ):

        raise ValueError(
            "No feasible driver plan could "
            "be created with the manually "
            "assigned zones."
        )

    # ========================================================
    # BUILD RESULTS
    # ========================================================

    results = []

    for driver_number, driver in enumerate(
        drivers,
        start=1
    ):

        team = normalized_assignments[
            driver
        ]["Team"]

        assigned_zones = sorted(
            normalized_assignments[
                driver
            ]["Zones"]
        )

        driver_zips = []

        parcels = 0

        actual_zones = set()

        # ----------------------------------------------------
        # Find ZIPs assigned to this driver
        # ----------------------------------------------------

        for zip_index, eligible in eligible_drivers.items():

            if driver not in eligible:
                continue

            assigned = solver.Value(
                assignment[
                    zip_index
                ][driver]
            )

            if assigned != 1:
                continue

            row = zip_data.loc[
                zip_index
            ]

            zone = int(
                row["zone"]
            )

            zipcode = str(
                row["Receiver Zipcode"]
            )

            parcel_count = int(
                row["Parcels"]
            )

            actual_zones.add(
                zone
            )

            driver_zips.append(
                zipcode
            )

            parcels += parcel_count

        results.append({

            "Driver":
                driver_number,

            "Driver Name":
                driver,

            "Team":
                team,

            "Assigned Zones":
                ", ".join(
                    str(zone)
                    for zone in assigned_zones
                ),

            "Zones Used":
                ", ".join(
                    str(zone)
                    for zone in sorted(
                        actual_zones
                    )
                ),

            "ZIP Codes":
                ", ".join(
                    driver_zips
                ),

            "Parcels":
                parcels,

            "Difference From Average":
                round(
                    parcels - average,
                    1
                ),

            "Difference From 50":
                parcels - TARGET_PARCELS,

        })

    # ========================================================
    # CREATE DATAFRAME
    # ========================================================

    plan = pd.DataFrame(
        results
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    acar_count = sum(
        1
        for settings
        in normalized_assignments.values()
        if settings["Team"] == "ACAR"
    )

    legno_count = sum(
        1
        for settings
        in normalized_assignments.values()
        if settings["Team"] == "LEGNO"
    )

    information = {

        "total_parcels":
            total_parcels,

        "drivers":
            driver_count,

        "acar_drivers":
            acar_count,

        "legno_drivers":
            legno_count,

        "average_parcels":
            round(
                average,
                1
            ),

        "status":
            "Optimization completed."

    }

    return plan, information
