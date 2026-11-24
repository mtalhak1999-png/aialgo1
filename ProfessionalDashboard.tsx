import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  Clock,
  Cpu,
  Gauge,
  History as HistoryIcon,
  LayoutDashboard,
  Pause,
  Play,
  ShieldCheck,
  TrendingUp,
  Wallet,
  Wifi,
  WifiOff,
  Zap,
  CircleAlert,
  Bot,
  Settings2,
  Layers,
  Menu,
  X,
  GitBranch,
  Radio,
  ChevronRight,
  LockKeyhole,
} from 'lucide-react';
import {
  AiDecision,
  AiEnsembleConfirmation,
  BotConfig,
  BotSignal,
  Candle,
  CombinedBrainDecision,
  HistoryTrade,
  Position,
  SymbolQuote,
  TradeDirection,
  XgbConfirmation,
} from '../types/trading';


type ProviderName = 'openai' | 'anthropic';
type ProviderInfo = { enabled: boolean; configured: boolean; model: string };
type ProviderConfig = Record<ProviderName, ProviderInfo>;
const PROVIDER_BRIDGE = 'http://127.0.0.1:8765';
const DEFAULT_PROVIDER_CONFIG: ProviderConfig = {
  openai: { enabled: false, configured: false, model: 'gpt-5.4-mini' },
  anthropic: { enabled: false, configured: false, model: 'claude-sonnet-5' },
};

type SmartProfitStatus = {
  enabled: boolean;
  trailPoints: number;
  stepPoints: number;
  breakEvenOffsetPoints: number;
  checkSeconds: number;
};

type PageKey = 'overview' | 'intelligence' | 'positions' | 'history' | 'settings';

interface ProfessionalDashboardProps {
  bridgeOnline: boolean;
  bridgeError: string;
  selectedSymbol: string;
  quote: SymbolQuote;
  quotes: Record<string, SymbolQuote>;
  candles: Candle[];
  positions: Position[];
  history: HistoryTrade[];
  balance: number;
  equity: number;
  margin: number;
  freeMargin: number;
  marginLevel: number;
  floatingProfit: number;
  deposit: number;
  signal: BotSignal | null;
  xgbConfirmation: XgbConfirmation | null;
  ensembleConfirmation: AiEnsembleConfirmation | null;
  aiDecision: AiDecision | null;
  brainDecision: CombinedBrainDecision | null;
  config: BotConfig;
  riskStatus: any;
  smartProfit: SmartProfitStatus;
  onToggleAutoTrade: () => void;
  onUpdateConfig: (newConfig: Partial<BotConfig>) => void;
  onManualTrade: (direction: TradeDirection) => void;
  onCloseAll: () => void;
  onSelectSymbol: (symbol: string) => void;
}

const money = (value: number) => new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
}).format(Number.isFinite(value) ? value : 0);

const price = (value: number, digits = 2) => Number(value || 0).toFixed(Math.max(0, Math.min(5, digits)));
const timeLabel = (value: number) => value ? new Date(value).toLocaleString([], { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

function Panel({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <section className={`nf-panel ${className}`}>{children}</section>;
}

function PanelHeading({ icon: Icon, title, detail, action }: { icon?: React.ElementType; title: string; detail?: string; action?: React.ReactNode }) {
  return (
    <div className="nf-panel-heading">
      <div className="nf-panel-heading-main">
        {Icon && <div className="nf-heading-icon"><Icon size={17} /></div>}
        <div className="nf-min-w-0">
          <h2>{title}</h2>
          {detail && <p>{detail}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}

function StatusPill({ good, children }: { good: boolean; children: React.ReactNode }) {
  return <span className={`nf-status ${good ? 'is-good' : 'is-warn'}`}><span className="nf-status-dot" />{children}</span>;
}

function MetricCard({ label, value, sub, icon: Icon, tone = 'blue', positive }: { label: string; value: string; sub: string; icon: React.ElementType; tone?: 'blue' | 'green' | 'amber' | 'violet'; positive?: boolean }) {
  return (
    <Panel className="nf-metric-card">
      <div className="nf-metric-copy">
        <p className="nf-eyebrow">{label}</p>
        <p className={`nf-metric-value ${positive === undefined ? '' : positive ? 'is-positive' : 'is-negative'}`}>{value}</p>
        <p className="nf-metric-sub">{sub}</p>
      </div>
      <div className={`nf-metric-icon tone-${tone}`}><Icon size={19} /></div>
    </Panel>
  );
}

function MarketChart({ candles, quote, positions }: { candles: Candle[]; quote: SymbolQuote; positions: Position[] }) {
  const chart = useMemo(() => {
    const source = (candles || []).slice(-64);
    if (!source.length) return null;
    const minRaw = Math.min(...source.map(c => c.low));
    const maxRaw = Math.max(...source.map(c => c.high));
    const pad = Math.max((maxRaw - minRaw) * 0.12, Math.abs(maxRaw) * 0.00015, 0.01);
    const min = minRaw - pad;
    const max = maxRaw + pad;
    const range = Math.max(max - min, 0.000001);
    const x = (index: number) => 18 + (index / Math.max(1, source.length - 1)) * 724;
    const y = (value: number) => 254 - ((value - min) / range) * 218;
    let ema9 = source[0].close;
    let ema21 = source[0].close;
    const k9 = 2 / 10;
    const k21 = 2 / 22;
    const rows = source.map((c, index) => {
      ema9 = c.close * k9 + ema9 * (1 - k9);
      ema21 = c.close * k21 + ema21 * (1 - k21);
      const cx = x(index);
      return {
        ...c,
        x: cx,
        openY: y(c.open), closeY: y(c.close), highY: y(c.high), lowY: y(c.low),
        bodyY: Math.min(y(c.open), y(c.close)),
        bodyH: Math.max(2, Math.abs(y(c.close) - y(c.open))),
        ema9Y: y(ema9), ema21Y: y(ema21),
        up: c.close >= c.open,
      };
    });
    return { rows, min, max, range, y, ema9Line: rows.map(r => `${r.x},${r.ema9Y}`).join(' '), ema21Line: rows.map(r => `${r.x},${r.ema21Y}`).join(' ') };
  }, [candles]);

  if (!chart) {
    return <div className="nf-chart-empty"><Activity size={22} /><span>Waiting for live MT5 candle history…</span></div>;
  }

  const currentY = Math.max(22, Math.min(258, chart.y(quote.bid)));
  return (
    <div className="nf-market-chart">
      <svg viewBox="0 0 760 280" preserveAspectRatio="none" className="nf-chart-svg">
        {[0, 1, 2, 3, 4].map(i => {
          const yy = 24 + i * 54;
          const val = chart.max - (i / 4) * chart.range;
          return <g key={i}><line x1="0" x2="758" y1={yy} y2={yy} stroke="rgba(201,190,163,0.13)" strokeDasharray="3 6" /><text x="754" y={yy - 5} textAnchor="end" fill="#948c7d" fontSize="9">{price(val, quote.digits)}</text></g>;
        })}
        <polyline points={chart.ema21Line} fill="none" stroke="#d1ad69" strokeWidth="1.5" strokeOpacity="0.9" />
        <polyline points={chart.ema9Line} fill="none" stroke="#8ccfc2" strokeWidth="1.6" strokeOpacity="0.95" />
        {chart.rows.map((c, i) => <g key={`${c.time}-${i}`}>
          <line x1={c.x} x2={c.x} y1={c.highY} y2={c.lowY} stroke={c.up ? '#53c6a0' : '#dc7772'} strokeWidth="1.2" />
          <rect x={c.x - 3.1} y={c.bodyY} width="6.2" height={c.bodyH} rx="1" fill={c.up ? '#53c6a0' : '#dc7772'} opacity="0.94" />
        </g>)}
        {positions.slice(0, 4).map((p, i) => {
          const yy = chart.y(p.openPrice);
          return <g key={p.id}><line x1="0" x2="744" y1={yy} y2={yy} stroke={p.direction === 'buy' ? '#83b7d4' : '#dc7772'} strokeDasharray="5 4" strokeWidth="1" opacity="0.9" /><text x="6" y={yy - 4} fill={p.direction === 'buy' ? '#a5d2e8' : '#efaaa3'} fontSize="9">{p.direction.toUpperCase()} #{i + 1}</text></g>;
        })}
        <line x1="0" x2="758" y1={currentY} y2={currentY} stroke="#f4e8ce" strokeDasharray="2 4" strokeOpacity="0.8" />
        <circle cx="742" cy={currentY} r="3.5" fill="#f4e8ce" />
      </svg>
      <div className="nf-chart-legend">
        <span><i className="legend-ema9" />EMA 9</span>
        <span><i className="legend-ema21" />EMA 21</span>
        <span><i className="legend-bull" />Bull candle</span>
        <span><i className="legend-bear" />Bear candle</span>
      </div>
    </div>
  );
}

function ModelCard({ name, description, enabled, ready, metric, note, accent = 'blue' }: { name: string; description: string; enabled: boolean; ready: boolean; metric?: string; note?: string; accent?: string }) {
  const status = !enabled ? 'NOT ENABLED' : ready ? 'READY' : 'WARMING UP';
  return (
    <div className={`nf-model-card accent-${accent}`}>
      <div className="nf-model-top">
        <div className="nf-model-icon"><Cpu size={17} /></div>
        <span className={`nf-model-status ${ready ? 'is-ready' : enabled ? 'is-warm' : 'is-off'}`}>{status}</span>
      </div>
      <h3>{name}</h3>
      <p className="nf-model-description">{description}</p>
      <div className="nf-model-foot"><span>{note || 'Latest output'}</span><strong>{metric || '—'}</strong></div>
    </div>
  );
}

function PositionsTable({ positions, onCloseAll }: { positions: Position[]; onCloseAll: () => void }) {
  return (
    <Panel className="nf-table-panel">
      <PanelHeading icon={Layers} title="Open positions" detail={`${positions.length} active position${positions.length === 1 ? '' : 's'}`} action={positions.length > 0 ? <button onClick={onCloseAll} className="nf-button nf-button-danger nf-button-small">Close basket</button> : <span className="nf-muted-small">No active trades</span>} />
      <div className="nf-table-scroll">
        <table className="nf-table nf-table-min-620">
          <thead><tr><th>Position</th><th>Volume</th><th>Open price</th><th>Current</th><th>Stop loss</th><th className="align-right">Floating P/L</th></tr></thead>
          <tbody>
            {positions.length === 0 ? <tr><td colSpan={6} className="nf-empty-cell">No open positions. New trades will appear here after MT5 confirms execution.</td></tr> : positions.map(p => <tr key={p.id}>
              <td><div className="nf-position-cell"><span className={`nf-side-icon ${p.direction === 'buy' ? 'is-buy' : 'is-sell'}`}>{p.direction === 'buy' ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}</span><div><strong>{p.symbol}</strong><small>{p.direction} · #{p.id}</small></div></div></td>
              <td className="nf-mono">{Number(p.lotSize).toFixed(2)}</td><td className="nf-mono">{price(p.openPrice, 3)}</td><td className="nf-mono">{price(p.currentPrice, 3)}</td><td className="nf-mono">{p.stopLoss ? price(p.stopLoss, 3) : '—'}</td><td className={`nf-mono align-right ${p.profit >= 0 ? 'nf-profit' : 'nf-loss'}`}>{money(p.profit)}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function HistoryTable({ history }: { history: HistoryTrade[] }) {
  return (
    <Panel className="nf-table-panel">
      <PanelHeading icon={HistoryIcon} title="Recent trade history" detail="Closed deals reported by MetaTrader 5" />
      <div className="nf-table-scroll">
        <table className="nf-table nf-table-min-620">
          <thead><tr><th>Symbol / side</th><th>Volume</th><th>Open</th><th>Close</th><th>Closed at</th><th className="align-right">Net P/L</th></tr></thead>
          <tbody>
            {history.length === 0 ? <tr><td colSpan={6} className="nf-empty-cell">No closed deals returned for the selected symbol yet.</td></tr> : history.slice(0, 30).map(h => <tr key={h.id}><td><div><strong>{h.symbol}</strong><small className={h.direction === 'buy' ? 'nf-profit' : 'nf-loss'}>{h.direction}</small></div></td><td className="nf-mono">{Number(h.lotSize).toFixed(2)}</td><td className="nf-mono">{price(h.openPrice, 3)}</td><td className="nf-mono">{price(h.closePrice, 3)}</td><td>{timeLabel(h.closeTime)}</td><td className={`nf-mono align-right ${h.profit >= 0 ? 'nf-profit' : 'nf-loss'}`}>{money(h.profit)}</td></tr>)}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function BrainMap({
  xgbConfirmation, selectedModels, modelStatuses, seqName, metaModel, lightgbm, catboost, aiDecision, ensembleConfirmation,
}: {
  xgbConfirmation: XgbConfirmation | null;
  selectedModels: string[];
  modelStatuses: NonNullable<AiEnsembleConfirmation['models']>;
  seqName?: string;
  metaModel: any;
  lightgbm: any;
  catboost: any;
  aiDecision: AiDecision | null;
  ensembleConfirmation: AiEnsembleConfirmation | null;
}) {
  const left = [
    { name: 'XGBoost', enabled: !!xgbConfirmation?.enabled, ready: !!xgbConfirmation?.ready, value: xgbConfirmation?.ready ? `${(xgbConfirmation.directionProbability * 100).toFixed(1)}%` : '—', accent: 'violet' },
    { name: 'LightGBM', enabled: selectedModels.includes('lightgbm'), ready: !!lightgbm?.available, value: lightgbm?.probabilityUp !== undefined ? `${(lightgbm.probabilityUp * 100).toFixed(1)}% UP` : '—', accent: 'green' },
    { name: 'CatBoost', enabled: selectedModels.includes('catboost'), ready: !!catboost?.available, value: catboost?.probabilityUp !== undefined ? `${(catboost.probabilityUp * 100).toFixed(1)}% UP` : '—', accent: 'amber' },
  ];
  const providerDecisions = aiDecision?.providers || {};
  const right = [
    { name: 'GRU / TCN', enabled: !!seqName, ready: !!(seqName && modelStatuses[seqName]?.available), value: seqName ? seqName.toUpperCase() : 'OPTIONAL', accent: 'blue' },
    { name: 'Meta-labeling', enabled: selectedModels.includes('meta'), ready: !!metaModel?.available, value: ensembleConfirmation ? `${(ensembleConfirmation.metaProbability * 100).toFixed(1)}% quality` : '—', accent: 'rose' },
    { name: 'OpenAI Brain', enabled: !!providerDecisions.openai?.enabled, ready: !!providerDecisions.openai?.ready, value: providerDecisions.openai?.ready ? `${(providerDecisions.openai.confidence * 100).toFixed(1)}% review` : 'SUPERVISOR', accent: 'teal' },
    { name: 'Claude Brain', enabled: !!providerDecisions.anthropic?.enabled, ready: !!providerDecisions.anthropic?.ready, value: providerDecisions.anthropic?.ready ? `${(providerDecisions.anthropic.confidence * 100).toFixed(1)}% review` : 'SUPERVISOR', accent: 'blue' },
  ];
  return (
    <div className="nf-brain-map">
      <div className="nf-brain-stack">
        {left.map(model => <div key={model.name} className={`nf-brain-node node-${model.accent}`}><span className="nf-node-orbit"><Cpu size={14} /></span><span className="nf-node-copy"><strong>{model.name}</strong><small>{model.value}</small></span><i className={`nf-node-indicator ${model.ready ? 'is-ready' : model.enabled ? 'is-warm' : 'is-off'}`} /></div>)}
      </div>
      <div className="nf-brain-core-wrap"><div className="nf-brain-wire nf-wire-left" /><div className="nf-brain-wire nf-wire-right" /><div className="nf-brain-core"><div className="nf-brain-core-ring"><BrainCircuit size={33} strokeWidth={1.55} /></div><span className="nf-core-kicker">NOVAFURY</span><strong>Unified<br />AI Brain</strong><small>One core · shared decision</small></div><span className="nf-core-badge"><GitBranch size={11} /> CONSENSUS CORE</span></div>
      <div className="nf-brain-stack">
        {right.map(model => <div key={model.name} className={`nf-brain-node node-${model.accent}`}><span className="nf-node-orbit"><Cpu size={14} /></span><span className="nf-node-copy"><strong>{model.name}</strong><small>{model.value}</small></span><i className={`nf-node-indicator ${model.ready ? 'is-ready' : model.enabled ? 'is-warm' : 'is-off'}`} /></div>)}
      </div>
    </div>
  );
}

export const ProfessionalDashboard: React.FC<ProfessionalDashboardProps> = (props) => {
  const [page, setPage] = useState<PageKey>('overview');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [providerConfig, setProviderConfig] = useState<ProviderConfig>(DEFAULT_PROVIDER_CONFIG);
  const [providerKeys, setProviderKeys] = useState({ openaiApiKey: '', anthropicApiKey: '' });
  const [providerLoading, setProviderLoading] = useState(true);
  const [providerSaving, setProviderSaving] = useState(false);
  const [providerNotice, setProviderNotice] = useState('');
  const [providerError, setProviderError] = useState('');

  const loadProviderConfig = async () => {
    try {
      const response = await fetch(`${PROVIDER_BRIDGE}/api/providers`, { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Unable to load AI provider settings');
      const next = { ...DEFAULT_PROVIDER_CONFIG, ...(data.providers || {}) } as ProviderConfig;
      setProviderConfig(next);
    } catch (error: any) {
      setProviderError(error?.message || 'AI provider settings are unavailable until the local MT5 bridge is running.');
    } finally {
      setProviderLoading(false);
    }
  };

  useEffect(() => { void loadProviderConfig(); }, []);

  const saveProviderConfig = async () => {
    setProviderSaving(true); setProviderNotice(''); setProviderError('');
    try {
      const response = await fetch(`${PROVIDER_BRIDGE}/api/providers`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          openaiEnabled: providerConfig.openai.enabled,
          openaiModel: providerConfig.openai.model,
          openaiApiKey: providerKeys.openaiApiKey,
          anthropicEnabled: providerConfig.anthropic.enabled,
          anthropicModel: providerConfig.anthropic.model,
          anthropicApiKey: providerKeys.anthropicApiKey,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Could not save AI provider settings');
      setProviderConfig({ ...DEFAULT_PROVIDER_CONFIG, ...(data.providers || {}) } as ProviderConfig);
      setProviderKeys({ openaiApiKey: '', anthropicApiKey: '' });
      setProviderNotice(data.message || 'Provider settings saved locally.');
    } catch (error: any) {
      setProviderError(error?.message || 'Unable to save provider settings');
    } finally {
      setProviderSaving(false);
    }
  };
  const {
    bridgeOnline, bridgeError, selectedSymbol, quote, quotes, candles, positions, history,
    balance, equity, margin, freeMargin, marginLevel, floatingProfit, deposit, signal,
    xgbConfirmation, ensembleConfirmation, aiDecision, brainDecision, config,
    riskStatus, smartProfit, onToggleAutoTrade, onUpdateConfig, onManualTrade, onCloseAll, onSelectSymbol,
  } = props;

  const pnlPositive = floatingProfit >= 0;
  const winRate = history.length ? (history.filter(item => item.profit > 0).length / history.length) * 100 : 0;
  const selectedModels = ensembleConfirmation?.selectedModels || [];
  const modelStatuses = ensembleConfirmation?.models || {};
  const seqName = selectedModels.find(name => name === 'gru' || name === 'tcn');
  const metaModel = modelStatuses.meta;
  const lightgbm = modelStatuses.lightgbm;
  const catboost = modelStatuses.catboost;
  const openAiStatus = aiDecision?.providers?.openai;
  const claudeStatus = aiDecision?.providers?.anthropic;
  const voteCount = brainDecision?.votes?.filter(v => v.ready && v.confirmed && v.direction === brainDecision.direction).length || 0;
  const navItems: { key: PageKey; label: string; icon: React.ElementType; group: string }[] = [
    { key: 'overview', label: 'Overview', icon: LayoutDashboard, group: 'Workspace' },
    { key: 'intelligence', label: 'AI intelligence', icon: BrainCircuit, group: 'Workspace' },
    { key: 'positions', label: 'Open positions', icon: Layers, group: 'Trading' },
    { key: 'history', label: 'Trade history', icon: HistoryIcon, group: 'Trading' },
    { key: 'settings', label: 'Settings', icon: Settings2, group: 'System' },
  ];
  const pageTitle = navItems.find(item => item.key === page)?.label || 'Overview';

  const navContent = (
    <>
      <div className="nf-brand">
        <div className="nf-brand-mark"><span>N</span><i /></div>
        <div><p>NovaFury</p><small>AI TRADING SYSTEM</small></div>
      </div>
      {(['Workspace', 'Trading', 'System'] as const).map(group => <div className="nf-nav-group" key={group}>
        <p className="nf-nav-label">{group}</p>
        {navItems.filter(item => item.group === group).map(item => {
          const Icon = item.icon;
          const active = page === item.key;
          return <button key={item.key} onClick={() => { setPage(item.key); setMobileNavOpen(false); }} className={`nf-nav-item ${active ? 'is-active' : ''}`}><Icon size={17} /><span>{item.label}</span>{item.key === 'positions' && positions.length > 0 && <b>{positions.length}</b>}</button>;
        })}
      </div>)}
      <div className="nf-sidebar-bottom">
        <div className="nf-bridge-card"><div className="nf-bridge-card-head"><span className={`nf-connection-dot ${bridgeOnline ? 'online' : 'offline'}`} /><strong>{bridgeOnline ? 'MT5 connected' : 'MT5 offline'}</strong></div><p>Local trading bridge</p><code>127.0.0.1:8765</code></div>
        <div className="nf-sidebar-foot"><ShieldCheck size={14} /><span>Unified decision gate active</span></div>
        <small className="nf-build-label">NOVAFURY · MT5 · V2 BALANCED</small>
      </div>
    </>
  );

  return (
    <div className="nf-app">
      <div className="nf-app-glow nf-glow-one" /><div className="nf-app-glow nf-glow-two" />
      <div className="nf-layout">
        <aside className="nf-sidebar">{navContent}</aside>
        {mobileNavOpen && <div className="nf-mobile-drawer"><button aria-label="Close navigation" className="nf-drawer-scrim" onClick={() => setMobileNavOpen(false)} /><aside className="nf-sidebar nf-mobile-sidebar"><button className="nf-drawer-close" onClick={() => setMobileNavOpen(false)} aria-label="Close menu"><X size={18} /></button>{navContent}</aside></div>}
        <div className="nf-workspace">
          <header className="nf-topbar">
            <div className="nf-topbar-title"><button className="nf-mobile-menu" onClick={() => setMobileNavOpen(true)} aria-label="Open navigation"><Menu size={19} /></button><div><p className="nf-breadcrumb">TRADING DESK <ChevronRight size={11} /> {pageTitle.toUpperCase()}</p><h1>{pageTitle}</h1></div></div>
            <div className="nf-topbar-actions">
              <div className="nf-topbar-connection"><span className={`nf-connection-dot ${bridgeOnline ? 'online' : 'offline'}`} /><span>{bridgeOnline ? 'LIVE MT5' : 'DISCONNECTED'}</span></div>
              <label className="nf-symbol-picker"><span>SYMBOL</span><select value={selectedSymbol} onChange={e => onSelectSymbol(e.target.value)}>{Object.keys(quotes).filter(symbol => symbol.toUpperCase().startsWith('XAUUSD')).map(symbol => <option key={symbol} value={symbol}>{symbol}</option>)}</select></label>
              <span className="nf-timeframe-chip">M5</span>
              <button onClick={onToggleAutoTrade} className={`nf-auto-toggle ${config.autoTrade ? 'is-on' : ''}`}><span className="nf-toggle-dot" />AUTO {config.autoTrade ? 'ON' : 'OFF'}</button>
            </div>
          </header>

          <main className="nf-main">
            {!bridgeOnline && <div className="nf-alert nf-alert-warn"><CircleAlert size={17} /><div><strong>MT5 bridge is not connected</strong><p>Start the local bridge to receive live account data and send orders.{bridgeError ? ` ${bridgeError}` : ''}</p></div></div>}

            {page === 'overview' && <>
              <div className="nf-page-intro"><div><p className="nf-eyebrow nf-gold-eyebrow">LIVE ACCOUNT MONITOR</p><h2>Trading overview<span className="nf-title-mark">.</span></h2><p>Market activity, unified intelligence and execution controls in one workspace.</p></div><div className="nf-market-state"><span className={`nf-market-state-dot ${bridgeOnline ? 'online' : 'offline'}`} />{bridgeOnline ? 'Market feed active' : 'Waiting for market feed'}<span className="nf-market-state-divider" />{selectedSymbol} / M5</div></div>

              <div className="nf-metrics-grid">
                <MetricCard label="Account balance" value={money(balance)} sub="Broker-reported balance" icon={Wallet} tone="blue" />
                <MetricCard label="Account equity" value={money(equity)} sub="Includes open position P/L" icon={BarChart3} tone="violet" />
                <MetricCard label="Floating P/L" value={money(floatingProfit)} sub={`${positions.length} open position${positions.length === 1 ? '' : 's'}`} icon={floatingProfit >= 0 ? TrendingUp : ArrowDownRight} tone={pnlPositive ? 'green' : 'amber'} positive={pnlPositive} />
                <MetricCard label="Closed-trade win rate" value={`${winRate.toFixed(1)}%`} sub={`${history.length} recent closed deals`} icon={Gauge} tone="amber" />
              </div>

              <div className="nf-primary-grid">
                <div className="nf-main-column">
                  <Panel className="nf-chart-panel">
                    <PanelHeading icon={Activity} title={`${selectedSymbol} · Market chart`} detail="Live MT5 candles · M5 · EMA 9 / EMA 21" action={<div className="nf-chart-price"><strong>{price(quote.bid, quote.digits)}</strong><span>BID PRICE</span></div>} />
                    <div className="nf-chart-stats"><div><span>ASK</span><strong>{price(quote.ask, quote.digits)}</strong></div><div><span>SPREAD</span><strong>{quote.spread} pts</strong></div><div><span>ACTIVE SIGNAL</span><strong className={signal?.type === 'buy' ? 'nf-profit' : signal?.type === 'sell' ? 'nf-loss' : ''}>{signal?.type || 'Scanning'}</strong></div><StatusPill good={bridgeOnline}>{bridgeOnline ? 'Feed active' : 'Feed offline'}</StatusPill></div>
                    <MarketChart candles={candles} quote={quote} positions={positions} />
                  </Panel>

                  <Panel className="nf-execution-panel">
                    <PanelHeading icon={Zap} title="Quick execution" detail="Manual controls and configured position sizing" action={<span className="nf-manual-tag">MANUAL OVERRIDE</span>} />
                    <div className="nf-execution-content">
                      <div className="nf-manual-actions"><button onClick={() => onManualTrade('buy')} disabled={!bridgeOnline || positions.length > 0} className="nf-trade-button nf-buy-button"><ArrowUpRight size={17} />BUY</button><button onClick={() => onManualTrade('sell')} disabled={!bridgeOnline || positions.length > 0} className="nf-trade-button nf-sell-button"><ArrowDownRight size={17} />SELL</button></div>
                      <p className="nf-manual-note">Manual orders bypass directional AI approval but never bypass the Risk Guardian, broker-side stop or account limits. Use only when you intentionally want to override the automated decision gate.</p>
                      <div className="nf-execution-settings"><label><span>BURST ORDERS</span><strong>10 fixed tickets</strong></label><label><span>COMPOUND MODE</span><strong>Capped profit-funded</strong></label></div>
                      <div className="nf-volume-summary"><div><span>LOT / ORDER</span><strong>Risk-sized <small>by broker contract</small></strong></div><div><span>BASKET RISK</span><strong>0.35% <small>+ capped profit bonus</small></strong></div></div>
                    </div>
                  </Panel>
                </div>

                <div className="nf-side-column">
                  <Panel className="nf-brain-panel">
                    <PanelHeading icon={BrainCircuit} title="Unified AI brain" detail="One central decision · all models connected" action={<StatusPill good={!!brainDecision?.approved}>{brainDecision?.approved ? 'Approved' : brainDecision?.ready ? 'Blocked' : 'Syncing'}</StatusPill>} />
                    <div className="nf-brain-panel-body">
                      <BrainMap xgbConfirmation={xgbConfirmation} selectedModels={selectedModels} modelStatuses={modelStatuses} seqName={seqName} metaModel={metaModel} lightgbm={lightgbm} catboost={catboost} aiDecision={aiDecision} ensembleConfirmation={ensembleConfirmation} />
                      <div className="nf-consensus-strip"><div><span>CONSENSUS</span><strong>{brainDecision ? `${(brainDecision.consensusProbability * 100).toFixed(1)}%` : '—'}</strong></div><div><span>FINAL DIRECTION</span><strong className={brainDecision?.direction === 'buy' ? 'nf-profit' : brainDecision?.direction === 'sell' ? 'nf-loss' : ''}>{brainDecision?.direction || 'Neutral'}</strong></div><div><span>VOTES ALIGNED</span><strong>{voteCount}/{brainDecision?.votes?.length || 0}</strong></div></div>
                      <div className="nf-consensus-meter"><span style={{ width: `${Math.max(0, Math.min(100, (brainDecision?.consensusProbability || 0) * 100))}%` }} /></div>
                      <div className="nf-brain-decision"><div className={`nf-decision-icon ${brainDecision?.approved ? 'is-approved' : 'is-blocked'}`}>{brainDecision?.approved ? <CheckCircle2 size={17} /> : <LockKeyhole size={16} />}</div><div><span>FINAL EXECUTION GATE</span><strong>{brainDecision?.approved ? 'APPROVED' : 'BLOCKED / WAITING'}</strong></div><small>Threshold {brainDecision ? `${(brainDecision.consensusThreshold * 100).toFixed(0)}%` : '—'}</small></div>
                      <p className="nf-brain-reason">{brainDecision?.reason || 'Waiting for the coordinated model assessment from the local bridge.'}</p>
                    </div>
                  </Panel>

                  <Panel className="nf-protection-panel">
                    <PanelHeading icon={ShieldCheck} title="Account protection" detail="Broker margin telemetry" />
                    <div className="nf-protection-grid"><div><span>USED MARGIN</span><strong>{money(margin)}</strong></div><div><span>FREE MARGIN</span><strong>{money(freeMargin)}</strong></div><div className="nf-protection-wide"><span>MARGIN LEVEL</span><strong>{marginLevel > 0 ? `${marginLevel.toFixed(1)}%` : '—'}</strong></div></div>
                  </Panel>

                  <Panel className="nf-diagnostics-panel">
                    <PanelHeading icon={Activity} title="Signal diagnostics" detail="Rule-based market context" />
                    <div className="nf-diagnostics-grid"><div><span>RSI 14</span><strong>{signal ? signal.indicatorReadings.rsi.toFixed(1) : '—'}</strong></div><div><span>TREND</span><strong className="nf-capitalize">{signal?.indicatorReadings.trend || '—'}</strong></div><div><span>SIGNAL QUALITY SCORE</span><strong>{signal ? `${signal.confidence.toFixed(1)}%` : '—'}</strong></div><div><span>LIQUIDITY SWEEP</span><strong>{signal ? signal.indicatorReadings.liquiditySweep ? 'Detected' : 'Not detected' : '—'}</strong></div></div>
                  </Panel>
                </div>
              </div>

              <PositionsTable positions={positions} onCloseAll={onCloseAll} />
              <footer className="nf-footer"><span>NovaFury AI Trading System · Connected to local MetaTrader 5 bridge</span><span><Clock size={13} /> Data refreshes from your local bridge</span></footer>
            </>}

            {page === 'intelligence' && <>
              <div className="nf-page-intro"><div><p className="nf-eyebrow nf-gold-eyebrow">MODEL COORDINATION</p><h2>AI intelligence<span className="nf-title-mark">.</span></h2><p>Independent model outputs converge through the unified decision coordinator.</p></div><StatusPill good={!!brainDecision?.approved}>{brainDecision?.approved ? 'Execution approved' : brainDecision?.ready ? 'Decision blocked' : 'Awaiting assessment'}</StatusPill></div>
              <Panel className="nf-intelligence-core-panel"><PanelHeading icon={BrainCircuit} title="Central intelligence architecture" detail="Shared candle input · unified consensus · final execution gate" /><div className="nf-intel-map-wrap"><BrainMap xgbConfirmation={xgbConfirmation} selectedModels={selectedModels} modelStatuses={modelStatuses} seqName={seqName} metaModel={metaModel} lightgbm={lightgbm} catboost={catboost} aiDecision={aiDecision} ensembleConfirmation={ensembleConfirmation} /></div><div className="nf-intel-result"><div><span>CONSENSUS PROBABILITY</span><strong>{brainDecision ? `${(brainDecision.consensusProbability * 100).toFixed(1)}%` : '—'}</strong></div><div><span>PROPOSED DIRECTION</span><strong>{brainDecision?.direction || signal?.type || '—'}</strong></div><div><span>VOTE ALIGNMENT</span><strong>{voteCount} / {brainDecision?.votes?.length || 0}</strong></div><div><span>EXECUTION GATE</span><strong className={brainDecision?.approved ? 'nf-profit' : 'nf-loss'}>{brainDecision?.approved ? 'APPROVED' : 'BLOCKED'}</strong></div></div></Panel>
              <div className="nf-section-heading"><div><p className="nf-eyebrow">CONNECTED ENGINES</p><h3>Model status</h3></div><span className="nf-muted-small">Live output from the local coordinator</span></div>
              <div className="nf-model-grid">
                <ModelCard name="XGBoost" description="Primary M5 direction model; must pass purged expanding-window walk-forward validation." enabled={!!xgbConfirmation?.enabled} ready={!!xgbConfirmation?.ready} metric={xgbConfirmation?.ready ? `${(xgbConfirmation.directionProbability * 100).toFixed(1)}% ${xgbConfirmation.direction.toUpperCase()}` : undefined} note={xgbConfirmation?.validationAuc != null ? `Purged WF AUC ${xgbConfirmation.validationAuc.toFixed(3)} · min fold ${(xgbConfirmation.minimumFoldAuc ?? 0).toFixed(3)} · ${xgbConfirmation.validationFolds ?? 0} folds` : xgbConfirmation?.error || 'Awaiting model training and validation'} accent="violet" />
                <ModelCard name="LightGBM" description="Fast gradient-boosted direction model; unavailable when validation fails." enabled={selectedModels.includes('lightgbm')} ready={!!lightgbm?.available} metric={lightgbm?.probabilityUp !== undefined ? `${(lightgbm.probabilityUp * 100).toFixed(1)}% UP` : undefined} note={lightgbm?.validationAuc != null ? `Purged WF AUC ${lightgbm.validationAuc.toFixed(3)} · min fold ${(lightgbm.minimumFoldAuc ?? 0).toFixed(3)} · ${lightgbm.validationFolds ?? 0} folds` : lightgbm?.error || 'No walk-forward score available'} accent="green" />
                <ModelCard name="CatBoost" description="Independent boosted-tree confirmation; validation is required before it can vote." enabled={selectedModels.includes('catboost')} ready={!!catboost?.available} metric={catboost?.probabilityUp !== undefined ? `${(catboost.probabilityUp * 100).toFixed(1)}% UP` : undefined} note={catboost?.validationAuc != null ? `Purged WF AUC ${catboost.validationAuc.toFixed(3)} · min fold ${(catboost.minimumFoldAuc ?? 0).toFixed(3)} · ${catboost.validationFolds ?? 0} folds` : catboost?.error || 'No walk-forward score available'} accent="amber" />
                <ModelCard name="GRU / TCN" description="Optional sequence-learning engine for multi-candle patterns." enabled={!!seqName} ready={!!(seqName && modelStatuses[seqName]?.available)} metric={seqName ? seqName.toUpperCase() : 'Not selected'} note={seqName ? modelStatuses[seqName]?.error || 'Sequence model selected' : 'Enable with PyTorch and sufficient history'} accent="blue" />
                <ModelCard name="Meta-labeling" description="Scores candidate quality; purged walk-forward validation is mandatory." enabled={selectedModels.includes('meta')} ready={!!metaModel?.available} metric={ensembleConfirmation ? `${(ensembleConfirmation.metaProbability * 100).toFixed(1)}% quality` : undefined} note={metaModel?.validationAuc != null ? `Purged WF AUC ${metaModel.validationAuc.toFixed(3)} · min fold ${(metaModel.minimumFoldAuc ?? 0).toFixed(3)} · ${metaModel.validationFolds ?? 0} folds` : metaModel?.error || `Threshold ${ensembleConfirmation ? `${(ensembleConfirmation.metaThreshold * 100).toFixed(0)}%` : '—'}`} accent="rose" />
                <ModelCard name="OpenAI supervisor" description="Optional GPT market-context and risk-quality review; not a price-probability voter." enabled={!!openAiStatus?.enabled} ready={!!openAiStatus?.ready} metric={openAiStatus?.ready && openAiStatus.confidence !== undefined ? `${(openAiStatus.confidence * 100).toFixed(1)}% confidence` : undefined} note={openAiStatus?.error || (openAiStatus?.enabled ? 'Awaiting OpenAI review' : 'Disabled · configure in Settings')} accent="teal" />
                <ModelCard name="Anthropic Claude supervisor" description="Independent Claude review; when enabled with OpenAI, both providers must approve." enabled={!!claudeStatus?.enabled} ready={!!claudeStatus?.ready} metric={claudeStatus?.ready && claudeStatus.confidence !== undefined ? `${(claudeStatus.confidence * 100).toFixed(1)}% confidence` : undefined} note={claudeStatus?.error || (claudeStatus?.enabled ? 'Awaiting Claude review' : 'Disabled · configure in Settings')} accent="blue" />
              </div>
              <Panel className="nf-table-panel"><PanelHeading icon={GitBranch} title="Model output detail" detail="Latest directional votes returned by the coordinator" /><div className="nf-vote-list">{(brainDecision?.votes || []).length ? brainDecision!.votes.map(v => <div key={v.brain} className="nf-vote-row"><div><strong>{v.brain}</strong><small>{v.ready ? 'Model returned a current result' : 'Model not ready'}</small></div><div className="nf-vote-value"><strong>{(v.directionProbability * 100).toFixed(1)}%</strong><span>{v.direction}</span>{v.confirmed && v.ready ? <CheckCircle2 size={16} className="nf-profit" /> : <CircleAlert size={16} className="nf-warn" />}</div></div>) : <p className="nf-empty-cell">Waiting for model votes.</p>}</div></Panel>
              <Panel className="nf-table-panel"><PanelHeading icon={ShieldCheck} title="Decision checks" detail="Final checks reported by the Python coordinator" /><div className="nf-checks-wrap">{Object.entries(brainDecision?.checks || {}).map(([name, passed]) => <span key={name} className={`nf-check-chip ${passed ? 'is-pass' : 'is-block'}`}>{name.replace(/[A-Z]/g, m => ` ${m}`).toUpperCase()} · {passed ? 'PASS' : 'BLOCK'}</span>)}</div><p className="nf-check-reason">{brainDecision?.reason || 'The coordinator has not returned a completed decision yet.'}</p></Panel>
            </>}

            {page === 'positions' && <>
              <div className="nf-page-intro"><div><p className="nf-eyebrow nf-gold-eyebrow">LIVE EXPOSURE</p><h2>Open positions<span className="nf-title-mark">.</span></h2><p>Positions returned by your connected MetaTrader 5 account.</p></div><div className="nf-position-total"><span>OPEN POSITIONS</span><strong>{positions.length}</strong></div></div>
              <PositionsTable positions={positions} onCloseAll={onCloseAll} />
              <div className="nf-alert nf-alert-soft"><ShieldCheck size={17} /><div><strong>Basket close control</strong><p>Closing the basket sends a close request to MT5 for all NovaFury positions on the selected symbol. Confirm the symbol and live account before using it.</p></div></div>
            </>}

            {page === 'history' && <>
              <div className="nf-page-intro"><div><p className="nf-eyebrow nf-gold-eyebrow">ACCOUNT ACTIVITY</p><h2>Trade history<span className="nf-title-mark">.</span></h2><p>Recent closed deals reported by the local MT5 bridge.</p></div><div className="nf-position-total"><span>CLOSED DEALS</span><strong>{history.length}</strong></div></div>
              <HistoryTable history={history} />
            </>}

            {page === 'settings' && <>
              <div className="nf-page-intro"><div><p className="nf-eyebrow nf-gold-eyebrow">BOT CONFIGURATION</p><h2>System settings<span className="nf-title-mark">.</span></h2><p>Dashboard controls for the existing strategy. Backend environment settings remain in your local .env file.</p></div></div>
              <div className="nf-settings-grid">
                <Panel><PanelHeading icon={Bot} title="Execution preferences" detail="Settings consumed by the frontend order flow" /><div className="nf-settings-content">
                  <label className="nf-setting-toggle"><span><strong>Automatic trading</strong><small>Allow fresh unified-brain approvals to trigger automatic entries.</small></span><button onClick={onToggleAutoTrade} className={`nf-switch ${config.autoTrade ? 'is-on' : ''}`} aria-label="Toggle automatic trading"><i /></button></label>
                  <div className="nf-setting-row"><label><span>ORDER COUNT</span><strong>10 fixed tickets</strong></label><label><span>COMPOUNDING</span><strong>Capped profit-funded</strong></label></div>
                  <label className="nf-setting-full"><span>RISK PROFILE</span><strong>Balanced · XAUUSD only</strong><small>Backend Risk Guardian calculates each ticket from equity, ATR stop distance, broker tick value and volume step.</small></label>
                  <div className="nf-settings-note"><LockKeyhole size={15} /><span>Every entry is required to include a broker-side stop-loss. Auto trading starts disarmed; manual orders still pass the same risk checks.</span></div>
                </div></Panel>
                <Panel><PanelHeading icon={ShieldCheck} title="Live account & Risk Guardian" detail="Broker account values and bridge-enforced Balanced limits" /><div className="nf-account-details">{[['Connection', bridgeOnline ? 'Connected' : 'Offline'], ['Symbol', selectedSymbol], ['Timeframe', 'M5'], ['Account balance', money(balance)], ['Account equity', money(equity)], ['Free margin', money(freeMargin)], ['Smart trailing', smartProfit.enabled ? 'Enabled' : 'Disabled'], ['Risk profile', riskStatus?.profile || 'Unavailable'], ['Execution armed', riskStatus?.executionArmed ? 'YES' : 'NO'], ['Daily loss', riskStatus ? `${(riskStatus.dailyLossPct * 100).toFixed(2)}% / ${(riskStatus.dailyLossLimitPct * 100).toFixed(2)}%` : '—'], ['Peak drawdown', riskStatus ? `${(riskStatus.peakDrawdownPct * 100).toFixed(2)}% / ${(riskStatus.peakDrawdownLimitPct * 100).toFixed(2)}%` : '—'], ['Base basket risk', riskStatus ? `${(riskStatus.baseBasketRiskPct * 100).toFixed(2)}%` : '—'], ['Maximum basket risk', riskStatus ? `${(riskStatus.maxBasketRiskPct * 100).toFixed(2)}%` : '—'], ['Recovery reserve', riskStatus ? `${(riskStatus.recoveryReservePct * 100).toFixed(2)}%` : '—'], ['Current broker-stop risk', riskStatus?.currentBrokerStopRisk != null ? `${money(riskStatus.currentBrokerStopRisk)} / ${money(riskStatus.currentBrokerStopRiskCap)}` : (riskStatus?.exposureCheckError ? 'UNVERIFIED' : '—')], ['Managed pending orders', riskStatus ? String(riskStatus.managedPendingOrderCount ?? '—') : '—'], ['Margin utilisation ceiling', riskStatus ? `${(riskStatus.maxMarginUtilizationPct * 100).toFixed(0)}%` : '—'], ['Margin level floor', riskStatus ? `${Number(riskStatus.minimumMarginLevelPct).toFixed(0)}%` : '—'], ['Risk Guardian state', riskStatus ? (riskStatus.halted ? 'HALTED' : riskStatus.riskStateError ? 'ERROR' : 'READY / DISARMED') : 'Unavailable'], ['Initial deposit reference', deposit > 0 ? money(deposit) : '—']].map(([k, v]) => <div key={k}><span>{k}</span><strong>{v}</strong></div>)}</div>{riskStatus?.riskStateError && <p className="nf-provider-message is-error">{riskStatus.riskStateError}</p>}{riskStatus?.exposureCheckError && <p className="nf-provider-message is-error">Broker-stop exposure cannot be verified: {riskStatus.exposureCheckError}</p>}</Panel>
              </div>

              <Panel className="nf-provider-panel">
                <PanelHeading icon={BrainCircuit} title="External AI supervisors" detail="Connect OpenAI and Anthropic Claude as optional final veto gates for the unified brain." action={<StatusPill good={!providerLoading && !providerError}>{providerLoading ? 'LOADING' : providerError ? 'BRIDGE UNAVAILABLE' : 'LOCAL CONFIG'}</StatusPill>} />
                <div className="nf-provider-content">
                  <div className="nf-provider-grid">
                    {(['openai', 'anthropic'] as ProviderName[]).map((name) => {
                      const provider = providerConfig[name];
                      const label = name === 'openai' ? 'OpenAI' : 'Anthropic Claude';
                      const keyName = name === 'openai' ? 'openaiApiKey' : 'anthropicApiKey';
                      const draft = providerKeys[keyName];
                      return <div className="nf-provider-card" key={name}>
                        <div className="nf-provider-card-head"><div><strong>{label}</strong><small>{name === 'openai' ? 'GPT supervisor' : 'Claude supervisor'}</small></div><StatusPill good={provider.configured}>{provider.configured ? 'KEY SAVED' : 'NO KEY'}</StatusPill></div>
                        <label className="nf-setting-toggle nf-provider-toggle"><span><strong>Activate {label}</strong><small>{provider.configured ? 'Allow this provider to review each candidate entry.' : 'Save an API key before enabling this provider.'}</small></span><button type="button" onClick={() => setProviderConfig(prev => ({ ...prev, [name]: { ...prev[name], enabled: !prev[name].enabled } }))} className={`nf-switch ${provider.enabled ? 'is-on' : ''}`} aria-label={`Toggle ${label}`}><i /></button></label>
                        <label className="nf-provider-field"><span>MODEL ID</span><input value={provider.model} onChange={event => setProviderConfig(prev => ({ ...prev, [name]: { ...prev[name], model: event.target.value } }))} placeholder={name === 'openai' ? 'gpt-5.4-mini' : 'claude-sonnet-5'} /></label>
                        <label className="nf-provider-field"><span>API KEY</span><input type="password" autoComplete="new-password" value={draft} onChange={event => setProviderKeys(prev => ({ ...prev, [keyName]: event.target.value }))} placeholder={provider.configured ? 'Key saved — enter only to replace' : `Paste ${label} API key`} /></label>
                      </div>;
                    })}
                  </div>
                  <div className="nf-provider-footer"><div><div className="nf-settings-note"><LockKeyhole size={15} /><span>API keys are stored only in this computer's local .env.local file. They are never sent back to the browser or written to dashboard storage.</span></div><p className="nf-provider-rule">When both providers are enabled, both must return a valid approval for the same direction. A provider error, timeout or rejection blocks the AI-supervised entry.</p></div><button type="button" className="nf-provider-save" onClick={saveProviderConfig} disabled={providerSaving || providerLoading}>{providerSaving ? 'SAVING…' : 'SAVE AI PROVIDERS'}</button></div>
                  {providerNotice && <p className="nf-provider-message is-success">{providerNotice}</p>}
                  {providerError && <p className="nf-provider-message is-error">{providerError}</p>}
                </div>
              </Panel>
            </>}

            <footer className="nf-footer"><span>NovaFury AI Trading System · Local MetaTrader 5 bridge</span><span><Radio size={13} /> Live data is supplied by the local bridge</span></footer>
          </main>
        </div>
      </div>
    </div>
  );
};
