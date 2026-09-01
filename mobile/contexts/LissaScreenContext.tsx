import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { JarvisContext } from '@/lib/api';

type LissaScreenContextValue = {
  context?: JarvisContext;
  setContext: (context?: JarvisContext) => void;
};

const LissaScreenContext = createContext<LissaScreenContextValue | null>(null);

export function LissaScreenContextProvider({ children }: { children: React.ReactNode }) {
  const [context, setContextState] = useState<JarvisContext | undefined>();
  const setContext = useCallback((next?: JarvisContext) => {
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