
import { GoogleGenAI, Type } from "@google/genai";
import { PredictionRequest, PredictionResponse } from "../types";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY || '' });

export async function generateProjection(request: PredictionRequest, historicalData: any): Promise<PredictionResponse> {
  const prompt = `
    Analyze the following soccer player prop bet:
    Player: ${request.playerName} (ID: ${request.playerId})
    Team: ${request.teamId}
    Opponent: ${request.opponentName} (ID: ${request.opponentId})
    League ID: ${request.leagueId}
    Venue: ${request.venue}
    Prop Type: ${request.propType}
    Line: ${request.line}

    Historical Context:
    ${JSON.stringify(historicalData)}

    Provide a detailed data-driven projection.
    CRITICAL INSTRUCTIONS:
    1. Provide a DEEP, comprehensive, multi-paragraph tactical analysis in the 'explanation' and 'tacticalInsights' fields. The API is data-rich, so explain the tactical matchups, historical trends, player roles, and specific reasons for the projection in detail. Do not be brief. Write at least 3-4 paragraphs of detailed analysis.
    2. Extract and provide at least 10 to 20 recent match samples in the 'recentSamples' array. Do not limit to just 5. Include the minutes played in each match.
    3. Ensure the 'reasoning' field is also detailed and explains the mathematical or statistical basis for the projection.
    
    Return the response in JSON format matching the PredictionResponse interface.
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
              team: { type: Type.STRING }
            },
            required: ["id", "name", "team"]
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
                minutesPlayed: { type: Type.NUMBER }
              },
              required: ["date", "opponent", "value", "minutesPlayed"]
            }
          },
          tacticalInsights: { type: Type.STRING },
          reasoning: { type: Type.STRING }
        },
        required: [
          "player", "opponent", "league", "propType", "line", "projectedValue", 
          "recommendation", "confidenceScore", "confidenceLevel", "confidenceInterval", 
          "explanation", "recentSamples", "tacticalInsights", "reasoning"
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
    - propType (passes, shots, saves)
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
          propType: { type: Type.STRING, enum: ["passes", "shots", "saves"] },
          line: { type: Type.NUMBER }
        }
      }
    }
  });

  return JSON.parse(response.text || '{}');
}
