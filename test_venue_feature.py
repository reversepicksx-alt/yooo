import requests
import json

# Test the venue filter feature by checking if backend returns recent samples with venue data
BASE_URL = "https://5e1e7943-4a41-4a29-a8e4-9314cbbb7644.preview.emergentagent.com"

def test_venue_feature():
    print("🔍 Testing Venue Filter Feature...")
    print("=" * 50)
    
    # First, get a well-known Premier League player
    print("\n1. Searching for Harry Kane...")
    search_response = requests.post(f"{BASE_URL}/api/players/search", 
                                  json={"query": "Harry Kane", "league_id": 39})
    
    if search_response.status_code == 200:
        players = search_response.json().get("players", [])
        if players:
            kane = players[0]
            print(f"   Found: {kane['name']} (ID: {kane['id']}, Team: {kane['teamName']})")
        else:
            print("   No Harry Kane found, trying different player...")
            # Try with a different search
            search_response = requests.post(f"{BASE_URL}/api/players/search", 
                                          json={"query": "Salah", "league_id": 39})
            players = search_response.json().get("players", [])
            if players:
                kane = players[0]
                print(f"   Found: {kane['name']} (ID: {kane['id']}, Team: {kane['teamName']})")
            else:
                print("   No suitable player found")
                return
    else:
        print(f"   Player search failed: {search_response.status_code}")
        return
    
    # Get Premier League teams for opponent
    print("\n2. Getting Premier League teams...")
    teams_response = requests.get(f"{BASE_URL}/api/leagues/39/teams")
    if teams_response.status_code == 200:
        teams = teams_response.json().get("teams", [])
        if teams:
            opponent = teams[0]  # Use first team as opponent
            print(f"   Using opponent: {opponent['name']} (ID: {opponent['id']})")
        else:
            print("   No teams found")
            return
    else:
        print(f"   Teams request failed: {teams_response.status_code}")
        return
    
    # Make prediction request
    print("\n3. Making prediction request...")
    prediction_data = {
        "leagueId": 39,
        "playerId": kane['id'],
        "playerName": kane['name'],
        "teamId": kane.get('teamId', 0),
        "opponentId": opponent['id'],
        "opponentName": opponent['name'],
        "venue": "home",
        "propType": "shots",
        "line": 2.5
    }
    
    print(f"   Request: {kane['name']} vs {opponent['name']} - {prediction_data['propType']} {prediction_data['line']}")
    
    prediction_response = requests.post(f"{BASE_URL}/api/predict", 
                                      json=prediction_data, 
                                      timeout=120)
    
    if prediction_response.status_code == 200:
        prediction = prediction_response.json()
        print(f"   ✅ Prediction successful")
        print(f"   Player: {prediction.get('player', {}).get('name', 'Unknown')}")
        print(f"   Projected Value: {prediction.get('projectedValue', 'None')}")
        print(f"   Recommendation: {prediction.get('recommendation', 'None')}")
        print(f"   Confidence: {prediction.get('confidenceScore', 0)}%")
        
        # Check recent samples
        recent_samples = prediction.get('recentSamples', [])
        print(f"\n4. Checking Recent Samples...")
        print(f"   Found {len(recent_samples)} recent samples")
        
        if len(recent_samples) > 0:
            print("   ✅ Recent samples found!")
            venue_count = {"home": 0, "away": 0, "unknown": 0}
            
            for i, sample in enumerate(recent_samples[:10]):  # Show first 10
                venue = sample.get('venue', 'unknown')
                venue_count[venue] += 1
                print(f"   Sample {i+1}: {sample.get('value', 'N/A')} vs {sample.get('opponent', 'Unknown')} ({venue})")
            
            print(f"\n   Venue Distribution: Home={venue_count['home']}, Away={venue_count['away']}, Unknown={venue_count['unknown']}")
            
            if venue_count['home'] > 0 and venue_count['away'] > 0:
                print("   ✅ VENUE FILTER FEATURE DATA CONFIRMED: Both home and away samples found")
                return True
            else:
                print("   ⚠️ Limited venue variety in samples")
                return False
        else:
            print("   ❌ No recent samples found - venue filter feature cannot work")
            print(f"   Reasoning: {prediction.get('reasoning', 'No reasoning provided')[:200]}...")
            return False
    else:
        print(f"   ❌ Prediction failed: {prediction_response.status_code}")
        try:
            error = prediction_response.json()
            print(f"   Error: {error}")
        except:
            print(f"   Error: {prediction_response.text[:200]}")
        return False

if __name__ == "__main__":
    success = test_venue_feature()
    if success:
        print("\n🎉 VENUE FILTER FEATURE TEST PASSED")
    else:
        print("\n❌ VENUE FILTER FEATURE TEST FAILED")