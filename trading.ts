export type TradeDirection = 'buy' | 'sell';

export interface Position {
  id: string;
  symbol: string;
  direction: TradeDirection;
  lotSize: number;
  openPrice: number;
  currentPrice: number;
  profit: number;
  openTime: number;
  stopLoss?: number;
  takeProfit?: number;
}

export interface HistoryTrade {
  id: string;
  symbol: string;
  direction: TradeDirection;
  lotSize: number;
  openPrice: number;
  closePrice: number;
  profit: number;
  openTime: number;
  closeTime: number;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SymbolQuote {
  symbol: string;
  name: string;
  bid: number;
  ask: number;
  spread: number;
  changePercent: number;
  high: number;
  low: number;
  digits: number;
}

export type BotState = 'idle' | 'analyzing' | 'holding' | 'closing' | 'cooldown';

export interface BotSignal {
  type: 'buy' | 'sell' | 'neutral';
  confidence: number;
  reason: string;
  timestamp: number;
  indicatorReadings: {
    rsi: number;
    emaFast: number;
    emaSlow: number;
    trend: 'bullish' | 'bearish' | 'neutral';
    liquiditySweep: boolean;
  };
}

export interface BotConfig {
  autoTrade: boolean;
  compoundMode: 'capped';
  burstOrdersCount: 10; // fixed ten-ticket logical basket
  simulationSpeed: 1 | 2 | 5 | 10;
  riskAggressiveness: 'balanced';
  targetProfitPips: number;
  soundEnabled: boolean;
  selectedSymbol: string;
}

export interface AccountStats {
  balance: number;
  equity: number;
  margin: number;
  freeMargin: number;
  marginLevel: number;
  initialDeposit: number;
  totalProfit: number;
  winRate: number;
  totalBursts: number;
  winningBursts: number;
}

export interface XgbConfirmation {
  enabled: boolean;
  available: boolean;
  ready: boolean;
  model: string;
  version: number;
  symbol: string;
  probabilityUp: number;
  probabilityDown: number;
  direction: TradeDirection | 'neutral';
  directionProbability: number;
  threshold: number;
  confirmed: boolean;
  trainedRows: number;
  ageSeconds: number | null;
  validationAuc?: number | null;
  minimumFoldAuc?: number | null;
  foldAucs?: number[];
  validationFolds?: number;
  validationMode?: string;
  error?: string;
}


export interface AiEnsembleModelStatus {
  available: boolean;
  validationAuc?: number | null;
  minimumFoldAuc?: number | null;
  foldAucs?: number[];
  validationFolds?: number;
  validationMode?: string;
  trainedRows?: number;
  validationRows?: number;
  probabilityUp?: number;
  successProbability?: number;
  error?: string;
}

export interface AiEnsembleConfirmation {
  enabled: boolean;
  available: boolean;
  ready: boolean;
  model: string;
  symbol: string;
  direction: TradeDirection | 'neutral';
  probabilityUp: number;
  directionProbability: number;
  metaProbability: number;
  threshold: number;
  metaThreshold: number;
  confirmed: boolean;
  selectedModels: string[];
  trainedBars: number;
  version: number;
  ageSeconds: number | null;
  models: Record<string, AiEnsembleModelStatus>;
  error?: string;
}

export interface AiDecision {
  enabled: boolean;
  available: boolean;
  ready: boolean;
  model: string;
  regime: string;
  allowed: boolean;
  preferredDirection: TradeDirection | 'neutral';
  confidence: number;
  entryQuality: number;
  riskMode: 'normal' | 'reduced' | 'blocked' | string;
  rationale: string;
  createdAt: number;
  ageSeconds: number | null;
  providers?: Record<string, {
    enabled: boolean;
    configured?: boolean;
    ready: boolean;
    available?: boolean;
    model?: string;
    confidence?: number;
    entryQuality?: number;
    allowed?: boolean;
    error?: string;
  }>;
  error?: string;
}

export interface CombinedBrainDecision {
  enabled: boolean;
  ready: boolean;
  approved: boolean;
  symbol: string;
  timeframe: string;
  direction: TradeDirection | 'neutral';
  consensusProbability: number;
  consensusThreshold: number;
  votes: Array<{
    brain: string;
    direction: TradeDirection | 'neutral';
    directionProbability: number;
    confirmed: boolean;
    ready: boolean;
  }>;
  checks: Record<string, boolean>;
  blockers: string[];
  reason: string;
  modelFlow: string[];
  timestamp: number;
}
