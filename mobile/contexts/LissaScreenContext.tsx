import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { LissaContext } from '@/lib/api';

type LissaScreenContextValue = {
  context?: LissaContext;
  setContext: (context?: LissaContext) => void;
};

const LissaScreenContext = createContext<LissaScreenContextValue | null>(null);

export function LissaScreenContextProvider({ children }: { children: React.ReactNode }) {
  const [context, setContextState] = useState<LissaContext | undefined>();
  const setContext = useCallback((next?: LissaContext) => {
    setContextState(next);
  }, []);
  const value = useMemo(() => ({ context, setContext }), [context, setContext]);

  return <LissaScreenContext.Provider value={value}>{children}</LissaScreenContext.Provider>;
}

export function useLissaScreenContext() {
  const value = useContext(LissaScreenContext);
  if (!value) {
    throw new Error('useLissaScreenContext must be used inside LissaScreenContextProvider');
  }
  return value;
}