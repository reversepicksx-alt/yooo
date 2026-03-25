import requests
import json
import sys
from datetime import datetime

class ReversePicsAPITester:
    def __init__(self, base_url="https://5e1e7943-4a41-4a29-a8e4-9314cbbb7644.preview.emergentagent.com"):
        self.base_url = base_url
        self.tests_run = 0
        self.tests_passed = 0
        self.chat_session_id = None

    def run_test(self, name, method, endpoint, expected_status, data=None, timeout=30):
        """Run a single API test"""
        url = f"{self.base_url}{endpoint}"
        headers = {'Content-Type': 'application/json'}

        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=timeout)

            print(f"   Status: {response.status_code}")
            
            success = response.status_code == expected_status
            if success:
                self.tests_passed += 1
                print(f"✅ Passed - Status: {response.status_code}")
                try:
                    response_data = response.json()
                    if isinstance(response_data, dict) and len(str(response_data)) < 500:
                        print(f"   Response: {response_data}")
                    elif isinstance(response_data, dict):
                        print(f"   Response keys: {list(response_data.keys())}")
                    return True, response_data
                except:
                    return True, {}
            else:
                print(f"❌ Failed - Expected {expected_status}, got {response.status_code}")
                try:
                    error_data = response.json()
                    print(f"   Error: {error_data}")
                except:
                    print(f"   Error: {response.text[:200]}")
                return False, {}

        except requests.exceptions.Timeout:
            print(f"❌ Failed - Request timeout after {timeout}s")
            return False, {}
        except Exception as e:
            print(f"❌ Failed - Error: {str(e)}")
            return False, {}

    def test_health_endpoint(self):
        """Test health check endpoint"""
        return self.run_test("Health Check", "GET", "/api/health", 200)

    def test_leagues_endpoint(self):
        """Test leagues endpoint"""
        success, response = self.run_test("Get Leagues", "GET", "/api/leagues", 200)
        if success and 'leagues' in response:
            leagues = response['leagues']
            print(f"   Found {len(leagues)} leagues")
            if len(leagues) > 0:
                print(f"   Sample league: {leagues[0]}")
        return success, response

    def test_premier_league_teams(self):
        """Test getting Premier League teams (league ID 39)"""
        success, response = self.run_test("Premier League Teams", "GET", "/api/leagues/39/teams", 200)
        if success and 'teams' in response:
            teams = response['teams']
            print(f"   Found {len(teams)} teams")
            if len(teams) > 0:
                print(f"   Sample team: {teams[0]}")
        return success, response

    def test_player_search(self):
        """Test player search functionality"""
        search_data = {
            "query": "Messi",
            "league_id": 39
        }
        success, response = self.run_test("Player Search", "POST", "/api/players/search", 200, search_data)
        if success and 'players' in response:
            players = response['players']
            print(f"   Found {len(players)} players")
            if len(players) > 0:
                print(f"   Sample player: {players[0]}")
        return success, response

    def test_chat_start(self):
        """Test chat session start"""
        success, response = self.run_test("Chat Start", "POST", "/api/chat/start", 200, {})
        if success and 'session_id' in response:
            self.chat_session_id = response['session_id']
            print(f"   Chat session ID: {self.chat_session_id}")
        return success, response

    def test_chat_message(self):
        """Test sending chat message"""
        if not self.chat_session_id:
            print("❌ Skipping chat message test - no session ID")
            return False, {}
        
        message_data = {
            "session_id": self.chat_session_id,
            "message": "What are the key factors for analyzing player performance?"
        }
        success, response = self.run_test("Chat Message", "POST", "/api/chat/message", 200, message_data, timeout=60)
        if success and 'response' in response:
            chat_response = response['response']
            print(f"   Chat response length: {len(chat_response)} characters")
            print(f"   Chat response preview: {chat_response[:100]}...")
        return success, response

    def test_natural_query_parsing(self):
        """Test natural query parsing"""
        query_data = {
            "query": "Lamine Yamal 52.5 passes vs Villarreal"
        }
        success, response = self.run_test("Natural Query Parse", "POST", "/api/parse-query", 200, query_data, timeout=45)
        if success:
            print(f"   Parsed query: {response}")
        return success, response

    def test_prediction_endpoint(self):
        """Test prediction endpoint with sample data"""
        # First get a player from search
        search_success, search_response = self.test_player_search()
        if not search_success or not search_response.get('players'):
            print("❌ Skipping prediction test - no players found")
            return False, {}
        
        player = search_response['players'][0]
        
        # Get teams for opponent
        teams_success, teams_response = self.test_premier_league_teams()
        if not teams_success or not teams_response.get('teams'):
            print("❌ Skipping prediction test - no teams found")
            return False, {}
        
        opponent = teams_response['teams'][0]
        
        prediction_data = {
            "leagueId": 39,
            "playerId": player['id'],
            "playerName": player['name'],
            "teamId": player.get('teamId', 0),
            "opponentId": opponent['id'],
            "opponentName": opponent['name'],
            "venue": "home",
            "propType": "pass_attempts",
            "line": 50.5
        }
        
        print(f"   Testing prediction for: {player['name']} vs {opponent['name']}")
        success, response = self.run_test("AI Prediction", "POST", "/api/predict", 200, prediction_data, timeout=120)
        if success:
            if 'player' in response and 'projectedValue' in response:
                print(f"   Prediction: {response.get('projectedValue')} {response.get('propType')}")
                print(f"   Recommendation: {response.get('recommendation')}")
                print(f"   Confidence: {response.get('confidenceScore')}%")
            else:
                print(f"   Response keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
        return success, response

    def test_football_api_status(self):
        """Test football API status endpoint"""
        return self.run_test("Football API Status", "GET", "/api/football/status", 200)

def main():
    print("🚀 Starting ReversePicks API Testing...")
    print("=" * 60)
    
    tester = ReversePicsAPITester()
    
    # Core API tests
    print("\n📋 CORE API TESTS")
    print("-" * 30)
    
    # Health check
    tester.test_health_endpoint()
    
    # Leagues
    tester.test_leagues_endpoint()
    
    # Teams
    tester.test_premier_league_teams()
    
    # Player search
    tester.test_player_search()
    
    # Football API status
    tester.test_football_api_status()
    
    print("\n🤖 AI INTEGRATION TESTS")
    print("-" * 30)
    
    # Chat functionality
    tester.test_chat_start()
    tester.test_chat_message()
    
    # Natural query parsing
    tester.test_natural_query_parsing()
    
    # AI Prediction (most complex test)
    print("\n🎯 PREDICTION TESTS")
    print("-" * 30)
    tester.test_prediction_endpoint()
    
    # Print results
    print("\n" + "=" * 60)
    print(f"📊 TEST RESULTS")
    print(f"Tests passed: {tester.tests_passed}/{tester.tests_run}")
    success_rate = (tester.tests_passed / tester.tests_run * 100) if tester.tests_run > 0 else 0
    print(f"Success rate: {success_rate:.1f}%")
    
    if success_rate >= 80:
        print("✅ Backend API testing PASSED")
        return 0
    else:
        print("❌ Backend API testing FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())