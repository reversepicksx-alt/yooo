import requests
import json
import sys
from datetime import datetime

class ReversePicsAPITester:
    def __init__(self, base_url="https://props-ai-predict.preview.emergentagent.com"):
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
        """Test prediction endpoint with M. Salah as suggested"""
        # Use the specific test case mentioned in agent context
        prediction_data = {
            "leagueId": 39,
            "playerId": 306,
            "playerName": "M. Salah",
            "teamId": 40,
            "opponentId": 42,
            "opponentName": "Arsenal",
            "venue": "home",
            "propType": "pass_attempts",
            "line": 30.5
        }
        
        print(f"   Testing prediction for: {prediction_data['playerName']} vs {prediction_data['opponentName']}")
        print(f"   Note: This may take 30-60 seconds due to API data gathering...")
        success, response = self.run_test("AI Prediction", "POST", "/api/predict", 200, prediction_data, timeout=120)
        
        if success:
            # Test basic prediction fields
            if 'player' in response and 'projectedValue' in response:
                print(f"   Prediction: {response.get('projectedValue')} {response.get('propType')}")
                print(f"   Recommendation: {response.get('recommendation')}")
                print(f"   Confidence: {response.get('confidenceScore')}%")
                
                # Test NEW deep analysis fields (iteration 5 focus)
                new_fields = ['sharpSummary', 'matchupBreakdown', 'venueAnalysis', 'formTrend', 'floorCeiling']
                missing_fields = []
                for field in new_fields:
                    if field not in response:
                        missing_fields.append(field)
                    else:
                        field_content = response.get(field, '')
                        if isinstance(field_content, str) and len(field_content) > 0:
                            print(f"   ✅ {field}: {len(field_content)} characters")
                        else:
                            print(f"   ❌ {field}: Empty or invalid")
                            missing_fields.append(field)
                
                if missing_fields:
                    print(f"   ❌ Missing new analysis fields: {missing_fields}")
                else:
                    print(f"   ✅ All new deep analysis fields present")
                
                # Test reasoning field (should be detailed multi-paragraph)
                reasoning = response.get('reasoning', '')
                if isinstance(reasoning, str) and len(reasoning) > 500:
                    paragraph_count = reasoning.count('\n\n') + 1
                    print(f"   ✅ Reasoning: {len(reasoning)} characters, ~{paragraph_count} paragraphs")
                else:
                    print(f"   ❌ Reasoning too short or missing: {len(reasoning) if isinstance(reasoning, str) else 0} characters")
                
                # Test recentSamples (should have at least 15 with venue tags)
                recent_samples = response.get('recentSamples', [])
                if isinstance(recent_samples, list) and len(recent_samples) >= 15:
                    venue_tagged = sum(1 for sample in recent_samples if isinstance(sample, dict) and 'venue' in sample)
                    print(f"   ✅ Recent samples: {len(recent_samples)} total, {venue_tagged} with venue tags")
                    if venue_tagged < len(recent_samples):
                        print(f"   ⚠️  Some samples missing venue tags")
                else:
                    print(f"   ❌ Insufficient recent samples: {len(recent_samples) if isinstance(recent_samples, list) else 0} (need 15+)")
                
            else:
                print(f"   Response keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
        return success, response

    def test_football_api_status(self):
        """Test football API status endpoint"""
        return self.run_test("Football API Status", "GET", "/api/football/status", 200)

    def test_whop_auth_owner_email(self):
        """Test Whop auth with owner email (should auto-verify)"""
        auth_data = {
            "email": "josselj001@gmail.com"
        }
        success, response = self.run_test("Whop Auth - Owner Email", "POST", "/api/auth/verify-whop", 200, auth_data)
        if success:
            if response.get('verified') == True:
                print(f"   ✅ Owner email auto-verified: {response.get('access_type')}")
                print(f"   Session token received: {bool(response.get('session_token'))}")
            else:
                print(f"   ❌ Owner email not auto-verified: {response}")
        return success, response

    def test_whop_auth_lifetime_email(self):
        """Test Whop auth with lifetime subscriber email"""
        auth_data = {
            "email": "faron2allen@gmail.com"
        }
        success, response = self.run_test("Whop Auth - Lifetime Email", "POST", "/api/auth/verify-whop", 200, auth_data)
        if success:
            if response.get('requires_password_setup') == True:
                print(f"   ✅ Lifetime email requires password setup: {response.get('access_type')}")
            elif response.get('requires_password') == True:
                print(f"   ✅ Lifetime email requires password (already set up)")
            else:
                print(f"   ❌ Unexpected response for lifetime email: {response}")
        return success, response

    def test_whop_auth_random_email(self):
        """Test Whop auth with random email (should be rejected)"""
        auth_data = {
            "email": "random.user@example.com"
        }
        success, response = self.run_test("Whop Auth - Random Email", "POST", "/api/auth/verify-whop", 200, auth_data)
        if success:
            if response.get('verified') == False:
                print(f"   ✅ Random email correctly rejected: {response.get('message')}")
            else:
                print(f"   ❌ Random email should be rejected: {response}")
        return success, response

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
    
    print("\n🔐 WHOP AUTHENTICATION TESTS")
    print("-" * 30)
    
    # Test Whop authentication system
    tester.test_whop_auth_owner_email()
    tester.test_whop_auth_lifetime_email()
    tester.test_whop_auth_random_email()
    
    print("\n🤖 AI INTEGRATION TESTS")
    print("-" * 30)
    
    # Chat functionality
    tester.test_chat_start()
    tester.test_chat_message()
    
    # Natural query parsing
    tester.test_natural_query_parsing()
    
    # AI Prediction (most complex test)
    print("\n🎯 PREDICTION TESTS (NEW DEEP ANALYSIS)")
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