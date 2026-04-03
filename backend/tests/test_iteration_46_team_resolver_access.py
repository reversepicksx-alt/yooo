"""
Iteration 46 Tests: Team Resolver SCAN_ALIASES and Access Type Display

Tests for:
1. Team resolver: SCAN_ALIASES dict with 80+ common team abbreviations
2. Team resolver: _generate_aliases with COMMON_PREFIXES for city name extraction
3. Access check: Returns correct access type strings ('Owner', 'Lifetime', 'Premium (Square)', 'Premium (Whop)', None)
"""

import pytest
import requests
import os
import sys

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://props-ai-predict.preview.emergentagent.com').rstrip('/')


class TestTeamResolverScanAliases:
    """Test team resolver with SCAN_ALIASES for common abbreviations"""
    
    @pytest.mark.asyncio
    async def test_mgladbach_resolves_to_borussia_monchengladbach(self):
        """Test: find_team('mgladbach') should resolve to Borussia Monchengladbach"""
        from team_resolver import find_team
        result = await find_team('mgladbach')
        assert result is not None, "mgladbach should resolve to a team"
        assert 'monchengladbach' in result['teamName'].lower() or 'gladbach' in result['teamName'].lower(), \
            f"Expected Borussia Monchengladbach, got {result['teamName']}"
        print(f"✓ mgladbach -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_gladbach_resolves_to_borussia_monchengladbach(self):
        """Test: find_team('gladbach') should resolve to Borussia Monchengladbach"""
        from team_resolver import find_team
        result = await find_team('gladbach')
        assert result is not None, "gladbach should resolve to a team"
        assert 'monchengladbach' in result['teamName'].lower() or 'gladbach' in result['teamName'].lower(), \
            f"Expected Borussia Monchengladbach, got {result['teamName']}"
        print(f"✓ gladbach -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_dortmund_resolves_to_borussia_dortmund(self):
        """Test: find_team('dortmund') should resolve to Borussia Dortmund"""
        from team_resolver import find_team
        result = await find_team('dortmund')
        assert result is not None, "dortmund should resolve to a team"
        assert 'dortmund' in result['teamName'].lower(), \
            f"Expected Borussia Dortmund, got {result['teamName']}"
        print(f"✓ dortmund -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_leverkusen_resolves_to_bayer_leverkusen(self):
        """Test: find_team('leverkusen') should resolve to Bayer Leverkusen"""
        from team_resolver import find_team
        result = await find_team('leverkusen')
        assert result is not None, "leverkusen should resolve to a team"
        assert 'leverkusen' in result['teamName'].lower(), \
            f"Expected Bayer Leverkusen, got {result['teamName']}"
        print(f"✓ leverkusen -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_frankfurt_resolves_to_eintracht_frankfurt(self):
        """Test: find_team('frankfurt') should resolve to Eintracht Frankfurt"""
        from team_resolver import find_team
        result = await find_team('frankfurt')
        assert result is not None, "frankfurt should resolve to a team"
        assert 'frankfurt' in result['teamName'].lower(), \
            f"Expected Eintracht Frankfurt, got {result['teamName']}"
        print(f"✓ frankfurt -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_heidenheim_resolves_to_fc_heidenheim(self):
        """Test: find_team('heidenheim') should resolve to 1. FC Heidenheim"""
        from team_resolver import find_team
        result = await find_team('heidenheim')
        assert result is not None, "heidenheim should resolve to a team"
        assert 'heidenheim' in result['teamName'].lower(), \
            f"Expected 1. FC Heidenheim, got {result['teamName']}"
        print(f"✓ heidenheim -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_psg_resolves_to_paris_saint_germain(self):
        """Test: find_team('psg') should resolve to Paris Saint Germain"""
        from team_resolver import find_team
        result = await find_team('psg')
        assert result is not None, "psg should resolve to a team"
        assert 'paris' in result['teamName'].lower() or 'psg' in result['teamName'].lower(), \
            f"Expected Paris Saint Germain, got {result['teamName']}"
        print(f"✓ psg -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_lyon_resolves_to_lyon(self):
        """Test: find_team('lyon') should resolve to Lyon/Olympique Lyonnais"""
        from team_resolver import find_team
        result = await find_team('lyon')
        assert result is not None, "lyon should resolve to a team"
        assert 'lyon' in result['teamName'].lower(), \
            f"Expected Lyon/Olympique Lyonnais, got {result['teamName']}"
        print(f"✓ lyon -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_inter_resolves_to_inter(self):
        """Test: find_team('inter') should resolve to Inter (Milan)"""
        from team_resolver import find_team
        result = await find_team('inter')
        assert result is not None, "inter should resolve to a team"
        assert 'inter' in result['teamName'].lower(), \
            f"Expected Inter, got {result['teamName']}"
        print(f"✓ inter -> {result['teamName']} (ID: {result['teamId']})")
    
    @pytest.mark.asyncio
    async def test_atletico_resolves_to_atletico_madrid(self):
        """Test: find_team('atletico') should resolve to Atletico Madrid"""
        from team_resolver import find_team
        result = await find_team('atletico')
        assert result is not None, "atletico should resolve to a team"
        assert 'atletico' in result['teamName'].lower() or 'atlético' in result['teamName'].lower(), \
            f"Expected Atletico Madrid, got {result['teamName']}"
        print(f"✓ atletico -> {result['teamName']} (ID: {result['teamId']})")


class TestScanAliasesDict:
    """Test that SCAN_ALIASES dict contains expected mappings"""
    
    def test_scan_aliases_contains_german_teams(self):
        """Verify SCAN_ALIASES has German team abbreviations"""
        from team_resolver import SCAN_ALIASES
        
        german_aliases = ['mgladbach', 'gladbach', 'dortmund', 'leverkusen', 
                          'frankfurt', 'heidenheim', 'hoffenheim', 'freiburg',
                          'mainz', 'augsburg', 'wolfsburg', 'bremen', 'bochum', 'stuttgart']
        
        for alias in german_aliases:
            assert alias in SCAN_ALIASES, f"SCAN_ALIASES missing German team alias: {alias}"
        print(f"✓ All {len(german_aliases)} German team aliases present in SCAN_ALIASES")
    
    def test_scan_aliases_contains_french_teams(self):
        """Verify SCAN_ALIASES has French team abbreviations"""
        from team_resolver import SCAN_ALIASES
        
        french_aliases = ['psg', 'paris sg', 'lyon', 'marseille', 'monaco', 
                          'lille', 'lens', 'nice', 'rennes', 'strasbourg', 'nantes', 'brest']
        
        for alias in french_aliases:
            assert alias in SCAN_ALIASES, f"SCAN_ALIASES missing French team alias: {alias}"
        print(f"✓ All {len(french_aliases)} French team aliases present in SCAN_ALIASES")
    
    def test_scan_aliases_contains_italian_teams(self):
        """Verify SCAN_ALIASES has Italian team abbreviations"""
        from team_resolver import SCAN_ALIASES
        
        italian_aliases = ['inter', 'napoli', 'atalanta', 'lazio', 'fiorentina',
                           'roma', 'juventus', 'milan', 'torino', 'genoa', 
                           'udinese', 'bologna', 'empoli', 'lecce', 'verona', 'parma']
        
        for alias in italian_aliases:
            assert alias in SCAN_ALIASES, f"SCAN_ALIASES missing Italian team alias: {alias}"
        print(f"✓ All {len(italian_aliases)} Italian team aliases present in SCAN_ALIASES")
    
    def test_scan_aliases_contains_spanish_teams(self):
        """Verify SCAN_ALIASES has Spanish team abbreviations"""
        from team_resolver import SCAN_ALIASES
        
        spanish_aliases = ['atletico', 'betis', 'sociedad', 'bilbao', 
                           'getafe', 'osasuna', 'vallecano', 'celta']
        
        for alias in spanish_aliases:
            assert alias in SCAN_ALIASES, f"SCAN_ALIASES missing Spanish team alias: {alias}"
        print(f"✓ All {len(spanish_aliases)} Spanish team aliases present in SCAN_ALIASES")


class TestAccessTypeDisplay:
    """Test access type returns correct strings for different user types"""
    
    @pytest.mark.asyncio
    async def test_owner_email_returns_owner(self):
        """Test: josselj001@gmail.com returns 'Owner'"""
        from routes.auth import check_access
        result = await check_access('josselj001@gmail.com')
        assert result == 'Owner', f"Expected 'Owner', got '{result}'"
        print(f"✓ josselj001@gmail.com -> {result}")
    
    @pytest.mark.asyncio
    async def test_lifetime_user_its2famous_returns_lifetime(self):
        """Test: its2famous@gmail.com returns 'Lifetime'"""
        from routes.auth import check_access
        result = await check_access('its2famous@gmail.com')
        assert result == 'Lifetime', f"Expected 'Lifetime', got '{result}'"
        print(f"✓ its2famous@gmail.com -> {result}")
    
    @pytest.mark.asyncio
    async def test_lifetime_user_mendezvincent17_returns_lifetime(self):
        """Test: mendezvincent17@gmail.com returns 'Lifetime' (newly added)"""
        from routes.auth import check_access
        result = await check_access('mendezvincent17@gmail.com')
        assert result == 'Lifetime', f"Expected 'Lifetime', got '{result}'"
        print(f"✓ mendezvincent17@gmail.com -> {result}")
    
    @pytest.mark.asyncio
    async def test_unknown_email_returns_none(self):
        """Test: unknown@test.com returns None (no access)"""
        from routes.auth import check_access
        result = await check_access('unknown@test.com')
        assert result is None, f"Expected None, got '{result}'"
        print(f"✓ unknown@test.com -> {result}")


class TestAccessTypeViaAPI:
    """Test access type via API endpoints"""
    
    def test_verify_whop_owner_returns_owner_access_type(self):
        """Test: POST /api/auth/verify-whop with owner email returns access_type='Owner'"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "josselj001@gmail.com"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data.get('verified') == True, "Owner should be verified"
        assert data.get('access_type') == 'Owner', f"Expected access_type='Owner', got '{data.get('access_type')}'"
        print(f"✓ API verify-whop owner -> access_type={data.get('access_type')}")
    
    def test_verify_whop_lifetime_returns_lifetime_access_type(self):
        """Test: POST /api/auth/verify-whop with lifetime email returns access_type='Lifetime'"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "its2famous@gmail.com"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        # Lifetime users need to set password first, so they get requires_password_setup
        assert data.get('access_type') == 'Lifetime', f"Expected access_type='Lifetime', got '{data.get('access_type')}'"
        print(f"✓ API verify-whop lifetime -> access_type={data.get('access_type')}")
    
    def test_verify_whop_mendezvincent17_returns_lifetime(self):
        """Test: POST /api/auth/verify-whop with mendezvincent17@gmail.com returns 'Lifetime'"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "mendezvincent17@gmail.com"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data.get('access_type') == 'Lifetime', f"Expected access_type='Lifetime', got '{data.get('access_type')}'"
        print(f"✓ API verify-whop mendezvincent17 -> access_type={data.get('access_type')}")
    
    def test_verify_whop_unknown_returns_no_access(self):
        """Test: POST /api/auth/verify-whop with unknown email returns verified=False"""
        response = requests.post(
            f"{BASE_URL}/api/auth/verify-whop",
            json={"email": "unknown_test_user_12345@test.com"}
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data.get('verified') == False, "Unknown user should not be verified"
        print(f"✓ API verify-whop unknown -> verified={data.get('verified')}")


class TestLifetimeSubEmailsConfig:
    """Test LIFETIME_SUB_EMAILS configuration in config.py"""
    
    def test_mendezvincent17_in_lifetime_emails(self):
        """Verify mendezvincent17@gmail.com is in LIFETIME_SUB_EMAILS"""
        from config import LIFETIME_SUB_EMAILS
        assert 'mendezvincent17@gmail.com' in LIFETIME_SUB_EMAILS, \
            "mendezvincent17@gmail.com should be in LIFETIME_SUB_EMAILS"
        print(f"✓ mendezvincent17@gmail.com found in LIFETIME_SUB_EMAILS")
    
    def test_its2famous_in_lifetime_emails(self):
        """Verify its2famous@gmail.com is in LIFETIME_SUB_EMAILS"""
        from config import LIFETIME_SUB_EMAILS
        assert 'its2famous@gmail.com' in LIFETIME_SUB_EMAILS, \
            "its2famous@gmail.com should be in LIFETIME_SUB_EMAILS"
        print(f"✓ its2famous@gmail.com found in LIFETIME_SUB_EMAILS")
    
    def test_lifetime_emails_are_lowercase(self):
        """Verify all LIFETIME_SUB_EMAILS are lowercase"""
        from config import LIFETIME_SUB_EMAILS
        for email in LIFETIME_SUB_EMAILS:
            assert email == email.lower(), f"Email {email} should be lowercase"
        print(f"✓ All {len(LIFETIME_SUB_EMAILS)} LIFETIME_SUB_EMAILS are lowercase")


class TestSquareAccessType:
    """Test Square subscription access type display"""
    
    @pytest.mark.asyncio
    async def test_square_subscriber_returns_premium_square(self):
        """Test: Square subscriber returns 'Premium (Square)'"""
        # This test requires a known Square subscriber in the database
        # We'll test the logic by checking the check_access function behavior
        from routes.auth import check_access
        from config import db
        
        # First, check if lgutierrez787@gmail.com has a Square subscription
        square_sub = await db.square_subscriptions.find_one(
            {"email": "lgutierrez787@gmail.com", "status": {"$in": ["ACTIVE", "PENDING"]}},
            {"_id": 0}
        )
        
        if square_sub:
            result = await check_access('lgutierrez787@gmail.com')
            assert result == 'Premium (Square)', f"Expected 'Premium (Square)', got '{result}'"
            print(f"✓ lgutierrez787@gmail.com -> {result}")
        else:
            # If no Square subscription exists, skip this test
            pytest.skip("No active Square subscription found for lgutierrez787@gmail.com")


# Run tests with pytest
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
