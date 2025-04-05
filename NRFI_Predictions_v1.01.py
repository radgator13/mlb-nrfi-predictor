import streamlit as st
import pandas as pd
import requests
from datetime import date
import math

# --- CACHED API FETCH ---
@st.cache_data(ttl=3600)
def fetch_json(url):
    try:
        res = requests.get(url)
        res.raise_for_status()
        return res.json()
    except:
        return {}

@st.cache_data(ttl=3600)
def fetch_schedule(selected_date):
    d = selected_date.strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d}"
    data = fetch_json(url)
    games = []
    for game in data.get('dates', [{}])[0].get('games', []):
        games.append({
            'game_id': game['gamePk'],
            'away': game['teams']['away']['team']['name'],
            'home': game['teams']['home']['team']['name'],
            'home_id': game['teams']['home']['team']['id'],
            'away_id': game['teams']['away']['team']['id']
        })
    return pd.DataFrame(games)

def get_probable_pitchers(game_id):
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
    data = fetch_json(url)
    pp = data.get('gameData', {}).get('probablePitchers', {})
    return {
        'home': pp.get('home', {}).get('id'),
        'away': pp.get('away', {}).get('id'),
        'home_name': pp.get('home', {}).get('fullName', 'N/A'),
        'away_name': pp.get('away', {}).get('fullName', 'N/A'),
    }

@st.cache_data(ttl=3600)
def fetch_stats(player_id, group):
    url = f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=career&group={group}"
    data = fetch_json(url)
    if data.get('stats'):
        return data['stats'][0].get('splits', [{}])[0].get('stat', {})
    return {}

def pitcher_score(stat):
    try:
        era = float(stat.get('era', 5.0))
        k9 = float(stat.get('strikeoutsPer9Inn', 6.0))
        bb9 = float(stat.get('walksPer9Inn', 3.0))
        score = (5.0 - era) * 12 + (k9 - 6.0) * 8 + (3.0 - bb9) * 5
        return max(0, min(100, score))
    except:
        return 50

@st.cache_data(ttl=3600)
def fetch_roster(team_id):
    url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
    data = fetch_json(url)
    return [p['person']['id'] for p in data.get('roster', [])]

def hitter_score(player_ids):
    scores = []
    for pid in player_ids:
        s = fetch_stats(pid, 'hitting')
        try:
            avg = float(s.get('avg', 0.250))
            obp = float(s.get('obp', 0.320))
            slg = float(s.get('slg', 0.400))
            val = (avg - 0.250) * 100 + (obp - 0.320) * 80 + (slg - 0.400) * 60
            scores.append(max(0, min(100, val)))
        except:
            continue
    return sum(scores) / len(scores) if scores else 50

# --- NRFI Model ---
def calculate_nrfi_probability(p1_score, p2_score, h1_score, h2_score):
    avg_pitch = (p1_score + p2_score) / 2
    avg_hit = (h1_score + h2_score) / 2
    model_input = avg_pitch * 1.0 - avg_hit * 0.3
    prob = 100 / (1 + math.exp(-0.1 * (model_input - 25)))
    return round(prob, 2)

# --- Streamlit App ---
st.title("⚾ NRFI Predictor – All Games (Optimized)")

selected_date = st.date_input("Select Game Date", date.today())
games_df = fetch_schedule(selected_date)

if games_df.empty:
    st.warning("No games found.")
    st.stop()

results = []
team_rosters = {}

def get_cached_roster(team_id):
    if team_id not in team_rosters:
        team_rosters[team_id] = fetch_roster(team_id)
    return team_rosters[team_id]

progress = st.progress(0)

with st.spinner("Analyzing matchups..."):
    for i, (_, game) in enumerate(games_df.iterrows()):
        game_id = game['game_id']
        matchup = f"{game['away']} @ {game['home']}"
        pitchers = get_probable_pitchers(game_id)

        if not pitchers['home'] or not pitchers['away']:
            progress.progress((i + 1) / len(games_df))
            continue

        # Stats
        home_p_stats = fetch_stats(pitchers['home'], 'pitching')
        away_p_stats = fetch_stats(pitchers['away'], 'pitching')
        home_p_score = pitcher_score(home_p_stats)
        away_p_score = pitcher_score(away_p_stats)

        home_roster = get_cached_roster(game['home_id'])
        away_roster = get_cached_roster(game['away_id'])
        home_h_score = hitter_score(home_roster)
        away_h_score = hitter_score(away_roster)

        nrfi_prob = calculate_nrfi_probability(home_p_score, away_p_score, home_h_score, away_h_score)
        prediction = "NRFI ✅" if nrfi_prob > 60 else "YRFI ⚠️" if nrfi_prob < 40 else "Toss-up 🤝"

        results.append({
            "Matchup": matchup,
            "Away Pitcher Score": round(away_p_score, 2),
            "Home Pitcher Score": round(home_p_score, 2),
            "Away Hitter Score": round(away_h_score, 2),
            "Home Hitter Score": round(home_h_score, 2),
            "NRFI Probability (%)": nrfi_prob,
            "Prediction": prediction
        })

        progress.progress((i + 1) / len(games_df))

# Show Results
df = pd.DataFrame(results)
st.dataframe(df.sort_values(by="NRFI Probability (%)", ascending=False).reset_index(drop=True))
