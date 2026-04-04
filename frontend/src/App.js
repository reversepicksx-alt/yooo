import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Zap, ChevronRight, RefreshCw, ArrowLeft, Clock, Activity,
  Shield, Send, Loader2, Trash2, User, Search, Users, Edit3, HelpCircle, ChevronDown,
  TrendingUp, TrendingDown, BarChart3, ShieldAlert, Target, LogOut, Lock, Mail, Bell, RotateCcw,
  Camera, Upload, Check, X, Settings, Eye, EyeOff
} from 'lucide-react';
import {
  getTeamsByLeague, searchPlayers, predict, predictCombo, startChat, sendChatMessage,
  startTactical, sendTacticalMessage,
  checkApiStatus, SUPPORTED_LEAGUES,
  verifyWhop, authLogin, setPassword as apiSetPassword, resetPassword, verifySession, authLogout,
  getPickOfTheDay, savePick, listPicks, deletePick, correctPick, liveUpdatePicks,
  scanProp, reResolvePick, analyzeMiss, getMisses, basketballSearchTeams, basketballPredict,
  getAdminSettings, updateAdminSetting, testApiKey
} from './api';
import { toast, Toaster } from 'sonner';
import './App.css';
import { PROP_TYPES, BASKETBALL_PROP_TYPES, OWNER_EMAIL, getPropLabel } from './constants';
import { ProjectionCard } from './components/app/ProjectionCard';
import { LoginPage } from './components/app/LoginPage';
import { PickOfTheDayCard } from './components/app/PickOfTheDayCard';
import { Header } from './components/app/Header';
import { GuideTab } from './components/app/GuideTab';
import { ProfileTab } from './components/app/ProfileTab';
import { TrackingTab } from './components/app/TrackingTab';

export default function App() {
  const [auth, setAuth] = useState(null);
  const [authChecking, setAuthChecking] = useState(true);
  const [activeTab, setActiveTab] = useState('scan');
  const [activeSport, setActiveSport] = useState('soccer');
  const [trackingView, setTrackingView] = useState('live');

  const [wizardStep, setWizardStep] = useState(1);
  const [wizardData, setWizardData] = useState({});
  const [wizardError, setWizardError] = useState(null);
  const [searchMode, setSearchMode] = useState('wizard');

  const [teams, setTeams] = useState([]);
  const [isTeamsLoading, setIsTeamsLoading] = useState(false);
  const [wizardPlayers, setWizardPlayers] = useState([]);
  const [isPlayersLoading, setIsPlayersLoading] = useState(false);

  const [projection, setProjection] = useState(null);
  const [isProjecting, setIsProjecting] = useState(false);
  const [excludedSampleIndices, setExcludedSampleIndices] = useState([]);

  const [savedPicks, setSavedPicks] = useState([]);
  const savedPicksRef = useRef([]);
  const [selectedPick, setSelectedPick] = useState(null);
  const [liveData, setLiveData] = useState({});
  const [notifications, setNotifications] = useState([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [reanalyzingPick, setReanalyzingPick] = useState(null);
  const [correctingPick, setCorrectingPick] = useState(null); // pickId being corrected
  const [correctValue, setCorrectValue] = useState('');
  const [missAnalyses, setMissAnalyses] = useState({}); // { pickId: analysis }
  const [analyzingMiss, setAnalyzingMiss] = useState({}); // { pickId: true/false }
  const [profileNewPw, setProfileNewPw] = useState('');
  const [profileConfirmPw, setProfileConfirmPw] = useState('');
  const [profilePwLoading, setProfilePwLoading] = useState(false);

  // Admin settings state (owner only)
  const [adminSettings, setAdminSettings] = useState({});
  const [adminEditKey, setAdminEditKey] = useState(null);
  const [adminEditValue, setAdminEditValue] = useState('');
  const [adminKeyLoading, setAdminKeyLoading] = useState(false);
  const [adminTestResult, setAdminTestResult] = useState(null);
  const [adminShowKey, setAdminShowKey] = useState(false);

  // Combo mode state
  const [comboMode, setComboMode] = useState(false);
  const [comboPlayer2, setComboPlayer2] = useState(null); // {playerId, playerName, teamId, teamName}
  const [comboLine, setComboLine] = useState(0);
  const [comboProjection, setComboProjection] = useState(null);
  const [isComboProjecting, setIsComboProjecting] = useState(false);
  const [comboProgress, setComboProgress] = useState('');

  const [chatMessages, setChatMessages] = useState([]);
  const [chatInput, setChatInput] = useState('');
  const [isChatting, setIsChatting] = useState(false);
  const [chatSessionId, setChatSessionId] = useState(null);
  const [apiStatus, setApiStatus] = useState('checking');
  const [potd, setPotd] = useState(null);
  const [potdLoading, setPotdLoading] = useState(true);

  // Scan tab state
  const [scanImage, setScanImage] = useState(null); // base64 preview
  const [scanResults, setScanResults] = useState(null); // extracted picks array
  const [isScanning, setIsScanning] = useState(false);
  const [scanError, setScanError] = useState(null);
  const [scanPrediction, setScanPrediction] = useState({}); // { idx: projection result }
  const [scanPredictingIdx, setScanPredictingIdx] = useState({}); // { idx: true/false }
  const [scanExpandedIdx, setScanExpandedIdx] = useState(null); // which prediction is expanded for full review
  const [scanExcludedIndices, setScanExcludedIndices] = useState([]);
  const [scanVenueOverrides, setScanVenueOverrides] = useState({});
  const [scanEditMode, setScanEditMode] = useState({});
  const [scanEditValues, setScanEditValues] = useState({});
  const [scanResolving, setScanResolving] = useState({});
  const scanFileRef = useRef(null);

  const searchTimeout = useRef(null);
  const chatEndRef = useRef(null);

  // Reverse Tactical state
  const [tacticalMessages, setTacticalMessages] = useState([]);
  const [tacticalInput, setTacticalInput] = useState('');
  const [isTacticalSending, setIsTacticalSending] = useState(false);
  const [tacticalSessionId, setTacticalSessionId] = useState(null);
  const tacticalEndRef = useRef(null);
  const tacticalInputRef = useRef(null);
  const tacticalFileRef = useRef(null);

  // Auth check on mount — intentionally runs once
  // If checkout_token is in URL, skip session check and force LoginPage
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const hasCheckoutToken = params.has('checkout_token');

    if (hasCheckoutToken) {
      // Force clear stale session so LoginPage renders and handles the token
      localStorage.removeItem('rp_email');
      localStorage.removeItem('rp_token');
      localStorage.removeItem('rp_access');
      setAuth(null);
      setAuthChecking(false);
      return;
    }

    const checkAuth = async () => {
      const email = localStorage.getItem('rp_email');
      const token = localStorage.getItem('rp_token');
      const access = localStorage.getItem('rp_access');
      if (email && token) {
        try {
          const res = await verifySession(email, token);
          if (res.valid) {
            setAuth({ email, token, accessType: res.access_type || access });
          } else {
            localStorage.removeItem('rp_email');
            localStorage.removeItem('rp_token');
            localStorage.removeItem('rp_access');
          }
        } catch (err) {
          console.error('[AUTH] Session check failed:', err);
          localStorage.removeItem('rp_email');
          localStorage.removeItem('rp_token');
          localStorage.removeItem('rp_access');
        }
      }
      setAuthChecking(false);
    };
    checkAuth();
  }, []); // eslint-disable-line

  // Keep savedPicksRef in sync
  useEffect(() => { savedPicksRef.current = savedPicks; }, [savedPicks]);

  // Load picks from MongoDB on auth
  useEffect(() => {
    if (!auth) return;
    listPicks(auth.email, auth.token)
      .then(data => setSavedPicks(data.picks || []))
      .catch(err => console.error('[PICKS] Load error:', err));
    checkApiStatus().then(ok => setApiStatus(ok ? 'online' : 'offline')).catch(() => setApiStatus('offline'));
    getPickOfTheDay()
      .then(data => setPotd(data))
      .catch(() => setPotd(null))
      .finally(() => setPotdLoading(false));
  }, [auth]);

  // Poll live picks every 2 minutes for real-time stats
  const livePickCount = savedPicks.filter(p => p.status === 'live').length;
  useEffect(() => {
    if (!auth || livePickCount === 0) return;

    const fetchLiveUpdates = async () => {
      try {
        const result = await liveUpdatePicks(auth.email, auth.token);
        if (result.updates && result.updates.length > 0) {
          const newLiveData = {};
          const settledIds = [];
          for (const u of result.updates) {
            newLiveData[u.pickId] = u;
            if (u.matchStatus === 'final' && u.result) {
              settledIds.push(u.pickId);
              // Find the pick to get player name
              const pick = savedPicksRef.current.find(p => p.pickId === u.pickId);
              if (pick) {
                const propLabel = [...PROP_TYPES, ...BASKETBALL_PROP_TYPES].find(pt => pt.key === pick.propType)?.label || pick.propType;
                const isHit = u.result === 'hit';
                const isPush = u.result === 'push';
                const notif = {
                  id: `${u.pickId}-${Date.now()}`,
                  pickId: u.pickId,
                  playerName: pick.playerName,
                  propType: propLabel,
                  line: pick.line,
                  recommendation: pick.recommendation,
                  result: u.result,
                  actualValue: u.actualValue,
                  matchScore: u.matchScore,
                  timestamp: Date.now(),
                  read: false,
                };
                setNotifications(prev => [notif, ...prev].slice(0, 50));
                // Toast notification
                if (isHit) {
                  toast.success(`${pick.playerName} — ${pick.recommendation.toUpperCase()} ${pick.line} ${propLabel} HIT (Actual: ${u.actualValue})`, { duration: 8000 });
                } else if (isPush) {
                  toast(`${pick.playerName} — ${pick.recommendation.toUpperCase()} ${pick.line} ${propLabel} PUSH (Actual: ${u.actualValue})`, { duration: 8000 });
                } else {
                  toast.error(`${pick.playerName} — ${pick.recommendation.toUpperCase()} ${pick.line} ${propLabel} MISS (Actual: ${u.actualValue})`, { duration: 8000 });
                }
              }
            }
          }
          setLiveData(prev => ({ ...prev, ...newLiveData }));
          if (settledIds.length > 0) {
            const refreshed = await listPicks(auth.email, auth.token);
            setSavedPicks(refreshed.picks || []);
          }
        }
      } catch (err) {
        console.error('[LIVE UPDATE] Error fetching live data:', err);
      }
    };

    fetchLiveUpdates();
    const interval = setInterval(fetchLiveUpdates, 30 * 1000);
    return () => clearInterval(interval);
  }, [auth, livePickCount]); // eslint-disable-line

  useEffect(() => {
    if (chatEndRef.current) chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  const handleStartChat = useCallback(async () => {
    try {
      const data = await startChat();
      setChatSessionId(data.session_id);
      setChatMessages([{ id: `init-${Date.now()}`, role: 'model', text: data.message }]);
    } catch (err) {
      setChatMessages([{ id: `err-${Date.now()}`, role: 'model', text: 'Failed to connect. Please try again.' }]);
    }
  }, []);

  useEffect(() => {
    if (searchMode === 'chat' && !chatSessionId) handleStartChat();
  }, [searchMode, chatSessionId, handleStartChat]);

  const handleSendMessage = async () => {
    if (!chatInput.trim() || !chatSessionId) return;
    const msg = chatInput;
    setChatInput('');
    setChatMessages(prev => [...prev, { id: `user-${Date.now()}`, role: 'user', text: msg }]);
    setIsChatting(true);
    try {
      const data = await sendChatMessage(chatSessionId, msg);
      setChatMessages(prev => [...prev, { id: `resp-${Date.now()}`, role: 'model', text: data.response }]);
    } catch {
      setChatMessages(prev => [...prev, { id: `err-${Date.now()}`, role: 'model', text: 'Error connecting to tactical search. Please try again.' }]);
    } finally {
      setIsChatting(false);
    }
  };

  const handleLeagueSelect = async (leagueId) => {
    setWizardData({ ...wizardData, leagueId });
    setWizardStep(2);
    setIsTeamsLoading(true);
    try {
      const data = await getTeamsByLeague(leagueId);
      setTeams(data.teams || []);
    } catch {
      setTeams([]);
    } finally {
      setIsTeamsLoading(false);
    }
  };

  const handlePlayerSearch = (query) => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (query.length < 3) { setWizardPlayers([]); return; }
    searchTimeout.current = setTimeout(async () => {
      setIsPlayersLoading(true);
      setWizardError(null);
      try {
        const data = await searchPlayers(query, wizardData.leagueId);
        setWizardPlayers(data.players || []);
      } catch (err) {
        setWizardError(err.message === 'Failed to fetch' ? 'Network error — check your connection and try again.' : err.message || 'Search failed. Please try again.');
      } finally {
        setIsPlayersLoading(false);
      }
    }, 500);
  };

  const handlePlayerSelect = (player) => {
    setWizardData({ ...wizardData, playerId: player.id, playerName: player.name, teamId: player.teamId, teamName: player.teamName || '' });
    setWizardStep(3);
  };

  const handleOpponentSelect = (team) => {
    setWizardData({ ...wizardData, opponentId: team.id, opponentName: team.name });
    setWizardStep(4);
  };

  const runProjection = async (data) => {
    setIsProjecting(true);
    setWizardError(null);
    try {
      const result = await predict(data);
      if (!result || !result.player) throw new Error('AI model failed to generate a valid projection.');
      setProjection(result);
      setExcludedSampleIndices([]);
    } catch (err) {
      setWizardError(err.message || 'Projection failed.');
    } finally {
      setIsProjecting(false);
    }
  };

  const handlePlayer2Select = (player) => {
    setComboPlayer2({ playerId: player.id, playerName: player.name, teamId: player.teamId, teamName: player.teamName || '' });
    setWizardStep(8);
  };

  const runComboProjection = async () => {
    if (!comboPlayer2) return;
    setIsComboProjecting(true);
    setWizardError(null);
    setComboProgress('Running combo analysis...');
    try {
      const result = await predictCombo({
        leagueId: wizardData.leagueId,
        player1Id: wizardData.playerId,
        player1Name: wizardData.playerName,
        player1TeamId: wizardData.teamId,
        player2Id: comboPlayer2.playerId,
        player2Name: comboPlayer2.playerName,
        player2TeamId: comboPlayer2.teamId,
        opponentId: wizardData.opponentId,
        opponentName: wizardData.opponentName,
        venue: wizardData.venue,
        propType: wizardData.propType,
        combinedLine: comboLine,
      });

      if (!result?.player1?.player || !result?.player2?.player) {
        throw new Error('One or both predictions failed.');
      }

      setComboProjection(result);
    } catch (err) {
      setWizardError(err.message || 'Combo projection failed.');
    } finally {
      setIsComboProjecting(false);
    }
  };

  const resetCombo = () => {
    setComboMode(false);
    setComboPlayer2(null);
    setComboLine(0);
    setComboProjection(null);
    setIsComboProjecting(false);
    setWizardStep(1);
    setWizardData({});
    setWizardError(null);
  };

  const submitCorrection = async (pickId) => {
    const val = parseFloat(correctValue);
    if (isNaN(val) || val < 0) { toast.error('Enter a valid number'); return; }
    try {
      const result = await correctPick(auth.email, auth.token, pickId, val);
      toast.success(`Corrected → ${result.result.toUpperCase()}`);
      setSavedPicks(prev => prev.map(p => p.pickId === pickId ? { ...p, actualValue: val, result: result.result, correctedManually: true } : p));
      setCorrectingPick(null);
      setCorrectValue('');
    } catch (err) {
      toast.error(err.message || 'Correction failed');
    }
  };

  const handleAnalyzeMiss = async (pickId) => {
    if (!auth) return;
    setAnalyzingMiss(prev => ({ ...prev, [pickId]: true }));
    try {
      const result = await analyzeMiss(auth.email, auth.token, pickId);
      setMissAnalyses(prev => ({ ...prev, [pickId]: result.analysis }));
      toast.success('Miss analysis complete');
    } catch (err) {
      toast.error(err.message || 'Analysis failed');
    } finally {
      setAnalyzingMiss(prev => ({ ...prev, [pickId]: false }));
    }
  };

  // Load miss analyses when switching to missed tab
  React.useEffect(() => {
    if (trackingView === 'lost' && auth) {
      getMisses(auth.email, auth.token).then(data => {
        if (data?.misses) {
          const analyses = {};
          data.misses.forEach(m => {
            if (m.missAnalysis) analyses[m.pickId] = m.missAnalysis;
          });
          setMissAnalyses(prev => ({ ...prev, ...analyses }));
        }
      }).catch(err => console.error('[MISS ANALYSES] Load error:', err));
    }
  }, [trackingView, auth]);

  const savePickFn = async () => {
    if (!projection || !auth) return;
    const newPick = {
      ...projection,
      id: Math.random().toString(36).substring(2, 9),
      timestamp: Date.now(),
      status: 'live',
      result: 'pending',
      excludedSampleIndices,
      _request: {
        leagueId: wizardData.leagueId,
        teamId: wizardData.teamId || projection.player?.teamId,
        opponentId: wizardData.opponentId,
        venue: wizardData.venue || 'home',
      },
    };
    try {
      await savePick(auth.email, auth.token, newPick);
      const refreshed = await listPicks(auth.email, auth.token);
      setSavedPicks(refreshed.picks || []);
    } catch (err) {
      console.error('[SAVE PICK] Error:', err);
    }
    setProjection(null);
    setExcludedSampleIndices([]);
    setWizardStep(1);
    setWizardData({});
    setActiveTab('tracking');
  };

  const removePickFn = async (pickId, e) => {
    e.stopPropagation();
    if (!auth) return;
    try {
      await deletePick(auth.email, auth.token, pickId);
      setSavedPicks(prev => prev.filter(p => p.pickId !== pickId));
    } catch (err) {
      console.error('[DELETE PICK] Error:', err);
    }
  };

  const reanalyzePick = async (pick, e) => {
    e.stopPropagation();
    if (!auth || reanalyzingPick) return;
    setReanalyzingPick(pick.pickId);
    try {
      const result = await predict({
        playerId: pick.playerId,
        playerName: pick.playerName,
        teamId: pick.teamId,
        opponentId: pick.opponentId,
        opponentName: pick.opponentName,
        leagueId: pick.leagueId,
        propType: pick.propType,
        line: pick.line,
        matchDate: new Date().toISOString().split('T')[0],
        venue: pick.venue || 'home',
      }, auth.token);
      // Save updated pick with new projection data
      const updatedPick = {
        ...result,
        id: pick.pickId,
        player: result.player || { id: pick.playerId, name: pick.playerName, team: pick.teamName },
        opponent: pick.opponentName,
        timestamp: Date.now(),
        status: 'live',
        result: 'pending',
        _request: {
          leagueId: pick.leagueId,
          teamId: pick.teamId,
          opponentId: pick.opponentId,
          venue: pick.venue || 'home',
        },
      };
      await savePick(auth.email, auth.token, updatedPick);
      const refreshed = await listPicks(auth.email, auth.token);
      setSavedPicks(refreshed.picks || []);
      toast.success(`Re-analyzed ${pick.playerName} — ${result.recommendation?.toUpperCase()} ${pick.line} (Proj: ${result.projectedValue}, Conf: ${result.confidenceScore}%)`);
    } catch {
      toast.error('Re-analysis failed. Try again.');
    } finally {
      setReanalyzingPick(null);
    }
  };

  // =============================================
  // SCAN TAB — Image Upload & Processing
  // =============================================
  const handleScanUpload = async (file) => {
    if (!file) return;
    setScanError(null);
    setScanResults(null);
    setScanPrediction({});
    setScanPredictingIdx({});
    setScanVenueOverrides({});

    // Convert to base64
    const reader = new FileReader();
    reader.onload = async (e) => {
      const base64Full = e.target.result;
      setScanImage(base64Full);

      // Extract just the base64 data (remove data:image/...;base64, prefix)
      const base64Data = base64Full.split(',')[1];

      setIsScanning(true);
      try {
        const result = await scanProp(base64Data, activeSport);
        if (result.picks && result.picks.length > 0) {
          setScanResults(result.picks);
          toast.success(`Found ${result.picks.length} prop${result.picks.length > 1 ? 's' : ''} in image`);
        } else {
          setScanError('No player props detected in this image. Try a clearer screenshot.');
        }
      } catch (err) {
        setScanError(err.message || 'Failed to scan image');
        toast.error('Scan failed — try a different screenshot');
      } finally {
        setIsScanning(false);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleScanPredict = async (pickData, idx) => {
    const isCombo = pickData.extracted?.isCombo;

    if (isCombo) {
      const rp = pickData.resolvedPlayers || [];
      if (!rp[0] || !rp[1]) {
        toast.error('Could not match both players — cannot run combo prediction');
        return;
      }
      setScanPredictingIdx(prev => ({ ...prev, [idx]: true }));
      try {
        const result = await predictCombo({
          leagueId: pickData.extracted.leagueId || 39,
          player1Id: rp[0].playerId,
          player1Name: rp[0].playerName,
          player1TeamId: rp[0].teamId,
          player2Id: rp[1].playerId,
          player2Name: rp[1].playerName,
          player2TeamId: rp[1].teamId,
          opponentId: rp[1].teamId,
          opponentName: rp[1].teamName || pickData.extracted?.players?.[1]?.team || 'TBD',
          venue: 'home',
          propType: pickData.extracted.propType,
          combinedLine: pickData.extracted.line,
        });
        if (!result?.player1?.player || !result?.player2?.player) {
          throw new Error('One or both predictions failed.');
        }
        setScanPrediction(prev => ({ ...prev, [idx]: { ...result, _isCombo: true, _comboLine: pickData.extracted.line } }));
        toast.success('Analysis complete!');
      } catch (err) {
        toast.error(err.message || 'Combo prediction failed');
      } finally {
        setScanPredictingIdx(prev => ({ ...prev, [idx]: false }));
      }
    } else {
      const isBasketballPick = pickData.sport === 'basketball' || pickData.extracted?.sport === 'basketball';

      if (isBasketballPick) {
        setScanPredictingIdx(prev => ({ ...prev, [idx]: true }));
        try {
          const teamId = pickData.resolved?.teamId || 0;
          const teamName = pickData.resolved?.teamName || pickData.extracted?.playerTeam || 'Unknown';
          const opponentId = pickData.resolvedOpponent?.teamId || 0;
          const opponentName = pickData.resolvedOpponent?.teamName || pickData.extracted?.opponentName || 'Unknown';
          const venue = scanVenueOverrides[idx] || pickData.extracted?.venue || 'home';
          const result = await basketballPredict({
            teamId, teamName, opponentId, opponentName,
            playerName: pickData.extracted?.playerName || 'Unknown',
            venue,
            propType: pickData.extracted?.propType || 'points',
            line: pickData.extracted?.line || 0,
          });
          setScanPrediction(prev => ({ ...prev, [idx]: result }));
          toast.success('Basketball analysis complete!');
        } catch (err) {
          toast.error(err.message || 'Basketball prediction failed');
        } finally {
          setScanPredictingIdx(prev => ({ ...prev, [idx]: false }));
        }
      } else {
      // SOCCER prediction flow
      const resolved = pickData.resolved;
      setScanPredictingIdx(prev => ({ ...prev, [idx]: true }));
      try {
        const opponentId = pickData.resolvedOpponent?.teamId || 0;
        const opponentName = pickData.resolvedOpponent?.teamName || pickData.extracted.opponentName || 'Unknown';
        const venue = scanVenueOverrides[idx] || pickData.extracted.venue || 'home';
        const result = await predict({
          playerId: resolved?.playerId || 0,
          playerName: resolved?.playerName || pickData.extracted?.playerName || 'Unknown',
          teamId: resolved?.teamId || 0,
          teamName: resolved?.teamName || pickData.extracted?.playerTeam || '',
          opponentId: opponentId,
          opponentName: opponentName,
          leagueId: pickData.extracted.leagueId || 39,
          venue: venue,
          propType: pickData.extracted.propType,
          line: pickData.extracted.line,
          positionOverride: pickData.extracted.position || '',
          roleOverride: pickData.extracted.role || '',
        });
        setScanPrediction(prev => ({ ...prev, [idx]: result }));
        toast.success('Analysis complete!');
      } catch (err) {
        toast.error(err.message || 'Prediction failed');
      } finally {
        setScanPredictingIdx(prev => ({ ...prev, [idx]: false }));
      }
      }
    }
  };

  // Predict All — run predictions for all detected props sequentially
  const handlePredictAll = async () => {
    if (!scanResults || scanResults.length <= 1) return;
    for (let i = 0; i < scanResults.length; i++) {
      if (scanExcludedIndices.includes(i)) continue;
      if (scanPrediction[i]) continue; // already predicted
      await handleScanPredict(scanResults[i], i);
    }
  };

  const scanSavePickFn = async (idx) => {
    const pred = scanPrediction[idx];
    if (!pred || !auth) return;
    const newPick = {
      ...pred,
      id: Math.random().toString(36).substring(2, 9),
      timestamp: Date.now(),
      status: 'live',
      result: 'pending',
      sport: pred.sport || activeSport || 'soccer',
      excludedSampleIndices: scanExcludedIndices,
      _request: pred._request || {},
    };
    try {
      await savePick(auth.email, auth.token, newPick);
      const refreshed = await listPicks(auth.email, auth.token);
      setSavedPicks(refreshed.picks || []);
      toast.success('Saved to Tracking!');
    } catch (err) {
      toast.error('Failed to save pick');
    }
  };

  const resetScan = () => {
    setScanImage(null);
    setScanResults(null);
    setScanError(null);
    setScanPrediction({});
    setScanPredictingIdx({});
    setScanVenueOverrides({});
    setScanEditMode({});
    setScanEditValues({});
    setScanResolving({});
    if (scanFileRef.current) scanFileRef.current.value = '';
  };

  const handleScanEdit = (idx, pick) => {
    const ext = pick.extracted;
    const res = pick.resolved;
    setScanEditMode(prev => ({ ...prev, [idx]: true }));
    setScanEditValues(prev => ({
      ...prev,
      [idx]: {
        playerName: res?.playerName || ext.playerName || '',
        playerTeam: res?.teamName || ext.playerTeam || '',
        opponentName: ext.opponentName || '',
        position: ext.position || '',
        role: ext.role || '',
      }
    }));
  };

  const handleScanEditCancel = (idx) => {
    setScanEditMode(prev => ({ ...prev, [idx]: false }));
  };

  const handleScanEditConfirm = async (idx) => {
    const vals = scanEditValues[idx];
    if (!vals?.playerName || !vals?.playerTeam) {
      toast.error('Player name and team are required');
      return;
    }
    setScanResolving(prev => ({ ...prev, [idx]: true }));
    try {
      const sport = scanResults[idx]?.sport || scanResults[idx]?.extracted?.sport || 'soccer';
      const result = await reResolvePick(vals.playerName, vals.playerTeam, vals.opponentName, sport);
      setScanResults(prev => {
        const updated = [...prev];
        const pick = { ...updated[idx] };
        pick.resolved = result.resolved;
        pick.resolvedOpponent = result.resolvedOpponent;
        pick.extracted = {
          ...pick.extracted,
          playerName: vals.playerName,
          playerTeam: vals.playerTeam,
          opponentName: vals.opponentName,
          leagueId: result.leagueId || pick.extracted.leagueId,
          league: result.leagueName || pick.extracted.league,
          position: vals.position || result.position?.position || '',
          role: vals.role || result.position?.role || '',
        };
        updated[idx] = pick;
        return updated;
      });
      setScanEditMode(prev => ({ ...prev, [idx]: false }));
      toast.success(result.resolved ? 'Player re-resolved successfully' : 'Player not found in database — you can still try predicting');
    } catch (e) {
      toast.error(e.message || 'Re-resolve failed');
    } finally {
      setScanResolving(prev => ({ ...prev, [idx]: false }));
    }
  };

  // ── Reverse Tactical ──
  const initTactical = useCallback(async () => {
    try {
      const res = await startTactical();
      setTacticalSessionId(res.session_id);
      setTacticalMessages([{ id: `init-${Date.now()}`, role: 'assistant', content: res.message }]);
    } catch (e) {
      setTacticalMessages([{ id: `err-${Date.now()}`, role: 'assistant', content: 'Failed to initialize tactical session. Please try again.' }]);
    }
  }, []);

  useEffect(() => {
    if (activeTab === 'tactical' && !tacticalSessionId) {
      initTactical();
    }
  }, [activeTab, tacticalSessionId, initTactical]);

  useEffect(() => {
    if (tacticalEndRef.current) {
      tacticalEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [tacticalMessages]);

  const sendTactical = async (imageBase64 = null) => {
    const msg = tacticalInput.trim();
    if (!msg && !imageBase64) return;
    if (isTacticalSending) return;
    setTacticalInput('');
    setTacticalMessages(prev => [...prev, {
      id: `user-${Date.now()}`,
      role: 'user',
      content: msg || (imageBase64 ? 'Analyze this prop screenshot' : ''),
      hasImage: !!imageBase64,
    }]);
    setIsTacticalSending(true);
    try {
      const res = await sendTacticalMessage(tacticalSessionId, msg, imageBase64);
      setTacticalMessages(prev => [...prev, {
        id: `resp-${Date.now()}`,
        role: 'assistant',
        content: res.response,
        scanEntries: res.scanEntries,
      }]);
    } catch (e) {
      setTacticalMessages(prev => [...prev, { id: `err-${Date.now()}`, role: 'assistant', content: `Error: ${e.message}` }]);
    } finally {
      setIsTacticalSending(false);
      setTimeout(() => tacticalInputRef.current?.focus(), 100);
    }
  };

  const handleTacticalImage = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = reader.result.split(',')[1];
      sendTactical(base64);
    };
    reader.readAsDataURL(file);
  };

  const resetTactical = () => {
    setTacticalSessionId(null);
    setTacticalMessages([]);
    setTacticalInput('');
    initTactical();
  };

  const handleProfilePasswordReset = async () => {
    if (profileNewPw.length < 6) { toast.error('Password must be at least 6 characters'); return; }
    if (profileNewPw !== profileConfirmPw) { toast.error('Passwords do not match'); return; }
    setProfilePwLoading(true);
    try {
      const res = await resetPassword(auth.email, profileNewPw);
      if (res.verified) {
        localStorage.setItem('rp_token', res.session_token);
        setAuth(prev => ({ ...prev, token: res.session_token }));
        toast.success('Password updated successfully');
        setProfileNewPw('');
        setProfileConfirmPw('');
      }
    } catch (err) {
      toast.error(err.message || 'Failed to reset password');
    } finally {
      setProfilePwLoading(false);
    }
  };

  const handleLogout = async () => {
    if (auth) {
      try { await authLogout(auth.email, auth.token); } catch (err) { console.error('[LOGOUT] Error:', err); }
    }
    localStorage.removeItem('rp_email');
    localStorage.removeItem('rp_token');
    localStorage.removeItem('rp_access');
    setAuth(null);
  };

  // Admin: load current settings when profile tab opens
  const isOwner = auth?.accessType === 'Owner';
  useEffect(() => {
    if (!isOwner || activeTab !== 'profile') return;
    getAdminSettings(auth.email, auth.token)
      .then(res => setAdminSettings(res.settings || {}))
      .catch(err => console.error('[ADMIN SETTINGS] Load error:', err));
  }, [isOwner, activeTab, auth?.email, auth?.token]);

  const handleTestApiKey = async () => {
    if (!adminEditValue.trim()) { toast.error('Enter a key to test'); return; }
    setAdminKeyLoading(true);
    setAdminTestResult(null);
    try {
      const res = await testApiKey(auth.email, auth.token, adminEditValue.trim());
      setAdminTestResult(res);
      if (res.valid) toast.success(`Key valid: ${res.plan} plan (${res.account})`);
      else toast.error(`Key invalid: ${res.error || 'Unknown error'}`);
    } catch (err) {
      toast.error(err.message);
    } finally {
      setAdminKeyLoading(false);
    }
  };

  const handleSaveAdminSetting = async (key) => {
    const val = adminEditValue.trim();
    if (!val) { toast.error('Enter a value'); return; }
    setAdminKeyLoading(true);
    try {
      await updateAdminSetting(auth.email, auth.token, key, val);
      toast.success(`${key} updated — live immediately`);
      setAdminEditKey(null);
      setAdminEditValue('');
      setAdminTestResult(null);
      const res = await getAdminSettings(auth.email, auth.token);
      setAdminSettings(res.settings || {});
    } catch (err) {
      toast.error(err.message);
    } finally {
      setAdminKeyLoading(false);
    }
  };

  const leaguesByType = (type) => SUPPORTED_LEAGUES.filter(l => l.type === type);

  // Auth loading state
  if (authChecking) {
    return (
      <div className="app" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <div className="loading-wrap">
          <div className="spinner-ring"><Zap className="inner-icon" style={{ width: 28, height: 28 }} /></div>
          <div className="loading-title">Loading...</div>
        </div>
      </div>
    );
  }

  // Show login if not authenticated
  if (!auth) {
    return <LoginPage onAuth={setAuth} />;
  }

  return (
    <div className="app">
      {/* Header */}
      <Header
        activeSport={activeSport} setActiveSport={setActiveSport} apiStatus={apiStatus}
        notifications={notifications} showNotifications={showNotifications}
        setShowNotifications={setShowNotifications} setNotifications={setNotifications}
        setActiveTab={setActiveTab} setTrackingView={setTrackingView}
        setScanPrediction={setScanPrediction} setScanResults={setScanResults}
        handleLogout={handleLogout}
      />

      {/* Main */}
      <main className="main-content">
        {/* PREDICT TAB */}
        {activeTab === 'predict' && (
          <div className="animate-fade-in space-y-6">
            {!projection && !isProjecting && !comboProjection && !isComboProjecting && (
              <>
                {/* Pick of the Day */}
                {potdLoading ? (
                  <div className="potd-skeleton" data-testid="potd-loading">
                    <div className="potd-skeleton-line wide" />
                    <div className="potd-skeleton-line" />
                    <div className="potd-skeleton-line narrow" />
                  </div>
                ) : potd?.available ? (
                  <PickOfTheDayCard
                    potd={potd}
                    onUse={() => {
                      const p = potd.pick;
                      const league = SUPPORTED_LEAGUES.find(l => l.id === p.leagueId) || SUPPORTED_LEAGUES[0];
                      setWizardData({
                        leagueId: league.id,
                        playerName: p.playerName,
                        opponentName: p.opponentName,
                        propType: p.propType,
                        line: p.suggestedLine,
                        venue: 'home',
                      });
                    }}
                  />
                ) : null}

                <div className="flex justify-between items-center">
                  <div>
                    <h2 className="section-title" data-testid="wizard-title">AI Wizard</h2>
                    <p className="section-subtitle">
                      {searchMode === 'wizard' ? `Step ${wizardStep} of ${comboMode ? 8 : 6}` : 'Tactical Search'}
                    </p>
                  </div>
                  {searchMode === 'wizard' && wizardStep > 1 && (
                    <button className="back-btn" onClick={() => {
                      if (wizardStep === 7 && comboMode) {
                        setComboMode(false);
                        setWizardStep(6);
                      } else {
                        setWizardStep(wizardStep - 1);
                      }
                    }} data-testid="wizard-back-btn">
                      <ArrowLeft /> Back
                    </button>
                  )}
                </div>

                {(searchMode === 'chat' || wizardStep === 1) && (
                  <div className="tab-switcher">
                    <button className={`tab-btn ${searchMode === 'wizard' ? 'active' : ''}`}
                      onClick={() => { setSearchMode('wizard'); setWizardStep(1); setWizardData({}); setWizardError(null); }}
                      data-testid="step-by-step-tab">Step-by-Step</button>
                    <button className={`tab-btn ${searchMode === 'chat' ? 'active' : ''}`}
                      onClick={() => { setSearchMode('chat'); }}
                      data-testid="tactical-uplink-tab">Tactical Search</button>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep > 1 && (
                  <div className="wizard-breadcrumb" data-testid="wizard-breadcrumb">
                    <div className="breadcrumb-steps">
                      {(comboMode
                        ? ['League', 'Player 1', 'Opponent', 'Venue', 'Prop', 'Line', 'Player 2', 'Combo']
                        : ['League', 'Player', 'Opponent', 'Venue', 'Prop', 'Line']
                      ).map((label, i) => {
                        const step = i + 1;
                        const isActive = wizardStep === step;
                        const isDone = wizardStep > step;
                        return (
                          <div key={label} className={`breadcrumb-step ${isActive ? 'active' : ''} ${isDone ? 'done' : ''}`}>
                            <div className="breadcrumb-dot">{isDone ? '\u2713' : step}</div>
                            <span className="breadcrumb-label">{label}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {searchMode === 'chat' && (
                  <div className="chat-container-inline" data-testid="tactical-uplink-inline">
                    <div className="chat-header">
                      <div>
                        <h3 className="chat-title" style={{ fontSize: 18 }}>Tactical Search</h3>
                        <p className="chat-subtitle">AI Strategic Analyst</p>
                      </div>
                      <button className="icon-btn" onClick={handleStartChat} data-testid="chat-reset-btn">
                        <RefreshCw />
                      </button>
                    </div>

                    <div className="chat-messages" data-testid="chat-messages" style={{ minHeight: 300, maxHeight: 450 }}>
                      {chatMessages.map((msg) => (
                        <div key={msg.id} className={`chat-msg ${msg.role}`} data-testid={`chat-msg-${msg.id}`}>
                          {msg.text}
                        </div>
                      ))}
                      {isChatting && (
                        <div className="chat-msg model">
                          <Loader2 className="animate-spin" style={{ width: 16, height: 16, color: 'var(--accent)' }} />
                        </div>
                      )}
                      <div ref={chatEndRef} />
                    </div>

                    <div className="chat-input-wrap">
                      <input className="chat-input" type="text" value={chatInput}
                        onChange={e => setChatInput(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && handleSendMessage()}
                        placeholder="Ask for tactical insights..."
                        data-testid="chat-input" />
                      <button className="chat-send-btn" onClick={handleSendMessage}
                        disabled={isChatting || !chatInput.trim()} data-testid="chat-send-btn">
                        <Send />
                      </button>
                    </div>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 1 && (
                  <div className="space-y-6" data-testid="league-list">
                    {['Domestic', 'International Club', 'International Team'].map(type => {
                      const leagues = leaguesByType(type);
                      if (!leagues.length) return null;
                      return (
                        <div key={type} className="space-y-3">
                          <div className="category-label">{type}</div>
                          <div className="space-y-2">
                            {leagues.map(league => (
                              <div key={league.id} className="card card-clickable" onClick={() => handleLeagueSelect(league.id)}
                                data-testid={`league-${league.id}`}>
                                <div className="league-item">
                                  <span className="name">{league.name}</span>
                                  <ChevronRight />
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 2 && (
                  <div className="space-y-4" data-testid="player-search-step">
                    {wizardError && <div className="error-box"><ShieldAlert /><p>{wizardError}</p></div>}
                    <div className="search-input-wrap">
                      <Search className="search-icon" />
                      <input className="search-input" type="text" placeholder="Search player name..."
                        onChange={e => handlePlayerSearch(e.target.value)} data-testid="player-search-input" />
                      {isPlayersLoading && <RefreshCw className="animate-spin" style={{ position: 'absolute', right: 14, top: '50%', transform: 'translateY(-50%)', width: 16, height: 16, color: 'var(--accent)' }} />}
                    </div>
                    <div className="space-y-2" style={{ maxHeight: 400, overflowY: 'auto' }}>
                      {wizardPlayers.map(player => (
                        <div key={player.id} className="card card-clickable" onClick={() => handlePlayerSelect(player)}
                          data-testid={`player-${player.id}`}>
                          <div className="player-item">
                            <div className="player-avatar"><User /></div>
                            <div style={{ flex: 1 }}>
                              <div className="player-name">{player.name}</div>
                              <div className="player-team">
                                {player.nationality && <span className="player-nationality">{player.nationality}</span>}
                                {player.nationality && player.teamName ? ' · ' : ''}
                                {player.teamName || 'Free Agent'}
                              </div>
                            </div>
                          </div>
                        </div>
                      ))}
                      {!wizardPlayers.length && !isPlayersLoading && (
                        <div className="text-center" style={{ padding: '32px 0', color: 'var(--text-secondary)', fontSize: 13 }}>
                          Type at least 3 characters to search players.
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 3 && (
                  <div className="space-y-4" data-testid="opponent-select-step">
                    <div className="stat-label">Select Opponent</div>
                    {isTeamsLoading ? (
                      <div className="loading-wrap"><div className="spinner-ring" /></div>
                    ) : teams.length > 0 ? (
                      <div className="space-y-2" style={{ maxHeight: 480, overflowY: 'auto' }}>
                        {teams.map(team => (
                          <div key={team.id} className="card card-clickable" onClick={() => handleOpponentSelect(team)}
                            data-testid={`team-${team.id}`}>
                            <div className="league-item">
                              <span className="name">{team.name}</span>
                              <ChevronRight />
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="text-center" style={{ padding: '32px 0', color: 'var(--text-secondary)', fontSize: 13 }}>
                        No teams found. <button className="btn-secondary mt-4" onClick={() => setWizardStep(1)}>Go to League Selection</button>
                      </div>
                    )}
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 4 && (
                  <div className="venue-grid" data-testid="venue-select-step">
                    <button className="venue-btn" onClick={() => { setWizardData({ ...wizardData, venue: 'home' }); setWizardStep(5); }}
                      data-testid="venue-home-btn">
                      <span className="label">Home</span>
                      <span className="sub">Venue</span>
                    </button>
                    <button className="venue-btn" onClick={() => { setWizardData({ ...wizardData, venue: 'away' }); setWizardStep(5); }}
                      data-testid="venue-away-btn">
                      <span className="label">Away</span>
                      <span className="sub">Venue</span>
                    </button>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 5 && (
                  <div className="space-y-2" data-testid="prop-type-step">
                    <div className="stat-label" style={{ marginBottom: 8 }}>Select Prop Type</div>
                    {PROP_TYPES.map(prop => (
                      <div key={prop.key} className="card card-clickable" onClick={() => { setWizardData({ ...wizardData, propType: prop.key }); setWizardStep(6); }}
                        data-testid={`prop-${prop.key}`}>
                        <div className="prop-item">
                          <div>
                            <span className="name">{prop.label}</span>
                            <span style={{ display: 'block', fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{prop.desc}</span>
                          </div>
                          <ChevronRight />
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 6 && (
                  <div className="space-y-6" data-testid="line-set-step">
                    <div className="line-setter">
                      <div className="line-setter-label">Set Prop Line</div>
                      <div className="line-setter-row">
                        <button className="line-btn" onClick={() => setWizardData({ ...wizardData, line: Math.max(0, (wizardData.line || 0) - 0.5) })}
                          data-testid="line-decrease-btn">-</button>
                        <input className="line-input" type="number" step="0.5" placeholder="0.0"
                          value={wizardData.line === 0 ? '' : wizardData.line || ''}
                          onChange={e => setWizardData({ ...wizardData, line: parseFloat(e.target.value) || 0 })}
                          data-testid="line-input" />
                        <button className="line-btn" onClick={() => setWizardData({ ...wizardData, line: (wizardData.line || 0) + 0.5 })}
                          data-testid="line-increase-btn">+</button>
                      </div>
                    </div>
                    {wizardError && <div className="error-box"><ShieldAlert /><p>{wizardError}</p></div>}
                    <button className="btn-primary" onClick={() => runProjection(wizardData)} data-testid="generate-projection-btn">
                      <Zap style={{ fill: 'currentColor' }} /> Generate Projection
                    </button>
                    <div style={{ position: 'relative', textAlign: 'center', margin: '8px 0' }}>
                      <div style={{ position: 'absolute', top: '50%', left: 0, right: 0, height: 1, background: 'rgba(255,255,255,0.08)' }} />
                      <span style={{ position: 'relative', background: 'var(--bg-primary)', padding: '0 12px', fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>or</span>
                    </div>
                    <button className="btn-secondary" data-testid="stack-player-btn" style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}
                      onClick={() => { setComboMode(true); setWizardPlayers([]); setWizardStep(7); }}>
                      <Users style={{ width: 16, height: 16 }} /> Stack 2nd Player (Combo)
                    </button>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 7 && comboMode && (
                  <div className="space-y-4" data-testid="combo-player2-step">
                    <div className="stat-label" style={{ marginBottom: 4 }}>Select 2nd Player</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
                      Stacking with <span style={{ color: 'var(--accent)', fontWeight: 700 }}>{wizardData.playerName}</span> &middot; {PROP_TYPES.find(p => p.key === wizardData.propType)?.label || wizardData.propType}
                    </div>
                    <div className="search-input-wrap">
                      <Search className="search-icon" />
                      <input className="search-input" type="text" placeholder="Search 2nd player..."
                        onChange={e => handlePlayerSearch(e.target.value)} data-testid="combo-player2-search" />
                      {isPlayersLoading && <RefreshCw className="animate-spin" style={{ position: 'absolute', right: 14, top: '50%', transform: 'translateY(-50%)', width: 16, height: 16, color: 'var(--accent)' }} />}
                    </div>
                    <div className="space-y-2" style={{ maxHeight: 400, overflowY: 'auto' }}>
                      {wizardPlayers.filter(p => p.id !== wizardData.playerId).map(player => (
                        <div key={player.id} className="card card-clickable" onClick={() => handlePlayer2Select(player)}
                          data-testid={`combo-player-${player.id}`}>
                          <div className="player-item">
                            <div className="player-avatar"><User /></div>
                            <div style={{ flex: 1 }}>
                              <div className="player-name">{player.name}</div>
                              <div className="player-team">
                                {player.teamName || 'Free Agent'}
                                {player.teamId === wizardData.teamId && <span style={{ color: 'var(--accent)', marginLeft: 6, fontSize: 10 }}>SAME TEAM</span>}
                                {player.teamId === wizardData.opponentId && <span style={{ color: '#f59e0b', marginLeft: 6, fontSize: 10 }}>OPPONENT</span>}
                              </div>
                            </div>
                          </div>
                        </div>
                      ))}
                      {!wizardPlayers.length && !isPlayersLoading && (
                        <div className="text-center" style={{ padding: '32px 0', color: 'var(--text-secondary)', fontSize: 13 }}>
                          Search for the 2nd player to stack.
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {searchMode === 'wizard' && wizardStep === 8 && comboMode && comboPlayer2 && (
                  <div className="space-y-6" data-testid="combo-line-step">
                    <div className="combo-summary-card" style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.2)', borderRadius: 12, padding: 16 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Combo Stack</div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
                        <div className="player-avatar" style={{ width: 28, height: 28 }}><User /></div>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{wizardData.playerName}</div>
                          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>{wizardData.teamName} &middot; {wizardData.venue?.toUpperCase()}</div>
                        </div>
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', padding: '4px 0 4px 20px' }}>+</div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                        <div className="player-avatar" style={{ width: 28, height: 28 }}><User /></div>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{comboPlayer2.playerName}</div>
                          <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                            {comboPlayer2.teamName}
                            {comboPlayer2.teamId === wizardData.opponentId ? ` · ${wizardData.venue === 'home' ? 'AWAY' : 'HOME'}` : ` · ${wizardData.venue?.toUpperCase()}`}
                          </div>
                        </div>
                      </div>
                      <div style={{ marginTop: 10, fontSize: 11, color: 'var(--accent)', fontWeight: 700 }}>
                        {PROP_TYPES.find(p => p.key === wizardData.propType)?.label || wizardData.propType} — Combined
                      </div>
                    </div>
                    <div className="line-setter">
                      <div className="line-setter-label">Set Combined Line</div>
                      <div className="line-setter-row">
                        <button className="line-btn" onClick={() => setComboLine(Math.max(0, comboLine - 0.5))} data-testid="combo-line-decrease">-</button>
                        <input className="line-input" type="number" step="0.5" placeholder="0.0"
                          value={comboLine === 0 ? '' : comboLine}
                          onChange={e => setComboLine(parseFloat(e.target.value) || 0)}
                          data-testid="combo-line-input" />
                        <button className="line-btn" onClick={() => setComboLine(comboLine + 0.5)} data-testid="combo-line-increase">+</button>
                      </div>
                    </div>
                    {wizardError && <div className="error-box"><ShieldAlert /><p>{wizardError}</p></div>}
                    <button className="btn-primary" onClick={runComboProjection} data-testid="generate-combo-btn">
                      <Zap style={{ fill: 'currentColor' }} /> Generate Combo Projection
                    </button>
                  </div>
                )}
              </>
            )}

            {isProjecting && (
              <div className="loading-wrap">
                <div className="spinner-ring">
                  <Zap className="inner-icon" style={{ width: 28, height: 28 }} />
                </div>
                <div className="loading-title">Analyzing Matchup...</div>
                <div className="loading-sub">Running analysis simulation</div>
              </div>
            )}

            {isComboProjecting && (
              <div className="loading-wrap">
                <div className="spinner-ring">
                  <Users className="inner-icon" style={{ width: 28, height: 28 }} />
                </div>
                <div className="loading-title">{comboProgress || 'Running Combo Analysis...'}</div>
                <div className="loading-sub">This takes about 2 minutes — analyzing each player separately</div>
              </div>
            )}

            {projection && !isProjecting && (
              <div className="space-y-6">
                <button className="back-btn" onClick={() => { setProjection(null); setWizardStep(1); setWizardData({}); }}
                  data-testid="back-to-search-btn">
                  <ArrowLeft /> Back to Search
                </button>
                <ProjectionCard
                  projection={projection}
                  onSave={savePickFn}
                  excludedIndices={excludedSampleIndices}
                  onToggleSample={idx => setExcludedSampleIndices(prev =>
                    prev.includes(idx) ? prev.filter(i => i !== idx) : [...prev, idx]
                  )}
                />
              </div>
            )}

            {comboProjection && !isComboProjecting && (
              <div className="space-y-6">
                <button className="back-btn" onClick={resetCombo} data-testid="combo-back-btn">
                  <ArrowLeft /> New Analysis
                </button>
                <div className="combo-result-card" data-testid="combo-result-card">
                  {/* COMBO HEADER */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
                    <Users style={{ width: 18, height: 18, color: 'var(--accent)' }} />
                    <div style={{ fontSize: 12, fontWeight: 800, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: 1.5 }}>Combo Projection</div>
                  </div>

                  {/* COMBINED RESULT */}
                  <div style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.15)', borderRadius: 12, padding: 20, marginBottom: 20, textAlign: 'center' }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 8 }}>
                      Combined {PROP_TYPES.find(p => p.key === wizardData.propType)?.label || wizardData.propType}
                    </div>
                    <div style={{ fontSize: 40, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-primary)', lineHeight: 1 }}>
                      {comboProjection.combined.projectedValue}
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--text-muted)', margin: '8px 0 12px' }}>
                      vs Line: <span style={{ fontWeight: 800, color: 'var(--text-primary)', fontFamily: "'JetBrains Mono', monospace" }}>{comboProjection.combined.line}</span>
                    </div>
                    <div style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 20px', borderRadius: 8,
                      background: comboProjection.combined.recommendation === 'over' ? 'rgba(16,185,129,0.15)' : 'rgba(244,63,94,0.15)',
                      border: `1px solid ${comboProjection.combined.recommendation === 'over' ? 'rgba(16,185,129,0.3)' : 'rgba(244,63,94,0.3)'}`,
                    }}>
                      {comboProjection.combined.recommendation === 'over'
                        ? <TrendingUp style={{ width: 16, height: 16, color: '#10b981' }} />
                        : <TrendingDown style={{ width: 16, height: 16, color: '#f43f5e' }} />}
                      <span style={{ fontSize: 16, fontWeight: 900, color: comboProjection.combined.recommendation === 'over' ? '#10b981' : '#f43f5e', textTransform: 'uppercase' }}>
                        {comboProjection.combined.recommendation}
                      </span>
                      <span className={`badge ${comboProjection.combined.confidenceLevel === 'High' ? 'neon' : comboProjection.combined.confidenceLevel === 'Medium' ? '' : 'caution'}`} style={{ fontSize: 10, marginLeft: 4 }}>
                        {comboProjection.combined.confidenceLevel}
                      </span>
                    </div>
                  </div>

                  {/* INDIVIDUAL BREAKDOWNS */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
                    {[comboProjection.player1, comboProjection.player2].map((pred, idx) => (
                      <div key={`combo-player-${idx + 1}`} style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10, padding: 14 }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: idx === 0 ? 'var(--accent)' : '#f59e0b', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
                          Player {idx + 1}
                        </div>
                        <div style={{ fontSize: 14, fontWeight: 800, color: 'var(--text-primary)', marginBottom: 4 }}>
                          {pred.player?.name || 'Unknown'}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
                          {pred.player?.team || ''} &middot; {pred.player?.position || ''}
                        </div>
                        <div style={{ fontSize: 28, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: 'var(--text-primary)' }}>
                          {pred.projectedValue}
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>projected</div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 8 }}>
                          {pred.recommendation === 'over'
                            ? <TrendingUp style={{ width: 12, height: 12, color: '#10b981' }} />
                            : <TrendingDown style={{ width: 12, height: 12, color: '#f43f5e' }} />}
                          <span style={{ fontSize: 11, fontWeight: 700, color: pred.recommendation === 'over' ? '#10b981' : '#f43f5e', textTransform: 'uppercase' }}>
                            {pred.recommendation}
                          </span>
                          <span style={{ fontSize: 10, color: 'var(--text-muted)', marginLeft: 4 }}>{pred.confidenceScore}%</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* SHARED MATCHUP OVERVIEW */}
                  {comboProjection.player1?.matchupOverview && (
                    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10, padding: 14, marginBottom: 16 }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>Matchup Overview</div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{comboProjection.player1.matchupOverview.homeTeam}</div>
                        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>vs</div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{comboProjection.player1.matchupOverview.awayTeam}</div>
                      </div>
                      {comboProjection.player1.matchupOverview.expectedPossession && (
                        <div style={{ marginBottom: 8 }}>
                          <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden' }}>
                            <div style={{ width: `${comboProjection.player1.matchupOverview.expectedPossession.home || 50}%`, background: 'var(--accent)' }} />
                            <div style={{ width: `${comboProjection.player1.matchupOverview.expectedPossession.away || 50}%`, background: '#f43f5e' }} />
                          </div>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                            <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent)' }}>{comboProjection.player1.matchupOverview.expectedPossession.home}%</span>
                            <span style={{ fontSize: 11, fontWeight: 700, color: '#f43f5e' }}>{comboProjection.player1.matchupOverview.expectedPossession.away}%</span>
                          </div>
                        </div>
                      )}
                      {comboProjection.player1.matchupOverview.expectedGameType && (
                        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>Game Type: <span style={{ color: 'var(--text-primary)', fontWeight: 700, textTransform: 'capitalize' }}>{comboProjection.player1.matchupOverview.expectedGameType}</span></div>
                      )}
                    </div>
                  )}

                  {/* SHARP TAKES */}
                  <div style={{ display: 'grid', gap: 10 }}>
                    {[comboProjection.player1, comboProjection.player2].map((pred, idx) => pred.sharpSummary && (
                      <div key={`combo-sharp-${idx + 1}`} style={{ background: 'rgba(99,102,241,0.04)', border: '1px solid rgba(99,102,241,0.15)', borderRadius: 10, padding: 12 }}>
                        <div style={{ fontSize: 10, fontWeight: 700, color: '#6366f1', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
                          {pred.player?.name} — Sharp Take
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>{pred.sharpSummary}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* TRACKING TAB */}
        {activeTab === 'tracking' && (
          <TrackingTab
            auth={auth} savedPicks={savedPicks} liveData={liveData} livePickCount={livePickCount}
            trackingView={trackingView} setTrackingView={setTrackingView}
            missAnalyses={missAnalyses} reanalyzePick={reanalyzePick} reanalyzingPick={reanalyzingPick}
            removePickFn={removePickFn}
            correctingPick={correctingPick} setCorrectingPick={setCorrectingPick}
            correctValue={correctValue} setCorrectValue={setCorrectValue}
            submitCorrection={submitCorrection}
            selectedPick={selectedPick} setSelectedPick={setSelectedPick}
          />
        )}




        {/* GUIDE TAB */}
        {activeTab === 'guide' && <GuideTab />}

        {/* SCAN TAB */}
        {activeTab === 'scan' && (
          <div className="animate-fade-in" data-testid="scan-tab" style={{ padding: '0 0 100px' }}>

            {/* ── SCAN UPLOAD & RESULTS VIEW ── */}
              <>
                {/* Header */}
                <div style={{ textAlign: 'center', marginBottom: 24 }}>
                  <div style={{ fontSize: 24, fontWeight: 800, color: '#fff', letterSpacing: '-0.5px' }}>
                    Scan a Prop
                  </div>
                  <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', marginTop: 6, fontWeight: 500 }}>
                    Upload a {activeSport === 'basketball' ? 'NBA' : 'soccer'} prop screenshot for instant AI analysis
                  </div>
                </div>

                {/* Upload Zone */}
                {!scanImage && (
                  <div
                    data-testid="scan-upload-zone"
                    onClick={() => scanFileRef.current?.click()}
                    onDragOver={(e) => { e.preventDefault(); e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.background = 'rgba(16,185,129,0.04)'; }}
                    onDragLeave={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.06)'; e.currentTarget.style.background = 'rgba(255,255,255,0.015)'; }}
                    onDrop={(e) => {
                      e.preventDefault();
                      e.currentTarget.style.borderColor = 'rgba(255,255,255,0.06)';
                      e.currentTarget.style.background = 'rgba(255,255,255,0.015)';
                      const file = e.dataTransfer.files[0];
                      if (file && file.type.startsWith('image/')) handleScanUpload(file);
                    }}
                    style={{
                      border: '1.5px dashed rgba(255,255,255,0.06)', borderRadius: 'var(--radius-xl)', padding: '56px 24px',
                      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 20,
                      cursor: 'pointer', transition: 'all 0.25s cubic-bezier(0.25, 0.1, 0.25, 1)',
                      background: 'rgba(255,255,255,0.015)',
                      position: 'relative', overflow: 'hidden',
                    }}
                  >
                    <div style={{
                      position: 'absolute', top: 0, left: '50%', transform: 'translateX(-50%)',
                      width: '60%', height: '1px', background: 'linear-gradient(90deg, transparent, rgba(16,185,129,0.2), transparent)',
                    }} />
                    <div style={{
                      width: 72, height: 72, borderRadius: 20, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.12)',
                      boxShadow: '0 0 32px rgba(16,185,129,0.06)',
                    }}>
                      <Camera style={{ width: 30, height: 30, color: 'var(--accent)' }} />
                    </div>
                    <div style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 15, fontWeight: 700, color: 'rgba(255,255,255,0.9)', letterSpacing: '-0.2px' }}>Tap to upload screenshot</div>
                      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.28)', marginTop: 6, fontWeight: 500 }}>or drag & drop an image</div>
                    </div>
                    <div style={{
                      padding: '10px 24px', borderRadius: 'var(--radius-sm)', fontSize: 11, fontWeight: 800,
                      background: 'var(--accent)', color: '#000', letterSpacing: '0.06em',
                      boxShadow: '0 4px 20px rgba(16,185,129,0.2)', display: 'flex', alignItems: 'center', gap: 6,
                    }}>
                      <Upload style={{ width: 14, height: 14 }} />
                      CHOOSE IMAGE
                    </div>
                    <input
                      ref={scanFileRef}
                      type="file"
                      accept="image/*"
                      style={{ display: 'none' }}
                      data-testid="scan-file-input"
                      onChange={(e) => {
                        const file = e.target.files[0];
                        if (file) handleScanUpload(file);
                      }}
                    />
                  </div>
                )}

                {/* Scanning Spinner */}
                {isScanning && (
                  <div style={{ textAlign: 'center', padding: '48px 0' }} data-testid="scan-loading">
                    <div className="spinner-ring"><Camera className="inner-icon" style={{ width: 24, height: 24 }} /></div>
                    <div style={{ fontSize: 15, fontWeight: 700, color: '#fff', marginTop: 20, letterSpacing: '-0.2px' }}>Analyzing screenshot...</div>
                    <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.3)', marginTop: 6, fontWeight: 500 }}>Reading player props with AI vision</div>
                  </div>
                )}


                {/* Error State */}
                {scanError && (
                  <div className="error-box" style={{ marginTop: 16 }} data-testid="scan-error">
                    <ShieldAlert style={{ width: 16, height: 16 }} />
                    <span>{scanError}</span>
                  </div>
                )}

                {/* Image Preview + Results */}
                {scanImage && !isScanning && (
                  <div style={{ marginTop: 8 }}>
                    {/* Preview */}
                    <div style={{ position: 'relative', marginBottom: 16 }}>
                      <img
                        src={scanImage}
                        alt="Uploaded prop"
                        data-testid="scan-preview-image"
                        style={{
                          width: '100%', maxHeight: 300, objectFit: 'contain',
                          borderRadius: 12, border: '1px solid rgba(100,100,120,0.2)',
                        }}
                      />
                      <button
                        onClick={resetScan}
                        data-testid="scan-reset-btn"
                        style={{
                          position: 'absolute', top: 8, right: 8, width: 32, height: 32,
                          borderRadius: 8, background: 'rgba(0,0,0,0.7)', border: '1px solid rgba(255,255,255,0.15)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer',
                        }}
                      >
                        <X style={{ width: 16, height: 16, color: '#fff' }} />
                      </button>
                    </div>

                    {/* Extracted Props */}
                    {scanResults && scanResults.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                              <div style={{ fontSize: 11, fontWeight: 900, letterSpacing: '0.15em', color: 'var(--accent)', textTransform: 'uppercase' }}>
                                {scanResults.length} Prop{scanResults.length > 1 ? 's' : ''} Detected
                              </div>
                              {scanResults.length > 1 && (
                                <button
                                  onClick={handlePredictAll}
                                  disabled={Object.values(scanPredictingIdx).some(v => v)}
                                  data-testid="scan-predict-all-btn"
                                  style={{
                                    padding: '6px 14px', borderRadius: 8, border: 'none',
                                    background: 'rgba(16,185,129,0.12)', color: 'var(--accent)',
                                    fontSize: 10, fontWeight: 900, letterSpacing: '0.08em',
                                    cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
                                  }}
                                >
                                  <Zap style={{ width: 12, height: 12 }} /> PREDICT ALL
                                </button>
                              )}
                            </div>
                        {scanResults.map((pick, idx) => {
                          const ext = pick.extracted;
                          const res = pick.resolved;
                          const isCombo = ext?.isCombo;
                          const resolvedPlayers = pick.resolvedPlayers || [];
                          const comboMatched = isCombo ? (resolvedPlayers[0] && resolvedPlayers[1]) : !!res;
                          const isPredicting = scanPredictingIdx[idx];
                          const prediction = scanPrediction[idx];
                          const propLabel = PROP_TYPES.find(p => p.key === ext.propType)?.label || ext.propType;

                          return (
                            <div key={pick.pickId || `scan-${idx}`} data-testid={`scan-result-${idx}`} style={{
                              background: '#0a0a0f', border: `2px solid ${isCombo ? 'rgba(168,85,247,0.3)' : 'rgba(16,185,129,0.12)'}`, borderRadius: 16,
                              overflow: 'hidden', boxShadow: isCombo ? '0 0 12px rgba(168,85,247,0.06)' : '0 0 10px rgba(16,185,129,0.04)',
                            }}>
                              {/* Combo Badge */}
                              {isCombo && (
                                <div style={{
                                  padding: '6px 16px', background: 'rgba(168,85,247,0.08)',
                                  borderBottom: '1px solid rgba(168,85,247,0.15)',
                                  display: 'flex', alignItems: 'center', gap: 6,
                                }}>
                                  <Users style={{ width: 12, height: 12, color: '#a855f7' }} />
                                  <span style={{ fontSize: 9, fontWeight: 900, letterSpacing: '0.15em', color: '#a855f7', textTransform: 'uppercase' }}>
                                    COMBO PROP
                                  </span>
                                </div>
                              )}

                              {/* Player Info */}
                              <div style={{ padding: '14px 16px', display: 'flex', gap: 12, alignItems: 'center' }}>
                                <div style={{
                                    width: 44, height: 44, borderRadius: 10,
                                    background: isCombo ? 'rgba(168,85,247,0.1)' : 'rgba(16,185,129,0.1)',
                                    border: `1px solid ${isCombo ? 'rgba(168,85,247,0.2)' : 'rgba(16,185,129,0.2)'}`,
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  }}>
                                    {isCombo
                                      ? <Users style={{ width: 20, height: 20, color: '#a855f7' }} />
                                      : <User style={{ width: 20, height: 20, color: 'var(--accent)' }} />
                                    }
                                  </div>
                                <div style={{ flex: 1 }}>
                                  {isCombo ? (
                                    <>
                                      <div style={{ fontSize: 14, fontWeight: 800, color: '#fff' }}>
                                        {resolvedPlayers[0]?.playerName || ext.players?.[0]?.name || '?'}
                                        <span style={{ color: '#a855f7', margin: '0 4px' }}>+</span>
                                        {resolvedPlayers[1]?.playerName || ext.players?.[1]?.name || '?'}
                                      </div>
                                      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
                                        <span>{resolvedPlayers[0]?.teamName || ext.players?.[0]?.team || '?'}</span>
                                        <span style={{ color: 'rgba(255,255,255,0.2)' }}>vs</span>
                                        <span>{resolvedPlayers[1]?.teamName || ext.players?.[1]?.team || '?'}</span>
                                      </div>
                                    </>
                                  ) : scanEditMode[idx] ? null : (
                                    <>
                                      <div style={{ fontSize: 15, fontWeight: 800, color: '#fff' }}>
                                        {res?.playerName || ext.playerName}
                                      </div>
                                      <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.4)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                        {ext.position && (
                                          <span style={{
                                            display: 'inline-block', background: 'rgba(59,130,246,0.15)',
                                            color: '#60a5fa', fontSize: 9, fontWeight: 800,
                                            padding: '1px 6px', borderRadius: 4, letterSpacing: '0.05em',
                                          }} data-testid={`scan-position-${idx}`}>
                                            {ext.position}{ext.role ? ` · ${ext.role}` : ''}
                                          </span>
                                        )}
                                        <span>{res?.teamName || ext.playerTeam || 'Unknown team'}</span>
                                        {ext.opponentName && (
                                          <span>
                                            {ext.venue === 'away' ? ' @ ' : ' vs '}
                                            {ext.opponentName}
                                          </span>
                                        )}
                                        <span
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            setScanVenueOverrides(prev => ({
                                              ...prev,
                                              [idx]: (prev[idx] || ext.venue || 'home') === 'home' ? 'away' : 'home'
                                            }));
                                          }}
                                          data-testid={`scan-venue-toggle-${idx}`}
                                          style={{
                                            padding: '2px 8px', borderRadius: 4, fontSize: 8, fontWeight: 900, letterSpacing: '0.1em',
                                            cursor: 'pointer', userSelect: 'none', transition: 'all 0.15s',
                                            background: (scanVenueOverrides[idx] || ext.venue || 'home') === 'away' ? 'rgba(244,63,94,0.15)' : 'rgba(59,130,246,0.15)',
                                            color: (scanVenueOverrides[idx] || ext.venue || 'home') === 'away' ? '#f43f5e' : '#3b82f6',
                                            border: `1px solid ${(scanVenueOverrides[idx] || ext.venue || 'home') === 'away' ? 'rgba(244,63,94,0.3)' : 'rgba(59,130,246,0.3)'}`,
                                          }}
                                        >
                                          {(scanVenueOverrides[idx] || ext.venue || 'home') === 'away' ? 'AWAY' : 'HOME'} &#x21C5;
                                        </span>
                                      </div>
                                    </>
                                  )}
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                  {!isCombo && !scanEditMode[idx] && (
                                    <div
                                      onClick={() => handleScanEdit(idx, pick)}
                                      data-testid={`scan-edit-btn-${idx}`}
                                      style={{
                                        width: 28, height: 28, borderRadius: 6, cursor: 'pointer',
                                        background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.25)',
                                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                                      }}
                                      title="Edit player/team info"
                                    >
                                      <Edit3 style={{ width: 13, height: 13, color: '#f59e0b' }} />
                                    </div>
                                  )}
                                  {comboMatched ? (
                                    <div style={{
                                      padding: '4px 10px', borderRadius: 6, fontSize: 9, fontWeight: 900,
                                      background: 'rgba(16,185,129,0.12)', color: '#10b981', border: '1px solid rgba(16,185,129,0.25)',
                                      letterSpacing: '0.1em',
                                    }}>
                                      {isCombo ? '2/2 MATCHED' : 'MATCHED'}
                                    </div>
                                  ) : isCombo && (resolvedPlayers[0] || resolvedPlayers[1]) ? (
                                    <div style={{
                                      padding: '4px 10px', borderRadius: 6, fontSize: 9, fontWeight: 900,
                                      background: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.25)',
                                      letterSpacing: '0.1em',
                                    }}>
                                      1/2 MATCHED
                                    </div>
                                  ) : (
                                    <div style={{
                                      padding: '4px 10px', borderRadius: 6, fontSize: 9, fontWeight: 900,
                                      background: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.25)',
                                      letterSpacing: '0.1em',
                                    }}>
                                      AI-ONLY MODE
                                    </div>
                                  )}
                                </div>
                              </div>

                              {/* Edit Mode Fields */}
                              {!isCombo && scanEditMode[idx] && (
                                <div style={{ padding: '0 16px 14px', display: 'flex', flexDirection: 'column', gap: 8 }} data-testid={`scan-edit-panel-${idx}`}>
                                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                                    <div>
                                      <label style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.1em', display: 'block', marginBottom: 4 }}>PLAYER</label>
                                      <input
                                        data-testid={`scan-edit-player-${idx}`}
                                        value={scanEditValues[idx]?.playerName || ''}
                                        onChange={(e) => setScanEditValues(prev => ({
                                          ...prev, [idx]: { ...prev[idx], playerName: e.target.value }
                                        }))}
                                        style={{
                                          width: '100%', padding: '8px 10px', borderRadius: 8, fontSize: 13, fontWeight: 700,
                                          background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(245,158,11,0.3)',
                                          color: '#fff', outline: 'none',
                                        }}
                                      />
                                    </div>
                                    <div>
                                      <label style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.1em', display: 'block', marginBottom: 4 }}>TEAM</label>
                                      <input
                                        data-testid={`scan-edit-team-${idx}`}
                                        value={scanEditValues[idx]?.playerTeam || ''}
                                        onChange={(e) => setScanEditValues(prev => ({
                                          ...prev, [idx]: { ...prev[idx], playerTeam: e.target.value }
                                        }))}
                                        style={{
                                          width: '100%', padding: '8px 10px', borderRadius: 8, fontSize: 13, fontWeight: 700,
                                          background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(245,158,11,0.3)',
                                          color: '#fff', outline: 'none',
                                        }}
                                      />
                                    </div>
                                  </div>
                                  <div>
                                    <label style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.1em', display: 'block', marginBottom: 4 }}>OPPONENT</label>
                                    <input
                                      data-testid={`scan-edit-opponent-${idx}`}
                                      value={scanEditValues[idx]?.opponentName || ''}
                                      onChange={(e) => setScanEditValues(prev => ({
                                        ...prev, [idx]: { ...prev[idx], opponentName: e.target.value }
                                      }))}
                                      style={{
                                        width: '100%', padding: '8px 10px', borderRadius: 8, fontSize: 13, fontWeight: 700,
                                        background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(245,158,11,0.3)',
                                        color: '#fff', outline: 'none',
                                      }}
                                    />
                                  </div>
                                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                                    <div>
                                      <label style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.1em', display: 'block', marginBottom: 4 }}>POSITION</label>
                                      <select
                                        data-testid={`scan-edit-position-${idx}`}
                                        value={scanEditValues[idx]?.position || ''}
                                        onChange={(e) => setScanEditValues(prev => ({
                                          ...prev, [idx]: { ...prev[idx], position: e.target.value }
                                        }))}
                                        style={{
                                          width: '100%', padding: '8px 10px', borderRadius: 8, fontSize: 13, fontWeight: 700,
                                          background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(245,158,11,0.3)',
                                          color: '#fff', outline: 'none', appearance: 'none',
                                        }}
                                      >
                                        <option value="" style={{ background: '#1a1a2e' }}>Auto-detect</option>
                                        <option value="GK" style={{ background: '#1a1a2e' }}>GK</option>
                                        <option value="CB" style={{ background: '#1a1a2e' }}>CB</option>
                                        <option value="LB" style={{ background: '#1a1a2e' }}>LB</option>
                                        <option value="RB" style={{ background: '#1a1a2e' }}>RB</option>
                                        <option value="CDM" style={{ background: '#1a1a2e' }}>CDM</option>
                                        <option value="CM" style={{ background: '#1a1a2e' }}>CM</option>
                                        <option value="CAM" style={{ background: '#1a1a2e' }}>CAM</option>
                                        <option value="LW" style={{ background: '#1a1a2e' }}>LW</option>
                                        <option value="RW" style={{ background: '#1a1a2e' }}>RW</option>
                                        <option value="CF" style={{ background: '#1a1a2e' }}>CF</option>
                                        <option value="ST" style={{ background: '#1a1a2e' }}>ST</option>
                                      </select>
                                    </div>
                                    <div>
                                      <label style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.4)', letterSpacing: '0.1em', display: 'block', marginBottom: 4 }}>ROLE</label>
                                      <input
                                        data-testid={`scan-edit-role-${idx}`}
                                        value={scanEditValues[idx]?.role || ''}
                                        onChange={(e) => setScanEditValues(prev => ({
                                          ...prev, [idx]: { ...prev[idx], role: e.target.value }
                                        }))}
                                        placeholder="e.g. Box-to-Box"
                                        style={{
                                          width: '100%', padding: '8px 10px', borderRadius: 8, fontSize: 13, fontWeight: 700,
                                          background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(245,158,11,0.3)',
                                          color: '#fff', outline: 'none',
                                        }}
                                      />
                                    </div>
                                  </div>
                                  <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                                    <button
                                      onClick={() => handleScanEditConfirm(idx)}
                                      disabled={scanResolving[idx]}
                                      data-testid={`scan-edit-confirm-${idx}`}
                                      style={{
                                        flex: 1, padding: '10px', borderRadius: 8, border: 'none',
                                        background: '#f59e0b', color: '#000', fontSize: 12, fontWeight: 900,
                                        cursor: scanResolving[idx] ? 'wait' : 'pointer',
                                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                                        letterSpacing: '0.06em', opacity: scanResolving[idx] ? 0.7 : 1,
                                      }}
                                    >
                                      {scanResolving[idx] ? (
                                        <><Loader2 style={{ width: 14, height: 14, animation: 'spin 1s linear infinite' }} /> RESOLVING...</>
                                      ) : (
                                        <><Check style={{ width: 14, height: 14 }} /> CONFIRM</>
                                      )}
                                    </button>
                                    <button
                                      onClick={() => handleScanEditCancel(idx)}
                                      data-testid={`scan-edit-cancel-${idx}`}
                                      style={{
                                        padding: '10px 16px', borderRadius: 8,
                                        background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(100,100,120,0.2)',
                                        color: 'rgba(255,255,255,0.5)', fontSize: 12, fontWeight: 700, cursor: 'pointer',
                                      }}
                                    >
                                      <X style={{ width: 14, height: 14 }} />
                                    </button>
                                  </div>
                                </div>
                              )}

                              {/* Prop Details */}
                              <div style={{
                                padding: '0 16px 14px', display: 'flex', gap: 8, alignItems: 'center',
                              }}>
                                <div style={{
                                  flex: 1, padding: '10px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.03)',
                                  border: '1px solid rgba(100,100,120,0.15)', textAlign: 'center',
                                }}>
                                  <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.1em', marginBottom: 4 }}>PROP</div>
                                  <div style={{ fontSize: 13, fontWeight: 800, color: '#fff' }}>{propLabel}{isCombo ? ' (Combo)' : ''}</div>
                                </div>
                                <div style={{
                                  flex: 1, padding: '10px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.03)',
                                  border: '1px solid rgba(100,100,120,0.15)', textAlign: 'center',
                                }}>
                                  <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.1em', marginBottom: 4 }}>LINE</div>
                                  <div style={{ fontSize: 13, fontWeight: 800, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace" }}>{ext.line}</div>
                                </div>
                                <div style={{
                                  flex: 1, padding: '10px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.03)',
                                  border: '1px solid rgba(100,100,120,0.15)', textAlign: 'center',
                                }}>
                                  <div style={{ fontSize: 9, fontWeight: 800, color: 'rgba(255,255,255,0.35)', letterSpacing: '0.1em', marginBottom: 4 }}>LEAGUE</div>
                                  <div style={{ fontSize: 11, fontWeight: 700, color: 'rgba(255,255,255,0.6)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {ext.league || 'Unknown'}
                                  </div>
                                </div>
                              </div>

                              {/* Action / Result Area */}
                              {prediction ? (
                                <div style={{ padding: '0 16px 14px' }} data-testid={`scan-prediction-result-${idx}`}>
                                  {/* Clickable Inline Prediction Summary */}
                                  <div
                                    onClick={() => setScanExpandedIdx(scanExpandedIdx === idx ? null : idx)}
                                    style={{
                                      background: 'rgba(16,185,129,0.04)', border: '2px solid rgba(16,185,129,0.15)',
                                      borderRadius: 12, padding: 14, marginBottom: 10, cursor: 'pointer',
                                      transition: 'all 0.25s cubic-bezier(0.34,1.56,0.64,1)',
                                    }}
                                    data-testid={`scan-prediction-toggle-${idx}`}
                                  >
                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                                      <span style={{ fontSize: 9, fontWeight: 900, letterSpacing: '0.15em', color: 'var(--accent)', textTransform: 'uppercase' }}>
                                        Projection Ready
                                      </span>
                                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                        <span style={{ fontSize: 11, fontWeight: 800, color: 'rgba(255,255,255,0.5)', fontFamily: "'JetBrains Mono', monospace" }}>
                                          {prediction.confidenceScore || prediction.combined?.confidenceScore || '—'}% conf
                                        </span>
                                        <ChevronRight style={{
                                          width: 14, height: 14, color: 'var(--accent)',
                                          transform: scanExpandedIdx === idx ? 'rotate(90deg)' : 'rotate(0)',
                                          transition: 'transform 0.25s',
                                        }} />
                                      </div>
                                    </div>

                                    {prediction._isCombo ? (
                                      <div>
                                        <div style={{ fontSize: 28, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: '#fff', textAlign: 'center' }}>
                                          {prediction.combined?.projectedValue ?? '—'}
                                        </div>
                                        <div style={{ textAlign: 'center', marginTop: 6 }}>
                                          <span style={{
                                            display: 'inline-flex', alignItems: 'center', gap: 4, padding: '4px 14px', borderRadius: 8,
                                            background: prediction.combined?.recommendation === 'over' ? 'rgba(16,185,129,0.15)' : 'rgba(244,63,94,0.15)',
                                            border: `2px solid ${prediction.combined?.recommendation === 'over' ? 'rgba(16,185,129,0.25)' : 'rgba(244,63,94,0.25)'}`,
                                          }}>
                                            {prediction.combined?.recommendation === 'over'
                                              ? <TrendingUp style={{ width: 12, height: 12, color: '#10b981' }} />
                                              : <TrendingDown style={{ width: 12, height: 12, color: '#f43f5e' }} />}
                                            <span style={{ fontSize: 13, fontWeight: 900, color: prediction.combined?.recommendation === 'over' ? '#10b981' : '#f43f5e', textTransform: 'uppercase' }}>
                                              {prediction.combined?.recommendation}
                                            </span>
                                          </span>
                                        </div>
                                      </div>
                                    ) : (
                                      <div>
                                        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, justifyContent: 'center' }}>
                                          <span style={{ fontSize: 28, fontWeight: 900, fontFamily: "'JetBrains Mono', monospace", color: '#fff' }}>
                                            {prediction.projectedValue ?? '—'}
                                          </span>
                                          <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.4)' }}>projected</span>
                                        </div>
                                        <div style={{ textAlign: 'center', marginTop: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                                          <span style={{
                                            display: 'inline-flex', alignItems: 'center', gap: 4, padding: '4px 14px', borderRadius: 8,
                                            background: prediction.recommendation === 'over' ? 'rgba(16,185,129,0.15)' : 'rgba(244,63,94,0.15)',
                                            border: `2px solid ${prediction.recommendation === 'over' ? 'rgba(16,185,129,0.25)' : 'rgba(244,63,94,0.25)'}`,
                                          }}>
                                            {prediction.recommendation === 'over'
                                              ? <TrendingUp style={{ width: 12, height: 12, color: '#10b981' }} />
                                              : <TrendingDown style={{ width: 12, height: 12, color: '#f43f5e' }} />}
                                            <span style={{ fontSize: 13, fontWeight: 900, color: prediction.recommendation === 'over' ? '#10b981' : '#f43f5e', textTransform: 'uppercase' }}>
                                              {prediction.recommendation}
                                            </span>
                                          </span>
                                          {prediction.matchContext && (
                                            <span style={{ fontSize: 9, fontWeight: 700, padding: '3px 8px', borderRadius: 6, background: 'rgba(99,102,241,0.12)', color: '#818cf8', border: '1px solid rgba(99,102,241,0.2)' }}>
                                              {prediction.matchContext.league}{prediction.matchContext.round ? ` · ${prediction.matchContext.round}` : ''}
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                    )}

                                    {/* Tap hint */}
                                    <div style={{ textAlign: 'center', marginTop: 8, fontSize: 9, color: 'rgba(255,255,255,0.25)', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                                      {scanExpandedIdx === idx ? 'Tap to collapse' : 'Tap for full breakdown'}
                                    </div>
                                  </div>

                                  {/* Expanded Full Review */}
                                  {scanExpandedIdx === idx && !prediction._isCombo && (
                                    <div style={{ marginBottom: 10, animation: 'fadeInUp 0.2s ease-out' }} data-testid={`scan-full-review-${idx}`}>
                                      <ProjectionCard
                                        projection={prediction}
                                        onSave={() => scanSavePickFn(idx)}
                                        excludedIndices={scanExcludedIndices}
                                        onToggleSample={si => setScanExcludedIndices(prev =>
                                          prev.includes(si) ? prev.filter(i => i !== si) : [...prev, si]
                                        )}
                                      />

                                      {/* H2H Section */}
                                      {prediction.h2hGames && prediction.h2hGames.length > 0 && (
                                        <div style={{
                                          background: '#0a0a0f', border: '2px solid rgba(16,185,129,0.12)',
                                          borderRadius: 14, overflow: 'hidden', marginTop: 10, boxShadow: '0 0 10px rgba(16,185,129,0.04)',
                                        }}>
                                          <div style={{
                                            padding: '10px 16px', borderBottom: '1px solid rgba(16,185,129,0.08)',
                                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                                          }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                              <Activity style={{ width: 14, height: 14, color: '#f59e0b' }} />
                                              <span style={{ fontSize: 11, fontWeight: 900, letterSpacing: '0.1em', color: '#f59e0b', textTransform: 'uppercase' }}>
                                                H2H vs {prediction.opponent || '—'} ({prediction.h2hGames.length})
                                              </span>
                                            </div>
                                          </div>
                                          <div style={{ padding: '10px 16px' }}>
                                            <div className="samples-grid">
                                              {prediction.h2hGames.map((g, gi) => {
                                                const isHitH2H = g.hit === (prediction.recommendation === 'over' ? 'over' : 'under');
                                                return (
                                                  <div key={`h2h-${gi}`} className={`sample-cell ${isHitH2H ? 'hit' : 'miss'}`} style={{ cursor: 'default' }}>
                                                    <span className="sample-value">{g.value}</span>
                                                    <span className="sample-minutes">{g.minutesPlayed}'</span>
                                                    <span className="sample-venue-tag">{g.venue === 'home' ? 'H' : 'A'}</span>
                                                    <span className="sample-opponent">{(g.opponent || '').substring(0, 3).toUpperCase()}</span>
                                                  </div>
                                                );
                                              })}
                                            </div>
                                          </div>
                                        </div>
                                      )}

                                    </div>
                                  )}

                                  {/* Save Button */}
                                  <button
                                    onClick={() => scanSavePickFn(idx)}
                                    data-testid={`scan-save-btn-${idx}`}
                                    style={{
                                      width: '100%', padding: '11px', borderRadius: 12, border: '2px solid rgba(59,130,246,0.3)',
                                      background: '#3b82f6', color: '#fff',
                                      fontSize: 12, fontWeight: 900, letterSpacing: '0.06em',
                                      cursor: 'pointer',
                                      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                                      boxShadow: '0 0 16px rgba(59,130,246,0.15)',
                                    }}
                                  >
                                    <Shield style={{ width: 14, height: 14 }} /> SAVE TO TRACKING
                                  </button>
                                </div>
                              ) : (
                                <div style={{ padding: '0 16px 14px' }}>
                                  <button
                                    onClick={() => handleScanPredict(pick, idx)}
                                    disabled={isPredicting}
                                    data-testid={`scan-predict-btn-${idx}`}
                                    style={{
                                      width: '100%', padding: '12px', borderRadius: 10, border: 'none',
                                      background: isPredicting ? 'rgba(16,185,129,0.15)' : (isCombo ? '#a855f7' : 'var(--accent)'),
                                      color: isPredicting ? 'var(--accent)' : '#000',
                                      fontSize: 13, fontWeight: 900, letterSpacing: '0.06em',
                                      cursor: !isPredicting ? 'pointer' : 'not-allowed',
                                      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                                    }}
                                  >
                                    {isPredicting ? (
                                      <><Loader2 style={{ width: 16, height: 16, animation: 'spin 1s linear infinite' }} /> {isCombo ? 'COMBO ANALYSIS...' : 'ANALYZING...'}</>
                                    ) : (
                                      <><Zap style={{ width: 16, height: 16 }} /> {isCombo ? 'RUN COMBO PREDICTION' : 'RUN PREDICTION'}</>
                                    )}
                                  </button>
                                </div>
                              )}
                            </div>
                          );
                        })}

                        {/* Scan Again */}
                        <button
                          onClick={resetScan}
                          data-testid="scan-again-btn"
                          style={{
                            width: '100%', padding: '14px', borderRadius: 12, border: '1px solid rgba(100,100,120,0.2)',
                            background: 'rgba(255,255,255,0.03)', color: 'rgba(255,255,255,0.5)',
                            fontSize: 13, fontWeight: 700, cursor: 'pointer', marginTop: 8,
                            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                          }}
                        >
                          <Camera style={{ width: 16, height: 16 }} /> Scan Another Image
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </>
          </div>
        )}


        {/* PROFILE TAB */}
        {activeTab === 'profile' && (
          <ProfileTab
            auth={auth} savedPicks={savedPicks} apiStatus={apiStatus} isOwner={isOwner}
            profileNewPw={profileNewPw} setProfileNewPw={setProfileNewPw}
            profileConfirmPw={profileConfirmPw} setProfileConfirmPw={setProfileConfirmPw}
            profilePwLoading={profilePwLoading} handleProfilePasswordReset={handleProfilePasswordReset}
            adminSettings={adminSettings} adminEditKey={adminEditKey} setAdminEditKey={setAdminEditKey}
            adminEditValue={adminEditValue} setAdminEditValue={setAdminEditValue}
            adminKeyLoading={adminKeyLoading} adminTestResult={adminTestResult} setAdminTestResult={setAdminTestResult}
            adminShowKey={adminShowKey} setAdminShowKey={setAdminShowKey}
            handleTestApiKey={handleTestApiKey} handleSaveAdminSetting={handleSaveAdminSetting}
            handleLogout={handleLogout}
          />
        )}
      </main>
      <nav className="bottom-nav" data-testid="bottom-nav">
        <div className="nav-items">
          <button className={`nav-item ${activeTab === 'scan' ? 'active' : ''}`}
            onClick={() => setActiveTab('scan')} data-testid="nav-scan">
            <Camera />
            <span>Scan</span>
          </button>
          <button className={`nav-item ${activeTab === 'tracking' ? 'active' : ''}`}
            onClick={() => setActiveTab('tracking')} data-testid="nav-tracking">
            <Activity />
            <span>Tracking</span>
          </button>
          <button className={`nav-item ${activeTab === 'profile' ? 'active' : ''}`}
            onClick={() => setActiveTab('profile')} data-testid="nav-profile">
            <User />
            <span>Profile</span>
          </button>
        </div>
      </nav>
      <Toaster position="top-center" theme="dark" richColors />
    </div>
  );
}
