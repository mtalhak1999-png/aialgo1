import React from 'react';
import { BotSignal, BotConfig, Position, XgbConfirmation, AiDecision, AiEnsembleConfirmation, CombinedBrainDecision } from '../types/trading';
import {
  BrainCircuit,
  Zap,
  TrendingUp,
  Activity,
  Clock,
  ShieldCheck,
  Target,
  BarChart3,
  Sliders,
  Volume2,
  VolumeX,
} from 'lucide-react';

interface AiStrategyRadarProps {
  signal: BotSignal | null;
  xgbConfirmation: XgbConfirmation | null;
  ensembleConfirmation: AiEnsembleConfirmation | null;
  aiDecision: AiDecision | null;
  brainDecision: CombinedBrainDecision | null;
  balance: number;
  equity: number;
  deposit: number;
  currentLotSize: number;
  totalBursts: number;
  winningBursts: number;
  config: BotConfig;
  positions: Position[];
  onUpdateConfig: (newConfig: Partial<BotConfig>) => void;
  onTriggerManualBurst: (direction: 'buy' | 'sell') => void;
  onCloseAll: () => void;
}

export const AiStrategyRadar: React.FC<AiStrategyRadarProps> = ({
  signal,
  xgbConfirmation,
  ensembleConfirmation,
  aiDecision,
  brainDecision,
  balance,
  equity,
  deposit,
  currentLotSize,
  totalBursts,
  winningBursts,
  config,
  positions,
  onUpdateConfig,
  onTriggerManualBurst,
  onCloseAll,
}) => {
  const growthMultiplier = deposit > 0 ? (balance / deposit).toFixed(2) : '—';

  return (
    <div className="bg-slate-900 border border-slate-800 text-white rounded-2xl p-6 shadow-xl flex flex-col gap-6">
      {/* Header: AI Status */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-blue-500/20 text-blue-400 border border-blue-500/30 flex items-center justify-center">
            <BrainCircuit className="w-5 h-5 animate-pulse" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-white tracking-tight">
                NovaFury V2 AI Execution Engine
              </h2>
              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                RISK CONTROLLED
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              XAUUSD only · M5 signal · Balanced Risk Guardian · 10-ticket basket
            </p>
          </div>
        </div>

        {/* Global Controls */}
        <div className="flex items-center gap-2">
          {/* Turbo Speed Selector */}
          <div className="flex items-center bg-slate-800 p-1 rounded-lg border border-slate-700">
            <span className="text-[11px] text-slate-400 px-2">Speed:</span>
            {([1, 2, 5, 10] as const).map(spd => (
              <button
                key={spd}
                onClick={() => onUpdateConfig({ simulationSpeed: spd })}
                className={`px-2 py-0.5 text-xs font-mono rounded ${
                  config.simulationSpeed === spd
                    ? 'bg-blue-600 text-white font-bold'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {spd}x
              </button>
            ))}
          </div>

          <button
            onClick={() => onUpdateConfig({ soundEnabled: !config.soundEnabled })}
            className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 transition-colors"
            title="Toggle Sound"
          >
            {config.soundEnabled ? <Volume2 className="w-4 h-4 text-emerald-400" /> : <VolumeX className="w-4 h-4 text-slate-500" />}
          </button>
        </div>
      </div>

      {/* Live AI Decision Radar */}
      <div className="bg-slate-950/70 border border-slate-800/80 rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between text-xs">
          <div className="flex items-center gap-2 text-slate-300 font-semibold">
            <Zap className="w-4 h-4 text-amber-400" />
            <span>Live Algorithmic Entry Analysis</span>
          </div>
          <div className="flex items-center gap-1.5 font-mono text-emerald-400 text-xs">
            <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block"></span>
            <span>{signal ? `${signal.confidence}% Signal quality (not win probability)` : 'Waiting for setup'}</span>
          </div>
        </div>

        <p className="text-xs text-slate-300 leading-relaxed font-mono bg-slate-900/80 p-3 rounded-lg border border-slate-800">
          {signal?.reason ||
            'Scanning completed XAUUSD M5 candles for prior swing sweeps, EMA structure, RSI, volume and ATR conditions...'}
        </p>

        {/* Live Indicator Telemetry */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1 text-xs">
          <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <span className="text-[11px] text-slate-400 block">RSI Momentum</span>
            <span className="font-mono text-white font-semibold text-sm">
              {signal?.indicatorReadings.rsi ?? 48.2}
            </span>
          </div>

          <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <span className="text-[11px] text-slate-400 block">Trend Bias</span>
            <span
              className={`font-mono font-semibold text-sm capitalize ${
                signal?.indicatorReadings.trend === 'bullish'
                  ? 'text-blue-400'
                  : signal?.indicatorReadings.trend === 'bearish'
                  ? 'text-rose-400'
                  : 'text-slate-300'
              }`}
            >
              {signal?.indicatorReadings.trend ?? 'Bullish'}
            </span>
          </div>

          <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <span className="text-[11px] text-slate-400 block">Next Burst Sizing</span>
            <span className="font-mono text-amber-400 font-bold text-sm">Risk-sized × 10 tickets</span>
          </div>

          <div className="bg-slate-900/60 p-2.5 rounded-lg border border-slate-800">
            <span className="text-[11px] text-slate-400 block">Equity multiple</span>
            <span className="font-mono text-emerald-400 font-bold text-sm">
              {growthMultiplier}x
            </span>
          </div>
        </div>

        {/* Unified cross-brain decision: the single approval used by auto-trading */}
      <div className="bg-slate-900/70 border border-violet-900/60 rounded-lg px-3 py-3 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] uppercase tracking-wider text-violet-300 font-semibold">Unified AI Brain Consensus</span>
          <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold border ${
            !brainDecision
              ? 'bg-slate-800 text-slate-400 border-slate-700'
              : brainDecision.approved
                ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                : 'bg-rose-500/15 text-rose-300 border-rose-500/30'
          }`}>
            {!brainDecision ? 'SYNCING' : brainDecision.approved ? 'ALL BRAINS APPROVED' : 'ENTRY BLOCKED'}
          </span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-xs">
          <div className="bg-slate-950/60 rounded-md p-2"><span className="text-slate-500 block">Combined Score</span><strong className="text-white">{brainDecision ? `${(brainDecision.consensusProbability * 100).toFixed(1)}%` : '—'}</strong></div>
          <div className="bg-slate-950/60 rounded-md p-2"><span className="text-slate-500 block">Required Score</span><strong className="text-white">{brainDecision ? `${(brainDecision.consensusThreshold * 100).toFixed(0)}%` : '—'}</strong></div>
          <div className="bg-slate-950/60 rounded-md p-2"><span className="text-slate-500 block">Shared Side</span><strong className="text-white uppercase">{brainDecision?.direction ?? '—'}</strong></div>
          <div className="bg-slate-950/60 rounded-md p-2"><span className="text-slate-500 block">Brain Votes</span><strong className="text-white">{brainDecision?.votes?.filter(vote => vote.ready && vote.confirmed && vote.direction === brainDecision?.direction).length ?? 0}/{brainDecision?.votes?.length ?? 0}</strong></div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(brainDecision?.votes || []).map(vote => (
            <span key={vote.brain} className={`rounded px-2 py-1 text-[10px] font-mono border ${
              vote.ready && vote.confirmed && vote.direction === brainDecision?.direction
                ? 'bg-emerald-500/10 text-emerald-300 border-emerald-800/70'
                : 'bg-rose-500/10 text-rose-300 border-rose-900/70'
            }`}>
              {vote.brain.toUpperCase()} · {(vote.directionProbability * 100).toFixed(1)}%
            </span>
          ))}
          {brainDecision?.checks && Object.entries(brainDecision.checks).map(([name, passed]) => (
            <span key={name} className={`rounded px-2 py-1 text-[10px] font-mono border ${passed ? 'text-emerald-300 border-emerald-900/70' : 'text-rose-300 border-rose-900/70'}`}>
              {name.replace(/[A-Z]/g, m => ` ${m}`).toUpperCase()}: {passed ? 'PASS' : 'BLOCK'}
            </span>
          ))}
        </div>
        <p className="text-[11px] text-slate-300">
          {brainDecision?.reason || 'Waiting for the unified coordinator. Automatic trading requires a fresh approval from the complete configured brain chain.'}
        </p>
      </div>

      {/* Additive XGBoost confirmation telemetry */}
        <div className="bg-slate-900/60 border border-slate-800 rounded-lg px-3 py-2.5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <span className="text-[11px] uppercase tracking-wider text-slate-400">XGBoost M5 Confirmation</span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
                xgbConfirmation?.confirmed
                  ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                  : xgbConfirmation?.ready
                  ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                  : 'bg-slate-800 text-slate-500 border border-slate-700'
              }`}
            >
              {xgbConfirmation?.confirmed ? 'CONFIRMED' : xgbConfirmation?.ready ? 'FILTERING' : 'LOADING'}
            </span>
          </div>
          <div className="font-mono text-xs text-slate-300">
            {xgbConfirmation?.ready
              ? `${(xgbConfirmation.directionProbability * 100).toFixed(1)}% ${xgbConfirmation.direction.toUpperCase()} • threshold ${(xgbConfirmation.threshold * 100).toFixed(0)}%`
              : xgbConfirmation?.error || 'Training on live XAUUSD M5 history...'}
          </div>
        </div>
      </div>

      {/* Multi-model AI ensemble: LightGBM + CatBoost + meta-labeling + optional GRU/TCN */}
      <div className="bg-slate-900/60 border border-cyan-900/50 rounded-lg px-3 py-3 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] uppercase tracking-wider text-cyan-300 font-semibold">AI Brain Ensemble</span>
          <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
            !ensembleConfirmation
              ? 'bg-slate-800 text-slate-500 border border-slate-700'
              : !ensembleConfirmation.enabled
                ? 'bg-slate-800 text-slate-500 border border-slate-700'
              : ensembleConfirmation.confirmed
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : ensembleConfirmation.ready
                  ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                  : 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
          }`}>
            {!ensembleConfirmation ? 'INITIALIZING' : !ensembleConfirmation.enabled ? 'ENSEMBLE OFF' : ensembleConfirmation.confirmed ? 'CONFIRMED' : ensembleConfirmation.ready ? 'FILTERING' : 'TRAINING / BLOCKED'}
          </span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-xs">
          <div className="bg-slate-950/60 rounded-md p-2"><span className="text-slate-500 block">Direction Score</span><strong className="text-white">{ensembleConfirmation?.ready ? `${(ensembleConfirmation.directionProbability * 100).toFixed(1)}%` : '—'}</strong></div>
          <div className="bg-slate-950/60 rounded-md p-2"><span className="text-slate-500 block">Meta Success</span><strong className="text-white">{ensembleConfirmation?.ready ? `${(ensembleConfirmation.metaProbability * 100).toFixed(1)}%` : '—'}</strong></div>
          <div className="bg-slate-950/60 rounded-md p-2"><span className="text-slate-500 block">Training Bars</span><strong className="text-white">{ensembleConfirmation?.trainedBars?.toLocaleString() ?? '—'}</strong></div>
          <div className="bg-slate-950/60 rounded-md p-2"><span className="text-slate-500 block">Signal</span><strong className="text-white uppercase">{ensembleConfirmation?.direction ?? '—'}</strong></div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(ensembleConfirmation?.selectedModels || ['lightgbm', 'catboost', 'meta']).map(name => {
            const state = ensembleConfirmation?.models?.[name];
            return <span key={name} className={`rounded px-2 py-1 text-[10px] font-mono border ${
              state?.available ? 'bg-emerald-500/10 text-emerald-300 border-emerald-800/70' : 'bg-slate-950/70 text-slate-400 border-slate-800'
            }`}>
              {name.toUpperCase()}{state?.available && state.validationAuc != null ? ` · AUC ${(state.validationAuc * 100).toFixed(0)}%` : state?.available ? ' · READY' : ''}
            </span>;
          })}
        </div>
        <p className="text-[11px] text-slate-400">
          {ensembleConfirmation?.error || (ensembleConfirmation?.enabled
            ? `Direction threshold ${(ensembleConfirmation.threshold * 100).toFixed(0)}% · meta-label threshold ${(ensembleConfirmation.metaThreshold * 100).toFixed(0)}%. Selected models must all train successfully.`
            : 'Multi-model brain is disabled in .env.local. Configure LightGBM, CatBoost and meta-labeling on the local bridge.')}
        </p>
      </div>

      {/* Optional OpenAI supervisory AI */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-lg px-3 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] uppercase tracking-wider text-slate-400">AI Regime Supervisor</span>
          <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
            aiDecision?.enabled
              ? aiDecision.allowed
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
              : 'bg-slate-800 text-slate-500 border border-slate-700'
          }`}>
            {aiDecision?.enabled ? (aiDecision.allowed ? 'AI ALLOW' : 'AI BLOCK') : 'AI OFF'}
          </span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-2 font-mono text-xs">
          <div><span className="text-slate-500">Regime</span><div className="text-white capitalize">{aiDecision?.regime ?? '—'}</div></div>
          <div><span className="text-slate-500">Quality</span><div className="text-white">{aiDecision ? `${(aiDecision.entryQuality * 100).toFixed(0)}%` : '—'}</div></div>
          <div><span className="text-slate-500">Risk</span><div className="text-white capitalize">{aiDecision?.riskMode ?? '—'}</div></div>
          <div><span className="text-slate-500">Direction</span><div className="text-white uppercase">{aiDecision?.preferredDirection ?? '—'}</div></div>
        </div>
        <p className="text-[11px] text-slate-400 mt-2">
          {aiDecision?.rationale || 'Optional OpenAI supervisor. Existing rules + XGBoost remain the primary strategy.'}
        </p>
      </div>

      {/* V2 Risk Guardian profile */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-3"><span className="text-[10px] uppercase tracking-wide text-slate-500">Base basket risk</span><strong className="block text-lg text-white font-mono">0.35% equity</strong></div>
        <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-3"><span className="text-[10px] uppercase tracking-wide text-slate-500">Max allocation</span><strong className="block text-lg text-white font-mono">0.50% equity</strong></div>
        <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-3"><span className="text-[10px] uppercase tracking-wide text-slate-500">Compounding</span><strong className="block text-lg text-white">Capped / profit-funded</strong></div>
      </div>

      {/* Bot Execution Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-4 border-t border-slate-800">
        <div className="flex items-center gap-2">
          <button
            onClick={() => onTriggerManualBurst('buy')}
            className="px-3.5 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors shadow-xs"
          >
            <Zap className="w-3.5 h-3.5" />
            <span>Burst BUY ({config.burstOrdersCount}x)</span>
          </button>
          <button
            onClick={() => onTriggerManualBurst('sell')}
            className="px-3.5 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors shadow-xs"
          >
            <Zap className="w-3.5 h-3.5" />
            <span>Burst SELL ({config.burstOrdersCount}x)</span>
          </button>
          {positions.length > 0 && (
            <button
              onClick={onCloseAll}
              className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-amber-400 border border-slate-700 rounded-lg text-xs font-semibold transition-colors"
            >
              Close All ({positions.length})
            </button>
          )}
        </div>

      </div>
    </div>
  );
};
