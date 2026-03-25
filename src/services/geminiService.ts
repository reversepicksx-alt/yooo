
import { GoogleGenAI, Type, ThinkingLevel, Modality } from "@google/genai";
import { PredictionRequest, PredictionResponse } from "../types";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY || '' });

export async function generateProjection(request: PredictionRequest, historicalData: any): Promise<PredictionResponse> {
  // Upgraded to Pro for maximum accuracy and tactical depth as requested
  const model = "gemini-3.1-pro-preview";
  const thinkingConfig = { thinkingLevel: ThinkingLevel.LOW };

  const prompt = `
    Analyze the following soccer player prop bet using a 3-layer Bayesian model approach:
    Player: ${request.playerName} (ID: ${request.playerId})
    Team: ${request.teamId}
    Opponent: ${request.opponentName} (ID: ${request.opponentId})
    Venue: ${request.venue}
    Prop Type: ${request.propType}
    Line: ${request.line}

    Historical Context:
    ${JSON.stringify(historicalData)}

    H2H DATA (CRITICAL):
    Analyze the 'h2hData' provided to identify historical patterns between these two specific teams. How has the player performed against this specific opponent in the past?

    MONEYLINE ODDS & GAME TYPE DETECTION (CRITICAL):
    Analyze the provided 'odds' (if available). Use these to categorize the game into one of the following types:
    - Dominance: One team is a heavy favorite (e.g., odds < 1.5). Expect high possession for the favorite and high defensive workload for the underdog.
    - Fragment: Odds are relatively even (e.g., both teams > 2.2). Expect a broken, transitional game with high turnover rates.
    - Pressure: A clear favorite but with a competitive underdog (e.g., favorite 1.7-2.0). Expect high-intensity pressing and tactical fouls.

    MATCH CONTEXT & KNOCKOUT ROUNDS (CRITICAL):
    Detect if this is a knockout match (e.g., Round of 16, Quarter-final, etc.) or a high-stakes derby.
    - Knockout matches often lead to more conservative play in early stages and high-risk play in late stages.
    - Derbies/Rivalries significantly increase tackling volume and foul rates.

    INJURY & LINEUP GUARDRAILS (SEARCH REQUIRED):
    Use the 'googleSearch' tool to find the latest injury news and confirmed lineups for this match.
    - If the player is a 'Game Time Decision' or returning from injury, adjust confidence down.
    - If key teammates are missing (e.g., the primary playmaker for a striker), adjust projected value down.

    PROBABILITY DENSITY (CRITICAL):
    Generate 10 data points for a probability density curve around the projected value.
    - The points should cover a range from 0 to 2x the projected value.
    - Ensure the peak of the curve aligns with your projected value.

    CORRELATIONS (CRITICAL):
    Identify 1-2 potential correlations with other players or props in this match.

    BAYESIAN MODEL LOGIC (CRITICAL - YOU MUST EXPLAIN THESE STEPS IN YOUR REASONING):
    1. Layer 1: Position Prior. Establish a base mean and standard deviation for the player's position (${request.propType} for this role).
    2. Layer 2: Gaussian Random Walk. Analyze time-varying recent form (momentum) using the provided 'matchHistory'. Is the player trending up or down?
    3. Layer 3: Covariates & Role Adjustments.
       - Opponent Strength: Adjust based on the opponent's defensive/offensive metrics (use 'opponentStats').
       - Home/Away: Factor in venue impact.
       - Tactical Role: Adjust based on the player's specific role (e.g., Pivot vs. Box-to-Box) and the multipliers found.
       - Reversal Flag: Identify if an upward or downward reversal is likely (e.g., over-performance due for regression).
    4. Likelihood: Use a Zero-Inflated Negative Binomial approach (best for soccer count stats like ${request.propType}).

    PROP-SPECIFIC LOGIC (DO NOT MIX):
    - Pass Attempts: Analyze team possession %, pressing intensity of opponent, and player's role in buildup. Focus on 'passes.total'.
    - Shots: Analyze xG per 90, opponent's shots allowed, and player's proximity to goal. Focus on 'shots.total'.
    - Saves: Analyze opponent's shot volume/profile (outside vs. inside box) and goalkeeper's save %. Focus on 'goals.saves'.
    - Clearances: Analyze opponent's cross volume, team's defensive style (low block vs. high line), and player's aerial dominance. Focus on 'tackles.blocks' and 'tackles.interceptions' as proxies if 'clearances' is missing.
    - Tackles: Analyze opponent's dribble volume, player's defensive engagement/role, and 'tackles.total'.

    CRITICAL INSTRUCTIONS:
    1. Detect and specify the Player's Exact Position and Tactical Role.
    2. Provide a DEEP tactical analysis covering pressing, possession, space/time, and matchup.
    3. Extract 10-20 recent match samples from the 'matchHistory' provided. For each, assign a 'matchDifficulty' (low/medium/high) and 'blockType' if applicable.
    4. Provide a multi-paragraph explanation (at least 3-4 paragraphs).
    5. YOUR 'reasoning' FIELD MUST EXPLICITLY DISCUSS THE 3-LAYER BAYESIAN MODEL STEPS (Prior, Momentum, Covariates) AND HOW THEY LEAD TO THE FINAL PROJECTION.
    6. Return the response in JSON format matching the PredictionResponse interface.
    7. Ensure the 'recentSamples' values are derived from the 'matchHistory' provided, not hallucinated.
  `;

  const response = await ai.models.generateContent({
    model: model,
    contents: prompt,
    config: {
      thinkingConfig: thinkingConfig,
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.OBJECT,
        properties: {
          player: {
            type: Type.OBJECT,
            properties: {
              id: { type: Type.NUMBER },
              name: { type: Type.STRING },
              team: { type: Type.STRING },
              role: { type: Type.STRING },
              position: { type: Type.STRING }
            },
            required: ["id", "name", "team", "role", "position"]
          },
          opponent: { type: Type.STRING },
          league: { type: Type.STRING },
          propType: { type: Type.STRING },
          line: { type: Type.NUMBER },
          projectedValue: { type: Type.NUMBER },
          recommendation: { type: Type.STRING, enum: ["over", "under"] },
          confidenceScore: { type: Type.NUMBER },
          confidenceLevel: { type: Type.STRING, enum: ["Low", "Medium", "High", "Very High"] },
          confidenceInterval: {
            type: Type.ARRAY,
            items: { type: Type.NUMBER }
          },
          explanation: { type: Type.STRING },
          odds: {
            type: Type.OBJECT,
            properties: {
              home: { type: Type.NUMBER },
              draw: { type: Type.NUMBER },
              away: { type: Type.NUMBER }
            }
          },
          gameType: { type: Type.STRING, enum: ["Dominance", "Fragment", "Pressure"] },
          matchContext: {
            type: Type.OBJECT,
            properties: {
              isKnockout: { type: Type.BOOLEAN },
              roundName: { type: Type.STRING },
              isDerby: { type: Type.BOOLEAN },
              rivalryContext: { type: Type.STRING }
            }
          },
          roleMultipliers: {
            type: Type.OBJECT,
            properties: {
              role: { type: Type.STRING },
              multiplier: { type: Type.NUMBER },
              reason: { type: Type.STRING }
            }
          },
          recentSamples: {
            type: Type.ARRAY,
            items: {
              type: Type.OBJECT,
              properties: {
                date: { type: Type.STRING },
                opponent: { type: Type.STRING },
                value: { type: Type.NUMBER },
                minutesPlayed: { type: Type.NUMBER },
                matchDifficulty: { type: Type.STRING, enum: ["low", "medium", "high"] },
                blockType: { type: Type.STRING }
              },
              required: ["date", "opponent", "value", "minutesPlayed", "matchDifficulty"]
            }
          },
          tacticalAnalysis: {
            type: Type.OBJECT,
            properties: {
              pressingStyle: { type: Type.STRING },
              possessionImpact: { type: Type.STRING },
              spaceAndTime: { type: Type.STRING },
              opponentShotProfile: { type: Type.STRING },
              defensiveWorkload: { type: Type.STRING }
            },
            required: ["pressingStyle", "possessionImpact", "spaceAndTime"]
          },
          bayesianMetrics: {
            type: Type.OBJECT,
            properties: {
              priorMean: { type: Type.NUMBER },
              momentumEffect: { type: Type.NUMBER },
              covariateAdjustment: { type: Type.NUMBER },
              reversalFlag: { type: Type.STRING, enum: ["upward_reversal_likely", "downward_reversal_likely", "stable"] }
            },
            required: ["priorMean", "momentumEffect", "covariateAdjustment", "reversalFlag"]
          },
          probabilityCurve: {
            type: Type.ARRAY,
            items: {
              type: Type.OBJECT,
              properties: {
                value: { type: Type.NUMBER },
                probability: { type: Type.NUMBER }
              }
            }
          },
          tacticalAlerts: {
            type: Type.ARRAY,
            items: {
              type: Type.OBJECT,
              properties: {
                type: { type: Type.STRING, enum: ["injury", "lineup", "tactical"] },
                message: { type: Type.STRING },
                severity: { type: Type.STRING, enum: ["low", "medium", "high"] }
              }
            }
          },
          correlations: {
            type: Type.ARRAY,
            items: {
              type: Type.OBJECT,
              properties: {
                player: { type: Type.STRING },
                prop: { type: Type.STRING },
                effect: { type: Type.STRING },
                impact: { type: Type.NUMBER }
              }
            }
          },
          tacticalInsights: { type: Type.STRING },
          reasoning: { type: Type.STRING }
        },
        required: [
          "player", "opponent", "league", "propType", "line", "projectedValue", 
          "recommendation", "confidenceScore", "confidenceLevel", "confidenceInterval", 
          "explanation", "recentSamples", "tacticalAnalysis", "bayesianMetrics", 
          "probabilityCurve", "tacticalInsights", "reasoning"
        ]
      },
      tools: [{ googleSearch: {} }],
    }
  });

  const text = response.text;
  if (!text || !text.trim()) {
    throw new Error("The AI model returned an empty response. This can happen if the tactical search fails or the model is overloaded. Please try again.");
  }

  try {
    // Clean potential markdown formatting
    const cleanedText = text.replace(/```json\n?|```/g, '').trim();
    if (!cleanedText) throw new Error("Empty JSON response");
    return JSON.parse(cleanedText);
  } catch (error) {
    console.error("JSON Parse Error. Raw text:", text);
    throw new Error("The AI model generated an invalid data format. Our engineers have been notified. Please try a different search or try again in a moment.");
  }
}

export async function getFastTacticalSummary(player: string, opponent: string): Promise<string> {
  const prompt = `Provide a lightning-fast tactical summary for ${player} vs ${opponent}. Focus on the most critical matchup detail. Max 2 sentences.`;
  const response = await ai.models.generateContent({
    model: "gemini-3.1-flash-lite-preview",
    contents: prompt
  });
  return response.text || "No summary available.";
}

export async function startTacticalChat() {
  return ai.chats.create({
    model: "gemini-3.1-pro-preview",
    config: {
      systemInstruction: "You are an elite soccer tactical analyst and prop betting expert. You help users understand the deep tactical nuances of player performances and match dynamics. Use data-driven reasoning and mention specific tactical concepts like 'low blocks', 'half-spaces', 'pressing triggers', and 'progressive passes'.",
    },
  });
}

export async function getMarketSentiment(playerName: string, propType: string, line: number): Promise<string> {
  const prompt = `
    Search for recent betting sentiment and line movement for ${playerName} ${propType} at ${line}.
    Identify if there is "Sharp" money movement or consensus among expert analysts.
    Provide a concise summary of the market sentiment.
  `;
  const response = await ai.models.generateContent({
    model: "gemini-3-flash-preview",
    contents: prompt,
    config: {
      tools: [{ googleSearch: {} }]
    }
  });
  return response.text || "No market sentiment data available.";
}

export async function generateVoiceBriefing(text: string): Promise<string | null> {
  try {
    const response = await ai.models.generateContent({
      model: "gemini-2.5-flash-preview-tts",
      contents: [{ parts: [{ text: `Give a professional, high-stakes tactical briefing for this betting slip: ${text}` }] }],
      config: {
        responseModalities: [Modality.AUDIO],
        speechConfig: {
          voiceConfig: {
            prebuiltVoiceConfig: { voiceName: 'Kore' },
          },
        },
      },
    });

    const base64Audio = response.candidates?.[0]?.content?.parts?.[0]?.inlineData?.data;
    return base64Audio ? `data:audio/mp3;base64,${base64Audio}` : null;
  } catch (error) {
    console.error("TTS failed:", error);
    return null;
  }
}

export async function analyzeSlipCorrelation(picks: any[]): Promise<string> {
  const prompt = `
    Analyze the correlation between the following soccer prop picks for a betting slip:
    ${JSON.stringify(picks)}

    Consider:
    - Team synergy (players on the same team).
    - Opponent impact (multiple players against the same weak/strong defense).
    - Game flow (e.g., if one team dominates possession, how does it affect both teams' props?).

    Provide a concise, high-level tactical summary of the correlation risk/reward.
  `;

  const response = await ai.models.generateContent({
    model: "gemini-3-flash-preview",
    contents: prompt,
    config: {
      tools: [{ googleSearch: {} }]
    }
  });

  return response.text || "No correlation analysis available.";
}

export async function parseNaturalLanguageQuery(query: string): Promise<Partial<PredictionRequest>> {
  const prompt = `
    Parse the following soccer prop query into a structured object:
    Query: "${query}"

    Extract:
    - playerName (e.g., "Lamine Yamal")
    - opponentName (e.g., "Villarreal")
    - venue (home/away)
    - propType: Map the user's input to one of these EXACT values: 
      - "pass_attempts" (for "passes", "pas attempts", "pass volume")
      - "shots" (for "shooting", "shots on target", "total shots")
      - "saves" (for "goalkeeper saves", "stops")
      - "clearances" (for "defensive clearances")
      - "tackles" (for "defensive tackles", "challenges")
    - line (number, e.g., 52.5)

    Return JSON.
  `;

  const response = await ai.models.generateContent({
    model: "gemini-3-flash-preview",
    contents: prompt,
    config: {
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.OBJECT,
        properties: {
          playerName: { type: Type.STRING },
          opponentName: { type: Type.STRING },
          venue: { type: Type.STRING, enum: ["home", "away"] },
          propType: { type: Type.STRING, enum: ["pass_attempts", "shots", "saves", "clearances", "tackles"] },
          line: { type: Type.NUMBER }
        }
      }
    }
  });

  const text = response.text;
  if (!text) return {};

  try {
    const cleanedText = text.replace(/```json\n?|```/g, '').trim();
    return JSON.parse(cleanedText);
  } catch (error) {
    console.error("Parse Query Error. Raw text:", text);
    return {};
  }
}
