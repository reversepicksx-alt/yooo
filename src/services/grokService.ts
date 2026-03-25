import OpenAI from 'openai';
import { PredictionRequest, PredictionResponse } from '../types';

let openai: OpenAI | null = null;

function getOpenAI(): OpenAI {
  if (!openai) {
    const apiKey = process.env.GROK_API_KEY;
    if (!apiKey) {
      throw new Error('GROK_API_KEY environment variable is required');
    }
    openai = new OpenAI({
      apiKey,
      baseURL: 'https://api.x.ai/v1',
    });
  }
  return openai;
}

export async function generateProjection(request: PredictionRequest, historicalData: any): Promise<PredictionResponse> {
  const prompt = `
    Analyze the following soccer player prop bet using only the provided API data:
    Player: ${request.playerName}
    Team: ${request.teamId}
    Opponent: ${request.opponentName}
    Venue: ${request.venue}
    Prop Type: ${request.propType}
    Line: ${request.line}

    Historical Context:
    ${JSON.stringify(historicalData)}

    CRITICAL INSTRUCTIONS:
    1. Use ONLY the provided API data for your analysis. Do not use external knowledge.
    2. Provide a data-driven tactical analysis covering pressing, possession, space/time, and matchup.
    3. Return the response in JSON format matching the PredictionResponse interface.
    4. Ensure the 'recentSamples' values are derived from the 'matchHistory' provided.
  `;

  const completion = await getOpenAI().chat.completions.create({
    model: 'grok-beta',
    messages: [
      { role: 'system', content: 'You are an expert sports analyst. You MUST ONLY use the provided API data for your analysis.' },
      { role: 'user', content: prompt }
    ],
    response_format: { type: 'json_object' }
  });

  const content = completion.choices[0].message.content;
  if (!content) throw new Error("Empty Grok response");

  return JSON.parse(content);
}
