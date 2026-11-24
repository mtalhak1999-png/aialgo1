import React, { useState, useEffect } from 'react';
import {
  Position,
  SymbolQuote,
  HistoryTrade,
  BotConfig,
  BotSignal,
  TradeDirection,
} from '../types/trading';
import {
  CandlestickChart,
  LineChart,
  ArrowUpRight,
  History as HistoryIcon,
  Settings as SettingsIcon,
  Plus,
  Layers,
  ChevronRight,
  TrendingUp,
} from 'lucide-react';
import { QuotesTab } from './QuotesTab';
import { ChartTab } from './ChartTab';
import { HistoryTab } from './HistoryTab';
import { BotControlPanel } from './BotControlPanel';

interface MobileTerminalProps {
  positions: Position[];
  quote: SymbolQuote;
  quotes: Record<string, SymbolQuote>;
  candles: any[];
  history: HistoryTrade[];
  balance: number;
  equity: number;
  margin: number;
  freeMargin: number;
  marginLevel: number;
  deposit: number;
  config: BotConfig;
  onUpdateConfig: (newConfig: Partial<BotConfig>) => void;
  onToggleAutoTrade: () => void;
  onTriggerManualBurst: (direction: TradeDirection) => void;
  onCloseAll: () => void;
  onSelectSymbol: (symbol: string) => void;
  currentLotSize: number;
  signal: BotSignal | null;
}

export const MobileTerminal: React.FC<MobileTerminalProps> = ({
  positions,
  quote,
  quotes,
  candles,
  history,
  balance,
  equity,
  margin,
  freeMargin,
  marginLevel,
  deposit,
  config,
  onUpdateConfig,
  onToggleAutoTrade,
  onTriggerManualBurst,
  onCloseAll,
  onSelectSymbol,
  currentLotSize,
  signal,
}) => {
  const [activeTab, setActiveTab] = useState<'quotes' | 'chart' | 'trade' | 'history' | 'settings'>('trade');
  const [currentTime, setCurrentTime] = useState('8:04');

  useEffect(() => {
    const update = () => {
      const now = new Date();
      setCurrentTime(
        now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: false })
      );
    };
    update();
    const interval = setInterval(update, 30000);
    return () => clearInterval(interval);
  }, []);

  const totalFloatingProfit = positions.reduce((sum, p) => sum + p.profit, 0);

  // Formatting helpers matching MetaTrader 5 on iOS
  const formatMoney = (val: number) => {
    return val.toLocaleString('en-US', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  };

  return (
    <div className="relative w-full max-w-[390px] h-[780px] bg-black rounded-[48px] p-3 shadow-2xl border-4 border-neutral-800 flex flex-col select-none overflow-hidden mx-auto">
      {/* iPhone Dynamic Island */}
      <div className="absolute top-4 left-1/2 -translate-x-1/2 w-28 h-6 bg-black rounded-full z-50 flex items-center justify-end px-2.5">
        <div className="w-2.5 h-2.5 rounded-full bg-neutral-900 border border-neutral-800"></div>
      </div>

      {/* iOS Status Bar */}
      <div className="pt-2 px-6 pb-1 flex items-center justify-between text-white text-[12px] font-semibold tracking-tight z-40 bg-[#121214]">
        <span>{currentTime}</span>
        <div className="flex items-center gap-1.5 text-[11px]">
          <span className="font-mono">5G</span>
          <div className="w-5 h-2.5 border border-white rounded-[3px] p-0.5 flex items-center">
            <div className="h-full w-3.5 bg-white rounded-[1px]"></div>
          </div>
        </div>
      </div>

      {/* Inner Screen Area */}
      <div className="flex-1 bg-[#121214] text-white flex flex-col overflow-hidden rounded-b-[36px]">
        {/* TRADE TAB (Main view from video) */}
        {activeTab === 'trade' && (
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* Top MT5 Bar */}
            <div className="px-4 py-2 border-b border-neutral-800/80 bg-[#121214]">
              {/* Top Icons Row */}
              <div className="flex items-center justify-between text-neutral-300 mb-1">
                <button
                  onClick={() => setActiveTab('quotes')}
                  className="p-1 hover:text-white transition-colors"
                  title="Quotes / Account"
                >
                  <Layers className="w-4 h-4 text-neutral-400" />
                </button>

                {/* Floating Profit or Static USD */}
                {positions.length > 0 ? (
                  <span
                    className={`font-mono text-base font-bold tabular-nums ${
                      totalFloatingProfit >= 0 ? 'text-[#2979FF]' : 'text-[#FF3B30]'
                    }`}
                  >
                    {totalFloatingProfit >= 0 ? '' : ''}
                    {formatMoney(totalFloatingProfit)} USD
                  </span>
                ) : (
                  <span className="font-semibold text-sm tracking-wide text-neutral-100">
                    USD
                  </span>
                )}

                <button
                  onClick={() => onTriggerManualBurst('buy')}
                  className="p-1 hover:text-white transition-colors"
                  title="New Order"
                >
                  <Plus className="w-4 h-4 text-neutral-300" />
                </button>
              </div>

              {/* Account Balance Metrics Grid (Exact MT5 layout from video) */}
              <div className="space-y-0.5 text-[12px] font-normal pt-1">
                <div className="flex justify-between items-center text-neutral-400">
                  <span className="text-neutral-300">Balance:</span>
                  <span className="font-mono tabular-nums text-white font-medium">
                    {formatMoney(balance)}
                  </span>
                </div>
                <div className="flex justify-between items-center text-neutral-400">
                  <span className="text-neutral-300">Equity:</span>
                  <span className="font-mono tabular-nums text-white font-medium">
                    {formatMoney(equity)}
                  </span>
                </div>
                {positions.length > 0 && (
                  <>
                    <div className="flex justify-between items-center text-neutral-400">
                      <span className="text-neutral-300">Margin:</span>
                      <span className="font-mono tabular-nums text-white font-medium">
                        {formatMoney(margin)}
                      </span>
                    </div>
                    <div className="flex justify-between items-center text-neutral-400">
                      <span className="text-neutral-300">Free Margin:</span>
                      <span className="font-mono tabular-nums text-white font-medium">
                        {formatMoney(freeMargin)}
                      </span>
                    </div>
                    <div className="flex justify-between items-center text-neutral-400">
                      <span className="text-neutral-300">Margin Level (%):</span>
                      <span className="font-mono tabular-nums text-white font-medium">
                        {formatMoney(marginLevel)}
                      </span>
                    </div>
                  </>
                )}
                {positions.length === 0 && (
                  <div className="flex justify-between items-center text-neutral-400">
                    <span className="text-neutral-300">Free Margin:</span>
                    <span className="font-mono tabular-nums text-white font-medium">
                      {formatMoney(freeMargin)}
                    </span>
                  </div>
                )}
              </div>

              {/* Positions Header */}
              {positions.length > 0 && (
                <div className="pt-2 text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">
                  Positions
                </div>
              )}
            </div>

            {/* Position List OR Iconic Rising Arrow Logo */}
            <div className="flex-1 overflow-y-auto">
              {positions.length > 0 ? (
                /* Staked burst order list matching video */
                <div className="divide-y divide-neutral-800/60">
                  {positions.map(pos => (
                    <div
                      key={pos.id}
                      className="px-4 py-2 flex items-center justify-between hover:bg-neutral-800/30 transition-colors"
                    >
                      {/* Left: Symbol, direction lot, prices */}
                      <div>
                        <div className="flex items-center gap-1.5">
                          <span className="font-bold text-xs text-white tracking-tight">
                            {pos.symbol}
                          </span>
                          <span
                            className={`text-xs font-semibold ${
                              pos.direction === 'buy' ? 'text-[#2979FF]' : 'text-[#FF3B30]'
                            }`}
                          >
                            {pos.direction} {pos.lotSize.toFixed(2)}
                          </span>
                        </div>
                        <div className="text-[11px] font-mono text-neutral-400 mt-0.5 tabular-nums">
                          {pos.openPrice.toFixed(3)} → {pos.currentPrice.toFixed(3)}
                        </div>
                      </div>

                      {/* Right: Floating Profit (blue if positive, red if negative) */}
                      <div className="text-right">
                        <span
                          className={`font-mono text-xs font-bold tabular-nums ${
                            pos.profit >= 0 ? 'text-[#2979FF]' : 'text-[#FF3B30]'
                          }`}
                        >
                          {pos.profit >= 0 ? '' : ''}
                          {pos.profit.toFixed(2)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                /* ICONIC LOGO WATERMARK (Exact design from 0:00, 0:03, 0:07, 0:11, 0:21 in the video) */
                <div className="h-full flex flex-col items-center justify-center p-6 text-center">
                  <div className="relative my-auto flex flex-col items-center">
                    {/* SVG of the exact stylized rising arrow from the video */}
                    <div className="w-24 h-24 text-white flex items-center justify-center relative">
                      <svg
                        viewBox="0 0 100 100"
                        className="w-20 h-20 fill-white filter drop-shadow-md transition-transform duration-500 hover:scale-105"
                      >
                        {/* Upper large ascending arrow with thick angled shaft */}
                        <path
                          d="M 40 60 
                             L 65 35 
                             L 65 47 
                             L 78 47 
                             L 78 22 
                             L 53 22 
                             L 53 35 
                             L 65 35 
                             L 35 65 
                             Z"
                          fill="white"
                        />
                        {/* Lower parallel diagonal accent bar 1 */}
                        <path
                          d="M 22 78 
                             L 34 66 
                             L 40 72 
                             L 28 84 
                             Z"
                          fill="white"
                        />
                        {/* Lower parallel diagonal accent bar 2 */}
                        <path
                          d="M 36 84 
                             L 48 72 
                             L 54 78 
                             L 42 90 
                             Z"
                          fill="white"
                        />
                      </svg>
                    </div>

                    <div className="mt-4 space-y-1">
                      <div className="text-xs font-mono font-semibold text-neutral-300 tracking-wider">
                        AI ALGO SCANNING LIQUIDITY
                      </div>
                      <div className="text-[11px] text-neutral-500 font-mono">
                        Target Symbol: {quote.symbol} · Risk-sized lot
                      </div>
                    </div>

                    {/* Scanning radar indicator */}
                    <div className="mt-5 w-44 h-1 bg-neutral-800 rounded-full overflow-hidden">
                      <div className="h-full bg-blue-500 rounded-full animate-pulse w-3/4"></div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* QUOTES TAB */}
        {activeTab === 'quotes' && (
          <QuotesTab
            quotes={quotes}
            selectedSymbol={config.selectedSymbol}
            onSelectSymbol={sym => {
              onSelectSymbol(sym);
              setActiveTab('trade');
            }}
          />
        )}

        {/* CHART TAB */}
        {activeTab === 'chart' && (
          <ChartTab
            candles={candles}
            quote={quote}
            positions={positions}
          />
        )}

        {/* HISTORY TAB */}
        {activeTab === 'history' && (
          <HistoryTab
            history={history}
            deposit={deposit}
            currentBalance={balance}
          />
        )}

        {/* SETTINGS / BOT TAB */}
        {activeTab === 'settings' && (
          <BotControlPanel
            config={config}
            onUpdateConfig={onUpdateConfig}
            onToggleAutoTrade={onToggleAutoTrade}
            onTriggerManualBurst={onTriggerManualBurst}
            onCloseAll={onCloseAll}
            positionsCount={positions.length}
            currentLotSize={currentLotSize}
            currentBalance={balance}
            signal={signal}
          />
        )}

        {/* Bottom Tab Bar (Exact 5 items matching MetaTrader 5 on iOS) */}
        <div className="h-16 border-t border-neutral-800 bg-[#121214] px-3 flex items-center justify-between text-neutral-400">
          <button
            onClick={() => setActiveTab('quotes')}
            className={`flex flex-col items-center gap-0.5 flex-1 transition-colors ${
              activeTab === 'quotes' ? 'text-[#2979FF]' : 'hover:text-neutral-200'
            }`}
          >
            <Layers className="w-5 h-5" />
            <span className="text-[10px] font-medium">Quotes</span>
          </button>

          <button
            onClick={() => setActiveTab('chart')}
            className={`flex flex-col items-center gap-0.5 flex-1 transition-colors ${
              activeTab === 'chart' ? 'text-[#2979FF]' : 'hover:text-neutral-200'
            }`}
          >
            <CandlestickChart className="w-5 h-5" />
            <span className="text-[10px] font-medium">Chart</span>
          </button>

          <button
            onClick={() => setActiveTab('trade')}
            className={`flex flex-col items-center gap-0.5 flex-1 transition-colors relative ${
              activeTab === 'trade' ? 'text-[#2979FF]' : 'hover:text-neutral-200'
            }`}
          >
            <div className="relative">
              <TrendingUp className="w-5 h-5" />
              {positions.length > 0 && (
                <span className="absolute -top-1 -right-2 px-1 text-[9px] font-mono font-bold bg-blue-600 text-white rounded-full">
                  {positions.length}
                </span>
              )}
            </div>
            <span className="text-[10px] font-medium">Trade</span>
          </button>

          <button
            onClick={() => setActiveTab('history')}
            className={`flex flex-col items-center gap-0.5 flex-1 transition-colors ${
              activeTab === 'history' ? 'text-[#2979FF]' : 'hover:text-neutral-200'
            }`}
          >
            <HistoryIcon className="w-5 h-5" />
            <span className="text-[10px] font-medium">History</span>
          </button>

          <button
            onClick={() => setActiveTab('settings')}
            className={`flex flex-col items-center gap-0.5 flex-1 transition-colors ${
              activeTab === 'settings' ? 'text-[#2979FF]' : 'hover:text-neutral-200'
            }`}
          >
            <SettingsIcon className="w-5 h-5" />
            <span className="text-[10px] font-medium">Settings</span>
          </button>
        </div>

        {/* Home Indicator Bar */}
        <div className="w-32 h-1 bg-neutral-600 rounded-full mx-auto my-1.5"></div>
      </div>
    </div>
  );
};
