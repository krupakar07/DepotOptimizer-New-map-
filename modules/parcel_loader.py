import pandas as pd


def load_parcel_file(uploaded_file):
    """
    Reads the uploaded Excel or CSV file.
    """

    if uploaded_file.name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)

    return df
