import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  SymbolQuote,
  Candle,
  HistoryTrade,
  Position,
  BotConfig,
  BotSignal,
  TradeDirection,
  XgbConfirmation,
  AiDecision,
  AiEnsembleConfirmation,
  CombinedBrainDecision,
} from './types/trading';
import { evaluateAiEntrySignal } from './utils/marketSimulator';
import { soundEffects } from './utils/audio';
import { ProfessionalDashboard } from './components/ProfessionalDashboard';

const BRIDGE = 'http://127.0.0.1:8765';

type SmartProfitStatus = {
  enabled: boolean;
  trailPoints: number;
  stepPoints: number;
  breakEvenOffsetPoints: number;
  checkSeconds: number;
};

const DEFAULT_SMART_PROFIT: SmartProfitStatus = {
  enabled: false,
  trailPoints: 60,
  stepPoints: 5,
  breakEvenOffsetPoints: 0,
  checkSeconds: 0.1,
};

const fallbackQuote: SymbolQuote = {
  symbol: 'XAUUSD',
  name: 'Gold vs US Dollar',
  bid: 0,
  ask: 0,
  spread: 0,
  changePercent: 0,
  high: 0,
  low: 0,
  digits: 2,
};

function normalizeSnapshot(data: any) {
  const q = data.quote;
  return {
    balance: Number(data.account?.balance ?? 0),
    equity: Number(data.account?.equity ?? 0),
    margin: Number(data.account?.margin ?? 0),
    freeMargin: Number(data.account?.freeMargin ?? 0),
    marginLevel: Number(data.account?.marginLevel ?? 0),
    deposit: Number(data.account?.balance ?? 0),
    quote: q ? {
      symbol: q.symbol,
      name: q.name || q.symbol,
      bid: Number(q.bid || 0),
      ask: Number(q.ask || 0),
      spread: Number(q.spread || 0),
      changePercent: Number(q.changePercent || 0),
      high: Number(q.high || q.bid || 0),
      low: Number(q.low || q.bid || 0),
      digits: Number(q.digits || 2),
    } : fallbackQuote,
    positions: (data.positions || []) as Position[],
    history: (data.history || []) as HistoryTrade[],
  };
}

export default function App() {
  const [balance, setBalance] = useState(0);
  const [accountEquity, setAccountEquity] = useState(0);
  const [accountMargin, setAccountMargin] = useState(0);
  const [accountFreeMargin, setAccountFreeMargin] = useState(0);
  const [accountMarginLevel, setAccountMarginLevel] = useState(0);
  const [accountLogin, setAccountLogin] = useState<number | null>(null);
  const [accountServer, setAccountServer] = useState('');
  const [deposit, setDeposit] = useState(0);
  const [quotes, setQuotes] = useState<Record<string, SymbolQuote>>({ XAUUSD: fallbackQuote });
  const [selectedSymbol, setSelectedSymbol] = useState('XAUUSD');
  const [candles, setCandles] = useState<Candle[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [history, setHistory] = useState<HistoryTrade[]>([]);
  const [viewLayout, setViewLayout] = useState<'dual' | 'mobile-only'>('dual');
  const [config, setConfig] = useState<BotConfig>({
    autoTrade: false,
    compoundMode: 'capped',
    burstOrdersCount: 10,
    simulationSpeed: 2,
    riskAggressiveness: 'balanced',
    targetProfitPips: 1.25,
    soundEnabled: true,
    selectedSymbol: 'XAUUSD',
  });
  const [signal, setSignal] = useState<BotSignal | null>(null);
  const [xgbConfirmation, setXgbConfirmation] = useState<XgbConfirmation | null>(null);
  const [ensembleConfirmation, setEnsembleConfirmation] = useState<AiEnsembleConfirmation | null>(null);
  const [aiDecision, setAiDecision] = useState<AiDecision | null>(null);
  const [brainDecision, setBrainDecision] = useState<CombinedBrainDecision | null>(null);
  const [bridgeOnline, setBridgeOnline] = useState(false);
  const [bridgeError, setBridgeError] = useState('');
  const [smartProfit, setSmartProfit] = useState<SmartProfitStatus>(DEFAULT_SMART_PROFIT);
  const [riskStatus, setRiskStatus] = useState<any>(null);
  const lastHistoryLengthRef = useRef(0);
  const cooldownRef = useRef(0);
  const previousPositionCountRef = useRef(0);
  const autoBusyRef = useRef(false);
  const lastBrainCheckRef = useRef(0);
  const lastBrainDirectionRef = useRef<TradeDirection | 'neutral'>('neutral');
  const brainBusyRef = useRef(false);
  const brainRequestEpochRef = useRef(0);

  useEffect(() => soundEffects.setEnabled(config.soundEnabled), [config.soundEnabled]);

  const currentQuote = quotes[selectedSymbol] || Object.values(quotes)[0] || fallbackQuote;
  const totalFloatingProfit = positions.reduce((sum, p) => sum + Number(p.profit || 0), 0);
  const equity = accountEquity > 0 ? accountEquity : Number((balance + totalFloatingProfit).toFixed(2));
  const margin = accountMargin;
  const freeMargin = accountFreeMargin;
  const marginLevel = accountMarginLevel;
  const refresh = useCallback(async () => {
    try {
      const [snapRes, candleRes, riskRes] = await Promise.all([
        fetch(`${BRIDGE}/api/snapshot?symbol=${encodeURIComponent(selectedSymbol)}`, { cache: 'no-store' }),
        fetch(`${BRIDGE}/api/candles?symbol=${encodeURIComponent(selectedSymbol)}&count=100`, { cache: 'no-store' }),
        fetch(`${BRIDGE}/api/risk`, { cache: 'no-store' }),
      ]);
      if (!snapRes.ok || !candleRes.ok) throw new Error(`Bridge HTTP ${snapRes.status}/${candleRes.status}`);
      const snap = await snapRes.json();
      const c = await candleRes.json();
      const risk = await riskRes.json().catch(() => ({ ok: false, error: 'Risk Guardian returned invalid JSON.' }));
      if (!snap.ok) throw new Error(snap.error || 'MT5 snapshot failed');
      setRiskStatus(riskRes.ok && risk.ok ? (risk.risk || null) : { riskStateError: risk.error || `Risk Guardian HTTP ${riskRes.status}`, halted: true, executionArmed: false });

      const n = normalizeSnapshot(snap);
      const actualSymbol = n.quote.symbol || selectedSymbol;
      setSmartProfit({ ...DEFAULT_SMART_PROFIT, ...(snap.smartProfit || {}) });
      setBridgeOnline(true);
      setBridgeError('');
      setBalance(n.balance);
      setAccountEquity(n.equity);
      setAccountMargin(n.margin);
      setAccountFreeMargin(n.freeMargin);
      setAccountMarginLevel(n.marginLevel);
      setAccountLogin(Number(snap.account?.login || 0) || null);
      setAccountServer(String(snap.account?.server || ''));
      if (typeof snap.runtime?.autoTrade === 'boolean') {
        setConfig(prev => prev.autoTrade === Boolean(snap.runtime.autoTrade) ? prev : ({ ...prev, autoTrade: Boolean(snap.runtime.autoTrade) }));
      }
      if (deposit <= 0) setDeposit(n.deposit);
      setQuotes(prev => ({ ...prev, [actualSymbol]: n.quote }));
      if (actualSymbol !== selectedSymbol) {
        setSelectedSymbol(actualSymbol);
        setConfig(prev => ({ ...prev, selectedSymbol: actualSymbol }));
      }
      setPositions(n.positions);
      setHistory(n.history);
      setCandles((c.candles || []) as Candle[]);

      if (n.history.length > lastHistoryLengthRef.current) {
        const newest = n.history[0];
        if (newest) {
          if (newest.profit > 0) {
            soundEffects.playTakeProfit();
          } else {
            soundEffects.playStopLoss();
          }
        }
      }
      lastHistoryLengthRef.current = n.history.length;

      if (n.positions.length === 0 && previousPositionCountRef.current > 0) {
        cooldownRef.current = Date.now() + 1800 / Math.max(1, config.simulationSpeed);
        // A closed basket requires a fresh coordinated brain vote before re-entry.
        setBrainDecision(null);
        lastBrainCheckRef.current = 0;
      }
      previousPositionCountRef.current = n.positions.length;

      if (n.positions.length === 0 && n.balance > 0 && (c.candles || []).length > 25) {
        const evalSignal = evaluateAiEntrySignal(c.candles, n.quote.bid);
        setSignal(evalSignal);

        const now = Date.now();
        if (evalSignal.type === 'neutral') {
          brainRequestEpochRef.current += 1;
          setBrainDecision(null);
          setXgbConfirmation(null);
          setEnsembleConfirmation(null);
          setAiDecision(null);
          lastBrainDirectionRef.current = 'neutral';
          lastBrainCheckRef.current = 0;
          return;
        }
        const directionChanged = lastBrainDirectionRef.current !== evalSignal.type;
        const brainRefreshDue = directionChanged || now - lastBrainCheckRef.current >= 5000;
        if (directionChanged) {
          brainRequestEpochRef.current += 1;
          // Do not leave a previous-direction approval visible while the new
          // direction's coordinated decision is being calculated.
          setBrainDecision(null);
          setXgbConfirmation(null);
          setEnsembleConfirmation(null);
          setAiDecision(null);
        }
        if (brainRefreshDue && !brainBusyRef.current) {
          lastBrainCheckRef.current = now;
          lastBrainDirectionRef.current = evalSignal.type;
          brainBusyRef.current = true;
          const requestEpoch = brainRequestEpochRef.current;
          try {
            // One coordinated request runs every configured brain against the same
            // MT5 candle snapshot. The bridge returns the individual outputs and
            // one final server-side approval verdict.
            const brainRes = await fetch(
              `${BRIDGE}/api/brain?symbol=${encodeURIComponent(selectedSymbol)}&direction=${encodeURIComponent(evalSignal.type)}`,
              { cache: 'no-store' },
            );
            const brainData = await brainRes.json();
            if (!brainRes.ok || !brainData.ok) {
              throw new Error(brainData?.error || `Unified brain HTTP ${brainRes.status}`);
            }
            if (requestEpoch !== brainRequestEpochRef.current) return;
            setXgbConfirmation(brainData.xgb as XgbConfirmation);
            setEnsembleConfirmation(brainData.ensemble as AiEnsembleConfirmation);
            setAiDecision(brainData.ai as AiDecision);
            setBrainDecision(brainData.brain as CombinedBrainDecision);
          } catch (brainErr: any) {
            if (requestEpoch !== brainRequestEpochRef.current) return;
            const reason = brainErr?.message || String(brainErr);
            setBrainDecision({
              enabled: true,
              ready: false,
              approved: false,
              symbol: selectedSymbol,
              timeframe: 'M5',
              direction: evalSignal.type,
              consensusProbability: 0,
              consensusThreshold: 0.60,
              votes: [],
              checks: { coordinator: false },
              blockers: ['coordinator'],
              reason: `Unified brain unavailable: ${reason}`,
              modelFlow: [],
              timestamp: Date.now(),
            });
          } finally {
            brainBusyRef.current = false;
          }
        }
      }
    } catch (err: any) {
      setBridgeOnline(false);
      setBridgeError(err?.message || String(err));
    }
  }, [selectedSymbol, deposit, config.simulationSpeed]);

  const triggerBurst = useCallback(async (direction: TradeDirection, requireBrainApproval = false) => {
    if (autoBusyRef.current) return;
    if (positions.length > 0) return;
    if (!requireBrainApproval) {
      const confirmed = window.confirm(`Submit a 10-ticket ${direction.toUpperCase()} XAUUSD basket on MT5 account ${accountLogin ?? 'unknown'}?

The backend Risk Guardian will calculate size and attach broker-side stops. This is a real order request.`);
      if (!confirmed) return;
    }
    if (!bridgeOnline) {
      setBridgeError('MT5 bridge is offline. Start START_MT5_BOT.ps1 first.');
      return;
    }

    autoBusyRef.current = true;
    try {
      const res = await fetch(`${BRIDGE}/api/order`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: selectedSymbol,
          direction,
          requireBrainApproval,
          manualConfirm: !requireBrainApproval,
          expectedLogin: accountLogin,
          expectedServer: accountServer,
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        if (Number(data.countFilled || 0) > 0) await refresh();
        throw new Error(data.error || `${data.countFilled || 0}/10 orders filled. ${data.results?.find((r: any) => !r.ok)?.comment || 'MT5 burst incomplete'}`);
      }
      setBridgeError('');
      soundEffects.playBurstOpen();
      await refresh();
    } catch (err: any) {
      setBridgeError(err?.message || String(err));
    } finally {
      autoBusyRef.current = false;
    }
  }, [bridgeOnline, positions.length, selectedSymbol, accountLogin, accountServer, refresh]);

  const closeAllPositions = useCallback(async () => {
    if (!positions.length) return;
    try {
      const res = await fetch(`${BRIDGE}/api/close_all`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: selectedSymbol }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error || data.results?.find((r: any) => !r.ok)?.comment || 'MT5 close failed');
      }
      await refresh();
    } catch (err: any) {
      setBridgeError(err?.message || String(err));
    }
  }, [positions.length, selectedSymbol, refresh]);

  const toggleAutoTrade = useCallback(async () => {
    if (!bridgeOnline || !accountLogin || !accountServer) {
      setBridgeError('Connect MT5 and verify the account before arming automatic trading.');
      return;
    }
    const enabling = !config.autoTrade;
    if (enabling) {
      const confirmed = window.confirm(`Arm NovaFury V2 automatic trading on MT5 account ${accountLogin} (${accountServer})?

XAUUSD only · Balanced risk · 10-ticket baskets · broker-side stop required. Start on a demo account.`);
      if (!confirmed) return;
    }
    try {
      const response = await fetch(`${BRIDGE}${enabling ? '/api/arm' : '/api/disarm'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(enabling ? { expectedLogin: accountLogin, expectedServer: accountServer } : {}),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Risk Guardian refused the state change');
      setConfig(prev => ({ ...prev, autoTrade: enabling }));
      setBridgeError('');
      await refresh();
    } catch (error: any) {
      setConfig(prev => ({ ...prev, autoTrade: false }));
      setBridgeError(error?.message || String(error));
    }
  }, [bridgeOnline, accountLogin, accountServer, config.autoTrade, refresh]);

  const resetBalance = useCallback(async () => {
    // Live MT5 balance is broker-controlled. Preserve the button in the UI,
    // but never fake or overwrite the real account balance.
    await refresh();
  }, [refresh]);

  const updateConfig = useCallback((newCfg: Partial<BotConfig>) => {
    setConfig(prev => ({ ...prev, ...newCfg, selectedSymbol: newCfg.selectedSymbol || prev.selectedSymbol }));
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!config.autoTrade || positions.length > 0 || !bridgeOnline || Date.now() < cooldownRef.current) return;
    if (!candles.length || !currentQuote.bid || autoBusyRef.current) return;

    const evalSignal = evaluateAiEntrySignal(candles, currentQuote.bid);
    setSignal(evalSignal);
    const unifiedBrainReady =
      !!brainDecision &&
      brainDecision.enabled &&
      brainDecision.ready &&
      brainDecision.approved &&
      brainDecision.direction === evalSignal.type &&
      brainDecision.symbol === selectedSymbol &&
      Date.now() - brainDecision.timestamp <= 15000;
    if (
      evalSignal.confidence >= 65 &&
      (evalSignal.type === 'buy' || evalSignal.type === 'sell') &&
      unifiedBrainReady
    ) {
      triggerBurst(evalSignal.type, true);
    }
  }, [config.autoTrade, positions.length, bridgeOnline, candles, currentQuote.bid, brainDecision, triggerBurst]);

  useEffect(() => {
    // Real MT5 has no safe way to "reset balance"; clicking the old simulator
    // control simply reloads the real account data.
    if (deposit > 0 && balance > 0 && positions.length === 0) return;
  }, [deposit, balance, positions.length]);

  return (
    <ProfessionalDashboard
      bridgeOnline={bridgeOnline}
      bridgeError={bridgeError}
      selectedSymbol={selectedSymbol}
      quote={currentQuote}
      quotes={quotes}
      candles={candles}
      positions={positions}
      history={history}
      balance={balance}
      equity={equity}
      margin={margin}
      freeMargin={freeMargin}
      marginLevel={marginLevel}
      floatingProfit={totalFloatingProfit}
      deposit={deposit}
      signal={signal}
      xgbConfirmation={xgbConfirmation}
      ensembleConfirmation={ensembleConfirmation}
      aiDecision={aiDecision}
      brainDecision={brainDecision}
      config={config}
      riskStatus={riskStatus}
      smartProfit={smartProfit}
      onToggleAutoTrade={toggleAutoTrade}
      onUpdateConfig={updateConfig}
      onManualTrade={direction => triggerBurst(direction)}
      onCloseAll={closeAllPositions}
      onSelectSymbol={symbol => { if (!symbol.toUpperCase().startsWith('XAUUSD')) return; setSelectedSymbol(symbol); updateConfig({ selectedSymbol: symbol }); }}
    />
  );
}
