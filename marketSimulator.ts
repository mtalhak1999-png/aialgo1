import { Candle, BotSignal } from '../types/trading';

// This module now contains signal-context calculations only. Live position sizing,
// burst execution and P/L all belong to the MT5 bridge; no simulated lots or
// synthetic positions are used by the live dashboard.
export function calculateIndicators(candles: Candle[]) {
  if (candles.length < 14) {
    const latest = candles[candles.length - 1]?.close || 0;
    return { rsi: 50, ema9: latest, ema21: latest };
  }

  const closes = candles.map(c => c.close);
  const calcEma = (period: number) => {
    const k = 2 / (period + 1);
    let ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
    for (let i = period; i < closes.length; i++) {
      ema = closes[i] * k + ema * (1 - k);
    }
    return ema;
  };

  const ema9 = calcEma(9);
  const ema21 = calcEma(21);
  let gains = 0;
  let losses = 0;
  for (let i = closes.length - 14; i < closes.length; i++) {
    const diff = closes[i] - closes[i - 1];
    if (diff >= 0) gains += diff;
    else losses += Math.abs(diff);
  }
  const avgGain = gains / 14;
  const avgLoss = losses / 14 || 0.0001;
  const rs = avgGain / avgLoss;
  const rsi = 100 - (100 / (1 + rs));
  return {
    rsi: Number(rsi.toFixed(1)),
    ema9: Number(ema9.toFixed(3)),
    ema21: Number(ema21.toFixed(3)),
  };
}

// V2 M5 signal evaluator. confidence is a rule-quality score, NOT a win
// probability. It uses completed candles, prior swing levels and measurable
// directional conditions; weak or ambiguous conditions return neutral.
export function evaluateAiEntrySignal(candles: Candle[], currentPrice: number): BotSignal {
  const neutral = (
    reason: string,
    score = 0,
    trend: 'bullish' | 'bearish' | 'neutral' = 'neutral',
    sweep = false,
    rsi = 50,
    ema9 = currentPrice,
    ema21 = currentPrice,
  ): BotSignal => ({
    type: 'neutral',
    confidence: score,
    reason,
    timestamp: Date.now(),
    indicatorReadings: { rsi, emaFast: ema9, emaSlow: ema21, trend, liquiditySweep: sweep },
  });

  if (candles.length < 35 || !Number.isFinite(currentPrice) || currentPrice <= 0) {
    return neutral('Waiting for at least 35 completed M5 candles and a valid quote.');
  }

  const { rsi, ema9, ema21 } = calculateIndicators(candles);
  const last = candles[candles.length - 1];
  const previous = candles.slice(-21, -1);
  const priorLow = Math.min(...previous.map(c => c.low));
  const priorHigh = Math.max(...previous.map(c => c.high));
  const sweepLow = last.low < priorLow && last.close > priorLow;
  const sweepHigh = last.high > priorHigh && last.close < priorHigh;
  const bullishTrend = ema9 > ema21 && last.close > ema9;
  const bearishTrend = ema9 < ema21 && last.close < ema9;
  const bullishCandle = last.close > last.open;
  const bearishCandle = last.close < last.open;
  const avgVolume = previous.slice(-20).reduce((sum, candle) => sum + candle.volume, 0)
    / Math.max(1, Math.min(20, previous.length));
  const volumeSupport = avgVolume > 0 && last.volume >= avgVolume * 0.8;
  const bullRsi = rsi >= 50 && rsi <= 68;
  const bearRsi = rsi >= 32 && rsi <= 50;

  const buyScore = (bullishTrend ? 25 : 0) + (sweepLow ? 30 : 0)
    + (bullRsi ? 20 : 0) + (bullishCandle ? 15 : 0) + (volumeSupport ? 10 : 0);
  const sellScore = (bearishTrend ? 25 : 0) + (sweepHigh ? 30 : 0)
    + (bearRsi ? 20 : 0) + (bearishCandle ? 15 : 0) + (volumeSupport ? 10 : 0);

  if (bullishTrend && (sweepLow || (bullRsi && bullishCandle)) && buyScore >= 65 && buyScore > sellScore) {
    return {
      type: 'buy',
      confidence: buyScore,
      reason: sweepLow
        ? `Bullish M5 reclaim: price swept the prior 20-bar low (${priorLow.toFixed(3)}) and closed back above it with EMA trend confirmation.`
        : 'Bullish M5 continuation: EMA(9) above EMA(21), close above EMA(9), RSI and candle momentum aligned.',
      timestamp: Date.now(),
      indicatorReadings: { rsi, emaFast: ema9, emaSlow: ema21, trend: 'bullish', liquiditySweep: sweepLow },
    };
  }

  if (bearishTrend && (sweepHigh || (bearRsi && bearishCandle)) && sellScore >= 65 && sellScore > buyScore) {
    return {
      type: 'sell',
      confidence: sellScore,
      reason: sweepHigh
        ? `Bearish M5 rejection: price swept the prior 20-bar high (${priorHigh.toFixed(3)}) and closed back below it with EMA trend confirmation.`
        : 'Bearish M5 continuation: EMA(9) below EMA(21), close below EMA(9), RSI and candle momentum aligned.',
      timestamp: Date.now(),
      indicatorReadings: { rsi, emaFast: ema9, emaSlow: ema21, trend: 'bearish', liquiditySweep: sweepHigh },
    };
  }

  const strongest = Math.max(buyScore, sellScore);
  return neutral(
    'No sufficiently aligned M5 setup; waiting rather than forcing a BUY/SELL signal.',
    strongest,
    bullishTrend ? 'bullish' : bearishTrend ? 'bearish' : 'neutral',
    sweepLow || sweepHigh,
    rsi,
    ema9,
    ema21,
  );
}
