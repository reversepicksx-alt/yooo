
import { GoogleGenAI, Type } from "@google/genai";
import { PredictionRequest, PredictionResponse } from "../types";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY || '' });

export async function generateProjection(request: PredictionRequest, historicalData: any): Promise<PredictionResponse> {
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

    BAYESIAN MODEL LOGIC (CRITICAL):
    1. Layer 1: Position Prior. Establish a base mean and standard deviation for the player's position (${request.propType} for this role).
    2. Layer 2: Gaussian Random Walk. Analyze time-varying recent form (momentum) using the provided 'matchHistory'. Is the player trending up or down?
    3. Layer 3: Covariates & Role Adjustments.
       - Opponent Strength: Adjust based on the opponent's defensive/offensive metrics.
       - Home/Away: Factor in venue impact.
       - Tactical Role: Adjust based on the player's specific role (e.g., Pivot vs. Box-to-Box).
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
    5. Return the response in JSON format matching the PredictionResponse interface.
    6. Ensure the 'recentSamples' values are derived from the 'matchHistory' provided, not hallucinated.
  `;

  const response = await ai.models.generateContent({
    model: "gemini-3.1-pro-preview",
    contents: prompt,
    config: {
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
          tacticalInsights: { type: Type.STRING },
          reasoning: { type: Type.STRING }
        },
        required: [
          "player", "opponent", "league", "propType", "line", "projectedValue", 
          "recommendation", "confidenceScore", "confidenceLevel", "confidenceInterval", 
          "explanation", "recentSamples", "tacticalAnalysis", "bayesianMetrics", "tacticalInsights", "reasoning"
        ]
      }
    }
  });

  return JSON.parse(response.text || '{}');
}

export async function parseNaturalLanguageQuery(query: string): Promise<Partial<PredictionRequest>> {
  const prompt = `
    Parse the following soccer prop query into a structured object:
    Query: "${query}"

    Extract:
    - playerName
    - opponentName
    - venue (home/away)
    - propType (pass_attempts, shots, saves, clearances, tackles)
    - line (number)

    Return JSON.
  `;

  const response = await ai.models.generateContent({
    model: "gemini-3.1-pro-preview",
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

  return JSON.parse(response.text || '{}');
}
