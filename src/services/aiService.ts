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

export async function getFastTacticalSummary(player: string, opponent: string): Promise<string> {
  const completion = await getOpenAI().chat.completions.create({
    model: 'grok-beta',
    messages: [
      { role: 'system', content: 'You are an expert sports analyst.' },
      { role: 'user', content: `Provide a lightning-fast tactical summary for ${player} vs ${opponent}. Focus on the most critical matchup detail. Max 2 sentences.` }
    ]
  });
  return completion.choices[0].message.content || "No summary available.";
}

export async function startTacticalChat() {
  const history: any[] = [];
  return {
    sendMessage: async ({ message }: { message: string }) => {
      const completion = await getOpenAI().chat.completions.create({
        model: 'grok-beta',
        messages: [
          { role: 'system', content: "You are an elite soccer tactical analyst and prop betting expert. You help users understand the deep tactical nuances of player performances and match dynamics. Use data-driven reasoning and mention specific tactical concepts like 'low blocks', 'half-spaces', 'pressing triggers', and 'progressive passes'." },
          ...history,
          { role: 'user', content: message }
        ]
      });
      const response = completion.choices[0].message.content || "";
      history.push({ role: 'user', content: message });
      history.push({ role: 'assistant', content: response });
      return { text: response };
    }
  };
}

export async function getMarketSentiment(playerName: string, propType: string, line: number): Promise<string> {
  const completion = await getOpenAI().chat.completions.create({
    model: 'grok-beta',
    messages: [
      { role: 'system', content: 'You are an expert sports analyst.' },
      { role: 'user', content: `Search for recent betting sentiment and line movement for ${playerName} ${propType} at ${line}. Identify if there is "Sharp" money movement or consensus among expert analysts. Provide a concise summary of the market sentiment.` }
    ]
  });
  return completion.choices[0].message.content || "No market sentiment data available.";
}

export async function generateVoiceBriefing(text: string): Promise<string | null> {
  return null;
}

export async function analyzeSlipCorrelation(picks: any[]): Promise<string> {
  const completion = await getOpenAI().chat.completions.create({
    model: 'grok-beta',
    messages: [
      { role: 'system', content: 'You are an expert sports analyst.' },
      { role: 'user', content: `Analyze the correlation between the following soccer prop picks for a betting slip: ${JSON.stringify(picks)}. Consider team synergy, opponent impact, and game flow. Provide a concise, high-level tactical summary of the correlation risk/reward.` }
    ]
  });
  return completion.choices[0].message.content || "No correlation analysis available.";
}

export async function parseNaturalLanguageQuery(query: string): Promise<Partial<PredictionRequest>> {
  const completion = await getOpenAI().chat.completions.create({
    model: 'grok-beta',
    messages: [
      { role: 'system', content: 'You are an expert sports analyst. Return JSON only.' },
      { role: 'user', content: `Parse the following soccer prop query into a structured object: "${query}". Extract playerName, opponentName, venue, propType, line. Return JSON.` }
    ],
    response_format: { type: 'json_object' }
  });
  const content = completion.choices[0].message.content;
  if (!content) return {};
  return JSON.parse(content);
}
