import React from 'react';
import { BotConfig, BotSignal } from '../types/trading';
import { Zap, Play, Pause, Volume2, VolumeX, ShieldAlert, TrendingUp } from 'lucide-react';

interface BotControlPanelProps {
  config: BotConfig;
  onUpdateConfig: (newConfig: Partial<BotConfig>) => void;
  onToggleAutoTrade: () => void;
  onTriggerManualBurst: (direction: 'buy' | 'sell') => void;
  onCloseAll: () => void;
  positionsCount: number;
  currentLotSize: number;
  currentBalance: number;
  signal: BotSignal | null;
}

export const BotControlPanel: React.FC<BotControlPanelProps> = ({
  config,
  onUpdateConfig,
  onToggleAutoTrade,
  onTriggerManualBurst,
  onCloseAll,
  positionsCount,
  currentLotSize,
  currentBalance,
  signal,
}) => {
  return (
    <div className="h-full flex flex-col bg-[#121214] text-white overflow-y-auto p-4 space-y-5">
      {/* Bot Master Toggle */}
      <div className="bg-[#18181b] border border-neutral-800 rounded-xl p-4 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm">AI Autonomous Bot</span>
            <span
              className={`w-2 h-2 rounded-full ${
                config.autoTrade ? 'bg-emerald-400 animate-pulse' : 'bg-neutral-500'
              }`}
            />
          </div>
          <p className="text-xs text-neutral-400 mt-1">
            {config.autoTrade
              ? 'Bot actively scanning market sweeps & executing bursts'
              : 'Bot is paused. Manual control available.'}
          </p>
        </div>

        <button
          onClick={onToggleAutoTrade}
          className={`px-4 py-2 text-xs font-semibold rounded-lg flex items-center gap-1.5 transition-colors ${
            config.autoTrade
              ? 'bg-rose-600 hover:bg-rose-500 text-white'
              : 'bg-emerald-600 hover:bg-emerald-500 text-white'
          }`}
        >
          {config.autoTrade ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
          <span>{config.autoTrade ? 'Pause Bot' : 'Start Bot'}</span>
        </button>
      </div>

      {/* Manual Instant Burst Action Triggers */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-neutral-400">
          <span className="font-semibold text-neutral-300">Instant Burst Trigger</span>
          <span className="font-mono text-neutral-400">
            Risk Guardian sizing · 10-order basket
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2.5">
          <button
            onClick={() => onTriggerManualBurst('buy')}
            className="py-2.5 px-3 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold transition-colors flex items-center justify-center gap-1.5 shadow-xs"
          >
            <Zap className="w-3.5 h-3.5 text-blue-200" />
            <span>Burst BUY (10x)</span>
          </button>
          <button
            onClick={() => onTriggerManualBurst('sell')}
            className="py-2.5 px-3 bg-red-600 hover:bg-red-500 text-white rounded-lg text-xs font-semibold transition-colors flex items-center justify-center gap-1.5 shadow-xs"
          >
            <Zap className="w-3.5 h-3.5 text-red-200" />
            <span>Burst SELL (10x)</span>
          </button>
        </div>

        {positionsCount > 0 && (
          <button
            onClick={onCloseAll}
            className="w-full py-2 bg-neutral-800 hover:bg-neutral-700 text-amber-400 border border-neutral-700 rounded-lg text-xs font-medium transition-colors flex items-center justify-center gap-1.5"
          >
            <ShieldAlert className="w-3.5 h-3.5" />
            <span>Close All {positionsCount} Open Positions Now</span>
          </button>
        )}
      </div>

      {/* Compounding Mode & Logic */}
      <div className="bg-[#18181b] border border-neutral-800 rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-neutral-200 flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5 text-blue-400" />
            Compound Lot Algorithm
          </span>
          <span className="text-[10px] text-neutral-400 font-mono">Dynamic Scaling</span>
        </div>

        <div className="grid grid-cols-3 gap-1.5 p-1 bg-neutral-900 rounded-lg">
          <div className="col-span-3 text-center py-1.5 text-xs font-semibold text-emerald-300">Capped profit-funded compounding</div>
        </div>

        <p className="text-[11px] text-neutral-400 leading-relaxed">
          Lot sizing is calculated from the fixed basket risk budget and broker contract. Only a capped share of realized net profits can fund a small risk bonus.
        </p>
      </div>

      {/* Burst Parameters */}
      <div className="bg-[#18181b] border border-neutral-800 rounded-xl p-4 space-y-3">
        <span className="text-xs font-semibold text-neutral-200 block">Burst Configuration</span>

        <div className="flex items-center justify-between text-xs">
          <span className="text-neutral-400">Simultaneous Orders per Burst:</span>
          <div className="flex items-center gap-1">
            <span className="px-2.5 py-1 rounded text-xs font-mono font-bold bg-neutral-200 text-neutral-900">10x FIXED</span>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs pt-2 border-t border-neutral-800">
          <span className="text-neutral-400">Simulation Speed:</span>
          <div className="flex items-center gap-1 font-mono">
            {([1, 2, 5, 10] as const).map(spd => (
              <button
                key={spd}
                onClick={() => onUpdateConfig({ simulationSpeed: spd })}
                className={`px-2 py-0.5 rounded text-xs ${
                  config.simulationSpeed === spd
                    ? 'bg-blue-600 text-white font-bold'
                    : 'bg-neutral-800 text-neutral-400 hover:text-white'
                }`}
              >
                {spd}x
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between text-xs pt-2 border-t border-neutral-800">
          <span className="text-neutral-400">Audio Feedback:</span>
          <button
            onClick={() => onUpdateConfig({ soundEnabled: !config.soundEnabled })}
            className="flex items-center gap-1 text-xs text-neutral-300 hover:text-white"
          >
            {config.soundEnabled ? (
              <>
                <Volume2 className="w-3.5 h-3.5 text-emerald-400" />
                <span>Enabled</span>
              </>
            ) : (
              <>
                <VolumeX className="w-3.5 h-3.5 text-neutral-500" />
                <span>Muted</span>
              </>
            )}
          </button>
        </div>
      </div>

      <div className="pt-2 border-t border-neutral-800/80 text-[11px] text-neutral-500 leading-relaxed">Account balance and equity are read-only MT5 values. Position size and protective stops are calculated by the backend Risk Guardian.</div>

    </div>
  );
};
