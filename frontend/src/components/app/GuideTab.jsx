import React from 'react';
import {
  Target, Search, Shield, ChevronRight, ChevronDown, BarChart3, Zap, TrendingUp, Activity
} from 'lucide-react';

export function GuideTab() {
  return (
    <div className="tab-content" data-testid="guide-tab" style={{ padding: '16px 16px 100px' }}>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <div style={{ fontSize: 24, fontWeight: 900, color: '#fff', letterSpacing: '-0.5px' }}>How to Use ReversePicks</div>
        <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.45)', marginTop: 6 }}>Follow these steps to get your first prediction</div>
      </div>

      {[
        {
          step: 1, title: 'Pick a League', icon: <Target style={{ width: 20, height: 20 }} />,
          desc: 'Tap the PREDICT tab and select the league your match is in. We support 30+ leagues including NWSL, Premier League, La Liga, and more.',
          tip: 'Start with leagues you know well for the best edge.',
        },
        {
          step: 2, title: 'Search Your Player', icon: <Search style={{ width: 20, height: 20 }} />,
          desc: 'Type at least 3 characters of the player\'s name. Select the player you want to analyze from the results.',
          tip: 'Use last names for faster results. If a player doesn\'t show up, try a different spelling.',
        },
        {
          step: 3, title: 'Select the Opponent', icon: <Shield style={{ width: 20, height: 20 }} />,
          desc: 'Choose which team your player is facing. This determines the matchup analysis and defensive stats.',
          tip: null,
        },
        {
          step: 4, title: 'Home or Away?', icon: <ChevronRight style={{ width: 20, height: 20 }} />,
          desc: 'Select whether your player\'s team is HOME or AWAY. This matters \u2014 players perform differently at home vs away.',
          tip: 'Home teams generally have higher pass counts and possession.',
        },
        {
          step: 5, title: 'Choose Your Prop', icon: <BarChart3 style={{ width: 20, height: 20 }} />,
          desc: 'Pick the stat type: Pass Attempts, Shots, Tackles, Saves, Key Passes, etc. This is the stat the AI will predict.',
          tip: null,
        },
        {
          step: 6, title: 'Set the Line & Generate', icon: <Zap style={{ width: 20, height: 20 }} />,
          desc: 'Enter the prop line (e.g. 25.5). Hit "Generate Projection" and wait ~30 seconds. The AI analyzes real stats, live news, and tactical data.',
          tip: 'Want to stack 2 players? Hit "Stack 2nd Player" to get a combined projection.',
        },
        {
          step: 7, title: 'Read Your Prediction', icon: <TrendingUp style={{ width: 20, height: 20 }} />,
          desc: 'You\'ll see: Projected Value, Over/Under recommendation, Confidence Score, Recent Form, Sharp Take, and full reasoning. Scroll down for the complete analysis.',
          tip: 'Higher confidence = stronger edge. Look for 65%+ confidence picks.',
        },
        {
          step: 8, title: 'Save & Track', icon: <Activity style={{ width: 20, height: 20 }} />,
          desc: 'Tap "Save to Tracking" to monitor your pick live during the match. Go to the TRACKING tab to see NOW/LINE/PACE/HIT% in real-time.',
          tip: 'Settled picks can be corrected if the API data was wrong \u2014 tap the pencil icon.',
        },
      ].map((item) => (
        <div key={item.step} style={{
          background: '#0a0a0f', border: '1.5px solid rgba(100,100,120,0.2)', borderRadius: 14,
          padding: 0, marginBottom: 12, overflow: 'hidden',
        }}>
          <div style={{ padding: '14px 16px', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
            <div style={{
              width: 40, height: 40, borderRadius: 10, flexShrink: 0,
              background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.2)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent)',
            }}>
              {item.icon}
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: 10, fontWeight: 900, color: 'var(--accent)', fontFamily: "'JetBrains Mono', monospace" }}>STEP {item.step}</span>
                <span style={{ fontSize: 15, fontWeight: 800, color: '#fff' }}>{item.title}</span>
              </div>
              <div style={{ fontSize: 12, color: 'rgba(255,255,255,0.55)', lineHeight: 1.5 }}>{item.desc}</div>
              {item.tip && (
                <div style={{ marginTop: 8, padding: '6px 10px', borderRadius: 6, background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.15)', fontSize: 11, color: '#818cf8', lineHeight: 1.4 }}>
                  {item.tip}
                </div>
              )}
            </div>
          </div>
        </div>
      ))}

      {/* FAQ Section */}
      <div style={{ marginTop: 24 }}>
        <div style={{ fontSize: 18, fontWeight: 900, color: '#fff', marginBottom: 14, letterSpacing: '-0.3px' }}>FAQ</div>
        {[
          { q: 'How long does a prediction take?', a: 'About 30-45 seconds. The AI searches live news, analyzes real stats, and runs tactical simulations.' },
          { q: 'Why does it say "Data Gap Detected"?', a: 'Some leagues (especially women\'s leagues) have incomplete stats from our data provider. The AI uses web-verified data to compensate.' },
          { q: 'Can I predict two players together?', a: 'Yes! On Step 6, tap "Stack 2nd Player" to combine two players\' projections for the same stat type.' },
          { q: 'How does the Tracking tab work?', a: 'Save a pick and it tracks live during the match \u2014 showing your player\'s current stat, pace, and hit probability in real-time.' },
          { q: 'A pick settled wrong. How do I fix it?', a: 'Go to History, find the pick, and tap the pencil icon. Enter the real number from SofaScore/FotMob and hit Save.' },
        ].map((faq) => (
          <details key={faq.q} style={{
            background: '#0a0a0f', border: '1.5px solid rgba(100,100,120,0.15)', borderRadius: 10,
            marginBottom: 8, overflow: 'hidden',
          }}>
            <summary style={{
              padding: '12px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 700, color: '#fff',
              listStyle: 'none', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              {faq.q}
              <ChevronDown style={{ width: 14, height: 14, color: 'rgba(255,255,255,0.3)', flexShrink: 0 }} />
            </summary>
            <div style={{ padding: '0 16px 12px', fontSize: 12, color: 'rgba(255,255,255,0.5)', lineHeight: 1.5 }}>{faq.a}</div>
          </details>
        ))}
      </div>
    </div>
  );
}
