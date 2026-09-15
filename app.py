import json
import os

import folium
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium

from modules.mannheim_optimizer import optimize_mannheim


# ============================================================
# SETTINGS
# ============================================================

MAP_FILE = "data/mannheim_zip_boundaries.geojson"
BUILD_VERSION = "2026-09-15-67258-FINAL-GUARD"
ALL_ZONES = list(range(1, 10))
MAX_DRIVERS = 10

# Depot location: Adam-Opel-Straße 22, 67227 Frankenthal.
# The published map coordinate for the adjacent address on the same Adam-Opel-Straße 22 site is used as the verified map point.
DEPOT_LAT = 49.54543
DEPOT_LON = 8.34155
DEPOT_LABEL = "DEPOT — Adam-Opel-Straße 22, 67227 Frankenthal"


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="Mannheim Depot Optimizer",
    page_icon="🗺️",
    layout="wide",
)

st.title("Mannheim Depot Optimizer")
st.caption(
    f"Build: {BUILD_VERSION} · 67258 enabled"
)
st.caption(
    "Upload the daily parcel report to see parcel activity by PIN code, "
    "then create the driver plan using your manual zone assignments."
)


# ============================================================
# HELPERS
# ============================================================

def normalize_zip(value):
    """Return a five-digit ZIP string whenever possible."""
    if pd.isna(value):
        return ""

    text = str(value).strip()

    if text.endswith(".0"):
        text = text[:-2]

    digits = "".join(ch for ch in text if ch.isdigit())

    if not digits:
        return ""

    return digits.zfill(5)[-5:]


# 67258 is Heßheim (Rhein-Pfalz-Kreis). The daily Excel reports can contain
# this valid delivery postcode even though the older Mannheim interactive HTML
# used for the original map did not contain a 67258 polygon.  Add the postcode
# area as a built-in fallback so it participates in the same parcel/map workflow.
# The fallback geometry is based on the published 67258/Heßheim area and its
# mapped centre; it is assigned to operational Zone 1.
ZIP_GEOMETRY_FALLBACKS = {
    "67258": {
        "route": "001",
        "postalCode": "67258",
        "place": "Heßheim",
        "lat": 49.545778,
        "lon": 8.309203,
        "sourceRow": "fallback-67258",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [8.29132121, 49.54735453],
                [8.29415925, 49.55586866],
                [8.30267338, 49.55799719],
                [8.31118750, 49.55586866],
                [8.32324918, 49.55515915],
                [8.32892526, 49.54877355],
                [8.32608722, 49.54025943],
                [8.32112065, 49.53458335],
                [8.31047799, 49.53316433],
                [8.29983533, 49.53529286],
                [8.29203072, 49.54025943],
                [8.29132121, 49.54735453],
            ]],
        },
    }
}


def _add_missing_zip_fallbacks(geojson):
    """Add valid operational postcode polygons absent from the legacy map."""
    existing = {
        normalize_zip(feature.get("properties", {}).get("postalCode", ""))
        for feature in geojson.get("features", [])
    }

    for zipcode, fallback in ZIP_GEOMETRY_FALLBACKS.items():
        if zipcode in existing:
            continue

        properties = {
            key: value
            for key, value in fallback.items()
            if key != "geometry"
        }
        properties["geometrySource"] = "67258 Heßheim fallback geometry"
        geojson.setdefault("features", []).append({
            "type": "Feature",
            "geometry": fallback["geometry"],
            "properties": properties,
        })

    return geojson


@st.cache_data(show_spinner=False)
def load_boundaries():
    if not os.path.exists(MAP_FILE):
        raise FileNotFoundError(
            f"Missing map file: {MAP_FILE}"
        )

    with open(MAP_FILE, "r", encoding="utf-8") as file:
        geojson = json.load(file)

    geojson = _add_missing_zip_fallbacks(geojson)

    # Hard guarantee: 67258 must be routable even if an older/corrupt map file
    # is accidentally supplied.  The fallback above supplies its zone + geometry.
    return geojson


@st.cache_data(show_spinner=False)
def build_zone_lookup():
    """Build ZIP -> zone directly from the supplied Mannheim map geometry."""
    geojson = load_boundaries()
    lookup = {}

    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        zipcode = normalize_zip(props.get("postalCode", ""))
        route = props.get("route", "")

        if not zipcode:
            continue

        try:
            zone = int(str(route).lstrip("0") or "0")
        except ValueError:
            continue

        if 1 <= zone <= 9:
            lookup[zipcode] = zone

    return lookup


def prepare_parcel_data(uploaded_file):
    df = pd.read_excel(uploaded_file)
    df.columns = df.columns.astype(str).str.strip()

    if "Receiver Zipcode" not in df.columns:
        raise ValueError(
            "The Excel file must contain a 'Receiver Zipcode' column."
        )

    df["Receiver Zipcode"] = df["Receiver Zipcode"].apply(normalize_zip)
    df = df[df["Receiver Zipcode"] != ""].copy()

    if df.empty:
        raise ValueError("No valid Receiver Zipcode values were found.")

    zone_lookup = build_zone_lookup()

    # Add the operational zone directly from the Mannheim map.
    df["zone"] = df["Receiver Zipcode"].map(zone_lookup)

    # 67258 is a valid operational delivery postcode (Heßheim / Zone 1).
    # Apply this explicit override after the map lookup as a final guard.
    # This prevents a stale Streamlit cache or an older map lookup from ever
    # rejecting 67258 during Excel validation.
    df.loc[df["Receiver Zipcode"].eq("67258"), "zone"] = 1

    if df["zone"].isna().any():
        unknown = sorted(
            df.loc[df["zone"].isna(), "Receiver Zipcode"].unique()
        )
        raise ValueError(
            "These ZIP codes are present in the Excel file but not in the "
            f"Mannheim map: {', '.join(unknown)}"
        )

    df["zone"] = df["zone"].astype(int)

    return df


# Permanent ZIP labels: no hover is needed. Labels show PIN code and parcel count.
def add_zip_labels(fmap, features):
    for feature in features:
        props = feature.get("properties", {})
        parcels = int(props.get("parcelCount", 0))
        if parcels <= 0:
            continue
        lat = props.get("lat")
        lon = props.get("lon")
        zipcode = props.get("postalCode", "")
        if lat is None or lon is None:
            continue
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            continue
        label = folium.DivIcon(
            html=(
                '<div style="font-family:Arial,sans-serif; font-size:12px; '
                'font-weight:700; color:#111827; text-align:center; '
                'white-space:nowrap; text-shadow:0 1px 2px #fff, 1px 0 2px #fff, '
                '-1px 0 2px #fff, 0 -1px 2px #fff;">'
                f'<div>{zipcode}</div>'
                f'<div style="font-size:14px; font-weight:800;">{parcels:,}</div>'
                '</div>'
            ),
            icon_size=(90, 32),
            icon_anchor=(45, 16),
        )
        folium.Marker([lat, lon], icon=label, interactive=False).add_to(fmap)


def add_depot_marker(fmap):
    """Add only the fixed Frankenthal depot pin to every map."""
    icon = folium.Icon(color="red", icon="home", prefix="fa")
    folium.Marker(
        [DEPOT_LAT, DEPOT_LON],
        icon=icon,
        z_index_offset=1000,
    ).add_to(fmap)


def build_pin_map(parcel_df):
    """Create an interactive ZIP polygon map with parcel-driven visibility."""
    geojson = load_boundaries()
    zone_lookup = build_zone_lookup()

    counts = (
        parcel_df.groupby("Receiver Zipcode")
        .size()
        .rename("Parcels")
        .reset_index()
    )

    count_lookup = dict(
        zip(
            counts["Receiver Zipcode"],
            counts["Parcels"].astype(int),
        )
    )

    # Maximum is used only for visual intensity.
    max_parcels = max(count_lookup.values(), default=1)

    # Distinct light pastel colors for Zones 1-9.
    zone_colors = {
        1: "#F4D35E",  # medium yellow
        2: "#E98B8B",  # medium red
        3: "#7EA6E0",  # medium blue
        4: "#8FCB9B",  # medium green
        5: "#B58AC8",  # medium purple
        6: "#F2A65A",  # medium orange
        7: "#70C9C9",  # medium cyan
        8: "#D8B07A",  # medium tan
        9: "#9B8FD4",  # medium violet
    }

    for feature in geojson.get("features", []):
        props = feature.setdefault("properties", {})
        zipcode = normalize_zip(props.get("postalCode", ""))
        parcels = int(count_lookup.get(zipcode, 0))
        zone = zone_lookup.get(zipcode, "—")

        props["postalCode"] = zipcode
        props["parcelCount"] = parcels
        props["zone"] = zone
        props["hasParcels"] = parcels > 0
        props["zoneColor"] = zone_colors.get(zone, "#d9dde3")

        if parcels > 0:
            props["fillOpacity"] = 0.22 + 0.48 * (parcels / max_parcels)
        else:
            props["fillOpacity"] = 0.045

    # The overview PIN map is independent of the click-planning viewport.
    # Use a stable default view here; only the dynamic planning map preserves
    # the user's zoom and center.
    center = [49.47, 8.56]
    zoom_level = 10

    fmap = folium.Map(
        location=center,
        zoom_start=zoom_level,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    # Keep the geographic background neutral so the zone colors stand out.
    # Only the base map tiles are desaturated; ZIP polygons and boundaries
    # remain fully colored and visible.
    fmap.get_root().html.add_child(folium.Element("""
    <style>
        .leaflet-tile-pane {
            filter: grayscale(100%) brightness(112%) contrast(82%);
        }
    </style>
    """))

    def style_function(feature):
        props = feature.get("properties", {})
        parcels = int(props.get("parcelCount", 0))

        if parcels == 0:
            return {
                "fillColor": "#d9dde3",
                "color": "#9aa1aa",
                "weight": 0.7,
                "fillOpacity": 0.045,
                "opacity": 0.45,
            }

        ratio = parcels / max_parcels
        zone = props.get("zone")
        return {
            "fillColor": props.get("zoneColor", zone_colors.get(zone, "#d9dde3")),
            "color": "#263238",
            "weight": 1.8,
            "fillOpacity": 0.52 + 0.32 * ratio,
            "opacity": 0.9,
        }


    folium.GeoJson(
        geojson,
        name="PIN Code Parcel Activity",
        style_function=style_function,
        smooth_factor=0.5,
    ).add_to(fmap)


    # Zone color legend.
    legend_items = "".join(
        f'<div style="margin:3px 0;">'
        f'<span style="display:inline-block;width:18px;height:18px;'
        f'background:{zone_colors[z]};border:1px solid #777;'
        f'margin-right:7px;vertical-align:middle;"></span>'
        f'Zone {z}</div>'
        for z in ALL_ZONES
    )
    legend_html = f"""
    <div style="
        position: fixed;
        bottom: 25px; left: 25px;
        z-index: 9999;
        background: white;
        border: 1px solid #999;
        border-radius: 6px;
        padding: 10px 12px;
        font-family: Arial;
        font-size: 12px;
        box-shadow: 0 1px 5px rgba(0,0,0,0.25);
    ">
        <b>Zone colors</b>
        {legend_items}
        <div style="margin-top:5px;color:#777;">Gray = no parcels</div>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl(collapsed=True).add_to(fmap)
    add_depot_marker(fmap)

    return fmap


def build_zone_map(parcel_df, selected_zone):
    """Create a map showing only the selected zone and its ZIP areas."""
    geojson = load_boundaries()
    zone_lookup = build_zone_lookup()

    counts = (
        parcel_df.groupby("Receiver Zipcode")
        .size()
        .rename("Parcels")
        .reset_index()
    )
    count_lookup = dict(zip(counts["Receiver Zipcode"], counts["Parcels"].astype(int)))
    max_parcels = max(count_lookup.values(), default=1)

    zone_colors = {
        1: "#F4D35E", 2: "#E98B8B", 3: "#7EA6E0", 4: "#8FCB9B",
        5: "#B58AC8", 6: "#F2A65A", 7: "#70C9C9", 8: "#D8B07A",
        9: "#9B8FD4",
    }

    selected_features = []
    for feature in geojson.get("features", []):
        props = feature.setdefault("properties", {})
        zipcode = normalize_zip(props.get("postalCode", ""))
        zone = zone_lookup.get(zipcode)
        if zone != selected_zone:
            continue

        parcels = int(count_lookup.get(zipcode, 0))
        props["postalCode"] = zipcode
        props["parcelCount"] = parcels
        props["zone"] = zone
        props["zoneColor"] = zone_colors[selected_zone]
        selected_features.append(feature)

    zone_geojson = {
        "type": "FeatureCollection",
        "features": selected_features,
    }

    # Center the selected zone using its map-provided ZIP coordinates.
    coords = []
    for feature in selected_features:
        props = feature.get("properties", {})
        lat = props.get("lat")
        lon = props.get("lon")
        if lat is not None and lon is not None:
            try:
                coords.append((float(lat), float(lon)))
            except (TypeError, ValueError):
                pass

    center = [
        sum(x[0] for x in coords) / len(coords),
        sum(x[1] for x in coords) / len(coords),
    ] if coords else [49.47, 8.56]

    fmap = folium.Map(
        location=center,
        zoom_start=11,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    def style_function(feature):
        props = feature.get("properties", {})
        parcels = int(props.get("parcelCount", 0))
        ratio = parcels / max_parcels if max_parcels else 0
        return {
            "fillColor": props.get("zoneColor", zone_colors[selected_zone]),
            "color": "#263238",
            "weight": 1.8,
            "fillOpacity": 0.20 if parcels == 0 else 0.52 + 0.32 * ratio,
            "opacity": 0.9,
        }


    folium.GeoJson(
        zone_geojson,
        name=f"Zone {selected_zone}",
        style_function=style_function,
        smooth_factor=0.5,
    ).add_to(fmap)

    add_zip_labels(fmap, selected_features)

    total = sum(count_lookup.get(normalize_zip(f.get("properties", {}).get("postalCode", "")), 0) for f in selected_features)
    title_html = f"""
    <div style="position: fixed; top: 15px; left: 50px; z-index: 9999;
         background: white; border: 1px solid #999; border-radius: 6px;
         padding: 8px 12px; font-family: Arial; box-shadow: 0 1px 5px rgba(0,0,0,0.2);">
        <b>Zone {selected_zone}</b> &nbsp;|&nbsp; {len(selected_features)} PIN areas
        &nbsp;|&nbsp; {total:,} parcels
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(title_html))
    add_depot_marker(fmap)
    return fmap


def build_driver_map(parcel_df, selected_driver, driver_assignments):
    """Create a map showing all zones assigned to the selected driver."""
    geojson = load_boundaries()
    zone_lookup = build_zone_lookup()

    counts = (
        parcel_df.groupby("Receiver Zipcode")
        .size()
        .rename("Parcels")
        .reset_index()
    )
    count_lookup = dict(zip(counts["Receiver Zipcode"], counts["Parcels"].astype(int)))
    max_parcels = max(count_lookup.values(), default=1)

    zone_colors = {
        1: "#F4D35E", 2: "#E98B8B", 3: "#7EA6E0", 4: "#8FCB9B",
        5: "#B58AC8", 6: "#F2A65A", 7: "#70C9C9", 8: "#D8B07A",
        9: "#9B8FD4",
    }

    selected_zones = set(driver_assignments[selected_driver]["Zones"])
    selected_features = []

    for feature in geojson.get("features", []):
        props = feature.setdefault("properties", {})
        zipcode = normalize_zip(props.get("postalCode", ""))
        zone = zone_lookup.get(zipcode)
        if zone not in selected_zones:
            continue

        parcels = int(count_lookup.get(zipcode, 0))
        props["postalCode"] = zipcode
        props["parcelCount"] = parcels
        props["zone"] = zone
        props["zoneColor"] = zone_colors.get(zone, "#d9dde3")
        selected_features.append(feature)

    driver_geojson = {
        "type": "FeatureCollection",
        "features": selected_features,
    }

    coords = []
    for feature in selected_features:
        props = feature.get("properties", {})
        try:
            coords.append((float(props["lat"]), float(props["lon"])))
        except (KeyError, TypeError, ValueError):
            pass

    center = [
        sum(x[0] for x in coords) / len(coords),
        sum(x[1] for x in coords) / len(coords),
    ] if coords else [49.47, 8.56]

    # This viewer is also independent of the click-planning viewport.
    center = center
    zoom_level = 10

    fmap = folium.Map(
        location=center,
        zoom_start=zoom_level,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    # Keep the geographic background neutral so the zone colors stand out.
    # Only the base map tiles are desaturated; ZIP polygons and boundaries
    # remain fully colored and visible.
    fmap.get_root().html.add_child(folium.Element("""
    <style>
        .leaflet-tile-pane {
            filter: grayscale(100%) brightness(112%) contrast(82%);
        }
    </style>
    """))

    def style_function(feature):
        props = feature.get("properties", {})
        parcels = int(props.get("parcelCount", 0))
        ratio = parcels / max_parcels if max_parcels else 0
        return {
            "fillColor": props.get("zoneColor", "#d9dde3"),
            "color": "#263238",
            "weight": 1.8,
            "fillOpacity": 0.10 if parcels == 0 else 0.52 + 0.32 * ratio,
            "opacity": 0.9,
        }


    folium.GeoJson(
        driver_geojson,
        name=selected_driver,
        style_function=style_function,
        smooth_factor=0.5,
    ).add_to(fmap)

    add_zip_labels(fmap, selected_features)

    total_parcels = sum(
        int(feature.get("properties", {}).get("parcelCount", 0))
        for feature in selected_features
    )

    zone_text = ", ".join(f"Zone {z}" for z in sorted(selected_zones)) or "No zones"
    title_html = f"""
    <div style="position: fixed; top: 15px; left: 50px; z-index: 9999;
         background: white; border: 1px solid #999; border-radius: 6px;
         padding: 8px 12px; font-family: Arial; box-shadow: 0 1px 5px rgba(0,0,0,0.2);">
        <b>{selected_driver}</b> &nbsp;|&nbsp; {zone_text}
        &nbsp;|&nbsp; {len(selected_features)} PIN areas
        &nbsp;|&nbsp; {total_parcels:,} parcels
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(title_html))

    legend_items = "".join(
        f'<div style="margin:3px 0;">'
        f'<span style="display:inline-block;width:16px;height:16px;'
        f'background:{zone_colors[z]};border:1px solid #777;'
        f'margin-right:6px;vertical-align:middle;"></span>'
        f'Zone {z}</div>'
        for z in sorted(selected_zones)
    )
    legend_html = f"""
    <div style="position: fixed; bottom: 25px; left: 25px; z-index: 9999;
         background: white; border: 1px solid #999; border-radius: 6px;
         padding: 8px 10px; font-family: Arial; font-size: 12px;
         box-shadow: 0 1px 5px rgba(0,0,0,0.2);">
        <b>{selected_driver} zones</b>{legend_items}
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(legend_html))
    add_depot_marker(fmap)

    return fmap



def build_dynamic_click_map(parcel_df, pin_to_driver, driver_colors, pending_driver=None, map_center=None, map_zoom=None):
    """Build the map used for click-to-assign dynamic driver planning."""
    geojson = load_boundaries()
    zone_lookup = build_zone_lookup()

    counts = (
        parcel_df.groupby("Receiver Zipcode")
        .size()
        .rename("Parcels")
        .reset_index()
    )
    count_lookup = dict(zip(counts["Receiver Zipcode"], counts["Parcels"].astype(int)))
    active_zips = set(count_lookup.keys())

    zone_colors = {
        1: "#F4D35E", 2: "#E98B8B", 3: "#7EA6E0", 4: "#8FCB9B",
        5: "#B58AC8", 6: "#F2A65A", 7: "#70C9C9", 8: "#D8B07A",
        9: "#9B8FD4",
    }

    features = []
    for feature in geojson.get("features", []):
        props = feature.setdefault("properties", {})
        zipcode = normalize_zip(props.get("postalCode", ""))
        if zipcode not in active_zips:
            continue
        zone = zone_lookup.get(zipcode)
        props["postalCode"] = zipcode
        props["parcelCount"] = int(count_lookup.get(zipcode, 0))
        props["zone"] = zone
        props["driver"] = pin_to_driver.get(zipcode, "")
        props["driverColor"] = driver_colors.get(
            props["driver"], zone_colors.get(zone, "#d9dde3")
        )
        features.append(feature)

    coords = []
    for feature in features:
        props = feature.get("properties", {})
        try:
            coords.append((float(props["lat"]), float(props["lon"])))
        except (KeyError, TypeError, ValueError):
            pass

    center = [
        sum(x[0] for x in coords) / len(coords),
        sum(x[1] for x in coords) / len(coords),
    ] if coords else [49.47, 8.56]

    # Preserve the user's current viewport across Streamlit reruns.
    if map_center and isinstance(map_center, (list, tuple)) and len(map_center) == 2:
        try:
            center = [float(map_center[0]), float(map_center[1])]
        except (TypeError, ValueError):
            pass

    try:
        zoom_level = int(map_zoom) if map_zoom is not None else 10
    except (TypeError, ValueError):
        zoom_level = 10
    zoom_level = max(1, min(19, zoom_level))

    fmap = folium.Map(
        location=center,
        zoom_start=zoom_level,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    fmap.get_root().html.add_child(folium.Element("""
    <style>
        .leaflet-tile-pane {
            filter: grayscale(100%) brightness(112%) contrast(82%);
        }
    </style>
    """))

    def style_function(feature):
        props = feature.get("properties", {})
        zipcode = props.get("postalCode", "")
        parcels = int(props.get("parcelCount", 0))
        driver = props.get("driver", "")
        zone = props.get("zone")

        if driver:
            return {
                "fillColor": props.get("driverColor", "#d9dde3"),
                "color": props.get("driverColor", "#111827"),
                "weight": 3.0,
                "fillOpacity": 0.78,
                "opacity": 1.0,
            }

        return {
            "fillColor": zone_colors.get(zone, "#d9dde3"),
            "color": "#263238",
            "weight": 1.8,
            "fillOpacity": 0.22 if parcels else 0.045,
            "opacity": 0.8,
        }

    # A single GeoJson layer keeps all postcode polygons clickable while
    # preserving the existing zone colors for unassigned PINs.
    popup = folium.GeoJsonPopup(
        fields=["postalCode", "parcelCount", "zone"],
        aliases=["PIN code", "Parcels", "Zone"],
        localize=True,
        labels=True,
        sticky=False,
    )
    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        name="Click-to-assign PINs",
        style_function=style_function,
        popup=popup,
        smooth_factor=0.5,
        zoom_on_click=False,
    ).add_to(fmap)

    # Permanent ZIP + parcel labels.
    add_zip_labels(fmap, features)
    add_depot_marker(fmap)

    # Zone legend + driver legend.
    zone_items = "".join(
        f'<div style="margin:2px 0;"><span style="display:inline-block;width:14px;height:14px;'
        f'background:{zone_colors[z]};border:1px solid #777;margin-right:5px;vertical-align:middle;"></span>'
        f'Zone {z}</div>'
        for z in ALL_ZONES
    )
    driver_items = "".join(
        f'<div style="margin:2px 0;"><span style="display:inline-block;width:14px;height:14px;'
        f'background:{driver_colors[d]};border:1px solid #333;margin-right:5px;vertical-align:middle;"></span>'
        f'{d}</div>'
        for d in driver_colors
    )
    legend_html = f"""
    <div style="position:fixed;bottom:25px;left:25px;z-index:9999;
         background:white;border:1px solid #999;border-radius:6px;
         padding:9px 11px;font-family:Arial;font-size:11px;
         max-height:360px;overflow-y:auto;box-shadow:0 1px 5px rgba(0,0,0,.22);">
        <b>Zone colors</b>{zone_items}
        <div style="margin:7px 0 4px;border-top:1px solid #ddd;padding-top:5px;"><b>Driver colors</b></div>
        {driver_items}
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(legend_html))

    instruction = (
        f"<b>{pending_driver}</b> is active — click postcode areas to add/remove them."
        if pending_driver else
        "Activate a driver on the right, then click postcode areas on the map."
    )
    instruction_html = f"""
    <div style="position:fixed;top:15px;left:50px;z-index:9999;
         background:white;border:1px solid #999;border-radius:6px;
         padding:8px 12px;font-family:Arial;font-size:12px;
         box-shadow:0 1px 5px rgba(0,0,0,.2);">
        {instruction}
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(instruction_html))

    return fmap


def build_dynamic_driver_map(parcel_df, pin_to_driver, driver_colors):
    """Show every active PIN while coloring selected PINs by driver."""
    geojson = load_boundaries()
    zone_lookup = build_zone_lookup()

    counts = (
        parcel_df.groupby("Receiver Zipcode")
        .size()
        .rename("Parcels")
        .reset_index()
    )
    count_lookup = dict(zip(counts["Receiver Zipcode"], counts["Parcels"].astype(int)))
    active_zips = set(count_lookup.keys())

    zone_colors = {
        1: "#F4D35E", 2: "#E98B8B", 3: "#7EA6E0", 4: "#8FCB9B",
        5: "#B58AC8", 6: "#F2A65A", 7: "#70C9C9", 8: "#D8B07A",
        9: "#9B8FD4",
    }

    features = []
    for feature in geojson.get("features", []):
        props = feature.setdefault("properties", {})
        zipcode = normalize_zip(props.get("postalCode", ""))
        if zipcode not in active_zips:
            continue
        zone = zone_lookup.get(zipcode)
        driver = pin_to_driver.get(zipcode)
        props["postalCode"] = zipcode
        props["parcelCount"] = int(count_lookup.get(zipcode, 0))
        props["zone"] = zone
        props["driver"] = driver or ""
        props["driverColor"] = driver_colors.get(driver, zone_colors.get(zone, "#d9dde3"))
        features.append(feature)

    fmap = folium.Map(
        location=[49.47, 8.56],
        zoom_start=10,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    fmap.get_root().html.add_child(folium.Element("""
    <style>
        .leaflet-tile-pane {
            filter: grayscale(100%) brightness(112%) contrast(82%);
        }
    </style>
    """))

    def style_function(feature):
        props = feature.get("properties", {})
        zipcode = props.get("postalCode", "")
        parcels = int(props.get("parcelCount", 0))
        driver = props.get("driver", "")
        zone = props.get("zone")
        if driver:
            return {
                "fillColor": props.get("driverColor", "#d9dde3"),
                "color": "#111827",
                "weight": 2.4,
                "fillOpacity": 0.72,
                "opacity": 0.98,
            }
        return {
            "fillColor": zone_colors.get(zone, "#d9dde3"),
            "color": "#aeb4ba",
            "weight": 1.0,
            "fillOpacity": 0.055 if parcels else 0.03,
            "opacity": 0.45,
        }

    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        name="Dynamic driver plan",
        style_function=style_function,
        smooth_factor=0.5,
    ).add_to(fmap)

    # Keep the permanent ZIP + parcel labels so the map remains operationally useful.
    add_zip_labels(fmap, features)

    selected_total = sum(
        int(f.get("properties", {}).get("parcelCount", 0))
        for f in features
        if f.get("properties", {}).get("driver")
    )
    selected_pins = sum(
        1 for f in features if f.get("properties", {}).get("driver")
    )

    driver_items = "".join(
        f'<div style="margin:4px 0;">'
        f'<span style="display:inline-block;width:17px;height:17px;'
        f'background:{driver_colors[d]};border:1px solid #333;'
        f'margin-right:7px;vertical-align:middle;"></span>{d}'
        f'</div>'
        for d in driver_colors
    )
    legend_html = f"""
    <div style="position:fixed;bottom:25px;left:25px;z-index:9999;
         background:white;border:1px solid #999;border-radius:6px;
         padding:9px 11px;font-family:Arial;font-size:12px;
         box-shadow:0 1px 5px rgba(0,0,0,.22);">
        <b>Driver colors</b>
        {driver_items}
        <div style="margin-top:6px;color:#777;border-top:1px solid #ddd;padding-top:5px;">
            Faded = unplanned PIN
        </div>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(legend_html))

    title_html = f"""
    <div style="position:fixed;top:15px;left:50px;z-index:9999;
         background:white;border:1px solid #999;border-radius:6px;
         padding:8px 12px;font-family:Arial;
         box-shadow:0 1px 5px rgba(0,0,0,.2);">
        <b>Dynamic driver plan</b> &nbsp;|&nbsp; {selected_pins:,} selected PINs
        &nbsp;|&nbsp; {selected_total:,} parcels
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(title_html))
    add_depot_marker(fmap)
    return fmap


def validate_assignments(driver_assignments, parcel_df):
    if not driver_assignments:
        raise ValueError("At least one driver is required.")

    for driver_name, info in driver_assignments.items():
        if not info["Zones"]:
            raise ValueError(
                f"{driver_name} has no zones assigned."
            )

    parcel_zones = set(
        parcel_df["zone"].astype(int).unique()
    )
    assigned_zones = set()

    for info in driver_assignments.values():
        assigned_zones.update(info["Zones"])

    missing = sorted(parcel_zones - assigned_zones)
    if missing:
        raise ValueError(
            "These parcel zones have not been assigned to any driver: "
            + ", ".join(str(zone) for zone in missing)
        )


# ============================================================
# FILE UPLOAD
# ============================================================

uploaded_file = st.file_uploader(
    "Upload daily Excel parcel report",
    type=["xlsx", "xls"],
)

if uploaded_file is None:
    st.info("Upload the Excel report to load the PIN-code parcel map.")
    st.stop()


# ============================================================
# PREPARE DATA
# ============================================================

try:
    parcel_df = prepare_parcel_data(uploaded_file)
except Exception as exc:
    st.error(str(exc))
    st.stop()

counts = (
    parcel_df.groupby("Receiver Zipcode")
    .size()
    .rename("Parcels")
    .sort_values(ascending=False)
)


# ============================================================
# SUMMARY
# ============================================================

col1, col2, col3 = st.columns(3)
col1.metric("Total parcels", f"{len(parcel_df):,}")
col2.metric("Active PIN codes", f"{len(counts):,}")
col3.metric("Mapped zones", f"{parcel_df['zone'].nunique():,}")


# ============================================================
# INTERACTIVE PIN MAP
# ============================================================

st.subheader("PIN-code parcel activity")
st.caption(
    "PIN code and parcel counts are shown directly on the map. PIN codes with "
    "no parcels remain on the map but are intentionally faded."
)

try:
    fmap = build_pin_map(parcel_df)
    components.html(
        fmap.get_root().render(),
        height=720,
        scrolling=False,
    )
except Exception as exc:
    st.error(f"Could not build the PIN-code map: {exc}")


# ============================================================
# PIN TABLE
# ============================================================

with st.expander("Show parcel count by active PIN code"):
    display_counts = counts.reset_index()
    display_counts.columns = ["PIN Code", "Parcels"]
    st.dataframe(
        display_counts,
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# DYNAMIC PLANNING — CLICK PIN CODES ON THE MAP
# ============================================================

st.subheader("Dynamic driver planning")
st.caption(
    "Activate one driver, click postcode areas directly on the map to assign them, "
    "then click DONE. Repeat for up to 10 drivers."
)

# Dynamic planning uses exactly 10 driver slots. The driver colors stay fixed so
# the same driver is always represented by the same color during the session.
DYNAMIC_DRIVERS = [f"Driver {n}" for n in range(1, MAX_DRIVERS + 1)]
driver_palette = [
    "#E63946", "#277DA1", "#2A9D8F", "#8E44AD", "#F4A261",
    "#D97706", "#4361EE", "#C2185B", "#00897B", "#6D4C41",
]
driver_colors = {
    driver: driver_palette[i]
    for i, driver in enumerate(DYNAMIC_DRIVERS)
}

if "dynamic_driver_pins" not in st.session_state:
    st.session_state.dynamic_driver_pins = {driver: [] for driver in DYNAMIC_DRIVERS}
if "dynamic_pending_pins" not in st.session_state:
    st.session_state.dynamic_pending_pins = {driver: [] for driver in DYNAMIC_DRIVERS}
if "dynamic_active_driver" not in st.session_state:
    st.session_state.dynamic_active_driver = None
if "dynamic_done_drivers" not in st.session_state:
    st.session_state.dynamic_done_drivers = set()
if "dynamic_map_center" not in st.session_state:
    st.session_state.dynamic_map_center = None
if "dynamic_map_zoom" not in st.session_state:
    st.session_state.dynamic_map_zoom = None

# Build parcel counts by PIN and a stable PIN -> zone lookup.
active_zip_rows = (
    parcel_df.groupby(["Receiver Zipcode", "zone"])
    .size()
    .reset_index(name="Parcels")
    .sort_values(["zone", "Receiver Zipcode"])
)
zip_parcels = {
    row["Receiver Zipcode"]: int(row["Parcels"])
    for _, row in active_zip_rows.iterrows()
}

# A PIN can belong to only one driver. This lookup is used to prevent accidental
# double assignment while planning.
committed_pin_to_driver = {}
for driver, pins in st.session_state.dynamic_driver_pins.items():
    for zipcode in pins:
        committed_pin_to_driver[zipcode] = driver

# ------------------------------------------------------------
# MAP + DRIVER CONTROLS SIDE BY SIDE
# ------------------------------------------------------------
map_col, control_col = st.columns([5.5, 1.5], gap="medium")

with control_col:
    st.markdown("### Driver planning")
    st.caption("Activate one driver")

    # Each checkbox is a temporary activation control. The widget key includes
    # the currently active driver so Streamlit naturally resets the other
    # checkboxes on the next run. This avoids modifying a checkbox's session
    # state after the widget has been instantiated.
    def activate_driver(driver_name, widget_key):
        checked = bool(st.session_state.get(widget_key, False))
        if checked:
            st.session_state.dynamic_active_driver = driver_name
            st.session_state.dynamic_pending_pins[driver_name] = list(
                st.session_state.dynamic_driver_pins[driver_name]
            )
        elif st.session_state.dynamic_active_driver == driver_name:
            st.session_state.dynamic_active_driver = None

    active_before_widgets = st.session_state.dynamic_active_driver
    active_token = active_before_widgets or "none"

    # Each driver gets a checkbox plus a small text box where the planner can
    # enter the actual driver's name. The internal Driver 1..10 IDs remain
    # unchanged so the planning data stays stable.
    if "dynamic_driver_names" not in st.session_state:
        st.session_state.dynamic_driver_names = {
            driver: "" for driver in DYNAMIC_DRIVERS
        }

    for driver in DYNAMIC_DRIVERS:
        checkbox_col, name_col = st.columns([1.05, 2.0], gap="small")
        widget_key = (
            f"dynamic_active_checkbox_{driver.replace(' ', '_').lower()}_"
            f"{active_token.replace(' ', '_').lower()}"
        )
        with checkbox_col:
            st.checkbox(
                driver,
                value=(driver == active_before_widgets),
                key=widget_key,
                on_change=activate_driver,
                args=(driver, widget_key),
            )
        with name_col:
            st.text_input(
                f"Name {driver}",
                value=st.session_state.dynamic_driver_names.get(driver, ""),
                key=f"dynamic_driver_name_{driver.replace(' ', '_').lower()}",
                placeholder="Driver name",
                label_visibility="collapsed",
            )
            st.session_state.dynamic_driver_names[driver] = st.session_state.get(
                f"dynamic_driver_name_{driver.replace(' ', '_').lower()}", ""
            )

    active_driver = st.session_state.dynamic_active_driver

    def driver_display_name(driver_name):
        entered = st.session_state.get(
            f"dynamic_driver_name_{driver_name.replace(' ', '_').lower()}", ""
        ).strip()
        return entered if entered else driver_name

    st.markdown("---")
    if active_driver:
        color = driver_colors[active_driver]
        st.markdown(
            f'<div style="border-left:8px solid {color};padding:8px 10px;'
            f'background:#f7f7f7;border-radius:4px;">'
            f'<b>{driver_display_name(active_driver)} ({active_driver}) ACTIVE</b><br>'
            f'Click postcode polygons on the map.</div>',
            unsafe_allow_html=True,
        )

        pending_pins = st.session_state.dynamic_pending_pins[active_driver]
        pending_total = sum(zip_parcels.get(z, 0) for z in pending_pins)
        st.metric("Selected PINs", len(pending_pins))
        st.metric("Packages", f"{pending_total:,}")

        if st.button(
            f"DONE — {active_driver}",
            type="primary",
            use_container_width=True,
            key="dynamic_done_button",
        ):
            st.session_state.dynamic_driver_pins[active_driver] = sorted(
                set(pending_pins)
            )
            st.session_state.dynamic_pending_pins[active_driver] = list(
                st.session_state.dynamic_driver_pins[active_driver]
            )
            st.session_state.dynamic_done_drivers.add(active_driver)
            st.session_state.dynamic_active_driver = None
            # Checkbox states are synchronized before widget creation on the next run.
            st.rerun()
    else:
        st.info("Activate a driver, then click postcode areas directly on the map.")

    st.markdown("---")
    st.markdown("### Completed drivers")
    completed = [d for d in DYNAMIC_DRIVERS if st.session_state.dynamic_driver_pins[d]]
    if not completed:
        st.caption("No driver territory completed yet.")
    else:
        for driver in completed:
            pins = st.session_state.dynamic_driver_pins[driver]
            total = sum(zip_parcels.get(z, 0) for z in pins)
            st.markdown(
                f'<div style="margin:4px 0;padding:5px 7px;border-radius:4px;'
                f'border-left:6px solid {driver_colors[driver]};background:#f7f7f7;">'
                f'<b>{driver_display_name(driver)}</b> <span style="opacity:.65">({driver})</span><br>{len(pins)} PINs · {total:,} packages</div>',
                unsafe_allow_html=True,
            )

# ------------------------------------------------------------
# BUILD THE CLICKABLE MAP
# ------------------------------------------------------------
all_pin_to_driver = {}
for driver in DYNAMIC_DRIVERS:
    pins = (
        st.session_state.dynamic_pending_pins[driver]
        if driver == st.session_state.dynamic_active_driver
        else st.session_state.dynamic_driver_pins[driver]
    )
    for zipcode in pins:
        if zipcode not in all_pin_to_driver:
            all_pin_to_driver[zipcode] = driver

with map_col:
    try:
        dynamic_map = build_dynamic_click_map(
            parcel_df,
            all_pin_to_driver,
            driver_colors,
            pending_driver=st.session_state.dynamic_active_driver,
            map_center=st.session_state.dynamic_map_center,
            map_zoom=st.session_state.dynamic_map_zoom,
        )
        map_data = st_folium(
            dynamic_map,
            width=None,
            height=720,
            returned_objects=[
                "last_object_clicked_popup",
                "last_object_clicked_count",
                "center",
                "zoom",
            ],
            key="dynamic_click_map",
        )
    except Exception as exc:
        st.error(f"Could not build the dynamic planning map: {exc}")
        map_data = {}

# ------------------------------------------------------------
# PROCESS MAP CLICKS
# ------------------------------------------------------------
# Save the exact viewport returned by the map before any click-triggered rerun.
# The next map build starts from this center/zoom instead of resetting to the
# full Mannheim view.
if map_data:
    returned_center = map_data.get("center")
    returned_zoom = map_data.get("zoom")
    if isinstance(returned_center, (list, tuple)) and len(returned_center) == 2:
        try:
            st.session_state.dynamic_map_center = [
                float(returned_center[0]), float(returned_center[1])
            ]
        except (TypeError, ValueError):
            pass
    if returned_zoom is not None:
        try:
            st.session_state.dynamic_map_zoom = int(returned_zoom)
        except (TypeError, ValueError):
            pass

active_driver = st.session_state.dynamic_active_driver
if active_driver and map_data:
    popup_text = map_data.get("last_object_clicked_popup")
    click_count = map_data.get("last_object_clicked_count")
    if popup_text and click_count is not None:
        import re
        matches = re.findall(r"\b\d{5}\b", str(popup_text))
        clicked_zip = matches[0] if matches else ""
        if clicked_zip and clicked_zip in zip_parcels:
            owner = committed_pin_to_driver.get(clicked_zip)
            pending = st.session_state.dynamic_pending_pins[active_driver]

            # Use the browser's click counter so clicking the same PIN twice
            # is treated as two distinct actions (select, then deselect).
            click_signature = f"{active_driver}:{clicked_zip}:{click_count}"
            if st.session_state.get("dynamic_last_click") != click_signature:
                st.session_state.dynamic_last_click = click_signature

                if owner and owner != active_driver:
                    st.warning(
                        f"PIN {clicked_zip} is already assigned to {owner}. "
                        "A PIN can belong to only one driver."
                    )
                elif clicked_zip in pending:
                    st.session_state.dynamic_pending_pins[active_driver] = [
                        z for z in pending if z != clicked_zip
                    ]
                    st.rerun()
                else:
                    st.session_state.dynamic_pending_pins[active_driver] = sorted(
                        set(pending + [clicked_zip])
                    )
                    st.rerun()

# ------------------------------------------------------------
# OVERALL DYNAMIC PLAN SUMMARY
# ------------------------------------------------------------
summary_rows = []
for driver in DYNAMIC_DRIVERS:
    pins = st.session_state.dynamic_driver_pins[driver]
    total = sum(zip_parcels.get(z, 0) for z in pins)
    summary_rows.append(
        {
            "Driver": driver,
            "PINs": len(pins),
            "Packages": total,
            "Status": "Completed" if driver in st.session_state.dynamic_done_drivers else "Not planned",
        }
    )

summary_df = pd.DataFrame(summary_rows)
summary_df = summary_df[summary_df["PINs"] > 0]
if not summary_df.empty:
    st.markdown("#### Dynamic driver plan")
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    planned_pins = int(summary_df["PINs"].sum())
    planned_packages = int(summary_df["Packages"].sum())
    m1, m2, m3 = st.columns(3)
    m1.metric("Planned PINs", f"{planned_pins:,}")
    m2.metric("Planned packages", f"{planned_packages:,}")
    m3.metric("Remaining PINs", f"{max(0, len(active_zip_rows) - planned_pins):,}")


# ============================================================
# MANUAL DRIVER ZONE ASSIGNMENT / OPTIMIZER
# ============================================================

st.subheader("Manual driver zone assignment")
st.caption(
    "The optimizer will only use zones you assign. The same zone can be "
    "assigned to more than one driver."
)


driver_assignments = {}

left, right = st.columns(2)

with left:
    st.markdown("### ACAR")
    for number in range(1, acar_drivers + 1):
        selected = st.multiselect(
            f"ACAR {number} — Zones",
            options=ALL_ZONES,
            key=f"acar_zones_{number}",
        )
        driver_assignments[f"ACAR {number}"] = {
            "Team": "ACAR",
            "Zones": selected,
        }

with right:
    st.markdown("### LEGNO")
    for number in range(1, legno_drivers + 1):
        selected = st.multiselect(
            f"LEGNO {number} — Zones",
            options=ALL_ZONES,
            key=f"legno_zones_{number}",
        )
        driver_assignments[f"LEGNO {number}"] = {
            "Team": "LEGNO",
            "Zones": selected,
        }


# ============================================================
# ZONE / DRIVER MAP VIEWERS
# ============================================================

assignments_complete = bool(driver_assignments) and all(
    info["Zones"] for info in driver_assignments.values()
)

assigned_zones = set()
for info in driver_assignments.values():
    assigned_zones.update(info["Zones"])

active_zones = set(parcel_df["zone"].astype(int).unique())
assignments_complete = assignments_complete and active_zones.issubset(assigned_zones)

if assignments_complete:
    st.subheader("Map viewer")
    st.caption(
        "Use the dropdowns below to view either one zone or the complete territory assigned to a driver."
    )

    viewer_col1, viewer_col2 = st.columns(2)

    with viewer_col1:
        selected_zone = st.selectbox(
            "View a specific zone",
            options=ALL_ZONES,
            format_func=lambda z: f"Zone {z}",
            key="selected_zone_map",
        )

    with viewer_col2:
        selected_driver = st.selectbox(
            "View a driver's assigned territory",
            options=list(driver_assignments.keys()),
            key="selected_driver_map",
        )

    map_col1, map_col2 = st.columns(2)

    with map_col1:
        st.markdown(f"#### Zone {selected_zone}")
        try:
            zone_map = build_zone_map(parcel_df, selected_zone)
            components.html(
                zone_map.get_root().render(),
                height=650,
                scrolling=False,
            )
        except Exception as exc:
            st.error(f"Could not build the selected zone map: {exc}")

    with map_col2:
        driver_zones = driver_assignments[selected_driver]["Zones"]
        st.markdown(f"#### {selected_driver}")
        st.caption(
            "Assigned zones: " + ", ".join(f"Zone {z}" for z in driver_zones)
            if driver_zones else "No zones assigned"
        )
        try:
            driver_map = build_driver_map(
                parcel_df,
                selected_driver,
                driver_assignments,
            )
            components.html(
                driver_map.get_root().render(),
                height=650,
                scrolling=False,
            )
        except Exception as exc:
            st.error(f"Could not build the selected driver map: {exc}")

# ============================================================
# RUN OPTIMIZER
# ============================================================

if st.button("Create driver plan", type="primary", use_container_width=True):
    try:
        validate_assignments(driver_assignments, parcel_df)

        plan, information = optimize_mannheim(
            parcel_df,
            driver_assignments,
        )

        if plan is None or plan.empty:
            st.error(
                information.get(
                    "status",
                    "No driver plan could be created.",
                )
            )
        else:
            st.success(
                information.get(
                    "status",
                    "Optimization completed.",
                )
            )

            st.subheader("Driver plan")

            if "Team" in plan.columns:
                acar_plan = plan[plan["Team"] == "ACAR"]
                legno_plan = plan[plan["Team"] == "LEGNO"]

                if not acar_plan.empty:
                    st.markdown("### ACAR plan")
                    st.dataframe(
                        acar_plan,
                        use_container_width=True,
                        hide_index=True,
                    )

                if not legno_plan.empty:
                    st.markdown("### LEGNO plan")
                    st.dataframe(
                        legno_plan,
                        use_container_width=True,
                        hide_index=True,
                    )
            else:
                st.dataframe(
                    plan,
                    use_container_width=True,
                    hide_index=True,
                )

    except Exception as exc:
        st.error(str(exc))
