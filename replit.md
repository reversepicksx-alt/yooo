# ReversePicks — Soccer Player Props Analytics

## Project Overview

ReversePicks is a soccer player props analytics platform. It combines a FastAPI + MongoDB backend with a React Native / Expo mobile frontend designed for App Store submission.

## Architecture

```
/
├── backend/          # FastAPI + MongoDB API server (port 8000)
│   ├── server.py     # Main FastAPI app with startup events
│   ├── emergentintegrations/llm/chat.py  # LLM shim (Google AI + OpenAI)
│   ├── grok_engine.py
│   ├── calibration.py
│   ├── team_resolver.py
│   └── routes/       # intel, picks, auth, etc.
│
├── mobile/           # Expo React Native app (port 5000, web preview)
│   ├── app/
│   │   ├── _layout.tsx        # Root layout with AuthContext
│   │   ├── (auth)/login.tsx   # Login screen
│   │   └── (tabs)/            # Tab navigator screens
│   │       ├── scan.tsx       # Scan/Predict tab
│   │       ├── picks.tsx      # Picks tab
│   │       ├── intel.tsx      # Intel tab
│   │       ├── chat.tsx       # Tactical Chat tab
│   │       └── account.tsx    # Account tab
│   ├── contexts/AuthContext.tsx
│   ├── lib/api.ts             # API client (EXPO_PUBLIC_API_URL)
│   ├── constants/colors.ts    # Dark navy/teal/gold theme
│   └── babel.config.js
│
└── frontend/         # Legacy React web frontend (not active)
```

## Key Configuration

- **Bundle ID**: `com.reversepicks.app`
- **Backend port**: 8000
- **Frontend port**: 5000 (Expo web preview)
- **MongoDB**: localhost:27017, DB `reversepicks`
- **API URL env var**: `EXPO_PUBLIC_API_URL=http://localhost:8000`

## Workflows

- **Start Backend**: Starts MongoDB, then uvicorn on `0.0.0.0:8000`
- **Start application**: Expo web dev server on port 5000

## Dependency Notes

- `react-native-reanimated` pinned to `~3.16.1` (v4.x requires `react-native-worklets` which is not available as a standalone package)
- `babel-preset-expo` pinned to `~54.0.10` (compatible with expo 54)
- `react-native-web` is `0.19.13` (Expo expects `^0.21.0` but works)
- `expo-linking` manually installed (not auto-included with expo-router)

## LLM Integration Shim

`backend/emergentintegrations/` is a local shim since the `emergentintegrations` package is not on PyPI. It maps:
- Gemini models → `google-generativeai`
- OpenAI models → `openai` AsyncOpenAI
- xAI/Grok models → `openai` with `https://api.x.ai/v1` base URL

## User Preferences

- Dark navy/teal/gold sports analytics theme
- App Store-ready Expo mobile frontend
- Backend and data pipelines remain unchanged
