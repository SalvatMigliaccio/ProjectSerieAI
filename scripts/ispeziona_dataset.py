import pandas as pd

df = pd.read_parquet("data/interim/matches_master.parquet")

print("FORMA:", df.shape)

print("\nCOLONNE NON-QUOTE:")
odds = ("B365", "BW", "IW", "PS", "WH", "VC", "Max", "Avg", "BF", "P>", "P<",
        "AH", ">2.5", "<2.5", "1XB", "BFE", "BFD", "Bb")
for c in df.columns:
    if not any(c.startswith(o) or o in c for o in odds):
        print(" ", c)

print("\nCOLONNE QUOTE (prime 20):")
print([c for c in df.columns if any(c.startswith(o) for o in odds)][:20])

print("\nULTIME PARTITE DEL NAPOLI:")
m = (df.home_team == "Napoli") | (df.away_team == "Napoli")
print(df[m].sort_values("date").tail(5)[
    ["date", "home_team", "away_team", "FTHG", "FTAG", "home_xg", "away_xg"]
].to_string(index=False))

print("\nVALORI MANCANTI 2026/27:")
cur = df[df.season == "2627"]
print(cur.isna().sum().sort_values(ascending=False).head(10).to_string())