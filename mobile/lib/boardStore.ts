/**
 * Lightweight module-level store for passing a market selection from
 * the Board tab into the Predict (scan) tab without requiring a
 * new state-management library.
 *
 * The Board tab calls setBoardPendingMarket() then navigates to the
 * Predict tab. scan.tsx reads and clears it on focus.
 */

import { MarketBoardItem } from './api';

let _pending: MarketBoardItem | null = null;

export function setBoardPendingMarket(market: MarketBoardItem): void {
  _pending = market;
}

export function getBoardPendingMarket(): MarketBoardItem | null {
  return _pending;
}

export function clearBoardPendingMarket(): void {
  _pending = null;
}
