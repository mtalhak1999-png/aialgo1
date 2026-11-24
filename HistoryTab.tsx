import React from 'react';
import { HistoryTrade } from '../types/trading';

interface HistoryTabProps {
  history: HistoryTrade[];
  deposit: number;
  currentBalance: number;
}

export const HistoryTab: React.FC<HistoryTabProps> = ({
  history,
  deposit,
  currentBalance,
}) => {
  const totalProfit = history.reduce((sum, t) => sum + t.profit, 0);

  return (
    <div className="h-full flex flex-col bg-[#121214] text-white">
      {/* History Top Summary Bar */}
      <div className="p-4 border-b border-neutral-800 space-y-2">
        <div className="flex items-center justify-between text-xs text-neutral-400">
          <span>Profit:</span>
          <span className={`font-mono font-semibold tabular-nums ${totalProfit >= 0 ? 'text-blue-400' : 'text-red-400'}`}>
            {totalProfit >= 0 ? '+' : ''}{totalProfit.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
          </span>
        </div>
        <div className="flex items-center justify-between text-xs text-neutral-400">
          <span>Deposit:</span>
          <span className="font-mono text-neutral-200 tabular-nums">
            {deposit.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
          </span>
        </div>
        <div className="flex items-center justify-between text-xs text-neutral-400 pt-1 border-t border-neutral-800/60">
          <span className="font-medium text-neutral-300">Balance:</span>
          <span className="font-mono font-bold text-white tabular-nums text-sm">
            {currentBalance.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USD
          </span>
        </div>
      </div>

      {/* Trades List */}
      <div className="flex-1 overflow-y-auto divide-y divide-neutral-800/70">
        {history.length === 0 ? (
          <div className="p-8 text-center text-xs text-neutral-500 italic">
            No closed trades yet. Activate AI Auto-Trader to begin compounding.
          </div>
        ) : (
          history.map(trade => (
            <div key={trade.id} className="px-4 py-2.5 flex items-center justify-between hover:bg-neutral-800/30 transition-colors">
              <div>
                <div className="flex items-center gap-1.5">
                  <span className="font-semibold text-xs text-neutral-100">{trade.symbol}</span>
                  <span className={`text-[11px] font-mono uppercase ${trade.direction === 'buy' ? 'text-blue-400' : 'text-red-400'}`}>
                    {trade.direction} {trade.lotSize.toFixed(2)}
                  </span>
                </div>
                <div className="text-[11px] font-mono text-neutral-400 mt-0.5">
                  {trade.openPrice.toFixed(3)} → {trade.closePrice.toFixed(3)}
                </div>
              </div>
              <div className="text-right">
                <span
                  className={`font-mono text-xs font-bold tabular-nums ${
                    trade.profit >= 0 ? 'text-[#2979FF]' : 'text-[#FF3B30]'
                  }`}
                >
                  {trade.profit >= 0 ? '+' : ''}{trade.profit.toFixed(2)}
                </span>
                <div className="text-[10px] text-neutral-500 font-mono mt-0.5">
                  {new Date(trade.closeTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
