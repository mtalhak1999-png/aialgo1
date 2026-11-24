import React, { useMemo } from 'react';
import { Candle, Position, SymbolQuote } from '../types/trading';

interface ChartTabProps {
  candles: Candle[];
  quote: SymbolQuote;
  positions: Position[];
}

export const ChartTab: React.FC<ChartTabProps> = ({ candles, quote, positions }) => {
  // Compute chart coordinates
  const { minPrice, maxPrice, priceRange, svgCandles, ema9Points, ema21Points } = useMemo(() => {
    if (!candles || candles.length === 0) {
      return { minPrice: 0, maxPrice: 1, priceRange: 1, svgCandles: [], ema9Points: '', ema21Points: '' };
    }

    const min = Math.min(...candles.map(c => c.low));
    const max = Math.max(...candles.map(c => c.high));
    const padding = (max - min) * 0.12 || 1;
    const minP = min - padding;
    const maxP = max + padding;
    const range = maxP - minP;

    const width = 360;
    const height = 380;
    const candleWidth = Math.max(3, (width / candles.length) * 0.7);

    // Calculate EMA values
    const k9 = 2 / (9 + 1);
    const k21 = 2 / (21 + 1);
    let ema9 = candles[0].close;
    let ema21 = candles[0].close;

    const ema9Coords: string[] = [];
    const ema21Coords: string[] = [];

    const computed = candles.map((c, idx) => {
      const x = (idx / (candles.length - 1 || 1)) * (width - 40) + 10;
      const yOpen = height - ((c.open - minP) / range) * (height - 40) - 20;
      const yClose = height - ((c.close - minP) / range) * (height - 40) - 20;
      const yHigh = height - ((c.high - minP) / range) * (height - 40) - 20;
      const yLow = height - ((c.low - minP) / range) * (height - 40) - 20;
      const isGreen = c.close >= c.open;

      ema9 = c.close * k9 + ema9 * (1 - k9);
      ema21 = c.close * k21 + ema21 * (1 - k21);

      const yEma9 = height - ((ema9 - minP) / range) * (height - 40) - 20;
      const yEma21 = height - ((ema21 - minP) / range) * (height - 40) - 20;

      ema9Coords.push(`${x},${yEma9}`);
      ema21Coords.push(`${x},${yEma21}`);

      return {
        x,
        yOpen,
        yClose,
        yHigh,
        yLow,
        isGreen,
        top: Math.min(yOpen, yClose),
        bodyHeight: Math.max(2, Math.abs(yClose - yOpen)),
        candleWidth,
      };
    });

    return {
      minPrice: minP,
      maxPrice: maxP,
      priceRange: range,
      svgCandles: computed,
      ema9Points: ema9Coords.join(' '),
      ema21Points: ema21Coords.join(' '),
    };
  }, [candles]);

  const currentPriceY = useMemo(() => {
    const height = 380;
    return height - ((quote.bid - minPrice) / (priceRange || 1)) * (height - 40) - 20;
  }, [quote.bid, minPrice, priceRange]);

  return (
    <div className="h-full flex flex-col bg-[#121214] text-white">
      {/* Chart Top bar */}
      <div className="px-4 py-2.5 border-b border-neutral-800 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-neutral-100">{quote.symbol}</span>
          <span className="text-neutral-500">M1</span>
          <span className="font-mono text-neutral-400 tabular-nums">
            {quote.bid.toFixed(quote.digits)}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="text-cyan-400 font-mono">EMA 9</span>
          <span className="text-amber-400 font-mono">EMA 21</span>
        </div>
      </div>

      {/* SVG Canvas Area */}
      <div className="flex-1 relative overflow-hidden flex items-center justify-center p-2">
        <svg
          viewBox="0 0 360 380"
          className="w-full h-full select-none"
          preserveAspectRatio="none"
        >
          {/* Horizontal grid lines */}
          {[0.2, 0.4, 0.6, 0.8].map(ratio => {
            const y = 380 * ratio;
            const price = maxPrice - ratio * priceRange;
            return (
              <g key={ratio}>
                <line
                  x1="0"
                  y1={y}
                  x2="360"
                  y2={y}
                  stroke="#26262a"
                  strokeDasharray="3 3"
                  strokeWidth="1"
                />
                <text
                  x="355"
                  y={y - 3}
                  textAnchor="end"
                  fill="#71717a"
                  fontSize="9"
                  fontFamily="monospace"
                >
                  {price.toFixed(quote.digits)}
                </text>
              </g>
            );
          })}

          {/* EMA Lines */}
          {ema21Points && (
            <polyline
              points={ema21Points}
              fill="none"
              stroke="#f59e0b"
              strokeWidth="1.5"
              strokeOpacity="0.8"
            />
          )}
          {ema9Points && (
            <polyline
              points={ema9Points}
              fill="none"
              stroke="#06b6d4"
              strokeWidth="1.5"
              strokeOpacity="0.85"
            />
          )}

          {/* Candlesticks */}
          {svgCandles.map((c, i) => (
            <g key={i}>
              {/* Wick */}
              <line
                x1={c.x}
                y1={c.yHigh}
                x2={c.x}
                y2={c.yLow}
                stroke={c.isGreen ? '#22c55e' : '#ef4444'}
                strokeWidth="1"
              />
              {/* Body */}
              <rect
                x={c.x - c.candleWidth / 2}
                y={c.top}
                width={c.candleWidth}
                height={c.bodyHeight}
                fill={c.isGreen ? '#22c55e' : '#ef4444'}
                rx="0.5"
              />
            </g>
          ))}

          {/* Active Burst Entry Lines */}
          {positions.length > 0 && (
            <g>
              {positions.slice(0, 3).map((pos, idx) => {
                const entryY = 380 - ((pos.openPrice - minPrice) / (priceRange || 1)) * (380 - 40) - 20;
                return (
                  <g key={idx}>
                    <line
                      x1="0"
                      y1={entryY}
                      x2="360"
                      y2={entryY}
                      stroke={pos.direction === 'buy' ? '#3b82f6' : '#ef4444'}
                      strokeDasharray="4 2"
                      strokeWidth="1"
                    />
                  </g>
                );
              })}
            </g>
          )}

          {/* Current Live Price Line */}
          <line
            x1="0"
            y1={currentPriceY}
            x2="360"
            y2={currentPriceY}
            stroke="#60a5fa"
            strokeWidth="1.2"
          />
        </svg>

        {/* Live floating price badge on right */}
        <div
          className="absolute right-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold bg-blue-600 text-white shadow-xs pointer-events-none tabular-nums"
          style={{ top: `${Math.max(10, Math.min(350, currentPriceY - 8))}px` }}
        >
          {quote.bid.toFixed(quote.digits)}
        </div>
      </div>

      {/* Bottom status */}
      <div className="px-4 py-2 border-t border-neutral-800 flex items-center justify-between text-[11px] text-neutral-400">
        <div className="flex items-center gap-2">
          <span>Active Positions:</span>
          <span className="font-mono text-white font-semibold">{positions.length}</span>
        </div>
        <div className="font-mono">
          High: {quote.high.toFixed(quote.digits)} · Low: {quote.low.toFixed(quote.digits)}
        </div>
      </div>
    </div>
  );
};
