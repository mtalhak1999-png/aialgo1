import React from 'react';
import { SymbolQuote } from '../types/trading';

interface QuotesTabProps {
  quotes: Record<string, SymbolQuote>;
  selectedSymbol: string;
  onSelectSymbol: (symbol: string) => void;
}

export const QuotesTab: React.FC<QuotesTabProps> = ({
  quotes,
  selectedSymbol,
  onSelectSymbol,
}) => {
  return (
    <div className="h-full flex flex-col bg-[#121214] text-white">
      {/* Header */}
      <div className="px-4 py-3 border-b border-neutral-800 flex items-center justify-between">
        <span className="text-sm font-semibold tracking-wide">Simple Quotes</span>
        <span className="text-xs text-neutral-400 font-mono">LIVE TICKS</span>
      </div>

      {/* Quotes list */}
      <div className="flex-1 overflow-y-auto divide-y divide-neutral-800/80">
        {Object.values(quotes).filter(q => q.symbol.toUpperCase().startsWith("XAUUSD")).map(q => {
          const isSelected = q.symbol === selectedSymbol;
          return (
            <div
              key={q.symbol}
              onClick={() => onSelectSymbol(q.symbol)}
              className={`px-4 py-3 cursor-pointer transition-colors flex items-center justify-between hover:bg-neutral-800/50 ${
                isSelected ? 'bg-neutral-800/30' : ''
              }`}
            >
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-sm tracking-tight">{q.symbol}</span>
                  {isSelected && (
                    <span className="text-[10px] text-blue-400 font-medium">ACTIVE</span>
                  )}
                </div>
                <div className="text-[11px] text-neutral-400 mt-0.5">{q.name}</div>
                <div className="text-[10px] text-neutral-500 font-mono mt-0.5">
                  Spread: {q.spread}
                </div>
              </div>

              <div className="text-right">
                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <span className="text-[10px] text-neutral-500 block">Bid</span>
                    <span className="font-mono text-xs font-semibold text-neutral-200 tabular-nums">
                      {q.bid.toFixed(q.digits)}
                    </span>
                  </div>
                  <div className="text-right">
                    <span className="text-[10px] text-neutral-500 block">Ask</span>
                    <span className="font-mono text-xs font-semibold text-blue-400 tabular-nums">
                      {q.ask.toFixed(q.digits)}
                    </span>
                  </div>
                </div>
                <div className="text-[11px] font-mono mt-1 text-right text-emerald-400 font-medium">
                  +{q.changePercent}%
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
