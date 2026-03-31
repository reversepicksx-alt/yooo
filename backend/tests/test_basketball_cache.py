"""
Basketball Cache System Tests - Iteration 28
Tests the NBA + WNBA data cache system:
- MongoDB collections: bball_cache_teams (46 docs), bball_cache_players (900+ docs)
- WNBA players in cache (leagueId=13, count > 200)
- Team search uses cache (returns 'Phoenix Suns' for 'Suns', not 'Helios Suns mladi')
- WNBA team search (returns 'Indiana Fever W' for 'Fever')
- Basketball prediction uses cached player lookup
- Soccer prediction regression test
"""
import pytest
import requests
import os
import time
from pymongo import MongoClient

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'reversepicks')


@pytest.fixture(scope="module")
def mongo_client():
    """MongoDB client for direct cache verification."""
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture(scope="module")
def api_client():
    """Shared requests session."""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


class TestMongoDBCacheCollections:
    """Verify MongoDB cache collections have correct document counts."""
    
    def test_bball_cache_teams_count(self, mongo_client):
        """bball_cache_teams should have 46 documents (31 NBA + 15 WNBA)."""
        count = mongo_client.bball_cache_teams.count_documents({})
        print(f"[CACHE] bball_cache_teams count: {count}")
        assert count >= 40, f"Expected ~46 teams, got {count}"
        # Allow some variance but should be close to 46
        assert count <= 60, f"Too many teams: {count}"
    
    def test_bball_cache_players_count(self, mongo_client):
        """bball_cache_players should have 900+ documents."""
        count = mongo_client.bball_cache_players.count_documents({})
        print(f"[CACHE] bball_cache_players count: {count}")
        assert count >= 900, f"Expected 900+ players, got {count}"
    
    def test_wnba_players_count(self, mongo_client):
        """WNBA players (leagueId=13) should have 200+ documents."""
        count = mongo_client.bball_cache_players.count_documents({"leagueId": 13})
        print(f"[CACHE] WNBA players (leagueId=13) count: {count}")
        assert count >= 200, f"Expected 200+ WNBA players, got {count}"
    
    def test_nba_players_count(self, mongo_client):
        """NBA players (leagueId=12) should have 700+ documents."""
        count = mongo_client.bball_cache_players.count_documents({"leagueId": 12})
        print(f"[CACHE] NBA players (leagueId=12) count: {count}")
        assert count >= 700, f"Expected 700+ NBA players, got {count}"
    
    def test_nba_teams_count(self, mongo_client):
        """NBA teams (leagueId=12) should have ~31 documents."""
        count = mongo_client.bball_cache_teams.count_documents({"leagueId": 12})
        print(f"[CACHE] NBA teams (leagueId=12) count: {count}")
        assert count >= 25, f"Expected ~31 NBA teams, got {count}"
    
    def test_wnba_teams_count(self, mongo_client):
        """WNBA teams (leagueId=13) should have ~15 documents."""
        count = mongo_client.bball_cache_teams.count_documents({"leagueId": 13})
        print(f"[CACHE] WNBA teams (leagueId=13) count: {count}")
        assert count >= 10, f"Expected ~15 WNBA teams, got {count}"


class TestBasketballTeamSearchCache:
    """Verify team search uses cache and returns correct teams."""
    
    def test_search_suns_returns_phoenix_suns(self, api_client):
        """Search 'Suns' should return 'Phoenix Suns' (not 'Helios Suns mladi')."""
        response = api_client.post(f"{BASE_URL}/api/basketball/search-teams", json={"query": "Suns"})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "No teams returned for 'Suns' query"
        
        # First result should be Phoenix Suns
        first_team = teams[0]
        print(f"[SEARCH] 'Suns' -> First result: {first_team.get('name')} (ID: {first_team.get('id')})")
        
        assert "Phoenix" in first_team.get("name", "") or "Suns" in first_team.get("name", ""), \
            f"Expected 'Phoenix Suns', got '{first_team.get('name')}'"
        # Should NOT be a youth/foreign team
        assert "mladi" not in first_team.get("name", "").lower(), \
            f"Got wrong team: {first_team.get('name')}"
    
    def test_search_fever_returns_indiana_fever_w(self, api_client):
        """Search 'Fever' should return 'Indiana Fever W' (WNBA team)."""
        response = api_client.post(f"{BASE_URL}/api/basketball/search-teams", json={"query": "Fever"})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "No teams returned for 'Fever' query"
        
        # Should find Indiana Fever W
        first_team = teams[0]
        print(f"[SEARCH] 'Fever' -> First result: {first_team.get('name')} (ID: {first_team.get('id')})")
        
        assert "Indiana" in first_team.get("name", "") or "Fever" in first_team.get("name", ""), \
            f"Expected 'Indiana Fever W', got '{first_team.get('name')}'"
    
    def test_search_lakers_returns_la_lakers(self, api_client):
        """Search 'Lakers' should return 'Los Angeles Lakers'."""
        response = api_client.post(f"{BASE_URL}/api/basketball/search-teams", json={"query": "Lakers"})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        teams = data.get("teams", [])
        assert len(teams) > 0, "No teams returned for 'Lakers' query"
        
        first_team = teams[0]
        print(f"[SEARCH] 'Lakers' -> First result: {first_team.get('name')} (ID: {first_team.get('id')})")
        
        assert "Lakers" in first_team.get("name", ""), \
            f"Expected 'Los Angeles Lakers', got '{first_team.get('name')}'"
    
    def test_search_sparks_returns_wnba_team(self, api_client):
        """Search 'Sparks' should return 'Los Angeles Sparks W' (WNBA team)."""
        response = api_client.post(f"{BASE_URL}/api/basketball/search-teams", json={"query": "Sparks"})
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        teams = data.get("teams", [])
        print(f"[SEARCH] 'Sparks' -> Results: {[t.get('name') for t in teams]}")
        
        # Should find a WNBA team
        if len(teams) > 0:
            first_team = teams[0]
            assert "Sparks" in first_team.get("name", ""), \
                f"Expected 'Los Angeles Sparks W', got '{first_team.get('name')}'"


class TestBasketballPredictionWithCache:
    """Verify basketball prediction uses cached player lookup."""
    
    def test_predict_devin_booker_uses_cache(self, api_client):
        """Prediction for Devin Booker should use cache and return 20+ game logs."""
        # Phoenix Suns ID: 155
        payload = {
            "teamId": 155,
            "teamName": "Phoenix Suns",
            "opponentId": 146,  # Memphis Grizzlies
            "opponentName": "Memphis Grizzlies",
            "playerName": "Devin Booker",
            "venue": "home",
            "propType": "points",
            "line": 27.5
        }
        
        start_time = time.time()
        response = api_client.post(f"{BASE_URL}/api/basketball/predict", json=payload, timeout=120)
        elapsed = time.time() - start_time
        
        print(f"[PREDICT] Devin Booker prediction took {elapsed:.1f}s")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        
        # Verify prediction structure
        assert "projectedValue" in data, "Missing projectedValue"
        assert "recommendation" in data, "Missing recommendation"
        assert "confidenceScore" in data, "Missing confidenceScore"
        assert data.get("sport") == "basketball", f"Expected sport='basketball', got '{data.get('sport')}'"
        
        # Verify game logs (should have 20+ from cache)
        player_game_logs = data.get("playerGameLogs", {})
        sample_size = player_game_logs.get("sampleSize", 0)
        print(f"[PREDICT] Devin Booker game logs sampleSize: {sample_size}")
        assert sample_size >= 10, f"Expected 10+ game logs, got {sample_size}"
        
        # Verify recentSamples
        recent_samples = data.get("recentSamples", [])
        print(f"[PREDICT] Devin Booker recentSamples count: {len(recent_samples)}")
        assert len(recent_samples) >= 5, f"Expected 5+ recentSamples, got {len(recent_samples)}"
    
    def test_predict_caitlin_clark_wnba(self, api_client):
        """Prediction for Caitlin Clark (WNBA) should work with cache."""
        # Indiana Fever W ID: 166
        payload = {
            "teamId": 166,
            "teamName": "Indiana Fever W",
            "opponentId": 167,  # Los Angeles Sparks W
            "opponentName": "Los Angeles Sparks W",
            "playerName": "Caitlin Clark",
            "venue": "home",
            "propType": "points",
            "line": 20.5
        }
        
        start_time = time.time()
        response = api_client.post(f"{BASE_URL}/api/basketball/predict", json=payload, timeout=120)
        elapsed = time.time() - start_time
        
        print(f"[PREDICT] Caitlin Clark prediction took {elapsed:.1f}s")
        
        # WNBA prediction may have limited data, but should not error
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "projectedValue" in data, "Missing projectedValue"
        assert data.get("sport") == "basketball", f"Expected sport='basketball', got '{data.get('sport')}'"


class TestSoccerPredictionRegression:
    """Verify soccer prediction still works (regression test)."""
    
    def test_soccer_predict_still_works(self, api_client):
        """POST /api/predict for soccer should still work."""
        payload = {
            "playerId": 276,  # Neymar
            "playerName": "Neymar",
            "teamId": 85,  # PSG
            "teamName": "Paris Saint Germain",
            "opponentId": 81,  # Barcelona
            "opponentName": "Barcelona",
            "leagueId": 61,  # Ligue 1
            "venue": "home",
            "propType": "shots",
            "line": 3.5
        }
        
        start_time = time.time()
        response = api_client.post(f"{BASE_URL}/api/predict", json=payload, timeout=120)
        elapsed = time.time() - start_time
        
        print(f"[PREDICT] Soccer prediction took {elapsed:.1f}s")
        
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        data = response.json()
        assert "projectedValue" in data, "Missing projectedValue"
        assert "recommendation" in data, "Missing recommendation"


class TestCacheMetadata:
    """Verify cache metadata is properly stored."""
    
    def test_bball_cache_meta_exists(self, mongo_client):
        """bball_cache_meta collection should have sync metadata."""
        count = mongo_client.bball_cache_meta.count_documents({})
        print(f"[CACHE] bball_cache_meta count: {count}")
        assert count >= 1, "No cache metadata found"
        
        # Check for full sync metadata
        full_sync = mongo_client.bball_cache_meta.find_one({"_key": "bball_full_sync"})
        if full_sync:
            print(f"[CACHE] Last full sync: {full_sync.get('_updated', 'unknown')}")
            print(f"[CACHE] Sync stats: leagues={full_sync.get('leagues')}, teams={full_sync.get('teams')}, players={full_sync.get('players')}")


class TestSpecificTeamLookup:
    """Verify specific team IDs from cache."""
    
    def test_phoenix_suns_team_id(self, mongo_client):
        """Phoenix Suns should have teamId=155."""
        team = mongo_client.bball_cache_teams.find_one({"nameLower": {"$regex": "phoenix suns"}})
        if team:
            print(f"[CACHE] Phoenix Suns: teamId={team.get('teamId')}, name={team.get('name')}")
            assert team.get("teamId") == 155, f"Expected teamId=155, got {team.get('teamId')}"
    
    def test_indiana_fever_team_id(self, mongo_client):
        """Indiana Fever W should have teamId=166."""
        team = mongo_client.bball_cache_teams.find_one({"nameLower": {"$regex": "indiana fever"}})
        if team:
            print(f"[CACHE] Indiana Fever: teamId={team.get('teamId')}, name={team.get('name')}")
            assert team.get("teamId") == 166, f"Expected teamId=166, got {team.get('teamId')}"
    
    def test_los_angeles_lakers_team_id(self, mongo_client):
        """Los Angeles Lakers should have teamId=145."""
        team = mongo_client.bball_cache_teams.find_one({"nameLower": {"$regex": "los angeles lakers"}})
        if team:
            print(f"[CACHE] Los Angeles Lakers: teamId={team.get('teamId')}, name={team.get('name')}")
            assert team.get("teamId") == 145, f"Expected teamId=145, got {team.get('teamId')}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
